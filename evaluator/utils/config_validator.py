"""配置校验模块

使用 Pydantic 实现配置的 Schema 校验和类型安全。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class PreprocessConfig(BaseModel):
    """预处理配置"""
    fill_missing: bool = Field(default=True, description="是否填充缺失值")
    repair_outlier: bool = Field(default=True, description="是否修复异常值")
    smooth: bool = Field(default=True, description="是否平滑处理")
    smooth_window: int = Field(default=3, ge=1, description="平滑窗口大小")
    normalize: bool = Field(default=True, description="是否标准化")

    @field_validator('smooth_window')
    @classmethod
    def validate_smooth_window(cls, v):
        if v < 1:
            raise ValueError('smooth_window must be >= 1')
        if v % 2 == 0:
            raise ValueError('smooth_window should be odd number for symmetric smoothing')
        return v


class SplitConfig(BaseModel):
    """切分配置"""
    train_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    valid_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    test_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    train_normal_ratio: Optional[float] = Field(default=None, gt=0.0, lt=1.0)
    seed: int = Field(default=42, description="随机种子")

    @field_validator('train_ratio', 'valid_ratio', 'test_ratio')
    @classmethod
    def validate_ratios(cls, v, info):
        if v is not None:
            train = info.data.get('train_ratio', 0)
            valid = info.data.get('valid_ratio', 0)
            test = info.data.get('test_ratio', 0)
            total = train + valid + test
            if total > 0 and abs(total - 1.0) > 1e-9:
                raise ValueError(f'train_ratio + valid_ratio + test_ratio must equal 1.0, got {total}')
        return v


class DatasetConfig(BaseModel):
    """数据集配置"""
    file: str = Field(..., description="数据文件路径")
    format: str = Field(default="csv", description="数据格式")
    mode: str = Field(default="basic", description="数据模式")
    table: Optional[str] = Field(default=None, description="表名 (SQLite)")
    clean_table: Optional[str] = Field(default=None, description="清洁表名")
    feature_cols: List[str] = Field(..., min_length=1, description="特征列名")
    label_col: Optional[str] = Field(default=None, description="标签列名")
    timestamp_col: Optional[str] = Field(default=None, description="时间戳列名")
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    split: SplitConfig = Field(default_factory=SplitConfig)
    simulation: Dict[str, Any] = Field(default_factory=dict)

    @field_validator('format')
    @classmethod
    def validate_format(cls, v):
        supported = ['csv', 'sqlite', 'db', 'historydb']
        if v.lower() not in supported:
            raise ValueError(f'Unsupported format: {v}. Supported: {supported}')
        return v.lower()

    @field_validator('mode')
    @classmethod
    def validate_mode(cls, v):
        supported = ['basic', 'history_db', 'simulated_db']
        if v.lower() not in supported:
            raise ValueError(f'Unsupported mode: {v}. Supported: {supported}')
        return v.lower()

    @field_validator('feature_cols')
    @classmethod
    def validate_feature_cols(cls, v):
        if not v:
            raise ValueError('feature_cols cannot be empty')
        return v


class DetectorConfig(BaseModel):
    """检测器配置

    两种声明方式：
    - `class_path`：直接指定类路径
    - `config_path`：引用 configs/detectors/*.yaml（文件内含 class_path 与 config）

    两者都可缺省，此时由 registry 按 name 解析。class_path 会在配置展开
    （resolve_detector_spec）之后才被填充，因此这里必须允许其缺失。
    """
    name: str = Field(..., description="检测器名称")
    class_path: Optional[str] = Field(default=None, description="检测器类路径")
    config_path: Optional[str] = Field(default=None, description="检测器配置文件路径")
    config: Dict[str, Any] = Field(default_factory=dict, description="检测器参数")


class RuntimeConfig(BaseModel):
    """运行时配置"""
    timeout_sec: Optional[int] = Field(default=None, ge=1, description="超时时间(秒)")
    timeout: Optional[int] = Field(default=None, ge=1, description="超时时间(秒)，timeout_sec 的别名")
    save_model: bool = Field(default=False, description="是否保存模型")
    predict_label: bool = Field(default=True, description="是否预测标签")


class MetricsConfig(BaseModel):
    """指标配置"""
    contamination: Optional[float] = Field(default=None, gt=0.0, lt=1.0, description="预期异常比例")
    enable_auc_roc: bool = Field(default=True, description="是否计算ROC-AUC")
    enable_auc_pr: bool = Field(default=True, description="是否计算PR-AUC")
    enable_f1: bool = Field(default=True, description="是否计算F1")
    enable_precision: bool = Field(default=True, description="是否计算Precision")
    enable_recall: bool = Field(default=True, description="是否计算Recall")
    enable_false_alarm_rate: bool = Field(default=True, description="是否计算误报率")


class ExperimentConfig(BaseModel):
    """实验配置"""
    name: Optional[str] = Field(default=None, description="实验名称")
    seed: int = Field(default=42, description="随机种子")
    output_dir: str = Field(default="outputs", description="输出目录")


class Config(BaseModel):
    """完整配置"""
    experiment: ExperimentConfig
    dataset: DatasetConfig
    detectors: List[DetectorConfig] = Field(..., min_length=1, description="检测器列表")
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)

    @field_validator('detectors')
    @classmethod
    def validate_detectors(cls, v):
        if not v:
            raise ValueError('detectors cannot be empty')
        return v


def validate_config(config: dict[str, Any]) -> Config:
    """校验配置

    Args:
        config: 原始配置字典

    Returns:
        Config: 校验后的配置对象

    Raises:
        ValidationError: 配置校验失败
    """
    return Config(**config)


def validate_dataset_config(dataset_config: dict[str, Any]) -> DatasetConfig:
    """校验数据集配置

    Args:
        dataset_config: 数据集配置字典

    Returns:
        DatasetConfig: 校验后的数据集配置对象
    """
    return DatasetConfig(**dataset_config)
