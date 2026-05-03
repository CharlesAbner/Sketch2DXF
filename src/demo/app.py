"""
本文件的作用：
- 提供最小可演示界面，方便你在答辩时展示输入、处理中间结果和最终输出。
- 它是把工程结果转成“可展示系统”的关键一层。

建议说明：
- 第一版界面可以非常简单，重点是流程完整和结果可见。
- 等主链路稳了，再补更好的前端体验。
"""

from __future__ import annotations

from src.config import get_default_config
from src.pipeline import run_pipeline


def infer(image_path: str) -> dict:
    """Run the pipeline from the demo UI."""
    return run_pipeline(image_path, get_default_config())


def build_demo():
    """Build a minimal Gradio demo."""
    import gradio as gr

    return gr.Interface(
        fn=infer,
        inputs=gr.Textbox(label="Image Path"),
        outputs=gr.JSON(label="Pipeline Result"),
        title="Sketch2DXF Demo",
        description="Minimal demo skeleton for the Sketch2DXF project.",
    )


if __name__ == "__main__":
    build_demo().launch()
