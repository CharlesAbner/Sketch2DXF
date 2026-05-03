from __future__ import annotations

import argparse
import os
import random
import shutil
import stat
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert CGHD Pascal VOC annotations into a filtered YOLO detection dataset."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("datasets") / "cghd",
        help="Root directory of the CGHD dataset.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("datasets") / "yolo_cghd",
        help="Output directory for YOLO images/labels and dataset.yaml.",
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        required=True,
        help="Class names to keep, for example: resistor capacitor.unpolarized inductor voltage.ac",
    )
    parser.add_argument(
        "--merge-class",
        action="append",
        default=[],
        help=(
            "Merge one or more source classes into a target training class. "
            "Format: source=target . Repeat this flag for multiple mappings, "
            "for example: --merge-class voltage.ac=power_source "
            "--merge-class voltage.dc=power_source "
            "--merge-class voltage.battery=power_source"
        ),
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Train split ratio.",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.2,
        help="Validation split ratio. Test split uses the remainder.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic splits.",
    )
    parser.add_argument(
        "--copy-images",
        action="store_true",
        help="Copy images instead of symlinking them.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Keep images with zero selected objects.",
    )
    parser.add_argument(
        "--flatten-names",
        action="store_true",
        help="Flatten output file names with drafter prefixes to avoid collisions.",
    )
    parser.add_argument(
        "--min-box-size",
        type=int,
        default=4,
        help="Minimum width and height in pixels for kept boxes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete the existing output directory before rebuilding.",
    )
    return parser


def find_drafter_dirs(source_root: Path) -> list[Path]:
    return sorted(
        path
        for path in source_root.iterdir()
        if path.is_dir() and path.name.startswith("drafter_")
    )


def read_voc_objects(xml_path: Path) -> tuple[int, int, str, list[dict]]:
    root = ET.parse(xml_path).getroot()
    width = int(root.findtext("size/width", default="0"))
    height = int(root.findtext("size/height", default="0"))
    filename = root.findtext("filename", default="")
    objects: list[dict] = []
    for obj in root.findall("object"):
        name = obj.findtext("name", default="").strip()
        bbox = obj.find("bndbox")
        if bbox is None:
            continue
        xmin = int(float(bbox.findtext("xmin", default="0")))
        ymin = int(float(bbox.findtext("ymin", default="0")))
        xmax = int(float(bbox.findtext("xmax", default="0")))
        ymax = int(float(bbox.findtext("ymax", default="0")))
        objects.append(
            {
                "class_name": name,
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
            }
        )
    return width, height, filename, objects


def clamp_bbox(
    xmin: int,
    ymin: int,
    xmax: int,
    ymax: int,
    width: int,
    height: int,
    min_box_size: int,
) -> tuple[int, int, int, int] | None:
    xmin = max(0, min(xmin, width - 1))
    ymin = max(0, min(ymin, height - 1))
    xmax = max(0, min(xmax, width))
    ymax = max(0, min(ymax, height))
    if xmax <= xmin or ymax <= ymin:
        return None
    if (xmax - xmin) < min_box_size or (ymax - ymin) < min_box_size:
        return None
    return xmin, ymin, xmax, ymax


def voc_to_yolo_line(class_id: int, bbox: tuple[int, int, int, int], width: int, height: int) -> str:
    xmin, ymin, xmax, ymax = bbox
    x_center = ((xmin + xmax) / 2.0) / width
    y_center = ((ymin + ymax) / 2.0) / height
    box_w = (xmax - xmin) / width
    box_h = (ymax - ymin) / height
    return f"{class_id} {x_center:.6f} {y_center:.6f} {box_w:.6f} {box_h:.6f}"


def split_samples(samples: list[dict], train_ratio: float, val_ratio: float, seed: int) -> dict[str, list[dict]]:
    if train_ratio <= 0 or val_ratio < 0 or train_ratio + val_ratio > 1:
        raise ValueError("train/val ratios must satisfy train>0, val>=0, train+val<=1")

    rng = random.Random(seed)
    items = samples[:]
    rng.shuffle(items)

    total = len(items)
    train_end = int(total * train_ratio)
    val_end = train_end + int(total * val_ratio)

    splits = {
        "train": items[:train_end],
        "val": items[train_end:val_end],
        "test": items[val_end:],
    }

    if total >= 2 and not splits["val"]:
        splits["val"].append(splits["train"].pop())
    if total >= 3 and not splits["test"]:
        source = "train" if len(splits["train"]) > 1 else "val"
        splits["test"].append(splits[source].pop())
    return splits


def safe_link_or_copy(src: Path, dst: Path, copy_images: bool) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy_images:
        shutil.copy2(src, dst)
        return
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy2(src, dst)


def make_output_name(sample: dict, flatten_names: bool) -> str:
    if not flatten_names:
        return sample["image_name"]
    stem = Path(sample["image_name"]).stem
    suffix = Path(sample["image_name"]).suffix
    return f"{sample['drafter_name']}__{stem}{suffix}"


def write_dataset_yaml(output_root: Path, classes: list[str]) -> Path:
    yaml_path = output_root / "dataset.yaml"
    lines = [
        f"path: {output_root.resolve().as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        f"nc: {len(classes)}",
        f"names: [{', '.join(repr(name) for name in classes)}]",
        "",
    ]
    yaml_path.write_text("\n".join(lines), encoding="utf-8")
    return yaml_path


def parse_merge_mappings(raw_mappings: list[str]) -> dict[str, str]:
    merge_map: dict[str, str] = {}
    for item in raw_mappings:
        if "=" not in item:
            raise ValueError(f"Invalid --merge-class value: {item!r}. Expected source=target.")
        source, target = item.split("=", 1)
        source = source.strip()
        target = target.strip()
        if not source or not target:
            raise ValueError(f"Invalid --merge-class value: {item!r}. Expected source=target.")
        merge_map[source] = target
    return merge_map


def collect_samples(
    source_root: Path,
    selected_classes: list[str],
    allow_empty: bool,
    min_box_size: int,
    merge_map: dict[str, str],
) -> tuple[list[dict], Counter]:
    class_to_id = {name: index for index, name in enumerate(selected_classes)}
    class_counts: Counter = Counter()
    samples: list[dict] = []

    for drafter_dir in find_drafter_dirs(source_root):
        annotations_dir = drafter_dir / "annotations"
        images_dir = drafter_dir / "images"
        if not annotations_dir.exists() or not images_dir.exists():
            continue

        for xml_path in sorted(annotations_dir.glob("*.xml")):
            width, height, image_name, objects = read_voc_objects(xml_path)
            if not image_name:
                continue
            image_path = images_dir / image_name
            if image_path.suffix.lower() not in IMAGE_SUFFIXES or not image_path.exists():
                continue

            yolo_lines: list[str] = []
            sample_counts: Counter = Counter()
            for obj in objects:
                raw_class_name = obj["class_name"]
                class_name = merge_map.get(raw_class_name, raw_class_name)
                if class_name not in class_to_id:
                    continue
                bbox = clamp_bbox(
                    obj["xmin"],
                    obj["ymin"],
                    obj["xmax"],
                    obj["ymax"],
                    width,
                    height,
                    min_box_size,
                )
                if bbox is None:
                    continue
                yolo_lines.append(voc_to_yolo_line(class_to_id[class_name], bbox, width, height))
                sample_counts[class_name] += 1
                class_counts[class_name] += 1

            if yolo_lines or allow_empty:
                samples.append(
                    {
                        "drafter_name": drafter_dir.name,
                        "image_name": image_name,
                        "image_path": image_path,
                        "label_lines": yolo_lines,
                        "class_counts": dict(sample_counts),
                    }
                )
    return samples, class_counts


def prepare_output_dirs(output_root: Path) -> None:
    for split in ("train", "val", "test"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)


def _on_rm_error(func, path, exc_info) -> None:
    _ = func, exc_info
    os.chmod(path, stat.S_IWRITE)
    os.unlink(path)


def reset_output_root(output_root: Path) -> None:
    if output_root.exists():
        shutil.rmtree(output_root, onerror=_on_rm_error)


def write_split_files(
    output_root: Path,
    splits: dict[str, list[dict]],
    copy_images: bool,
    flatten_names: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for split_name, items in splits.items():
        counts[split_name] = len(items)
        for sample in items:
            output_name = make_output_name(sample, flatten_names)
            image_dst = output_root / "images" / split_name / output_name
            label_dst = output_root / "labels" / split_name / f"{Path(output_name).stem}.txt"
            safe_link_or_copy(sample["image_path"], image_dst, copy_images)
            label_dst.write_text("\n".join(sample["label_lines"]), encoding="utf-8")
    return counts


def write_summary(output_root: Path, selected_classes: list[str], class_counts: Counter, split_counts: dict[str, int]) -> None:
    summary_path = output_root / "summary.txt"
    lines = ["Selected classes:"]
    lines.extend(f"- {name}: {class_counts.get(name, 0)} boxes" for name in selected_classes)
    lines.append("")
    lines.append("Split sizes:")
    lines.extend(f"- {split}: {count} images" for split, count in split_counts.items())
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = build_parser().parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    selected_classes = args.classes
    merge_map = parse_merge_mappings(args.merge_class)

    if not source_root.exists():
        raise SystemExit(f"Source dataset root not found: {source_root}")
    if len(selected_classes) != len(set(selected_classes)):
        raise SystemExit("Duplicate class names are not allowed.")
    merged_targets = set(merge_map.values())
    missing_targets = sorted(target for target in merged_targets if target not in selected_classes)
    if missing_targets:
        raise SystemExit(
            "Merged target classes must also be listed in --classes: "
            + ", ".join(missing_targets)
        )

    samples, class_counts = collect_samples(
        source_root,
        selected_classes,
        args.allow_empty,
        args.min_box_size,
        merge_map,
    )
    if not samples:
        raise SystemExit("No matching samples found for the selected classes.")

    if output_root.exists():
        if not args.force:
            raise SystemExit(
                f"Output directory already exists: {output_root}\n"
                "Use a new --output-root or pass --force to rebuild it."
            )
        reset_output_root(output_root)
    prepare_output_dirs(output_root)

    splits = split_samples(samples, args.train_ratio, args.val_ratio, args.seed)
    split_counts = write_split_files(output_root, splits, args.copy_images, args.flatten_names)
    yaml_path = write_dataset_yaml(output_root, selected_classes)
    write_summary(output_root, selected_classes, class_counts, split_counts)

    print(f"Prepared YOLO dataset at: {output_root}")
    print(f"dataset.yaml: {yaml_path}")
    for split_name, count in split_counts.items():
        print(f"{split_name}: {count} images")
    for class_name in selected_classes:
        print(f"{class_name}: {class_counts.get(class_name, 0)} boxes")


if __name__ == "__main__":
    main()
