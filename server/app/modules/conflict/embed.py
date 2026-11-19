"""本地语义向量：BAAI/bge-small-zh-v1.5（ONNX 推理），用于条款语义召回。

- fastembed 走 onnxruntime（与 RapidOCR 同栈），模型缓存到 server/data/models；
- 默认走 hf-mirror 镜像下载，国内网络开箱即用；
- 未安装 fastembed / 模型加载失败时自动降级：available() 返回 False，
  调用方回退到关键词召回，功能不中断。
"""
from __future__ import annotations

import os
import pathlib
import threading

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
CACHE_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "models"

# fastembed 经 huggingface_hub 下载模型；国内直连 HF 不通，默认走镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

_model = None
_model_lock = threading.Lock()
_model_failed = False


def _get_model():
    global _model, _model_failed
    if _model_failed:
        return None
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from fastembed import TextEmbedding

            _model = TextEmbedding(MODEL_NAME, cache_dir=str(CACHE_DIR))
        except Exception:
            _model_failed = True
            _model = None
        return _model


def available() -> bool:
    return _get_model() is not None


def encode(texts: list[str]) -> list[list[float]] | None:
    """批量向量化；模型不可用返回 None。超长文本截断到 300 字（模型上限 512 token）。"""
    model = _get_model()
    if model is None:
        return None
    clipped = [(t or "空").strip()[:300] or "空" for t in texts]
    return [v.tolist() for v in model.embed(clipped, batch_size=64)]
