"""Final demo/evaluation case registry for Sketch2DXF.

The final project evaluation uses:
- 14 baseline/stress cases: 001, 003, 004, 005, 101-110
- 3 showcase cases: 202, 203, 204
- 3 advanced showcase cases: 301, 302, 303
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    image_path: Path
    group: str


BASE_CASES = [
    CaseSpec("001", PROJECT_ROOT / "data" / "samples_easy" / "001_series_loop.png", "baseline"),
    CaseSpec("003", PROJECT_ROOT / "data" / "samples_easy" / "003_parallel_branches.png", "baseline"),
    CaseSpec("004", PROJECT_ROOT / "data" / "samples_easy" / "004_china_R.jpg", "baseline"),
    CaseSpec("005", PROJECT_ROOT / "data" / "samples_easy" / "005_min.png", "baseline"),
]

STRESS_CASES = [
    CaseSpec("101", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "101_series_clean.png", "stress"),
    CaseSpec("102", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "102_parallel_branches.png", "stress"),
    CaseSpec("103", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "103_broken_gap.png", "stress"),
    CaseSpec("104", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "104_text_noise_near_wire.png", "stress"),
    CaseSpec("105", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "105_crossing_no_connect.png", "stress"),
    CaseSpec("106", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "106_fake_line_near_terminal.png", "stress"),
    CaseSpec("107", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "107_slanted_wires.png", "stress"),
    CaseSpec("108", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "108_t_junction_branch.png", "stress"),
    CaseSpec("109", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "109_tiny_loop.png", "stress"),
    CaseSpec("110", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "110_rc_ladder.png", "stress"),
]

SHOWCASE_CASES = [
    CaseSpec(
        "202",
        PROJECT_ROOT / "data" / "generated" / "handdrawn_showcase" / "202_resistor_bridge_network.png",
        "showcase",
    ),
    CaseSpec(
        "203",
        PROJECT_ROOT / "data" / "generated" / "handdrawn_showcase" / "203_parallel_load_bank.png",
        "showcase",
    ),
    CaseSpec(
        "204",
        PROJECT_ROOT / "data" / "generated" / "handdrawn_showcase" / "204_dual_loop_shared_branch.png",
        "showcase",
    ),
]

ADVANCED_CASES = [
    CaseSpec(
        "301",
        PROJECT_ROOT
        / "data"
        / "generated"
        / "handdrawn_advanced_showcase"
        / "301_three_stage_rc_ladder.png",
        "advanced_showcase",
    ),
    CaseSpec(
        "302",
        PROJECT_ROOT
        / "data"
        / "generated"
        / "handdrawn_advanced_showcase"
        / "302_rectangular_bridge_network.png",
        "advanced_showcase",
    ),
    CaseSpec(
        "303",
        PROJECT_ROOT
        / "data"
        / "generated"
        / "handdrawn_advanced_showcase"
        / "303_mixed_parallel_filter_bank.png",
        "advanced_showcase",
    ),
]

FINAL_CASES = [*BASE_CASES, *STRESS_CASES, *SHOWCASE_CASES, *ADVANCED_CASES]


def final_cases(case_ids: list[str] | None = None) -> list[CaseSpec]:
    if not case_ids:
        return list(FINAL_CASES)
    wanted = {str(case_id) for case_id in case_ids}
    return [case for case in FINAL_CASES if case.case_id in wanted]


def missing_generated_case_paths() -> list[Path]:
    return [
        case.image_path
        for case in [*STRESS_CASES, *SHOWCASE_CASES, *ADVANCED_CASES]
        if not case.image_path.exists()
    ]

