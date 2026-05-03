"""Write JSON and Markdown outputs for the agent audit workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _as_dict(model_or_dict: Any) -> dict:
    if isinstance(model_or_dict, BaseModel):
        return model_or_dict.dict()
    return dict(model_or_dict)


def _severity_label(severity: str) -> str:
    return {
        "error": "错误",
        "warning": "警告",
        "info": "信息",
    }.get(severity, severity)


def render_markdown_report(report: dict) -> str:
    semantic = report.get("topology_semantic_audit", {})
    diagnoses = report.get("stage_diagnoses", [])
    actions = report.get("recommended_actions", [])
    evidence = report.get("evidence", [])
    llm = report.get("llm_assessment", {})

    lines = [
        f"# Agent Audit Report: {report.get('case_id')}",
        "",
        "## 结论",
        "",
        f"- 总体状态：`{report.get('overall_status')}`",
        f"- 主要问题：`{report.get('primary_issue')}`",
        f"- 疑似阶段：`{report.get('suspected_stage')}`",
        f"- 置信度：`{report.get('confidence')}`",
        f"- 后端：`{report.get('backend')}`，LLM used: `{report.get('llm_used')}`",
        "",
        "## 拓扑语义审查",
        "",
        f"- 是否有电源：`{semantic.get('has_power_source')}`",
        f"- 是否有地：`{semantic.get('has_ground')}`",
        f"- 所有 pin 是否接入 net：`{semantic.get('all_pins_connected')}`",
        f"- 单 pin net：`{semantic.get('single_pin_nets', [])}`",
        f"- zero-pin net：`{semantic.get('zero_pin_nets', [])}`",
        f"- 连通分量数量：`{semantic.get('connected_component_count')}`",
        f"- 电路完整性标签：`{semantic.get('circuit_completeness')}`",
        "",
    ]

    semantic_risks = semantic.get("semantic_risks", [])
    if semantic_risks:
        lines.extend(["### 语义风险", ""])
        for risk in semantic_risks:
            lines.append(
                f"- [{_severity_label(risk.get('severity'))}] "
                f"`{risk.get('risk_type')}`：{risk.get('message')}"
            )
        lines.append("")

    lines.extend(["## 阶段诊断", ""])
    for item in diagnoses:
        lines.append(
            f"- [{_severity_label(item.get('severity'))}] "
            f"`{item.get('suspected_stage')}` / `{item.get('issue_type')}` "
            f"(conf={item.get('confidence')}): {item.get('rationale')}"
        )
    if not diagnoses:
        lines.append("- 无阶段诊断。")
    lines.append("")

    lines.extend(["## 证据摘要", ""])
    for item in evidence:
        lines.append(f"- `{item.get('code')}`：{item.get('message')}")
    if not evidence:
        lines.append("- 无额外证据。")
    lines.append("")

    lines.extend(["## 建议动作", ""])
    for action in actions:
        lines.append(
            f"- `{action.get('action_id')}` / `{action.get('action_type')}` "
            f"({action.get('priority')}): {action.get('description')}"
        )
    if not actions:
        lines.append("- 无建议动作。")
    lines.append("")

    if llm.get("used") or llm.get("error"):
        lines.extend(["## LLM 增强", ""])
        if llm.get("error"):
            lines.append(f"- LLM 未使用：{llm.get('error')}")
        else:
            lines.append(f"- 摘要：{llm.get('summary')}")
            lines.append(f"- 疑似根因：{llm.get('suspected_root_cause')}")
            confirmed = llm.get("confirmed_by_artifacts", [])
            hypotheses = llm.get("hypotheses", [])
            low_notes = llm.get("low_priority_notes", [])
            recommendations = llm.get("recommended_actions", [])
            if confirmed:
                lines.append("- Artifact 支持的判断：")
                for item in confirmed:
                    lines.append(f"  - {item}")
            if hypotheses:
                lines.append("- 假设性判断：")
                for item in hypotheses:
                    lines.append(f"  - {item}")
            if low_notes:
                lines.append("- 低优先级备注：")
                for item in low_notes:
                    lines.append(f"  - {item}")
            notes = llm.get("reasoning_notes", [])
            if notes:
                lines.append("- 推理备注：")
                for note in notes:
                    lines.append(f"  - {note}")
            if recommendations:
                lines.append("- LLM 建议：")
                for item in recommendations:
                    lines.append(f"  - {item}")
        lines.append("")

    return "\n".join(lines)


def write_agent_audit_outputs(report: Any, output_dir: str | Path) -> dict[str, str]:
    """Write report JSON and Markdown files."""
    report_dict = _as_dict(report)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "agent_audit_report.json"
    md_path = out_dir / "agent_audit_report.md"
    json_path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report_dict), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
    }
