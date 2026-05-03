"""
本文件的作用：
- 负责保存和读取结构化中间结果，比如 perception、topology、audit。
- 统一 JSON 读写习惯，保证调试和答辩展示时输出可追踪。
- 已兼容 Numpy 数据类型的自动序列化。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import numpy as np


class NumpyEncoder(json.JSONEncoder):
    """
    自定义的 JSON 编码器，用于处理 Numpy 数据类型。
    将 ndarray 转换为 list，将 numpy 数值转换为 python 原生数值。
    """
    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        # 如果还有其他无法序列化的类型，交给父类处理（会抛出标准的 TypeError）
        return super().default(obj)


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    """Serialize a dict payload to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 在 dumps 时传入 cls=NumpyEncoder
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, cls=NumpyEncoder), 
        encoding="utf-8"
    )


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON file into a dictionary."""
    return json.loads(Path(path).read_text(encoding="utf-8"))