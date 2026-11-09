"""AAS adapters for torch_like USAD and TranAD model architectures.

Derived and modified from clonetimer/torch_like@1e603915896e33fd9f326073c42a477718166b68
under Apache License 2.0; LICENSE saved in licenses/torch_like-LICENSE.
Adds BaseDetector lifecycle, validation, frozen inference and checkpointing.
The upstream architectures' fit_step/score_batch mathematics are retained.
"""
from __future__ import annotations

import math
import numpy as np

from detectors.base import BaseDetector


def _torch():
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:
        raise ImportError("USAD/TranAD require PyTorch >=2.0") from exc
    return torch, nn


def _sequence(values):
    x = np.asarray(values, dtype=np.float32)
    if x.ndim == 2:
        x = x[:, None, :]
    if x.ndim != 3 or any(d == 0 for d in x.shape) or not np.isfinite(x).all():
        raise ValueError("neural detector expects finite 2D samples or 3D independent windows")
    return np.ascontiguousarray(x)


class _USADModel:  # wrapper keeps nn.Module import lazy for users without torch
    @staticmethod
    def create(channels, length, hidden_dim, latent_dim, dropout, alpha):
        torch, nn = _torch()
        class USADNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.Sequential(nn.Linear(channels*length, hidden_dim), nn.ReLU(),
                                             nn.Dropout(dropout), nn.Linear(hidden_dim, latent_dim), nn.ReLU())
                def decoder():
                    return nn.Sequential(nn.Linear(latent_dim, hidden_dim), nn.ReLU(),
                                         nn.Dropout(dropout), nn.Linear(hidden_dim, channels*length))
                self.decoder_1 = decoder()
                self.decoder_2 = decoder()
            def reconstruct(self, x):
                flat=x.reshape(x.size(0),-1)
                z=self.encoder(flat)
                w1=self.decoder_1(z).reshape_as(x)
                w2=self.decoder_2(z).reshape_as(x)
                w3=self.decoder_2(self.encoder(w1.reshape(w1.size(0),-1))).reshape_as(x)
                return w1,w2,w3
            def fit_step(self,x):
                w1,w2,w3=self.reconstruct(x)
                return (w1-x).pow(2).mean()+(w2-x).pow(2).mean()+0.5*(w3-x).pow(2).mean()
            def score_batch(self,x):
                w1,_,w3=self.reconstruct(x)
                return (alpha*(w1[:,-1]-x[:,-1]).pow(2)+
                        (1-alpha)*(w3[:,-1]-x[:,-1]).pow(2)).mean(dim=-1)
        return USADNet()


class _TranADModel:
    @staticmethod
    def create(channels, length, d_model, n_heads, e_layers, d_ff, dropout):
        torch, nn = _torch()
        class TranADNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.input_projection=nn.Linear(2*channels,d_model)
                self.position_embedding=nn.Parameter(torch.zeros(1,length,d_model))
                layer=nn.TransformerEncoderLayer(d_model,n_heads,d_ff,dropout,"gelu",
                                                 batch_first=True,norm_first=True)
                self.encoder=nn.TransformerEncoder(layer,e_layers,enable_nested_tensor=False)
                self.reconstruction_head=nn.Sequential(nn.LayerNorm(d_model),nn.Linear(d_model,channels))
                nn.init.trunc_normal_(self.position_embedding,std=0.02)
            def phase(self,x,focus):
                h=self.input_projection(torch.cat([x,focus],dim=-1))+self.position_embedding
                return self.reconstruction_head(self.encoder(h))
            def reconstruct(self,x):
                p1=self.phase(x,torch.zeros_like(x))
                p2=self.phase(x,(p1-x).pow(2).detach())
                return p1,p2
            def fit_step(self,x):
                p1,p2=self.reconstruct(x)
                return 0.5*((p1-x).pow(2).mean()+(p2-x).pow(2).mean())
            def score_batch(self,x):
                p1,p2=self.reconstruct(x)
                return 0.5*((p1[:,-1]-x[:,-1]).pow(2)+(p2[:,-1]-x[:,-1]).pow(2)).mean(dim=-1)
        return TranADNet()


class _TorchLikeNeural(BaseDetector):
    default_threshold_method = "quantile"
    supports_multivariate = True
    _algorithm_name = ""

    def __init__(self, config):
        super().__init__(config)
        self.epochs = int(config.get("epochs", 10))
        self.batch_size = int(config.get("batch_size", 64))
        self.learning_rate = float(config.get("learning_rate", 0.001))
        self.weight_decay = float(config.get("weight_decay", 0.0))
        self.seed = int(config.get("random_state", 42))
        self.contamination = float(config.get("contamination", 0.05))
        self.device_name = str(config.get("device", "cpu"))
        if (not 1<=self.epochs<=500 or not 1<=self.batch_size<=4096 or
            not math.isfinite(self.learning_rate) or not 0<self.learning_rate<=0.1 or
            not math.isfinite(self.weight_decay) or self.weight_decay<0 or
            not 0<self.contamination<0.5):
            raise ValueError("invalid neural training hyperparameters")
        self.torch, _ = _torch()
        if self.device_name == "cuda" and not self.torch.cuda.is_available():
            raise ValueError("CUDA is unavailable; select device='cpu'")
        if self.device_name not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        self.device = self.torch.device(self.device_name)
        self.window_size = int(config.get("window_size", 8))
        if not 1 <= self.window_size <= 256:
            raise ValueError("window_size must be 1..256")
        self._segment_lengths = None
        self.model = None
        self._shape = None
        self.train_stats = {}

    def set_segments(self, lengths):
        if lengths is not None and (not lengths or any(type(n) is not int or n<1 for n in lengths)):
            raise ValueError("segment lengths must be positive integers")
        self._segment_lengths=list(lengths) if lengths is not None else None

    def _prepare(self, values):
        x=_sequence(values)
        if np.asarray(values).ndim == 3:
            return x
        lengths=self._segment_lengths or [len(x)]
        if sum(lengths)!=len(x):
            raise ValueError("run segment lengths do not match neural input")
        # Trailing causal windows; left padding repeats first observation of EACH
        # run, never draws points from a preceding simulation run.
        result=np.empty((len(x), self.window_size, x.shape[-1]), dtype=np.float32)
        offset=0
        for n in lengths:
            run=x[offset:offset+n,0,:]
            pad=np.concatenate([np.repeat(run[:1],self.window_size-1,axis=0),run],axis=0)
            for j in range(n):
                result[offset+j]=pad[j:j+self.window_size]
            offset+=n
        return result

    def _create(self, channels, length):
        raise NotImplementedError

    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):
        x = self._prepare(train_X)
        if x.shape[0] < 4:
            raise ValueError("neural detector needs >=4 normal training windows")
        self.torch.manual_seed(self.seed)
        if self.torch.cuda.is_available():
            self.torch.cuda.manual_seed_all(self.seed)
        self._shape = tuple(x.shape[1:])
        self.model = self._create(x.shape[-1], x.shape[1]).to(self.device)
        generator = self.torch.Generator().manual_seed(self.seed)
        data = self.torch.from_numpy(x)
        loader = self.torch.utils.data.DataLoader(self.torch.utils.data.TensorDataset(data),
            batch_size=self.batch_size, shuffle=True, generator=generator, num_workers=0)
        optimizer = self.torch.optim.Adam(self.model.parameters(), lr=self.learning_rate,
                                           weight_decay=self.weight_decay)
        self.model.train()
        for _ in range(self.epochs):
            for (batch,) in loader:
                batch=batch.to(self.device)
                optimizer.zero_grad(set_to_none=True)
                loss=self.model.fit_step(batch)
                if not bool(self.torch.isfinite(loss)):
                    raise ValueError("nonfinite neural training loss")
                loss.backward()
                self.torch.nn.utils.clip_grad_norm_(self.model.parameters(), 5.0)
                optimizer.step()
        self.model.eval()
        reference=valid_X if valid_X is not None and len(valid_X)>1 else train_X
        scores=self.score(reference)
        extra={"contamination":self.contamination}
        if "threshold" in self.config:
            extra["threshold"]=float(self.config["threshold"])
            default="fixed"
        else:
            default="quantile"
        self.fit_threshold(scores,default_method=default,config=extra)
        self.train_stats={"channels":self._shape[-1],"sequence_length":self._shape[0],"epochs":self.epochs}

    def score(self, test_X):
        if self.model is None:
            raise RuntimeError("neural detector is not fitted")
        x=self._prepare(test_X)
        if tuple(x.shape[1:])!=self._shape:
            raise ValueError(f"neural input shape {x.shape[1:]} differs from fit {self._shape}")
        loader=self.torch.utils.data.DataLoader(self.torch.utils.data.TensorDataset(self.torch.from_numpy(x)),
                                                 batch_size=self.batch_size,shuffle=False,num_workers=0)
        self.model.eval()
        out=[]
        with self.torch.inference_mode():
            for (batch,) in loader:
                out.extend(self.model.score_batch(batch.to(self.device)).detach().cpu().tolist())
        return [float(v) for v in out]

    def export_checkpoint(self):
        if self.model is None:
            raise RuntimeError("cannot export an untrained detector")
        return {"format":"aas-detector-checkpoint-v1","algorithm":self._algorithm_name,
                "config":dict(self.config),"input_shape":list(self._shape),
                "state_dict":{k:v.detach().cpu() for k,v in self.model.state_dict().items()},
                "threshold_state":self.export_threshold_state(),"train_stats":dict(self.train_stats)}

    def restore_checkpoint(self, checkpoint):
        if checkpoint.get("format")!="aas-detector-checkpoint-v1" or checkpoint.get("algorithm")!=self._algorithm_name:
            raise ValueError("checkpoint algorithm/format mismatch")
        shape=checkpoint.get("input_shape")
        if not isinstance(shape,list) or len(shape)!=2 or any(type(x)!=int or x<1 for x in shape):
            raise ValueError("invalid neural checkpoint input shape")
        self._shape=tuple(shape)
        self.model=self._create(shape[1],shape[0]).to(self.device)
        self.model.load_state_dict(checkpoint["state_dict"],strict=True)
        self.model.eval()
        self.restore_threshold_state(checkpoint.get("threshold_state"))
        self.train_stats=dict(checkpoint.get("train_stats") or {})

    def get_meta(self):
        return {**super().get_meta(),"name":self._algorithm_name,"library":"torch_like (adapted)"}


class TorchLikeUSADDetector(_TorchLikeNeural):
    _algorithm_name="torch_like_usad"
    def __init__(self, config):
        super().__init__(config)
        self.hidden_dim=int(config.get("hidden_dim",64))
        self.latent_dim=int(config.get("latent_dim",16))
        self.alpha=float(config.get("alpha",0.5))
        self.dropout=float(config.get("dropout",0.1))
        if not (self.hidden_dim>0 and self.latent_dim>0 and 0<=self.alpha<=1 and 0<=self.dropout<1):
            raise ValueError("invalid USAD model parameters")
    def _create(self,channels,length):
        return _USADModel.create(channels,length,self.hidden_dim,self.latent_dim,self.dropout,self.alpha)


class TorchLikeTranADDetector(_TorchLikeNeural):
    _algorithm_name="torch_like_tranad"
    def __init__(self, config):
        super().__init__(config)
        self.d_model=int(config.get("d_model",32))
        self.n_heads=int(config.get("n_heads",4))
        self.e_layers=int(config.get("e_layers",1))
        self.d_ff=int(config.get("d_ff",64))
        self.dropout=float(config.get("dropout",0.1))
        if (self.d_model<1 or self.n_heads<1 or self.d_model%self.n_heads or self.e_layers<1 or
            self.d_ff<1 or not 0<=self.dropout<1):
            raise ValueError("invalid TranAD attention parameters")
    def _create(self,channels,length):
        return _TranADModel.create(channels,length,self.d_model,self.n_heads,self.e_layers,self.d_ff,self.dropout)
