"""Generate richer hand-drawn showcase circuits for Sketch2DXF.

These cases are intentionally more complex than the small regression samples,
but they only use classes already supported by the current pipeline:
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
    draw_hand_line,
    draw_label,
    draw_resistor,
    draw_source,
    finish_image,
    paper_background,
)


def draw_node_dot(image: np.ndarray, point: tuple[int, int]) -> None:
    cv2.circle(image, point, 7, (35, 35, 35), thickness=-1, lineType=cv2.LINE_AA)


def add_case_notes(
    metadata: dict,
    *,
    difficulty: str,
    supported_classes: list[str] | None = None,
) -> dict:
    return {
        **metadata,
        "difficulty": difficulty,
        "supported_classes": supported_classes
        or ["voltage_source", "resistor", "capacitor"],
        "intended_use": "showcase",
    }


def case_201_rc_ladder_two_shunts(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (140, 330), rng, "B1", "v"),
        draw_resistor(image, (335, 165), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (585, 165), rng, "R2", "h", "box"),
        draw_capacitor(image, (460, 360), rng, "C1", "v"),
        draw_capacitor(image, (780, 360), rng, "C2", "v"),
    ]

    connect_path(image, [(140, 246), (140, 165), (261, 165)], rng)
    connect_path(image, [(409, 165), (511, 165)], rng)
    connect_path(image, [(659, 165), (780, 165), (780, 286)], rng)
    connect_path(image, [(460, 165), (460, 286)], rng)
    connect_path(image, [(460, 434), (460, 520), (140, 520), (140, 414)], rng)
    connect_path(image, [(780, 434), (780, 520)], rng)

    for point in [(460, 165), (460, 520), (780, 520)]:
        draw_node_dot(image, point)
    draw_label(image, "R1", (315, 120), rng, 0.85)
    draw_label(image, "R2", (565, 120), rng, 0.85)
    draw_label(image, "C1", (485, 355), rng, 0.8)
    draw_label(image, "C2", (805, 355), rng, 0.8)

    return finish_image(image, rng), add_case_notes(
        {
            "case_id": "201_rc_ladder_two_shunts",
            "stressors": ["rc_ladder", "two_t_junctions", "multi_pin_return_bus"],
            "expected_components": comps,
            "expected_nets": [
                ["B1.p1", "R1.p1"],
                ["R1.p2", "R2.p1", "C1.p1"],
                ["R2.p2", "C2.p1"],
                ["B1.p2", "C1.p2", "C2.p2"],
            ],
        },
        difficulty="medium_showcase",
    )


def case_202_bridge_network(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (125, 330), rng, "B1", "v"),
        draw_resistor(image, (390, 180), rng, "R1", "h", "box"),
        draw_resistor(image, (620, 180), rng, "R2", "h", "box"),
        draw_resistor(image, (390, 470), rng, "R3", "h", "zigzag"),
        draw_resistor(image, (620, 470), rng, "R4", "h", "box"),
        draw_resistor(image, (505, 325), rng, "R5", "v", "box"),
    ]

    connect_path(image, [(125, 246), (125, 180), (250, 180), (316, 180)], rng)
    connect_path(image, [(250, 180), (250, 470), (316, 470)], rng)
    connect_path(image, [(464, 180), (546, 180)], rng)
    connect_path(image, [(464, 470), (546, 470)], rng)
    connect_path(image, [(505, 180), (505, 251)], rng)
    connect_path(image, [(505, 399), (505, 470)], rng)
    connect_path(image, [(694, 180), (790, 180), (790, 470), (694, 470)], rng)
    connect_path(image, [(125, 414), (125, 560), (790, 560), (790, 470)], rng)

    for point in [(250, 180), (250, 470), (505, 180), (505, 470), (790, 470)]:
        draw_node_dot(image, point)
    draw_label(image, "bridge", (420, 105), rng, 0.75)
    draw_label(image, "R5", (525, 330), rng, 0.75)

    return finish_image(image, rng), add_case_notes(
        {
            "case_id": "202_resistor_bridge_network",
            "stressors": ["bridge_network", "shared_mid_nodes", "six_components"],
            "expected_components": comps,
            "expected_nets": [
                ["B1.p1", "R1.p1", "R3.p1"],
                ["R1.p2", "R2.p1", "R5.p1"],
                ["R3.p2", "R4.p1", "R5.p2"],
                ["B1.p2", "R2.p2", "R4.p2"],
            ],
        },
        difficulty="hard_showcase",
    )


def case_203_parallel_load_bank(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (130, 335), rng, "B1", "v"),
        draw_resistor(image, (315, 165), rng, "R0", "h", "box"),
        draw_resistor(image, (435, 355), rng, "R1", "v", "box"),
        draw_capacitor(image, (600, 355), rng, "C1", "v"),
        draw_resistor(image, (765, 355), rng, "R2", "v", "zigzag"),
    ]

    connect_path(image, [(130, 251), (130, 165), (241, 165)], rng)
    connect_path(image, [(389, 165), (765, 165), (765, 281)], rng)
    connect_path(image, [(435, 165), (435, 281)], rng)
    connect_path(image, [(600, 165), (600, 281)], rng)
    connect_path(image, [(435, 429), (435, 525), (130, 525), (130, 419)], rng)
    connect_path(image, [(600, 429), (600, 525)], rng)
    connect_path(image, [(765, 429), (765, 525)], rng)
    connect_path(image, [(435, 525), (765, 525)], rng)

    for point in [(435, 165), (600, 165), (765, 165), (435, 525), (600, 525), (765, 525)]:
        draw_node_dot(image, point)
    draw_label(image, "load bank", (500, 115), rng, 0.75)

    return finish_image(image, rng), add_case_notes(
        {
            "case_id": "203_parallel_load_bank",
            "stressors": ["parallel_loads", "large_multi_pin_nodes", "mixed_component_types"],
            "expected_components": comps,
            "expected_nets": [
                ["B1.p1", "R0.p1"],
                ["R0.p2", "R1.p1", "C1.p1", "R2.p1"],
                ["B1.p2", "R1.p2", "C1.p2", "R2.p2"],
            ],
        },
        difficulty="medium_showcase",
    )


def case_204_dual_loop_shared_branch(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (135, 330), rng, "B1", "v"),
        draw_resistor(image, (415, 165), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (735, 330), rng, "R2", "v", "box"),
        draw_capacitor(image, (415, 520), rng, "C1", "h"),
        draw_resistor(image, (315, 335), rng, "R3", "v", "box"),
    ]

    connect_path(image, [(135, 246), (135, 165), (315, 165), (341, 165)], rng)
    connect_path(image, [(315, 165), (315, 261)], rng)
    connect_path(image, [(315, 409), (315, 520), (341, 520)], rng)
    connect_path(image, [(489, 165), (735, 165), (735, 256)], rng)
    connect_path(image, [(735, 404), (735, 520), (489, 520)], rng)
    connect_path(image, [(341, 520), (135, 520), (135, 414)], rng)

    for point in [(315, 165), (315, 520), (735, 520)]:
        draw_node_dot(image, point)
    draw_label(image, "shared R", (240, 330), rng, 0.7)

    return finish_image(image, rng), add_case_notes(
        {
            "case_id": "204_dual_loop_shared_branch",
            "stressors": ["dual_loop", "shared_vertical_branch", "mixed_series_parallel"],
            "expected_components": comps,
            "expected_nets": [
                ["B1.p1", "R1.p1", "R3.p1"],
                ["R1.p2", "R2.p1"],
                ["R2.p2", "C1.p2"],
                ["B1.p2", "R3.p2", "C1.p1"],
            ],
        },
        difficulty="hard_showcase",
    )


def case_205_rc_feedback_like_grid(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (130, 330), rng, "B1", "v"),
        draw_resistor(image, (330, 170), rng, "R1", "h", "box"),
        draw_resistor(image, (620, 170), rng, "R2", "h", "zigzag"),
        draw_capacitor(image, (475, 350), rng, "C1", "v"),
        draw_resistor(image, (760, 350), rng, "R3", "v", "box"),
        draw_capacitor(image, (620, 520), rng, "C2", "h"),
    ]

    connect_path(image, [(130, 246), (130, 170), (256, 170)], rng)
    connect_path(image, [(404, 170), (546, 170)], rng)
    connect_path(image, [(694, 170), (760, 170), (760, 276)], rng)
    connect_path(image, [(475, 170), (475, 276)], rng)
    connect_path(image, [(475, 424), (475, 520), (546, 520)], rng)
    connect_path(image, [(760, 424), (760, 520), (694, 520)], rng)
    connect_path(image, [(546, 520), (130, 520), (130, 414)], rng)

    # A short feedback-looking but valid connected bus segment between middle nodes.
    draw_hand_line(image, (475, 170), (475, 210), rng)
    draw_hand_line(image, (475, 210), (620, 210), rng)
    draw_hand_line(image, (620, 210), (620, 170), rng)

    for point in [(475, 170), (620, 170), (475, 520), (760, 520)]:
        draw_node_dot(image, point)
    draw_label(image, "RC grid", (530, 115), rng, 0.75)

    return finish_image(image, rng), add_case_notes(
        {
            "case_id": "205_rc_feedback_like_grid",
            "stressors": ["six_components", "short_bus_loop", "dense_but_supported"],
            "expected_components": comps,
            "expected_nets": [
                ["B1.p1", "R1.p1"],
                ["R1.p2", "R2.p1", "C1.p1"],
                ["R2.p2", "R3.p1"],
                ["B1.p2", "C1.p2", "C2.p1"],
                ["R3.p2", "C2.p2"],
            ],
        },
        difficulty="hard_showcase",
    )


CASES = [
    case_201_rc_ladder_two_shunts,
    case_202_bridge_network,
    case_203_parallel_load_bank,
    case_204_dual_loop_shared_branch,
    case_205_rc_feedback_like_grid,
]


def generate(output_dir: Path, seed: int, count: int | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_cases = CASES if count is None else CASES[: max(0, min(count, len(CASES)))]
    manifest = {
        "schema_version": "handdrawn-showcase-v1",
        "seed": seed,
        "generator": "tools/generate_showcase_circuits.py",
        "case_count": len(selected_cases),
        "supported_classes": ["voltage_source", "resistor", "capacitor"],
        "cases": [],
    }
    for index, case_fn in enumerate(selected_cases, start=1):
        rng = random.Random(seed + index * 313)
        image, metadata = case_fn(rng)
        file_name = f"{metadata['case_id']}.png"
        path = output_dir / file_name
        cv2.imwrite(str(path), image)
        manifest["cases"].append(
            {
                **metadata,
                "image_path": str(path).replace("\\", "/"),
            }
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate richer showcase circuit images.")
    parser.add_argument(
        "--output-dir",
        default="data/generated/handdrawn_showcase",
        help="Directory where generated images and manifest.json are saved.",
    )
    parser.add_argument("--seed", type=int, default=20260506, help="Random seed.")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Generate the first N showcase cases. Defaults to all 5.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = generate(Path(args.output_dir), args.seed, args.count)
    print(f"Generated {len(manifest['cases'])} showcase cases in {args.output_dir}")
    print(f"Manifest: {Path(args.output_dir) / 'manifest.json'}")
    for case in manifest["cases"]:
        print(f"- {case['case_id']}: {case['image_path']}")


if __name__ == "__main__":
    main()
