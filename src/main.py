"""
本文件的作用：
- 提供命令行入口，读取输入图像并触发完整 pipeline。
- 保持 CLI 尽量薄，只负责参数解析和结果打印。

建议说明：
- 当前定位是最小可运行入口，方便联调和快速验证。
- 后续如果需要批处理或配置文件加载，可在不污染主 pipeline 的前提下单独扩展。
"""

from __future__ import annotations

import argparse
from pprint import pprint

from src.config import get_default_config
from src.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sketch2DXF pipeline entry")
    parser.add_argument("image_path", help="Path to an input PNG/JPG image")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_pipeline(args.image_path, get_default_config())
    pprint(result)


if __name__ == "__main__":
    main()
