"""Generate advanced hand-drawn circuit showcase images.

The generated cases are deliberately denser than the normal showcase set while
staying inside the current detector/topology vocabulary:
voltage_source, resistor, and capacitor.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

from generate_handdrawn_tests import (
    connect_path,
    draw_capacitor,
    draw_label,
    draw_resistor,
    draw_source,
    finish_image,
)


CANVAS_W = 1280
CANVAS_H = 820


def paper_background(rng: random.Random) -> np.ndarray:
    base = np.full((CANVAS_H, CANVAS_W, 3), 238, dtype=np.uint8)
    paper_noise = np.random.default_rng(rng.randint(1, 999999)).normal(0, 5, base.shape).astype(np.int16)
    image = np.clip(base.astype(np.int16) + paper_noise, 0, 255).astype(np.uint8)
    for _ in range(5):
        x = rng.randint(80, CANVAS_W - 80)
        cv2.line(image, (x, 0), (x + rng.randint(-25, 25), CANVAS_H), (224, 224, 224), 1)
    for _ in range(2):
        y = rng.randint(80, CANVAS_H - 80)
        cv2.line(image, (0, y), (CANVAS_W, y + rng.randint(-12, 12)), (228, 228, 228), 1)
    return image


def draw_node_dot(image: np.ndarray, point: tuple[int, int]) -> None:
    cv2.circle(image, point, 7, (35, 35, 35), thickness=-1, lineType=cv2.LINE_AA)


def metadata(
    *,
    case_id: str,
    stressors: list[str],
    expected_components: list[dict],
    expected_nets: list[list[str]],
    difficulty: str,
) -> dict:
    return {
        "case_id": case_id,
        "difficulty": difficulty,
        "intended_use": "advanced_showcase",
        "supported_classes": ["voltage_source", "resistor", "capacitor"],
        "stressors": stressors,
        "expected_components": expected_components,
        "expected_nets": expected_nets,
    }


def case_301_three_stage_rc_ladder(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (110, 420), rng, "B1", "v"),
        draw_resistor(image, (330, 140), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (610, 140), rng, "R2", "h", "box"),
        draw_resistor(image, (890, 140), rng, "R3", "h", "zigzag"),
        draw_capacitor(image, (470, 415), rng, "C1", "v"),
        draw_capacitor(image, (750, 415), rng, "C2", "v"),
        draw_capacitor(image, (1030, 415), rng, "C3", "v"),
        draw_resistor(image, (1140, 415), rng, "R4", "v", "box"),
    ]

    connect_path(image, [(110, 336), (110, 140), (256, 140)], rng)
    connect_path(image, [(404, 140), (536, 140)], rng)
    connect_path(image, [(684, 140), (816, 140)], rng)
    connect_path(image, [(964, 140), (1140, 140), (1140, 341)], rng)
    for x, top, bottom in [(470, 341, 489), (750, 341, 489), (1030, 341, 489)]:
        connect_path(image, [(x, 140), (x, top)], rng)
        connect_path(image, [(x, bottom), (x, 690)], rng)
    connect_path(image, [(1140, 489), (1140, 690), (110, 690), (110, 504)], rng)
    for point in [(470, 140), (750, 140), (1030, 140), (470, 690), (750, 690), (1030, 690)]:
        draw_node_dot(image, point)
    draw_label(image, "3 stage RC ladder", (430, 84), rng, 0.8)

    return finish_image(image, rng), metadata(
        case_id="301_three_stage_rc_ladder",
        difficulty="advanced",
        stressors=["three_stage_ladder", "eight_components", "long_return_bus", "multiple_t_junctions"],
        expected_components=comps,
        expected_nets=[
            ["B1.p1", "R1.p1"],
            ["R1.p2", "R2.p1", "C1.p1"],
            ["R2.p2", "R3.p1", "C2.p1"],
            ["R3.p2", "C3.p1", "R4.p1"],
            ["B1.p2", "C1.p2", "C2.p2", "C3.p2", "R4.p2"],
        ],
    )


def case_302_rectangular_bridge_network(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (115, 390), rng, "B1", "v"),
        draw_resistor(image, (395, 180), rng, "R1", "h", "box"),
        draw_resistor(image, (735, 180), rng, "R2", "h", "box"),
        draw_resistor(image, (395, 610), rng, "R3", "h", "zigzag"),
        draw_resistor(image, (735, 610), rng, "R4", "h", "box"),
        draw_resistor(image, (565, 395), rng, "R5", "v", "box"),
        draw_capacitor(image, (1000, 395), rng, "C1", "v"),
        draw_resistor(image, (1145, 395), rng, "R6", "v", "box"),
    ]

    connect_path(image, [(115, 306), (115, 180), (321, 180)], rng)
    connect_path(image, [(115, 474), (115, 610), (321, 610)], rng)
    connect_path(image, [(469, 180), (661, 180)], rng)
    connect_path(image, [(469, 610), (661, 610)], rng)
    connect_path(image, [(565, 180), (565, 321)], rng)
    connect_path(image, [(565, 469), (565, 610)], rng)
    connect_path(image, [(809, 180), (1145, 180), (1145, 321)], rng)
    connect_path(image, [(809, 610), (1145, 610), (1145, 469)], rng)
    connect_path(image, [(1000, 180), (1000, 321)], rng)
    connect_path(image, [(1000, 469), (1000, 610)], rng)
    for point in [(565, 180), (565, 610), (1000, 180), (1000, 610), (1145, 180), (1145, 610)]:
        draw_node_dot(image, point)
    draw_label(image, "rect bridge", (505, 115), rng, 0.8)

    return finish_image(image, rng), metadata(
        case_id="302_rectangular_bridge_network",
        difficulty="advanced",
        stressors=["bridge_network", "eight_components", "two_rails", "central_bridge"],
        expected_components=comps,
        expected_nets=[
            ["B1.p1", "R1.p1"],
            ["B1.p2", "R3.p1"],
            ["R1.p2", "R2.p1", "R5.p1"],
            ["R3.p2", "R4.p1", "R5.p2"],
            ["R2.p2", "C1.p1", "R6.p1"],
            ["R4.p2", "C1.p2", "R6.p2"],
        ],
    )


def case_303_mixed_parallel_filter_bank(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (115, 410), rng, "B1", "v"),
        draw_resistor(image, (305, 155), rng, "R0", "h", "box"),
        draw_resistor(image, (480, 300), rng, "R1", "v", "box"),
        draw_capacitor(image, (480, 540), rng, "C1", "v"),
        draw_capacitor(image, (710, 410), rng, "C2", "v"),
        draw_resistor(image, (940, 280), rng, "R2", "v", "zigzag"),
        draw_resistor(image, (940, 540), rng, "R3", "v", "box"),
    ]

    connect_path(image, [(115, 326), (115, 155), (231, 155)], rng)
    connect_path(image, [(379, 155), (1040, 155)], rng)
    connect_path(image, [(115, 494), (115, 675), (1040, 675)], rng)
    connect_path(image, [(480, 155), (480, 226)], rng)
    connect_path(image, [(480, 374), (480, 466)], rng)
    connect_path(image, [(480, 614), (480, 675)], rng)
    connect_path(image, [(710, 155), (710, 336)], rng)
    connect_path(image, [(710, 484), (710, 675)], rng)
    connect_path(image, [(940, 155), (940, 206)], rng)
    connect_path(image, [(940, 354), (940, 466)], rng)
    connect_path(image, [(940, 614), (940, 675)], rng)
    for point in [(480, 155), (710, 155), (940, 155), (480, 675), (710, 675), (940, 675)]:
        draw_node_dot(image, point)
    draw_label(image, "parallel filter bank", (430, 98), rng, 0.75)

    return finish_image(image, rng), metadata(
        case_id="303_mixed_parallel_filter_bank",
        difficulty="advanced",
        stressors=["parallel_filter_bank", "series_branch_components", "large_bus_nodes"],
        expected_components=comps,
        expected_nets=[
            ["B1.p1", "R0.p1"],
            ["R0.p2", "R1.p1", "C2.p1", "R2.p1"],
            ["R1.p2", "C1.p1"],
            ["R2.p2", "R3.p1"],
            ["B1.p2", "C1.p2", "C2.p2", "R3.p2"],
        ],
    )


CASES = [
    case_301_three_stage_rc_ladder,
    case_302_rectangular_bridge_network,
    case_303_mixed_parallel_filter_bank,
]


def generate(output_dir: Path, seed: int, count: int | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_cases = CASES if count is None else CASES[: max(0, min(count, len(CASES)))]
    manifest = {
        "schema_version": "handdrawn-advanced-showcase-selected-v1",
        "seed": seed,
        "generator": "tools/generators/generate_advanced_showcase_circuits.py",
        "canvas_size": [CANVAS_W, CANVAS_H],
        "case_count": len(selected_cases),
        "supported_classes": ["voltage_source", "resistor", "capacitor"],
        "cases": [],
    }
    for index, case_fn in enumerate(selected_cases, start=1):
        rng = random.Random(seed + index * 419)
        image, case_metadata = case_fn(rng)
        file_name = f"{case_metadata['case_id']}.png"
        path = output_dir / file_name
        cv2.imwrite(str(path), image)
        manifest["cases"].append(
            {
                **case_metadata,
                "image_path": str(path).replace("\\", "/"),
            }
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate selected advanced hand-drawn showcase circuits (301-303).")
    parser.add_argument(
        "--output-dir",
        default="data/generated/handdrawn_advanced_showcase",
        help="Directory where generated images and manifest.json are saved.",
    )
    parser.add_argument("--seed", type=int, default=20260506, help="Random seed.")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Generate the first N selected advanced cases. Defaults to 301-303.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = generate(Path(args.output_dir), args.seed, args.count)
    print(f"Generated {len(manifest['cases'])} advanced showcase cases in {args.output_dir}")
    print(f"Manifest: {Path(args.output_dir) / 'manifest.json'}")
    for case in manifest["cases"]:
        print(f"- {case['case_id']}: {case['image_path']}")


if __name__ == "__main__":
    main()
