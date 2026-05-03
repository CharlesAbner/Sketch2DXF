"""Run the production Sketch2DXF pipeline on one image."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import OUTPUTS_DIR, get_default_config
from src.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Sketch2DXF production pipeline.")
    parser.add_argument("image_path", help="Path to an input image.")
    parser.add_argument(
        "--proposal-backend",
        choices=("traditional", "yolo"),
        default=None,
        help="Override the configured component proposal backend.",
    )
    parser.add_argument(
        "--output-summary",
        default=None,
        help="Optional summary JSON path. Defaults to outputs/reports/<image_stem>_pipeline_summary.json.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    image_path = Path(args.image_path)
    config = get_default_config()
    if args.proposal_backend:
        config["detector"]["proposal_backend"] = args.proposal_backend

    state = run_pipeline(str(image_path), config)
    output_path = (
        Path(args.output_summary)
        if args.output_summary
        else OUTPUTS_DIR / "reports" / f"{image_path.stem}_pipeline_summary.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "image_path": str(image_path),
        "proposal_backend": config["detector"]["proposal_backend"],
        "case_summary": state.get("validation", {}).get("case_summary", {}),
        "export": state.get("export", {}),
        "netlist": state.get("topology", {}).get("netlist", {}),
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": str(output_path), "export": summary["export"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
