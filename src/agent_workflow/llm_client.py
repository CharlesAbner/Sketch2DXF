"""Optional LLM enhancement for artifact-driven audit and advisor workflows."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from typing import Any

from src.agent_workflow.schemas import LlmAssessment


SYSTEM_PROMPT = """You are an audit assistant for Sketch2DXF.
You receive compact deterministic facts extracted from a circuit-topology
recovery pipeline. Do not inspect images. Do not invent new measurements.
Return JSON only. Focus on stage attribution, topology plausibility, and
non-mutating next actions.

Strict rules:
- The deterministic overall_status is authoritative.
- If deterministic overall_status is pass, do not claim failure; put any extra
  suspicion under low_priority_notes or hypotheses.
- Separate confirmed_by_artifacts from hypotheses.
- Never recommend mutating topology directly; recommend inspection or dry-run
  tools only."""


def _compact_payload(facts: dict[str, Any], report_so_far: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": {
            "case_id": facts.get("case_id"),
            "image_path": facts.get("image_path"),
            "known_stressors": facts.get("known_stressors", []),
        },
        "summary": facts.get("summary", {}),
        "semantic_audit": report_so_far.get("topology_semantic_audit", {}),
        "stage_diagnoses": report_so_far.get("stage_diagnoses", []),
        "evidence": report_so_far.get("evidence", []),
        "recommended_actions": report_so_far.get("recommended_actions", []),
    }


def _mock_assessment(facts: dict[str, Any], report_so_far: dict[str, Any]) -> LlmAssessment:
    return LlmAssessment(
        used=True,
        backend="mock",
        model="mock",
        summary=f"Mock LLM assessment for {facts.get('case_id')}.",
        suspected_root_cause=report_so_far.get("primary_issue"),
        reasoning_notes=["Mock backend was selected; no external model was called."],
        recommended_actions=[
            action.get("description", "")
            for action in report_so_far.get("recommended_actions", [])[:3]
        ],
    )


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return json.loads(cleaned)


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(
                item.get("description")
                or item.get("rationale")
                or item.get("summary")
                or json.dumps(item, ensure_ascii=False)
            )
        else:
            result.append(str(item))
    return result


def _normalize_llm_parsed(parsed: dict[str, Any], report_so_far: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "summary": parsed.get("summary"),
        "suspected_root_cause": parsed.get("suspected_root_cause"),
        "confirmed_by_artifacts": _as_text_list(parsed.get("confirmed_by_artifacts")),
        "hypotheses": _as_text_list(parsed.get("hypotheses")),
        "low_priority_notes": _as_text_list(parsed.get("low_priority_notes")),
        "reasoning_notes": _as_text_list(parsed.get("reasoning_notes")),
        "recommended_actions": _as_text_list(parsed.get("recommended_actions")),
    }
    if report_so_far.get("overall_status") == "pass":
        root_cause = normalized.get("suspected_root_cause")
        if root_cause and str(root_cause) != "no_blocking_issue_detected":
            normalized["low_priority_notes"].append(
                f"LLM hypothesis despite deterministic pass: {root_cause}"
            )
            normalized["suspected_root_cause"] = "no_blocking_issue_detected_by_deterministic_audit"
        normalized["recommended_actions"] = [
            action if action.lower().startswith("low-priority") else f"Low-priority spot-check: {action}"
            for action in normalized["recommended_actions"]
        ]
    return normalized


def _chat_completion_http(
    api_key: str,
    base_url: str | None,
    request_payload: dict[str, Any],
) -> str:
    if not base_url:
        base_url = "https://api.openai.com/v1"
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            response_json = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    choices = response_json.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices returned: {response_json}")
    message = choices[0].get("message", {})
    return message.get("content") or "{}"


def _first_value(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _provider_config(
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> dict[str, str | None]:
    if backend == "deepseek":
        key_env = api_key_env or "DEEPSEEK_API_KEY"
        return {
            "api_key": _first_value(api_key, os.environ.get(key_env)),
            "api_key_name": key_env,
            "base_url": _first_value(
                base_url,
                os.environ.get("DEEPSEEK_BASE_URL"),
                os.environ.get("OPENAI_BASE_URL"),
                "https://api.deepseek.com",
            ),
            "model": _first_value(
                model,
                os.environ.get("DEEPSEEK_MODEL"),
                os.environ.get("OPENAI_MODEL"),
                "deepseek-v4-flash",
            ),
        }
    if backend == "custom":
        key_env = api_key_env or "CUSTOM_LLM_API_KEY"
        return {
            "api_key": _first_value(api_key, os.environ.get(key_env)),
            "api_key_name": key_env,
            "base_url": _first_value(
                base_url,
                os.environ.get("CUSTOM_LLM_BASE_URL"),
                os.environ.get("OPENAI_BASE_URL"),
            ),
            "model": _first_value(
                model,
                os.environ.get("CUSTOM_LLM_MODEL"),
                os.environ.get("OPENAI_MODEL"),
            ),
        }
    key_env = api_key_env or "OPENAI_API_KEY"
    return {
        "api_key": _first_value(api_key, os.environ.get(key_env)),
        "api_key_name": key_env,
        "base_url": _first_value(base_url, os.environ.get("OPENAI_BASE_URL")),
        "model": _first_value(model, os.environ.get("OPENAI_MODEL"), "gpt-4.1-mini"),
    }


def _compatible_chat_assessment(
    facts: dict[str, Any],
    report_so_far: dict[str, Any],
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> LlmAssessment:
    config = _provider_config(backend, model, base_url, api_key, api_key_env)
    api_key = config["api_key"]
    base_url = config["base_url"]
    selected_model = config["model"]
    if backend == "custom" and not base_url:
        return LlmAssessment(
            used=False,
            backend=backend,
            model=selected_model,
            base_url=base_url,
            error="Custom provider requires --base-url or CUSTOM_LLM_BASE_URL.",
        )
    if not selected_model:
        return LlmAssessment(
            used=False,
            backend=backend,
            model=selected_model,
            base_url=base_url,
            error="Model is required. Use --model or the provider model environment variable.",
        )
    if not api_key:
        return LlmAssessment(
            used=False,
            backend=backend,
            model=selected_model,
            base_url=base_url,
            error=f"{config['api_key_name']} is not set.",
        )
    payload = _compact_payload(facts, report_so_far)
    prompt = (
        "Analyze the following Sketch2DXF audit facts and return JSON with keys: "
        "summary, suspected_root_cause, confirmed_by_artifacts, hypotheses, "
        "low_priority_notes, reasoning_notes, recommended_actions.\n"
        "Use confirmed_by_artifacts only for statements directly supported by the payload. "
        "Use hypotheses for possible causes. If overall_status is pass, keep extra suspicions "
        "as low_priority_notes and do not override the pass result.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        request = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        try:
            from openai import OpenAI  # type: ignore

            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            client = OpenAI(**client_kwargs)
            try:
                response = client.chat.completions.create(
                    **request,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(**request)
            content = response.choices[0].message.content or "{}"
        except ImportError:
            try:
                content = _chat_completion_http(
                    api_key,
                    base_url,
                    {**request, "response_format": {"type": "json_object"}},
                )
            except Exception:
                content = _chat_completion_http(api_key, base_url, request)
        parsed = _json_from_text(content)
        normalized = _normalize_llm_parsed(parsed, report_so_far)
        return LlmAssessment(
            used=True,
            backend=backend,
            model=selected_model,
            base_url=base_url,
            summary=normalized.get("summary"),
            suspected_root_cause=normalized.get("suspected_root_cause"),
            confirmed_by_artifacts=normalized["confirmed_by_artifacts"],
            hypotheses=normalized["hypotheses"],
            low_priority_notes=normalized["low_priority_notes"],
            reasoning_notes=normalized["reasoning_notes"],
            recommended_actions=normalized["recommended_actions"],
        )
    except Exception as exc:  # pragma: no cover - network/API dependent
        return LlmAssessment(
            used=False,
            backend=backend,
            model=selected_model,
            base_url=base_url,
            error=f"OpenAI call failed: {exc}",
        )


def complete_json(
    system_prompt: str,
    user_payload: dict[str, Any],
    backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call an OpenAI-compatible JSON chat completion without exposing secrets."""
    if backend == "rule":
        return {
            "used": False,
            "backend": "rule",
            "model": model,
            "base_url": base_url,
            "content": {},
            "error": None,
        }
    if backend == "mock":
        return {
            "used": True,
            "backend": "mock",
            "model": "mock",
            "base_url": base_url,
            "content": {
                "summary": "Mock LLM response; no external model was called.",
                "tool_calls": [{"tool_name": "repair_dry_run", "reason": "Mock planner selected the repair tool."}],
                "final_decision": "no_action",
                "repair_plan": {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []},
                "rationale": "Mock backend was selected.",
                "confirmed_by_artifacts": [],
                "risks": [],
                "next_actions": ["Review deterministic tool outputs."],
            },
            "error": None,
        }
    if backend not in {"openai", "deepseek", "custom"}:
        return {
            "used": False,
            "backend": backend,
            "model": model,
            "base_url": base_url,
            "content": {},
            "error": f"Unknown LLM backend: {backend}",
        }

    config = _provider_config(backend, model, base_url, api_key, api_key_env)
    selected_key = config["api_key"]
    selected_base_url = config["base_url"]
    selected_model = config["model"]
    if backend == "custom" and not selected_base_url:
        return {
            "used": False,
            "backend": backend,
            "model": selected_model,
            "base_url": selected_base_url,
            "content": {},
            "error": "Custom provider requires --base-url or CUSTOM_LLM_BASE_URL.",
        }
    if not selected_model:
        return {
            "used": False,
            "backend": backend,
            "model": selected_model,
            "base_url": selected_base_url,
            "content": {},
            "error": "Model is required. Use --model or the provider model environment variable.",
        }
    if not selected_key:
        return {
            "used": False,
            "backend": backend,
            "model": selected_model,
            "base_url": selected_base_url,
            "content": {},
            "error": f"{config['api_key_name']} is not set.",
        }

    request = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
        ],
        "temperature": temperature,
    }
    try:
        try:
            from openai import OpenAI  # type: ignore

            client_kwargs = {"api_key": selected_key}
            if selected_base_url:
                client_kwargs["base_url"] = selected_base_url
            client = OpenAI(**client_kwargs)
            try:
                response = client.chat.completions.create(
                    **request,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(**request)
            content = response.choices[0].message.content or "{}"
        except ImportError:
            try:
                content = _chat_completion_http(
                    selected_key,
                    selected_base_url,
                    {**request, "response_format": {"type": "json_object"}},
                )
            except Exception:
                content = _chat_completion_http(selected_key, selected_base_url, request)
        return {
            "used": True,
            "backend": backend,
            "model": selected_model,
            "base_url": selected_base_url,
            "content": _json_from_text(content),
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - network/API dependent
        return {
            "used": False,
            "backend": backend,
            "model": selected_model,
            "base_url": selected_base_url,
            "content": {},
            "error": f"OpenAI-compatible call failed: {exc}",
        }


def enhance_with_llm(
    facts: dict[str, Any],
    report_so_far: dict[str, Any],
    backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
) -> LlmAssessment:
    """Optionally ask an LLM to interpret deterministic audit facts."""
    if backend == "rule":
        return LlmAssessment(used=False, backend="rule", model=model)
    if backend == "mock":
        return _mock_assessment(facts, report_so_far)
    if backend in {"openai", "deepseek", "custom"}:
        return _compatible_chat_assessment(
            facts,
            report_so_far,
            backend,
            model,
            base_url,
            api_key,
            api_key_env,
        )
    return LlmAssessment(
        used=False,
        backend=backend,
        model=model,
        error=f"Unknown LLM backend: {backend}",
    )
