# Sketch2DXF Detailed Handoff Report

Date: 2026-05-07  
Workspace: `D:\code\pywork\Sketch2DXF`  
Main language/context: Chinese discussion, Python project, Windows PowerShell, conda environment `Sketch2DXF` / `sketch2dxf`.

This report is intended for another model or developer to continue the project without needing the prior conversation. It describes the current project goal, deterministic topology pipeline, agent workflow, important files, current outputs, known issues, and next action points.

## 1. Project Goal

The project is for the assignment topic:

> Semantic parsing and structured reconstruction of 2D engineering drawings. Input is a normal bitmap image, such as a hand-drawn circuit sketch or scanned schematic. Output is a reusable/editable standardized vector drawing, especially DXF.

The actual target is not pixel-perfect stroke restoration. The target is:

```text
hand-drawn circuit bitmap
-> detected components
-> component terminals / pins
-> electrical nodes
-> topology / netlist
-> DXF vector export
-> agent-assisted audit / repair / evaluation
```

The key design principle agreed throughout development:

```text
YOLO/component semantics -> wire evidence -> terminal-anchored graph inference -> topology
```

Wire extraction is treated as evidence, not final truth. The final topology should be decided by terminal/node inference and electrical semantics, not by expecting Hough/wire extraction to be perfect.

## 2. High-Level Current Architecture

Current deterministic chain is roughly version `2.2`.

Current agent chain is roughly version `3.9` in code, although some older docs still mention `3.7` or `3.8`.

Main architecture:

```text
input image
-> preprocess
-> component proposal / YOLO
-> component classification / class alternatives
-> wire evidence extraction
-> junction / endpoint extraction
-> evidence_graph
-> terminal_attachments
-> supported_graph
-> graph_nodes_dry_run
-> graph_node_selector
-> topology / netlist
-> overlay / DXF
-> audit_inputs / repair_candidates / case_summary
-> agent repair advisor
-> human-approved repair apply
-> agent eval harness
-> optional failure memory
```

Important semantic separation:

- Deterministic pipeline creates the base topology.
- Agent does not inspect pixels directly.
- Agent reads structured artifacts and calls safe tools.
- Agent proposes a `repair_plan`.
- `run_agent_repair_apply.py` only creates corrected artifacts after explicit human approval.
- Original `topology.json`, `netlist.json`, and `14_export.dxf` are not overwritten by repair apply.

## 3. Environment And Common Commands

The user usually activates conda first and then runs `python` directly:

```powershell
conda activate Sketch2DXF
python -B <script> <args>
```

Direct interpreter path sometimes used:

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B <script> <args>
```

Dependencies are in `requirements.txt`, but the project also needs `openai` for OpenAI-compatible LLM calls and `langgraph` for `--workflow-engine langgraph`.

For DeepSeek, use environment variables. Do not hard-code API keys in reports or commits:

```powershell
$env:DEEPSEEK_API_KEY="your_key"
```

Important security note:

- `tools/run_full_agent_eval14.py` currently contains a literal API key placeholder/value in its USER CONFIG block. Before pushing to GitHub or sharing the repo, remove it or replace it with an empty string. Do not expose the real key.

## 4. Repository Structure

Important root files:

- `README.md`: Current usage guide, but some agent wording may lag behind the latest `3.9` code.
- `AGENT_WORKFLOW.md`: Agent workflow guide, also partly outdated in wording.
- `PROJECT_HANDOFF.md`: Older handoff summary from 2.x / 3.x development.
- `PAPER_DRAFT.md`: Paper-style draft.
- `DATA_SCHEMA.md`, `CONFIDENCE_SCHEMA.md`, `ISSUE_TAXONOMY.md`, `GLOSSARY.md`: Schema and terminology docs.
- `debug_run.py`: Single-image step-by-step debug runner.
- `run_regression.py`: Regression runner.
- `requirements.txt`: Baseline dependencies.

Main source folders:

```text
src/
  config.py
  pipeline.py
  state.py
  preprocess/
  perception/
  topology/
  export/
  agent/
  agent_workflow/
  demo/
  eval/
  io_utils/

tools/
  generate_handdrawn_tests.py
  generate_showcase_circuits.py
  generate_advanced_showcase_circuits.py
  run_pipeline.py
  run_generalization_probe.py
  run_agent_repair_advisor.py
  run_agent_repair_apply.py
  run_agent_eval_harness.py
  run_agent_failure_memory.py
  run_full_agent_eval14.py
  run_advanced_showcase_agent_eval.py
```

Generated/test data:

```text
data/samples_easy/
  001_series_loop.png
  002_grounded_loop.png
  003_parallel_branches.png
  004_china_R.jpg
  005_min.png

data/generated/handdrawn_stress/
  101_series_clean.png ... 110_rc_ladder.png

data/generated/handdrawn_showcase/
  201 ... 205

data/generated/handdrawn_advanced_showcase/
  301_three_stage_rc_ladder.png
  302_rectangular_bridge_network.png
  303_mixed_parallel_filter_bank.png
  304_dual_loop_multi_shunt.png
  305_dense_supported_grid.png
```

## 5. Deterministic Pipeline Details

The production entry is `src/pipeline.py::run_pipeline`.

The debug entry is `debug_run.py`, which runs the same core chain but saves intermediate files and images.

### 5.1 Stages In `debug_run.py`

Valid stages:

```python
STAGES = ("preprocess", "proposal", "wire", "junction", "node", "topology", "overlay", "all")
```

`--stage` means the highest stage to execute. Later stages are skipped, earlier stages run automatically.

`--debug-level`:

- `standard`: compact audit artifacts and useful debug images.
- `full`: heavier intermediate JSON/images.

`--dxf-mode`:

- `clean`: cleaner DXF, hides internal pin/node labels by default.
- `debug`: shows debug-oriented markers/labels.

Example:

```powershell
python -B debug_run.py data\samples_easy\001_series_loop.png --proposal-backend yolo --run-name my_001_check --debug-level standard
```

### 5.2 Production Pipeline Call Sequence

From `src/pipeline.py`:

```python
image = load_image(image_path_obj)
preprocess_result = run_preprocess(image, runtime_config)
proposal_result = extract_component_proposals(preprocess_result, runtime_config)
wire_result = extract_wires(preprocess_result, proposal_result, runtime_config)
junction_result = detect_junctions_and_endpoints(preprocess_result, wire_result, runtime_config)
evidence_graph_result = build_evidence_graph(wire_result, junction_result, runtime_config)
classification_result = classify_component_proposals(proposal_result, runtime_config)
perception_result = fuse_perception_results(...)
pin_result = locate_component_pins(perception_result, runtime_config)
terminal_attachment_result = build_terminal_attachments(pin_result, evidence_graph_result, runtime_config)
supported_graph_result = build_supported_graph(evidence_graph_result, terminal_attachment_result, runtime_config)
legacy_raw_node_result = build_nodes(junction_result, wire_result, runtime_config)
legacy_match_result = match_components_to_nodes(...)
legacy_node_result = filter_nodes_by_terminal_support(...)
graph_nodes_dry_run_result = build_graph_nodes_dry_run(...)
selection_result = select_node_result_with_graph_fallback(...)
topology_result = selection_result["topology_result"]
overlay_result = render_overlay(...)
export_result = export_to_dxf(...)
audit_inputs_result = build_audit_inputs(...)
repair_candidates_result = build_repair_candidates(...)
case_summary_result = build_case_summary(...)
```

### 5.3 Preprocess Layer

Files:

- `src/preprocess/preprocess.py`
- `src/preprocess/binarize.py`
- `src/preprocess/denoise.py`
- `src/preprocess/deskew.py`
- `src/preprocess/skeletonize.py`

Outputs commonly saved by debug:

- `01_gray.png`
- `02_binary.png`
- `03_clean.png`
- `04_deskewed.png`
- `05_skeleton.png`
- `preprocess_stats.json`
- `preprocess.json`

Config section:

```python
"preprocess": {
    "adaptive_block_size": 31,
    "adaptive_c": 8,
    "median_ksize": 3,
    "morph_ksize": 3,
    "enable_deskew": False,
    "enable_skeleton": True,
}
```

### 5.4 Component Proposal / YOLO Layer

Files:

- `src/perception/component_proposals.py`
- `src/perception/yolo_component_detector.py`
- `src/perception/component_classifier.py`
- `src/perception/perception_fusion.py`

The user has already tested the YOLO layer and considers it generally good. For the main project discussion, the bottleneck is no longer YOLO but topology recovery from detected boxes to DXF.

Important behavior:

- YOLO proposal returns bbox, class name, score, and class alternatives where available.
- Class alternatives matter for cases like `005`, where a power source may be detected as a capacitor-like symbol but with an alternative power/source probability.
- The deterministic pipeline should not hard-code a class correction. Misclassification should be represented as evidence/candidates for the agent to reason about.

Config section:

```python
"detector": {
    "proposal_backend": "traditional",
    "yolo_weights": ".../detector/runs/train/cghd_power_detector/weights/best.pt",
    "yolo_imgsz": 1024,
    "yolo_conf": 0.25,
    "yolo_iou": 0.45,
    "yolo_duplicate_iou": 0.92,
    "yolo_device": "0",
}
```

Use:

```powershell
--proposal-backend yolo
```

or fallback:

```powershell
--proposal-backend traditional
```

### 5.5 Wire Evidence Layer

File:

- `src/perception/wire_extract.py`

Concept:

- Mask component regions.
- Extract line/segment evidence from the residual image.
- Hough and cleanup are evidence generators, not final topology truth.
- Wire extraction is intentionally not expected to perfectly restore all hand-drawn wires.

Important config parameters:

```python
"perception": {
    "hough_threshold": 15,
    "hough_min_line_length": 15,
    "hough_max_gap": 10,
    "wire_orientation_angle_thresh": 15.0,
    "wire_merge_axis_gap": 25,
    "wire_merge_endpoint_gap": 20,
    "wire_corner_gap": 20,
    "wire_corner_axis_slack": 20,
    "wire_corner_extension_gap": 20,
    "wire_segment_min_length": 20,
    "wire_bridge_margin": 20,
    "wire_support_axis_gap": 10,
    "wire_support_gap": 25,
    "wire_noise_near_component_margin": 18,
}
```

Known risk:

- Rules are numerous.
- This project intentionally tests rule robustness using stress/generalization probes.
- Parameters should not be tuned to only 001/003/004/005.

### 5.6 Junction / Endpoint Layer

File:

- `src/perception/junction_detect.py`

Outputs:

- `junctions.json`
- endpoints
- junction candidates

Used downstream by:

- `evidence_graph.py`
- legacy `node_builder.py`

### 5.7 Evidence Graph

File:

- `src/topology/evidence_graph.py`

Output:

- `evidence_graph.json`

Purpose:

- Convert raw wire segments/endpoints/junctions into an auditable graph.
- Preserve raw components / evidence groups before terminal support pruning.

This layer is still geometric evidence. It does not decide final electrical nodes by itself.

### 5.8 Terminal Attachments

File:

- `src/topology/terminal_attachment.py`

Output:

- `terminal_attachments.json`

Purpose:

- For each generated terminal/pin, search along a terminal corridor/ray.
- Attach pin hypotheses to nearby raw evidence/components/segments.
- Produce ranked attachment candidates and scores.

Terminal corridor is a mechanism where, instead of asking “which node is nearest to this pin globally,” the system looks forward from the pin in the expected terminal direction within a narrow corridor. This helps avoid random nearby text/noise and makes terminals the anchor for graph support.

Config:

```python
"topology": {
    "pin_corridor_enabled": True,
    "pin_corridor_length": 48,
    "pin_corridor_width": 18,
    "pin_corridor_backtrack": 4,
    "terminal_attachment_candidate_limit": 8,
}
```

### 5.9 Supported Graph

File:

- `src/topology/supported_graph.py`

Output:

- `supported_graph.json`

Purpose:

- Keep raw graph evidence that is terminal-supported or relay-supported.
- Downweight/drop unsupported evidence.
- Distinguish meaningful circuit evidence from isolated geometric noise.

Config:

```python
"topology": {
    "supported_graph_min_best_attachment_score": 0.3,
    "supported_graph_min_candidate_attachment_score": 0.5,
    "supported_graph_candidate_score_margin": 0.15,
    "supported_graph_relay_min_supported_neighbors": 2,
}
```

### 5.10 Legacy Nodes vs Graph-Derived Nodes

Files:

- `src/topology/node_builder.py`
- `src/topology/component_node_matcher.py`
- `src/topology/graph_node_dry_run.py`
- `src/topology/graph_node_selector.py`

Legacy node flow:

```text
wire/junction geometry
-> build_nodes
-> match_components_to_nodes
-> filter_nodes_by_terminal_support
```

Graph-derived node flow:

```text
supported_graph
-> graph_nodes_dry_run
-> graph_node_selector
```

Selection behavior:

- Prefer graph-derived nodes.
- Keep legacy as fallback.
- Write `node_selection.json`.

Config:

```python
"topology": {
    "use_graph_derived_nodes": True,
    "graph_nodes_enable_fallback": True,
    "graph_nodes_min_diff_match_ratio": 1.0,
    "graph_nodes_min_match_count_ratio": 1.0,
    "graph_nodes_fallback_on_repair": True,
}
```

Fallback can happen when:

- graph-derived result fails consistency thresholds,
- graph-derived result seems worse than legacy,
- repair/fallback policy triggers.

To inspect fallback:

- Open `node_selection.json`.
- Look at `selected_node_source`, `fallback_used`, `fallback_reasons`, `consistency_score`.

### 5.11 Topology / Netlist

File:

- `src/topology/topology_builder.py`

Outputs:

- `topology.json`
- `netlist.json`
- `nets.json`

Topology should be based on active electrical nodes only, not raw noisy evidence.

Important concepts:

- `components`: detected components with id/refdes/class/bbox/score/class alternatives.
- `pins`: component terminal hypotheses.
- `nodes`: electrical nodes.
- `connections`: pin-to-node attachments.
- `nets`: electrical nets.
- `component_nets`: per-component pin/net mapping.
- `netlist`: compact component/net representation.

### 5.12 Audit Inputs / Repair Candidates / Case Summary

Files:

- `src/topology/audit_inputs.py`
- `src/topology/repair_candidates.py`
- `src/topology/case_summary.py`

Outputs:

- `audit_inputs.json`
- `repair_candidates.json`
- `case_summary.json`

Purpose:

- Give humans and agents a compact structured view.
- Avoid making the LLM read dozens of huge raw artifacts.

`case_summary.json` is usually the first file to inspect for a run.

## 6. DXF Export

Files:

- `src/export/dxf_exporter.py`
- `src/export/layout_engine.py`
- `src/export/dxf_symbols.py`
- `src/export/overlay_renderer.py`

DXF export now has a cleaner mode and layout normalization.

Config:

```python
"export": {
    "drawing_unit": "mm",
    "enable_layout_normalization": True,
    "dxf_mode": "clean",
    "dxf_grid_size": 5,
    "dxf_snap_radius": 18,
    "dxf_min_wire_length": 4,
    "dxf_merge_collinear_wires": True,
    "dxf_merge_gap": 8,
    "dxf_active_wires_only": True,
    "dxf_active_wire_min_keep_ratio": 0.25,
    "dxf_output_name_mode": "input_stem",
    "netlist_output_name_mode": "input_stem",
}
```

Meaning:

- `clean` hides many internal debug markers.
- `debug` can expose internal nodes/pins/net labels.
- `active_wires_only` tries to export only wires that belong to active topology.
- Layout normalization snaps/cleans the diagram into a more schematic-like DXF.

Current limitation:

- DXF is good enough for demonstration but not yet a perfect CAD-grade schematic layout engine.
- Further polish can include standardized title block, symbol spacing, layer naming, visual net labels, and better symbol orientation.

## 7. Agent Workflow Current State

Main file:

- `src/agent_workflow/repair_advisor.py`

Current schema:

```python
SCHEMA_VERSION = "3.9-hypothesis-tool-agent"
```

Important CLI:

- `tools/run_agent_repair_advisor.py`
- `tools/run_agent_repair_apply.py`
- `tools/run_agent_eval_harness.py`
- `tools/run_agent_failure_memory.py`

Agent is not a fixed “call 5 tools” script. It is a planner/tool/reviewer workflow.

### 7.1 Agent Workflow Topology

With `--workflow-engine langgraph`, the outer loop is:

```text
audit_tool
-> observe
-> plan_next_action
-> execute_tool
-> update_state
-> decide_continue
   -> repeat plan/execute/update when needed
-> critic
-> reviewer
```

With `--workflow-engine local`, the semantics are similar, but not through LangGraph runtime.

### 7.2 LLM-Driven vs Rule-Driven Parts

LLM-driven:

- planner: chooses next tools, forms hypotheses, open questions, deferred questions.
- reviewer: reads tool results and writes final decision / repair plan.
- optional semantic eval in eval harness.
- optional agent audit if `--audit-backend deepseek/openai/custom`.

Rule/deterministic-driven:

- preprocessing, wire extraction, topology construction.
- tool execution.
- candidate validation/ranking.
- apply mechanics.
- safety checks.
- eval metric calculation.

This is intentional. In rigorous agent projects, the LLM usually orchestrates and reasons over constrained tools instead of directly mutating data structures.

### 7.3 Available Tools For Planner

Allowed tools in `repair_advisor.py`:

```text
get_case_summary
get_single_pin_nets
get_terminal_attachments
get_repair_candidates
repair_dry_run
inspect_single_pin_nets
inspect_terminal_attachments
inspect_component_class_candidates
inspect_component_terminal_axis
inspect_gap_bridge_candidates
inspect_single_pin_stub
dry_run_merge_nodes
dry_run_component_class_override
dry_run_component_axis_flip
dry_run_reattach_pin
dry_run_gap_bridge_merge
dry_run_single_pin_stub_bridge
validate_candidate
```

Granular dry-run tools:

```text
dry_run_merge_nodes
dry_run_component_class_override
dry_run_component_axis_flip
dry_run_reattach_pin
dry_run_gap_bridge_merge
dry_run_single_pin_stub_bridge
```

Applyable repair types:

```text
merge_nodes
reattach_pin
gap_bridge_merge
single_pin_stub_bridge
component_pin_axis_flip
component_class_override
```

Review-only repair type example:

```text
evidence_review
```

### 7.4 Tool Families

Latest important change: planner now receives `tool_families`.

Tool families currently in `repair_advisor.py::_available_tool_families()`:

1. `terminal_axis_or_pin_orientation`
   - consider when component has unmatched pins or weak evidence under current pin axis.
   - typical sequence:
     - `inspect_component_terminal_axis`
     - `dry_run_component_axis_flip`
   - related:
     - `inspect_terminal_attachments`
     - `dry_run_reattach_pin`

2. `single_pin_terminal_stub`
   - consider when a net has exactly one pin, an isolated pin/stub remains, or a terminal stops near a valid supported node.
   - typical sequence:
     - `inspect_single_pin_stub`
     - `dry_run_single_pin_stub_bridge`
   - related:
     - `inspect_single_pin_nets`
     - `inspect_terminal_attachments`
     - `dry_run_merge_nodes`

3. `component_class_ambiguity`
   - consider when class confidence/alternatives are ambiguous, or circuit lacks plausible source while a source-like symbol was detected as passive.
   - typical sequence:
     - `inspect_component_class_candidates`
     - `dry_run_component_class_override`

4. `gap_between_supported_nodes`
   - consider when supported nodes are near but separate, or wire evidence has a short gap.
   - typical sequence:
     - `inspect_gap_bridge_candidates`
     - `dry_run_gap_bridge_merge`

This is meant to give the LLM a tool map without hard-coding the diagnosis.

### 7.5 Open Question / Deferred Question Guard

Latest important change:

- Planner prompt now requires `deferred_questions`.
- If planner returns no tool calls while leaving `open_questions`, `_planner_self_consistency_feedback()` adds guardrail feedback.
- LangGraph/local loop will call planner again if guardrail feedback exists.
- This is not hard-coding “N4 must be fixed.” It enforces self-consistency: if the LLM itself says a problem is unresolved, it must either call a relevant tool or explicitly defer it with an artifact-based reason.

This change was made specifically because case `301` had both:

- component axis problem, fixed by `AXF1`,
- remaining single-pin/stub problem around `N4`, not fixed by the previous run.

### 7.6 Advisor Output

Default output files:

```text
agent_repair_advisor_report.json
agent_repair_advisor_report.md
agent_human_review_dossier.json
agent_human_review_dossier.md
tool_calls/
```

Important fields:

- `schema_version`
- `workflow_engine`
- `workflow_mode`
- `llm_used`
- `available_tools`
- `observation`
- `planner`
- `tool_results`
- `critic`
- `reviewer`
- `final_decision`
- `repair_plan`
- `selected_hypothesis_ids`
- `human_review_dossier`
- `agent_trace`

`repair_plan` is now the authoritative selection mechanism. Earlier fields like `selected_candidate_ids` were intentionally removed from current semantics.

Example plan shape:

```json
{
  "plan_id": "PLAN1",
  "status": "pending_human_review",
  "steps": [
    {
      "step_id": "S1",
      "candidate_id": "AXF1",
      "repair_type": "component_pin_axis_flip",
      "hypothesis_id": "H1",
      "candidate_mode": "applyable",
      "expected_improvement": ["unmatched_pin_count"],
      "depends_on": []
    }
  ]
}
```

For multi-problem cases, desired output is a plan with multiple steps, for example:

```json
{
  "plan_id": "PLAN1",
  "status": "pending_human_review",
  "steps": [
    {"step_id": "S1", "candidate_id": "AXF1", "repair_type": "component_pin_axis_flip", "depends_on": []},
    {"step_id": "S2", "candidate_id": "STB2", "repair_type": "single_pin_stub_bridge", "depends_on": ["S1"]}
  ]
}
```

## 8. Repair Dry-Run

Main file:

- `src/agent_workflow/repair_dry_run.py`

Current granular dry-run schema:

```python
"3.9-granular-repair-dry-run"
```

Candidate generators include:

- `_generate_merge_node_candidates`
- `_generate_reattach_pin_candidates`
- `_generate_component_class_override_candidates`
- `_generate_axis_flip_candidates`
- `_generate_evidence_review_candidates`
- `_generate_gap_bridge_merge_candidates`
- `_generate_single_pin_stub_bridge_candidates`

Candidate types:

```text
MRG* -> merge_nodes
RPN* -> reattach_pin
CLS* -> component_class_override
AXF* -> component_pin_axis_flip
EVR* -> evidence_review
GBR* -> gap_bridge_merge
STB* -> single_pin_stub_bridge
```

Important thresholds/constants in `repair_dry_run.py`:

```python
MERGE_NODE_MAX_CANDIDATES = 8
REATTACH_PIN_MAX_CANDIDATES = 8
AXIS_FLIP_MAX_CANDIDATES = 8
CLASS_OVERRIDE_MAX_CANDIDATES = 8
EVIDENCE_REVIEW_MAX_CANDIDATES = 8
GAP_BRIDGE_MERGE_MAX_CANDIDATES = 8
SINGLE_PIN_STUB_BRIDGE_MAX_CANDIDATES = 8
SINGLE_PIN_STUB_BRIDGE_MAX_BBOX_GAP = 90.0
REPAIR_DRY_RUN_MAX_CANDIDATES = 16
REATTACH_PIN_MIN_ATTACHMENT_SCORE = 0.55
REATTACH_PIN_WEAK_CONFIDENCE = 0.75
STUB_BRIDGE_MIN_ATTACHMENT_SCORE = 0.55
```

Dry-run candidates are validated by `candidate_validator.py` and ranked by `candidate_ranker.py`.

`accept_for_human_review` is a deterministic validator/ranker recommendation, not direct LLM approval.

## 9. Repair Apply

Main file:

- `src/agent_workflow/repair_apply.py`

CLI:

```powershell
python -B tools\run_agent_repair_apply.py <debug_dir> --advisor-dir <advisor_dir> --plan-id PLAN1 --approval accept --approved-by Lzk --notes "..." --output-dir <repair_dir>
```

Apply requires human approval:

- `--approval pending`: only writes approval request.
- `--approval accept`: writes corrected topology/netlist/DXF.
- `--approval reject`: writes rejected replay/decision.

Supported apply repair types:

```text
merge_nodes
gap_bridge_merge
single_pin_stub_bridge
reattach_pin
component_pin_axis_flip
component_class_override
```

Implementation mapping:

- `merge_nodes`, `gap_bridge_merge`, `single_pin_stub_bridge` use `_apply_merge_nodes`.
- `reattach_pin` uses `_apply_reattach_pin`.
- `component_pin_axis_flip` uses `_apply_component_pin_axis_flip`.
- `component_class_override` uses `_apply_component_class_override`.

Output files:

```text
approval_request.json
approval_request.md
approval_decision.json
corrected_topology.json
corrected_netlist.json
corrected_export.dxf
repair_replay_report.json
repair_replay_report.md
```

Important:

- `topology_mutated_in_place` should remain false.
- Corrected artifacts live under the repair output dir.
- Original debug artifacts remain unchanged.

## 10. Eval Harness

Main file:

- `src/agent_workflow/eval_harness.py`

Schemas:

```python
EVAL_SCHEMA_VERSION = "3.8-agent-eval-harness"
SUMMARY_SCHEMA_VERSION = "3.8-agent-eval-summary"
```

CLI:

```powershell
python -B tools\run_agent_eval_harness.py --case-dir <debug_dir> --advisor-dir <advisor_dir> --apply-dir <repair_dir> --strategy-name <name> --llm-backend rule --output-dir <eval_dir>
```

With LLM semantic eval:

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B tools\run_agent_eval_harness.py --case-dir <debug_dir> --advisor-dir <advisor_dir> --apply-dir <repair_dir> --strategy-name <name> --llm-backend deepseek --model deepseek-v4-pro --api-key-env DEEPSEEK_API_KEY --output-dir <eval_dir>
```

Batch summary:

```powershell
python -B tools\run_agent_eval_harness.py --cases-dir outputs\debug_runs --pattern "eval14_*_baseline" --strategy-name eval14_deepseek --llm-backend rule --output-dir outputs\debug_runs\agent_eval_summary_eval14
```

Eval tracks:

- agent behavior score
- repair effect score
- optional semantic score
- overall score
- deterministic findings
- no-action/review-only/pending/unsupported/missing apply semantics
- component identity changes

No-apply-aware statuses include:

```text
no_action_expected
review_only_expected
pending_human_approval
approval_not_accepted
unsupported_apply_type
missing_apply_unexpected
```

Semantic LLM eval prompt checks whether corrected topology is semantically more plausible, and looks for short circuit risks, missing source/load structure, suspicious merges, etc.

## 11. Batch Harnesses

### 11.1 Full 14-Case Harness

File:

- `tools/run_full_agent_eval14.py`

Purpose:

Run 14 cases:

- samples: 001, 003, 004, 005
- stress: 101-110

Flow:

```text
debug run
-> LLM advisor
-> auto-approve applyable repair plan
-> LLM/rule eval
-> summary
```

Warning:

- Current file contains a hard-coded API key value in the USER CONFIG block. Remove it before GitHub/share.
- Better pattern is to leave `API_KEY = ""` and set `$env:DEEPSEEK_API_KEY`.

### 11.2 Advanced Showcase Harness

File:

- `tools/run_advanced_showcase_agent_eval.py`

Purpose:

Run five harder generated images 301-305.

Flow:

```text
ensure generated images
-> debug run per case
-> DeepSeek advisor
-> auto-apply applyable repairs
-> eval
-> summary
```

This script reads the key from env var and does not hard-code it.

## 12. Current Important Cases And Known Issues

### 12.1 Case 005

Input:

```text
data\samples_easy\005_min.png
```

Observed problem:

- The source-like symbol is detected as `capacitor.unpolarized` with class confidence around 0.68.
- It should semantically be a power source/battery in the circuit.

Correct design:

- Do not hard-code deterministic correction from capacitor to power source.
- Keep current detected class in topology.
- Preserve class alternatives like `[capacitor.unpolarized, power_source]`.
- Let the agent inspect class ambiguity:
  - `inspect_component_class_candidates`
  - `dry_run_component_class_override`
- If dry-run produces `CLS1`, reviewer can put it in `repair_plan`, then human-approved apply can produce corrected artifacts.

### 12.2 Case 301

Input:

```text
data\generated\handdrawn_advanced_showcase\301_three_stage_rc_ladder.png
```

Recent run directory:

```text
outputs\debug_runs\agent40_301_plan
```

Current old advisor output:

```text
outputs\debug_runs\agent40_301_plan\agent_deepseek_plan
```

Current old repair output:

```text
outputs\debug_runs\agent40_301_plan\repair_deepseek_plan
```

Old run result before latest code change:

- Advisor selected only `AXF1`.
- Apply fixed component axis/pins for `cp_1`.
- `N4` remained a single-pin net.
- Before repair:
  - `single_pin_net_ids = ["N4"]`
  - `connection_count = 14`
- After AXF1:
  - `single_pin_net_ids = ["N4"]`
  - `connection_count = 16`

Meaning:

- `AXF1` fixed one real issue.
- The case still has a second real issue: single-pin/stub around `N4`.
- The previous LLM did see N4 in open questions but stopped without calling `inspect_single_pin_stub` / `dry_run_single_pin_stub_bridge`.

Latest code change after that run:

- Added tool family guidance.
- Added `deferred_questions`.
- Added self-consistency guard for open questions with no tool calls.

This latest code has not yet been verified by a successful DeepSeek rerun in the conversation.

Expected next rerun:

```powershell
$env:DEEPSEEK_API_KEY="your_key"

python -B tools\run_agent_repair_advisor.py outputs\debug_runs\agent40_301_plan --backend deepseek --model deepseek-v4-pro --api-key-env DEEPSEEK_API_KEY --audit-backend deepseek --refresh-audit --workflow-engine langgraph --max-tool-calls-per-step 1 --max-agent-tool-steps 12 --output-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily

python -B tools\run_agent_repair_apply.py outputs\debug_runs\agent40_301_plan --advisor-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily --plan-id PLAN1 --approval accept --approved-by Lzk --notes "approved tool-family repair plan for 301" --output-dir outputs\debug_runs\agent40_301_plan\repair_deepseek_toolfamily
```

Desired advisor behavior:

- It should not stop after `AXF1` if it still has open questions about N4/single-pin stub.
- It should call `inspect_single_pin_stub`.
- Then it should call `dry_run_single_pin_stub_bridge`.
- Ideally `repair_plan.steps` should include both:
  - `AXF1` for component axis flip.
  - `STB*` for single-pin stub bridge.

If it still does not, next model should inspect:

- `src/agent_workflow/repair_advisor.py::_available_tool_families`
- `src/agent_workflow/repair_advisor.py::_planner_self_consistency_feedback`
- LangGraph `decide_continue_node`
- `src/agent_workflow/repair_advisor.py::_ground_tool_call_arguments`
- `src/agent_workflow/repair_dry_run.py::_generate_single_pin_stub_bridge_candidates`

## 13. Commands To Reproduce Main Flows

### 13.1 Single Debug Run

```powershell
python -B debug_run.py data\samples_easy\001_series_loop.png --proposal-backend yolo --run-name my_001_check --debug-level standard
```

### 13.2 Single Pipeline Summary

```powershell
python -B tools\run_pipeline.py data\samples_easy\001_series_loop.png --proposal-backend yolo --output-summary outputs\reports\my_001_pipeline_summary.json
```

### 13.3 Generate Stress Images

```powershell
python -B tools\generate_handdrawn_tests.py --output-dir data\generated\handdrawn_stress
```

### 13.4 Generalization Probe

```powershell
python -B tools\run_generalization_probe.py --manifest data\generated\handdrawn_stress\manifest.json --start-index 1 --first-n 10 --output-dir outputs\generalization_probe\my_probe
```

### 13.5 Generate Advanced Showcase Images

```powershell
python -B tools\generate_advanced_showcase_circuits.py --output-dir data\generated\handdrawn_advanced_showcase
```

### 13.6 Agent Advisor With DeepSeek

Use a single-line command in PowerShell to avoid backtick mistakes:

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B tools\run_agent_repair_advisor.py outputs\debug_runs\agent40_301_plan --backend deepseek --model deepseek-v4-pro --api-key-env DEEPSEEK_API_KEY --audit-backend deepseek --refresh-audit --workflow-engine langgraph --max-tool-calls-per-step 1 --max-agent-tool-steps 12 --output-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily
```

Correct PowerShell multiline form:

```powershell
python -B tools\run_agent_repair_advisor.py outputs\debug_runs\agent40_301_plan `
  --backend deepseek `
  --model deepseek-v4-pro `
  --api-key-env DEEPSEEK_API_KEY `
  --audit-backend deepseek `
  --refresh-audit `
  --workflow-engine langgraph `
  --max-tool-calls-per-step 1 `
  --max-agent-tool-steps 12 `
  --output-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily
```

PowerShell warning:

- Backtick must be the final character on the line.
- Do not write `` `--backend deepseek` ``.
- If the parser says `invalid choice: 'deepseek\n--model'`, the line continuation was malformed.

### 13.7 Apply Repair Plan

```powershell
python -B tools\run_agent_repair_apply.py outputs\debug_runs\agent40_301_plan --advisor-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily --plan-id PLAN1 --approval accept --approved-by Lzk --notes "approved tool-family repair plan for 301" --output-dir outputs\debug_runs\agent40_301_plan\repair_deepseek_toolfamily
```

### 13.8 Eval Single Case

```powershell
python -B tools\run_agent_eval_harness.py --case-dir outputs\debug_runs\agent40_301_plan --advisor-dir outputs\debug_runs\agent40_301_plan\agent_deepseek_toolfamily --apply-dir outputs\debug_runs\agent40_301_plan\repair_deepseek_toolfamily --strategy-name toolfamily_301 --llm-backend deepseek --model deepseek-v4-pro --api-key-env DEEPSEEK_API_KEY --output-dir outputs\debug_runs\agent40_301_plan\agent_eval_toolfamily
```

### 13.9 Full 14-Case Harness

Before running, remove any hard-coded key from the script and use env var:

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B tools\run_full_agent_eval14.py
```

### 13.10 Advanced Showcase Harness

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B tools\run_advanced_showcase_agent_eval.py
```

## 14. Important Output Files And How To Read Them

For a debug run:

```text
outputs/debug_runs/<run_name>/
```

Most important human-readable images:

- `00_input.png`
- `06_proposals.png`
- `07_wires.png`
- `08_junctions.png`
- `12_active_nodes.png`
- `13_overlay.png`
- `14_export.dxf`

Most important JSON:

- `case_summary.json`: compact health summary.
- `audit_inputs.json`: best structured audit input.
- `node_selection.json`: graph-derived vs fallback decision.
- `terminal_attachments.json`: pin to evidence candidates.
- `supported_graph.json`: terminal-supported evidence.
- `graph_nodes_dry_run.json`: graph-derived nodes before final selection.
- `nodes.json`: final nodes.
- `matches.json`: pin-node matches.
- `topology.json`: final topology.
- `netlist.json`: final netlist.
- `repair_candidates.json`: deterministic review/repair candidates.

For agent advisor:

```text
outputs/debug_runs/<run_name>/<agent_dir>/
  agent_repair_advisor_report.json
  agent_repair_advisor_report.md
  agent_human_review_dossier.json
  agent_human_review_dossier.md
  tool_calls/
```

For repair apply:

```text
outputs/debug_runs/<run_name>/<repair_dir>/
  approval_request.json
  approval_request.md
  approval_decision.json
  corrected_topology.json
  corrected_netlist.json
  corrected_export.dxf
  repair_replay_report.json
  repair_replay_report.md
```

For eval:

```text
agent_eval_report.json
agent_eval_report.md
agent_eval_summary.json
agent_eval_summary.md
```

## 15. Current Limitations / Risks

1. Rule count and parameter count are high.
   - This is why generalization probes and stress tests matter.
   - Avoid tuning only for 001/003/004/005.

2. YOLO/class semantics are good but not perfect.
   - Case 005 shows capacitor/source ambiguity.
   - Correct approach is class alternatives + agent class override, not deterministic forced correction.

3. Multi-problem planning is still being actively improved.
   - Case 301 shows that fixing one candidate (`AXF1`) may leave a second problem (`N4` single-pin stub).
   - Latest code attempts to fix this with tool families and open-question guard.
   - Needs rerun with real LLM to verify.

4. Generated advanced showcase images may be harder than the deterministic pipeline can fully solve.
   - That is acceptable if the project presents them as stress/showcase cases with agent repair, not as guaranteed perfect baseline.

5. Docs are partly stale.
   - README/AGENT_WORKFLOW still mention 3.7 in places.
   - Current code is 3.9-style with granular tools, repair_plan, tool families, and deferred_questions.
   - Future cleanup should unify docs after 301 rerun verifies behavior.

6. Security cleanup needed.
   - Remove hard-coded API key from `tools/run_full_agent_eval14.py`.
   - Ensure no output reports contain real keys.

7. DXF output is improved but not final-product perfect.
   - It is good for demo and editable vector export.
   - Further visual polish remains possible.

## 16. Suggested Next Steps

Immediate next step:

1. Rerun case 301 with the latest `agent_deepseek_toolfamily` command.
2. Check whether the planner calls:
   - `inspect_single_pin_stub`
   - `dry_run_single_pin_stub_bridge`
3. Check whether `repair_plan.steps` includes both `AXF1` and `STB*`.
4. Apply `PLAN1`.
5. Check `repair_replay_report.json`:
   - `single_pin_net_count` should ideally go from 1 to 0.
   - `applied_candidates` should include both axis flip and stub bridge.

If 301 still fails:

- Inspect `agent_repair_advisor_report.md`, especially planner iterations.
- If planner defers N4, examine whether the deferral reason is valid.
- If planner calls `inspect_single_pin_stub` but no `STB*` candidate appears, debug `repair_dry_run._generate_single_pin_stub_bridge_candidates`.
- If `STB*` appears but reviewer does not include it in `repair_plan`, adjust reviewer prompt/normalization.
- If `repair_plan` includes `STB*` but apply fails, debug `repair_apply._apply_candidate` and `_apply_merge_nodes`.

After 301 works:

1. Rerun 005 to verify class override path.
2. Run 14-case harness.
3. Run advanced showcase harness.
4. Update README and AGENT_WORKFLOW to current 3.9 semantics.
5. Remove hard-coded key from `run_full_agent_eval14.py`.
6. Prepare PPT/demo:
   - show input image,
   - debug overlay,
   - topology/netlist,
   - agent tool trace,
   - human-approved repair,
   - corrected DXF.

## 17. Mental Model For Continuing Development

When debugging, do not jump straight to the final DXF. Use this order:

```text
1. 13_overlay.png / 12_active_nodes.png
2. case_summary.json
3. node_selection.json
4. terminal_attachments.json
5. supported_graph.json
6. graph_nodes_dry_run.json
7. topology.json / netlist.json
8. agent_repair_advisor_report.md
9. repair_replay_report.md
10. agent_eval_report.md
```

When deciding whether a fix belongs in deterministic code or agent code:

- If it is a stable geometric/electrical invariant, deterministic code is appropriate.
- If it is an ambiguous hypothesis requiring multiple artifacts and explanation, expose it as a tool/candidate and let the agent reason over it.
- Do not use the agent as decoration. It should choose tools, compare hypotheses, produce repair plans, and document unresolved uncertainty.

The current project is closest to:

```text
deterministic topology engine
+ LLM-driven tool planner/reviewer
+ human-approved repair replay
+ eval harness
```

This is a reasonable “real agent system” shape for a rigorous engineering task because the LLM is constrained by tools and cannot directly corrupt topology.

