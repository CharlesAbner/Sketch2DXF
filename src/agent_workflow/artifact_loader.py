"""Load debug-run artifacts for the agent audit workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ARTIFACT_FILES = {
    "case_summary": "case_summary.json",
    "audit_inputs": "audit_inputs.json",
    "repair_candidates": "repair_candidates.json",
    "terminal_attachments": "terminal_attachments.json",
    "supported_graph": "supported_graph.json",
    "graph_nodes_dry_run": "graph_nodes_dry_run.json",
    "topology": "topology.json",
    "node_selection": "node_selection.json",
    "validation": "validation.json",
}

REQUIRED_ARTIFACT_KEYS = {
    "case_summary",
    "audit_inputs",
    "repair_candidates",
    "terminal_attachments",
    "supported_graph",
    "graph_nodes_dry_run",
    "topology",
    "validation",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_path(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\\", "/")


def _load_manifest_stressors(case_id: str, image_path: str | None, debug_dir: Path) -> list[str]:
    candidates = [
        Path("data/generated/handdrawn_stress/manifest.json"),
        debug_dir.parents[1] / "data" / "generated" / "handdrawn_stress" / "manifest.json"
        if len(debug_dir.parents) > 1
        else Path("data/generated/handdrawn_stress/manifest.json"),
    ]
    image_path_norm = _normalize_path(image_path)
    for manifest_path in candidates:
        if not manifest_path.exists():
            continue
        try:
            manifest = _read_json(manifest_path) or {}
        except json.JSONDecodeError:
            continue
        for case in manifest.get("cases", []):
            if case.get("case_id") == case_id:
                return list(case.get("stressors", []))
            if image_path_norm and _normalize_path(case.get("image_path")) == image_path_norm:
                return list(case.get("stressors", []))
    return []


def load_case_artifacts(debug_dir: str | Path) -> dict[str, Any]:
    """Load all known audit artifacts from a debug-run directory."""
    base_dir = Path(debug_dir)
    artifacts: dict[str, Any] = {}
    artifact_paths: dict[str, str] = {}
    missing = []
    for key, file_name in ARTIFACT_FILES.items():
        path = base_dir / file_name
        data = _read_json(path)
        if data is None:
            missing.append(file_name)
            continue
        artifacts[key] = data
        artifact_paths[key] = str(path)
    missing_required = [
        ARTIFACT_FILES[key]
        for key in sorted(REQUIRED_ARTIFACT_KEYS)
        if key not in artifacts
    ]

    case_summary = artifacts.get("case_summary", {})
    case_id = case_summary.get("case_id") or base_dir.name
    image_path = case_summary.get("image_path")
    known_stressors = _load_manifest_stressors(case_id, image_path, base_dir)

    return {
        "debug_dir": str(base_dir),
        "case_id": case_id,
        "image_path": image_path,
        "known_stressors": known_stressors,
        "artifacts": artifacts,
        "artifact_paths": artifact_paths,
        "missing_artifacts": missing,
        "missing_required_artifacts": missing_required,
        "core_artifacts_ok": not missing_required,
    }
