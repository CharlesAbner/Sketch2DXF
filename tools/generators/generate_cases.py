"""Generate the final synthetic case suites used by the project.

This replaces the older habit of running several separate generator scripts by
hand.  The final suite keeps 101-110, 202-204, and 301-303.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_DIR = Path(__file__).resolve().parent
if str(GENERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATOR_DIR))

from generate_handdrawn_tests import generate as generate_stress_cases
import generate_advanced_showcase_circuits as advanced_gen
import generate_showcase_circuits as showcase_gen


CaseFn = Callable[[random.Random], tuple[np.ndarray, dict]]


def _write_selected_cases(
    *,
    output_dir: Path,
    seed: int,
    schema_version: str,
    generator_name: str,
    selected_cases: list[tuple[int, CaseFn]],
    rng_multiplier: int,
    canvas_size: list[int] | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": schema_version,
        "seed": seed,
        "generator": generator_name,
        "case_count": len(selected_cases),
        "supported_classes": ["voltage_source", "resistor", "capacitor"],
        "cases": [],
    }
    if canvas_size is not None:
        manifest["canvas_size"] = canvas_size

    for case_number, case_fn in selected_cases:
        rng = random.Random(seed + case_number * rng_multiplier)
        image, metadata = case_fn(rng)
        file_name = f"{metadata['case_id']}.png"
        path = output_dir / file_name
        cv2.imwrite(str(path), image)
        manifest["cases"].append({**metadata, "image_path": str(path).replace("\\", "/")})

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def generate_showcase_selected(output_dir: Path, seed: int) -> dict:
    return _write_selected_cases(
        output_dir=output_dir,
        seed=seed,
        schema_version="handdrawn-showcase-final-v1",
        generator_name="tools/generators/generate_cases.py::showcase_202_204",
        selected_cases=[
            (2, showcase_gen.case_202_bridge_network),
            (3, showcase_gen.case_203_parallel_load_bank),
            (4, showcase_gen.case_204_dual_loop_shared_branch),
        ],
        rng_multiplier=313,
    )


def generate_advanced_selected(output_dir: Path, seed: int) -> dict:
    return _write_selected_cases(
        output_dir=output_dir,
        seed=seed,
        schema_version="handdrawn-advanced-showcase-final-v1",
        generator_name="tools/generators/generate_cases.py::advanced_301_303",
        selected_cases=[
            (1, advanced_gen.case_301_three_stage_rc_ladder),
            (2, advanced_gen.case_302_rectangular_bridge_network),
            (3, advanced_gen.case_303_mixed_parallel_filter_bank),
        ],
        rng_multiplier=419,
        canvas_size=[advanced_gen.CANVAS_W, advanced_gen.CANVAS_H],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate final Sketch2DXF synthetic cases.")
    parser.add_argument(
        "--suite",
        choices=("final", "stress", "showcase", "advanced"),
        default="final",
        help="Which suite to generate.",
    )
    parser.add_argument("--seed", type=int, default=20260506, help="Base random seed.")
    parser.add_argument(
        "--stress-seed",
        type=int,
        default=20260430,
        help="Seed for stress cases 101-110.",
    )
    parser.add_argument(
        "--stress-output-dir",
        default="data/generated/handdrawn_stress",
        help="Output directory for cases 101-110.",
    )
    parser.add_argument(
        "--showcase-output-dir",
        default="data/generated/handdrawn_showcase",
        help="Output directory for cases 202-204.",
    )
    parser.add_argument(
        "--advanced-output-dir",
        default="data/generated/handdrawn_advanced_showcase",
        help="Output directory for cases 301-303.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    reports = {}
    if args.suite in {"final", "stress"}:
        reports["stress"] = generate_stress_cases(Path(args.stress_output_dir), args.stress_seed)
    if args.suite in {"final", "showcase"}:
        reports["showcase"] = generate_showcase_selected(Path(args.showcase_output_dir), args.seed)
    if args.suite in {"final", "advanced"}:
        reports["advanced"] = generate_advanced_selected(Path(args.advanced_output_dir), args.seed)

    print(
        json.dumps(
            {
                "generated_suites": list(reports.keys()),
                "case_counts": {name: report.get("case_count") for name, report in reports.items()},
                "manifests": {
                    "stress": str(Path(args.stress_output_dir) / "manifest.json"),
                    "showcase": str(Path(args.showcase_output_dir) / "manifest.json"),
                    "advanced": str(Path(args.advanced_output_dir) / "manifest.json"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
