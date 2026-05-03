from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train a YOLO detector on a prepared dataset.yaml file."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("datasets") / "yolo_cghd" / "dataset.yaml",
        help="Path to dataset.yaml.",
    )
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="Ultralytics model name or local .pt checkpoint.",
    )
    parser.add_argument("--epochs", type=int, default=80, help="Training epochs.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Input image size.")
    parser.add_argument("--batch", type=int, default=8, help="Batch size.")
    parser.add_argument("--device", default="0", help="CUDA device id or cpu.")
    parser.add_argument("--workers", type=int, default=4, help="Dataloader workers.")
    parser.add_argument(
        "--project",
        type=Path,
        default=Path("runs") / "train",
        help="Directory to store training outputs.",
    )
    parser.add_argument(
        "--name",
        default="cghd_detector",
        help="Run name inside the project directory.",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=30,
        help="Early stopping patience.",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable image caching for faster repeated training.",
    )
    parser.add_argument(
        "--pretrained",
        action="store_true",
        help="Keep pretrained weights enabled.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics is not installed. Install it with `pip install ultralytics` first."
        ) from exc

    data_path = args.data.resolve()
    if not data_path.exists():
        raise SystemExit(f"dataset.yaml not found: {data_path}")

    args.project.mkdir(parents=True, exist_ok=True)
    model = YOLO(args.model)
    model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        project=str(args.project.resolve()),
        name=args.name,
        patience=args.patience,
        cache=args.cache,
        pretrained=args.pretrained,
    )


if __name__ == "__main__":
    main()
