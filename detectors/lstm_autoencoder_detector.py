from __future__ import annotations

from detectors.base import BaseDetector
from detectors.decision import DECISION_MODE_SCORE_THRESHOLD


class LSTMAutoEncoderDetector(BaseDetector):  # pylint: disable=abstract-method
    default_decision_mode = DECISION_MODE_SCORE_THRESHOLD
    supported_decision_modes = (DECISION_MODE_SCORE_THRESHOLD,)

    """PyTorch LSTM autoencoder anomaly detector with portable checkpoint support."""

    default_threshold_method = "quantile"

    def __init__(self, config: dict):
        super().__init__(config)
        try:
            import torch
            import torch.nn as nn
        except ImportError as exc:
            raise ImportError(
                "LSTMAutoEncoderDetector requires PyTorch. "
                "Install it with: uv add torch or pip install torch"
            ) from exc

        self.torch = torch
        self.nn = nn
        self.device = torch.device(config.get("device", "cpu"))
        self.hidden_size = int(config.get("hidden_size", 32))
        self.num_layers = int(config.get("num_layers", 1))
        self.learning_rate = float(config.get("learning_rate", 1e-3))
        self.epochs = int(config.get("epochs", 20))
        self.batch_size = int(config.get("batch_size", 32))
        self.threshold_value = float(config["threshold"]) if "threshold" in config else None
        self.contamination = float(config.get("contamination", 0.05))
        self.model = None
        self._input_size = None

    def _build_model(self, input_size: int):
        nn = self.nn
        hidden_size = self.hidden_size
        num_layers = self.num_layers

        class AutoEncoder(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.LSTM(
                    input_size=input_size,
                    hidden_size=hidden_size,
                    num_layers=num_layers,
                    batch_first=True,
                )
                self.decoder = nn.LSTM(
                    input_size=hidden_size,
                    hidden_size=input_size,
                    num_layers=num_layers,
                    batch_first=True,
                )

            def forward(self, x):
                encoded, _ = self.encoder(x)
                decoded, _ = self.decoder(encoded)
                return decoded

        self.model = AutoEncoder().to(self.device)
        self._input_size = int(input_size)
        return self.model

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None) -> None:
        train_tensor = self._to_sequence_tensor(train_X)
        input_size = int(train_tensor.size(-1))
        self._build_model(input_size)

        optimizer = self.torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)
        criterion = self.nn.MSELoss()
        dataset = self.torch.utils.data.TensorDataset(train_tensor)
        loader = self.torch.utils.data.DataLoader(dataset, batch_size=self.batch_size, shuffle=True)

        self.model.train()
        for _ in range(self.epochs):
            for (batch,) in loader:
                batch = batch.to(self.device)
                optimizer.zero_grad()
                reconstructed = self.model(batch)
                loss = criterion(reconstructed, batch)
                loss.backward()
                optimizer.step()

        reference_X = valid_X if valid_X is not None and len(valid_X) > 0 else train_X
        reference_scores = self.score(reference_X)
        extra = {"contamination": self.contamination}
        if self.threshold_value is not None:
            extra["threshold"] = self.threshold_value
            default_method = "fixed"
        else:
            default_method = "quantile"
        self.threshold_value = self.fit_threshold(
            reference_scores,
            default_method=default_method,
            config=extra,
        )

    def score(self, test_X) -> list[float]:
        """Return per-sample reconstruction error."""
        if self.model is None:
            raise RuntimeError("Model is not fitted.")

        inputs = self._to_sequence_tensor(test_X).to(self.device)
        self.model.eval()
        with self.torch.no_grad():
            reconstructed = self.model(inputs)
            errors = ((reconstructed - inputs) ** 2).mean(dim=(1, 2))

        result = errors.cpu().tolist()
        if not isinstance(result, list):
            return [float(result)]
        return [float(value) for value in result]

    def export_checkpoint(self) -> dict:
        """Export enough state to reconstruct the detector in a new process."""
        if self.model is None or self._input_size is None:
            raise RuntimeError("Model is not fitted.")
        return {
            "format": "aas-detector-checkpoint-v1",
            "algorithm": "lstm_autoencoder_detector",
            "config": dict(self.config),
            "input_size": int(self._input_size),
            "hidden_size": int(self.hidden_size),
            "num_layers": int(self.num_layers),
            "threshold_value": self.threshold_value,
            "threshold_state": self.export_threshold_state(),
            "state_dict": self.model.state_dict(),
        }

    def restore_checkpoint(self, checkpoint: dict) -> None:
        """Restore a checkpoint produced by :meth:`export_checkpoint`."""
        if not isinstance(checkpoint, dict):
            raise TypeError("checkpoint must be a dict")
        if checkpoint.get("format") != "aas-detector-checkpoint-v1":
            raise ValueError("unsupported checkpoint format")
        if "input_size" not in checkpoint or "state_dict" not in checkpoint:
            raise ValueError("checkpoint is missing input_size/state_dict")

        self.hidden_size = int(checkpoint.get("hidden_size", self.hidden_size))
        self.num_layers = int(checkpoint.get("num_layers", self.num_layers))
        self.threshold_value = checkpoint.get("threshold_value", self.threshold_value)
        self._build_model(int(checkpoint["input_size"]))
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.to(self.device)
        self.model.eval()
        self.restore_threshold_state(checkpoint.get("threshold_state"))

    def get_meta(self) -> dict:
        meta = super().get_meta()
        meta.update({"name": "lstm_autoencoder_detector", "library": "torch"})
        return meta

    def _to_sequence_tensor(self, samples):
        tensor = self.torch.tensor(samples, dtype=self.torch.float32)
        if tensor.ndim == 2:
            tensor = tensor.unsqueeze(1)
        if tensor.ndim != 3:
            raise ValueError("LSTMAutoEncoderDetector expects 2D or 3D input samples.")
        return tensor
