from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run YOLO inference and save visualization outputs."
    )
    parser.add_argument(
        "--weights",
        type=Path,
        default=Path("runs") / "train" / "cghd_detector" / "weights" / "best.pt",
        help="Path to trained weights.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Image file or directory to run inference on.",
    )
    parser.add_argument("--imgsz", type=int, default=1024, help="Inference image size.")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold.")
    parser.add_argument("--device", default="0", help="CUDA device id or cpu.")
    parser.add_argument(
        "--project",
        type=Path,
        default=Path("runs") / "predict",
        help="Directory to store inference outputs.",
    )
    parser.add_argument(
        "--name",
        default="cghd_detector",
        help="Run name inside the project directory.",
    )
    parser.add_argument(
        "--save-txt",
        action="store_true",
        help="Also save YOLO txt predictions.",
    )
    parser.add_argument(
        "--save-conf",
        action="store_true",
        help="Save confidence values into txt predictions.",
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

    weights_path = args.weights.resolve()
    source_path = args.source.resolve()
    if not weights_path.exists():
        raise SystemExit(f"Weights not found: {weights_path}")
    if not source_path.exists():
        raise SystemExit(f"Source not found: {source_path}")

    args.project.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(weights_path))
    model.predict(
        source=str(source_path),
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        project=str(args.project.resolve()),
        name=args.name,
        save=True,
        save_txt=args.save_txt,
        save_conf=args.save_conf,
    )


if __name__ == "__main__":
    main()
