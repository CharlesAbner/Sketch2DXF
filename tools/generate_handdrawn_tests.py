"""Generate synthetic hand-drawn circuit stress images for Sketch2DXF."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import cv2
import numpy as np


CANVAS_W = 960
CANVAS_H = 640


def jitter_point(point: tuple[int, int], rng: random.Random, amount: int = 3) -> tuple[int, int]:
    return (
        int(point[0] + rng.randint(-amount, amount)),
        int(point[1] + rng.randint(-amount, amount)),
    )


def draw_hand_line(
    image: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    rng: random.Random,
    color: tuple[int, int, int] = (35, 35, 35),
    thickness: int = 5,
    jitter: int = 3,
    passes: int = 2,
) -> None:
    for pass_index in range(passes):
        s = jitter_point(start, rng, jitter + pass_index)
        e = jitter_point(end, rng, jitter + pass_index)
        cv2.line(image, s, e, color, max(1, thickness - pass_index), lineType=cv2.LINE_AA)


def draw_polyline(
    image: np.ndarray,
    points: list[tuple[int, int]],
    rng: random.Random,
    color: tuple[int, int, int] = (35, 35, 35),
    thickness: int = 5,
) -> None:
    for start, end in zip(points, points[1:]):
        draw_hand_line(image, start, end, rng, color=color, thickness=thickness)


def draw_label(image: np.ndarray, text: str, pos: tuple[int, int], rng: random.Random, scale: float = 1.0) -> None:
    x, y = jitter_point(pos, rng, 4)
    cv2.putText(
        image,
        text,
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (45, 45, 45),
        3,
        lineType=cv2.LINE_AA,
    )


def bbox_from_points(points: list[tuple[int, int]], pad: int = 14) -> list[int]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def draw_resistor(
    image: np.ndarray,
    center: tuple[int, int],
    rng: random.Random,
    component_id: str,
    orientation: str = "h",
    style: str = "box",
) -> dict:
    cx, cy = center
    if orientation == "h":
        if style == "zigzag":
            points = [(cx - 70, cy), (cx - 45, cy), (cx - 35, cy - 24), (cx - 20, cy + 24),
                      (cx - 5, cy - 24), (cx + 10, cy + 24), (cx + 25, cy - 24),
                      (cx + 40, cy + 24), (cx + 50, cy), (cx + 70, cy)]
            draw_polyline(image, points, rng)
            bbox = bbox_from_points(points, 18)
        else:
            draw_hand_line(image, (cx - 74, cy), (cx - 42, cy), rng)
            draw_hand_line(image, (cx + 42, cy), (cx + 74, cy), rng)
            rect = [(cx - 42, cy - 22), (cx + 42, cy - 22), (cx + 42, cy + 22), (cx - 42, cy + 22), (cx - 42, cy - 22)]
            draw_polyline(image, rect, rng)
            bbox = [cx - 58, cy - 34, cx + 58, cy + 34]
        pins = [(cx - 74, cy), (cx + 74, cy)]
    else:
        draw_hand_line(image, (cx, cy - 74), (cx, cy - 42), rng)
        draw_hand_line(image, (cx, cy + 42), (cx, cy + 74), rng)
        rect = [(cx - 22, cy - 42), (cx + 22, cy - 42), (cx + 22, cy + 42), (cx - 22, cy + 42), (cx - 22, cy - 42)]
        draw_polyline(image, rect, rng)
        bbox = [cx - 34, cy - 58, cx + 34, cy + 58]
        pins = [(cx, cy - 74), (cx, cy + 74)]
    return {
        "id": component_id,
        "class_name": "resistor",
        "bbox": bbox,
        "pins": pins,
        "orientation": orientation,
    }


def draw_capacitor(
    image: np.ndarray,
    center: tuple[int, int],
    rng: random.Random,
    component_id: str,
    orientation: str = "h",
) -> dict:
    cx, cy = center
    if orientation == "h":
        draw_hand_line(image, (cx - 74, cy), (cx - 18, cy), rng)
        draw_hand_line(image, (cx + 18, cy), (cx + 74, cy), rng)
        draw_hand_line(image, (cx - 12, cy - 46), (cx - 12, cy + 46), rng)
        draw_hand_line(image, (cx + 12, cy - 46), (cx + 12, cy + 46), rng)
        bbox = [cx - 34, cy - 58, cx + 34, cy + 58]
        pins = [(cx - 74, cy), (cx + 74, cy)]
    else:
        draw_hand_line(image, (cx, cy - 74), (cx, cy - 18), rng)
        draw_hand_line(image, (cx, cy + 18), (cx, cy + 74), rng)
        draw_hand_line(image, (cx - 46, cy - 12), (cx + 46, cy - 12), rng)
        draw_hand_line(image, (cx - 46, cy + 12), (cx + 46, cy + 12), rng)
        bbox = [cx - 58, cy - 34, cx + 58, cy + 34]
        pins = [(cx, cy - 74), (cx, cy + 74)]
    return {
        "id": component_id,
        "class_name": "capacitor",
        "bbox": bbox,
        "pins": pins,
        "orientation": orientation,
    }


def draw_source(
    image: np.ndarray,
    center: tuple[int, int],
    rng: random.Random,
    component_id: str,
    orientation: str = "v",
) -> dict:
    cx, cy = center
    if orientation == "v":
        draw_hand_line(image, (cx, cy - 84), (cx, cy - 42), rng)
        draw_hand_line(image, (cx, cy + 42), (cx, cy + 84), rng)
        draw_hand_line(image, (cx - 42, cy - 22), (cx + 42, cy - 22), rng)
        draw_hand_line(image, (cx - 28, cy + 20), (cx + 28, cy + 20), rng)
        bbox = [cx - 54, cy - 42, cx + 54, cy + 42]
        pins = [(cx, cy - 84), (cx, cy + 84)]
    else:
        draw_hand_line(image, (cx - 84, cy), (cx - 42, cy), rng)
        draw_hand_line(image, (cx + 42, cy), (cx + 84, cy), rng)
        draw_hand_line(image, (cx - 22, cy - 42), (cx - 22, cy + 42), rng)
        draw_hand_line(image, (cx + 20, cy - 28), (cx + 20, cy + 28), rng)
        bbox = [cx - 42, cy - 54, cx + 42, cy + 54]
        pins = [(cx - 84, cy), (cx + 84, cy)]
    return {
        "id": component_id,
        "class_name": "voltage_source",
        "bbox": bbox,
        "pins": pins,
        "orientation": orientation,
    }


def connect_path(image: np.ndarray, points: list[tuple[int, int]], rng: random.Random, gap_index: int | None = None) -> None:
    for index, (start, end) in enumerate(zip(points, points[1:])):
        if gap_index is not None and index == gap_index:
            mid_x = int((start[0] + end[0]) / 2)
            mid_y = int((start[1] + end[1]) / 2)
            draw_hand_line(image, start, (mid_x - 14, mid_y), rng)
            draw_hand_line(image, (mid_x + 16, mid_y), end, rng)
        else:
            draw_hand_line(image, start, end, rng)


def paper_background(rng: random.Random) -> np.ndarray:
    base = np.full((CANVAS_H, CANVAS_W, 3), 238, dtype=np.uint8)
    noise = rng.normalvariate(0, 1)
    _ = noise
    paper_noise = np.random.default_rng(rng.randint(1, 999999)).normal(0, 5, base.shape).astype(np.int16)
    image = np.clip(base.astype(np.int16) + paper_noise, 0, 255).astype(np.uint8)
    for _ in range(3):
        x = rng.randint(60, CANVAS_W - 60)
        cv2.line(image, (x, 0), (x + rng.randint(-20, 20), CANVAS_H), (225, 225, 225), 1)
    return image


def finish_image(image: np.ndarray, rng: random.Random) -> np.ndarray:
    if rng.random() < 0.65:
        image = cv2.GaussianBlur(image, (3, 3), 0)
    return image


def case_series_clean(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (170, 320), rng, "B1", "v"),
        draw_resistor(image, (430, 170), rng, "R1", "h", "box"),
        draw_resistor(image, (680, 320), rng, "R2", "v", "box"),
    ]
    connect_path(image, [(170, 236), (170, 170), (356, 170)], rng)
    connect_path(image, [(504, 170), (680, 170), (680, 246)], rng)
    connect_path(image, [(680, 394), (680, 500), (170, 500), (170, 404)], rng)
    draw_label(image, "R1", (410, 130), rng)
    draw_label(image, "R2", (710, 320), rng)
    return finish_image(image, rng), {
        "case_id": "101_series_clean",
        "stressors": ["baseline", "hand_jitter"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "R2.p1"], ["R2.p2", "B1.p2"]],
    }


def case_parallel_branches(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (160, 320), rng, "B1", "v"),
        draw_resistor(image, (470, 210), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (470, 430), rng, "R2", "h", "box"),
    ]
    connect_path(image, [(160, 236), (300, 236), (300, 210), (396, 210)], rng)
    connect_path(image, [(300, 236), (300, 430), (396, 430)], rng)
    connect_path(image, [(544, 210), (760, 210), (760, 430), (544, 430)], rng)
    connect_path(image, [(760, 430), (760, 520), (160, 520), (160, 404)], rng)
    draw_label(image, "R1", (450, 165), rng)
    draw_label(image, "R2", (450, 390), rng)
    return finish_image(image, rng), {
        "case_id": "102_parallel_branches",
        "stressors": ["parallel", "multi_terminal_same_node"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1", "R2.p1"], ["R1.p2", "R2.p2", "B1.p2"]],
    }


def case_broken_gap(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (160, 320), rng, "B1", "v"),
        draw_resistor(image, (455, 170), rng, "R1", "h", "box"),
        draw_capacitor(image, (720, 320), rng, "C1", "v"),
    ]
    connect_path(image, [(160, 236), (160, 170), (381, 170)], rng, gap_index=1)
    connect_path(image, [(529, 170), (720, 170), (720, 246)], rng)
    connect_path(image, [(720, 394), (720, 505), (160, 505), (160, 404)], rng)
    draw_label(image, "gap", (250, 140), rng, 0.75)
    return finish_image(image, rng), {
        "case_id": "103_broken_gap",
        "stressors": ["intentional_wire_gap", "gap_bridge_review"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "C1.p1"], ["C1.p2", "B1.p2"]],
    }


def case_text_noise(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (155, 335), rng, "B1", "v"),
        draw_resistor(image, (450, 190), rng, "R1", "h", "box"),
        draw_resistor(image, (730, 335), rng, "R2", "v", "box"),
    ]
    connect_path(image, [(155, 251), (155, 190), (376, 190)], rng)
    connect_path(image, [(524, 190), (730, 190), (730, 261)], rng)
    connect_path(image, [(730, 409), (730, 500), (155, 500), (155, 419)], rng)
    draw_label(image, "R", (300, 350), rng, 1.6)
    draw_label(image, "1", (335, 390), rng, 1.1)
    return finish_image(image, rng), {
        "case_id": "104_text_noise_near_wire",
        "stressors": ["letter_residue", "unsupported_evidence_review"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "R2.p1"], ["R2.p2", "B1.p2"]],
    }


def case_crossing_no_connect(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (150, 310), rng, "B1", "v"),
        draw_resistor(image, (425, 160), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (425, 460), rng, "R2", "h", "box"),
        draw_capacitor(image, (750, 310), rng, "C1", "v"),
    ]
    connect_path(image, [(150, 226), (150, 160), (351, 160)], rng)
    connect_path(image, [(499, 160), (750, 160), (750, 236)], rng)
    connect_path(image, [(750, 384), (750, 460), (499, 460)], rng)
    connect_path(image, [(351, 460), (150, 460), (150, 394)], rng)
    draw_hand_line(image, (450, 70), (450, 255), rng)
    draw_hand_line(image, (450, 365), (450, 555), rng)
    draw_label(image, "no dot", (465, 330), rng, 0.7)
    return finish_image(image, rng), {
        "case_id": "105_crossing_no_connect",
        "stressors": ["crossing_without_connection", "false_junction_risk"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "C1.p1"], ["C1.p2", "R2.p2"], ["R2.p1", "B1.p2"]],
    }


def case_fake_near_terminal(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (160, 330), rng, "B1", "v"),
        draw_resistor(image, (450, 180), rng, "R1", "h", "box"),
        draw_resistor(image, (730, 330), rng, "R2", "v", "box"),
    ]
    connect_path(image, [(160, 246), (160, 180), (376, 180)], rng)
    connect_path(image, [(524, 180), (730, 180), (730, 256)], rng)
    connect_path(image, [(730, 404), (730, 500), (160, 500), (160, 414)], rng)
    draw_hand_line(image, (525, 215), (615, 215), rng, thickness=4)
    draw_label(image, "fake", (540, 255), rng, 0.7)
    return finish_image(image, rng), {
        "case_id": "106_fake_line_near_terminal",
        "stressors": ["near_terminal_false_candidate", "ambiguous_attachment"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "R2.p1"], ["R2.p2", "B1.p2"]],
    }


def case_slanted_wires(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (170, 320), rng, "B1", "v"),
        draw_resistor(image, (460, 165), rng, "R1", "h", "zigzag"),
        draw_capacitor(image, (720, 320), rng, "C1", "v"),
    ]
    connect_path(image, [(170, 236), (190, 170), (386, 165)], rng)
    connect_path(image, [(534, 165), (705, 185), (720, 246)], rng)
    connect_path(image, [(720, 394), (705, 505), (170, 500), (170, 404)], rng)
    return finish_image(image, rng), {
        "case_id": "107_slanted_wires",
        "stressors": ["slanted_handdrawn_wire", "orientation_threshold"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "C1.p1"], ["C1.p2", "B1.p2"]],
    }


def case_t_junction(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (150, 320), rng, "B1", "v"),
        draw_resistor(image, (420, 170), rng, "R1", "h", "box"),
        draw_resistor(image, (640, 360), rng, "R2", "v", "box"),
        draw_capacitor(image, (420, 500), rng, "C1", "h"),
    ]
    connect_path(image, [(150, 236), (150, 170), (346, 170)], rng)
    connect_path(image, [(494, 170), (640, 170), (640, 286)], rng)
    connect_path(image, [(640, 434), (640, 500), (494, 500)], rng)
    connect_path(image, [(346, 500), (150, 500), (150, 404)], rng)
    draw_hand_line(image, (640, 500), (640, 170), rng)
    return finish_image(image, rng), {
        "case_id": "108_t_junction_branch",
        "stressors": ["t_junction", "multi_pin_node"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "R2.p1"], ["R2.p2", "C1.p2"], ["C1.p1", "B1.p2"]],
    }


def case_tiny_loop(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (250, 320), rng, "B1", "v"),
        draw_resistor(image, (500, 240), rng, "R1", "h", "box"),
    ]
    connect_path(image, [(250, 236), (250, 240), (426, 240)], rng)
    connect_path(image, [(574, 240), (640, 240), (640, 420), (250, 420), (250, 404)], rng)
    return finish_image(image, rng), {
        "case_id": "109_tiny_loop",
        "stressors": ["small_layout", "short_segments"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "B1.p2"]],
    }


def case_ladder(rng: random.Random) -> tuple[np.ndarray, dict]:
    image = paper_background(rng)
    comps = [
        draw_source(image, (130, 330), rng, "B1", "v"),
        draw_resistor(image, (350, 190), rng, "R1", "h", "zigzag"),
        draw_resistor(image, (610, 190), rng, "R2", "h", "box"),
        draw_capacitor(image, (480, 390), rng, "C1", "v"),
    ]
    connect_path(image, [(130, 246), (130, 190), (276, 190)], rng)
    connect_path(image, [(424, 190), (536, 190)], rng)
    connect_path(image, [(684, 190), (780, 190), (780, 520), (130, 520), (130, 414)], rng)
    connect_path(image, [(480, 190), (480, 316)], rng)
    connect_path(image, [(480, 464), (480, 520)], rng)
    return finish_image(image, rng), {
        "case_id": "110_rc_ladder",
        "stressors": ["ladder", "branch_to_bus", "t_junction"],
        "expected_components": comps,
        "expected_nets": [["B1.p1", "R1.p1"], ["R1.p2", "R2.p1", "C1.p1"], ["R2.p2", "C1.p2", "B1.p2"]],
    }


CASES = [
    case_series_clean,
    case_parallel_branches,
    case_broken_gap,
    case_text_noise,
    case_crossing_no_connect,
    case_fake_near_terminal,
    case_slanted_wires,
    case_t_junction,
    case_tiny_loop,
    case_ladder,
]


def generate(output_dir: Path, seed: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "handdrawn-stress-v1",
        "seed": seed,
        "generator": "tools/generate_handdrawn_tests.py",
        "cases": [],
    }
    for index, case_fn in enumerate(CASES, start=1):
        rng = random.Random(seed + index * 101)
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
    parser = argparse.ArgumentParser(description="Generate hand-drawn circuit stress images.")
    parser.add_argument(
        "--output-dir",
        default="data/generated/handdrawn_stress",
        help="Directory where generated images and manifest.json are saved.",
    )
    parser.add_argument("--seed", type=int, default=20260430, help="Random seed.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = generate(Path(args.output_dir), args.seed)
    print(f"Generated {len(manifest['cases'])} cases in {args.output_dir}")
    print(f"Manifest: {Path(args.output_dir) / 'manifest.json'}")


if __name__ == "__main__":
    main()
