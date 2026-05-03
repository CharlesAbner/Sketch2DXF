"""Annotate evidence graph items with terminal-support status.

This is still an audit/dry-run layer. It does not replace node_builder or
topology_builder; it only marks which raw evidence is supported by terminal
attachments and which bridge candidates connect supported evidence blocks.
"""

from __future__ import annotations


TERMINAL_SUPPORTED_STATUSES = {"best_terminal_supported", "candidate_terminal_supported"}
PATH_SUPPORTED_STATUSES = {*TERMINAL_SUPPORTED_STATUSES, "relay_supported"}


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _config_values(config: dict) -> dict:
    topology_cfg = config["topology"]
    return {
        "min_best_attachment_score": float(topology_cfg.get("supported_graph_min_best_attachment_score", 0.3)),
        "min_candidate_attachment_score": float(
            topology_cfg.get("supported_graph_min_candidate_attachment_score", 0.5)
        ),
        "candidate_score_margin": float(topology_cfg.get("supported_graph_candidate_score_margin", 0.15)),
        "relay_min_supported_neighbors": int(topology_cfg.get("supported_graph_relay_min_supported_neighbors", 2)),
        "force_all_components_supported": bool(
            topology_cfg.get("supported_graph_force_all_components_supported", False)
        ),
    }


def _raw_component_lookups(evidence_graph: dict) -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    components_by_id = {}
    vertex_to_component = {}
    edge_to_component = {}
    for component in evidence_graph.get("raw_components", []):
        component_id = component["raw_component_id"]
        components_by_id[component_id] = component
        for vertex_id in component.get("vertex_ids", []):
            vertex_to_component[vertex_id] = component_id
        for edge_id in component.get("edge_ids", []):
            edge_to_component[edge_id] = component_id
    return components_by_id, vertex_to_component, edge_to_component


def _support_links_from_attachments(terminal_attachments: dict, config_values: dict) -> list[dict]:
    support_links = []
    for attachment in terminal_attachments.get("attachments", []):
        best_attachment_id = attachment.get("best_attachment_id")
        best_score = float(attachment.get("best_attachment_score", 0.0))
        for candidate in attachment.get("candidates", []):
            raw_component_id = candidate.get("raw_component_id")
            if raw_component_id is None:
                continue
            score = float(candidate.get("attachment_score", 0.0))
            support_kind = None
            if candidate.get("attachment_id") == best_attachment_id:
                if score >= config_values["min_best_attachment_score"]:
                    support_kind = "best_terminal_support"
            elif (
                score >= config_values["min_candidate_attachment_score"]
                and best_score - score <= config_values["candidate_score_margin"]
            ):
                support_kind = "candidate_terminal_support"
            if support_kind is None:
                continue

            support_links.append(
                {
                    "support_link_id": f"SL{len(support_links) + 1}",
                    "support_kind": support_kind,
                    "attachment_id": candidate.get("attachment_id"),
                    "pin_id": attachment["pin_id"],
                    "component_id": attachment["component_id"],
                    "raw_component_id": raw_component_id,
                    "evidence_kind": candidate.get("evidence_kind"),
                    "evidence_id": candidate.get("evidence_id"),
                    "graph_edge_id": candidate.get("graph_edge_id"),
                    "graph_vertex_id": candidate.get("graph_vertex_id"),
                    "attachment_score": round(float(score), 3),
                    "distance": candidate.get("distance"),
                    "in_corridor": candidate.get("in_corridor"),
                    "projected_point": candidate.get("projected_point"),
                }
            )
    return support_links


def _support_summary(support_links: list[dict]) -> dict:
    pin_ids = _unique([link["pin_id"] for link in support_links])
    component_ids = _unique([link["component_id"] for link in support_links])
    best_links = [link for link in support_links if link["support_kind"] == "best_terminal_support"]
    candidate_links = [link for link in support_links if link["support_kind"] == "candidate_terminal_support"]
    scores = [float(link.get("attachment_score", 0.0)) for link in support_links]
    return {
        "support_pin_ids": pin_ids,
        "support_component_ids": component_ids,
        "best_support_pin_ids": _unique([link["pin_id"] for link in best_links]),
        "candidate_support_pin_ids": _unique([link["pin_id"] for link in candidate_links]),
        "terminal_support_count": len(pin_ids),
        "component_support_count": len(component_ids),
        "best_support_count": len(best_links),
        "candidate_support_count": len(candidate_links),
        "max_attachment_score": round(max(scores), 3) if scores else 0.0,
        "avg_attachment_score": round(sum(scores) / max(len(scores), 1), 3) if scores else 0.0,
        "support_link_ids": [link["support_link_id"] for link in support_links],
    }


def _component_status(summary: dict) -> str:
    if summary["best_support_count"] > 0:
        return "best_terminal_supported"
    if summary["candidate_support_count"] > 0:
        return "candidate_terminal_supported"
    return "unsupported"


def _force_component_supported_for_ablation(component: dict, support_info: dict) -> dict:
    if support_info.get("support_status") != "unsupported":
        return support_info
    return {
        **support_info,
        "support_status": "forced_supported_for_ablation",
        "force_support_reason": "unsupported_filtering_ablation",
        "support_pin_ids": support_info.get("support_pin_ids", []),
        "support_component_ids": support_info.get("support_component_ids", []),
    }


def _annotate_raw_components(
    evidence_graph: dict,
    support_links: list[dict],
    force_all_components_supported: bool,
) -> tuple[list[dict], dict[str, dict]]:
    support_by_component: dict[str, list[dict]] = {}
    for link in support_links:
        support_by_component.setdefault(link["raw_component_id"], []).append(link)

    annotated_components = []
    support_info_by_component = {}
    for component in evidence_graph.get("raw_components", []):
        component_links = support_by_component.get(component["raw_component_id"], [])
        summary = _support_summary(component_links)
        status = _component_status(summary)
        support_info = {
            "support_status": status,
            **summary,
        }
        if force_all_components_supported:
            support_info = _force_component_supported_for_ablation(component, support_info)
        support_info_by_component[component["raw_component_id"]] = support_info
        annotated_components.append(
            {
                **component,
                **support_info,
            }
        )
    return annotated_components, support_info_by_component


def _direct_support_by_field(support_links: list[dict], field_name: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for link in support_links:
        value = link.get(field_name)
        if value is None:
            continue
        result.setdefault(value, []).append(link)
    return result


def _item_status(
    direct_links: list[dict],
    raw_component_id: str | None,
    support_info_by_component: dict[str, dict],
) -> str:
    if any(link["support_kind"] == "best_terminal_support" for link in direct_links):
        return "direct_best_supported"
    if any(link["support_kind"] == "candidate_terminal_support" for link in direct_links):
        return "direct_candidate_supported"
    if raw_component_id is not None:
        component_status = support_info_by_component.get(raw_component_id, {}).get("support_status")
        if component_status in TERMINAL_SUPPORTED_STATUSES:
            return "component_supported"
        if component_status == "relay_supported":
            return "relay_component_supported"
    return "unsupported"


def _annotate_edges(
    evidence_graph: dict,
    edge_to_component: dict[str, str],
    support_info_by_component: dict[str, dict],
    support_links: list[dict],
) -> list[dict]:
    direct_support_by_edge = _direct_support_by_field(support_links, "graph_edge_id")
    annotated_edges = []
    for edge in evidence_graph.get("edges", []):
        edge_id = edge["edge_id"]
        raw_component_id = edge_to_component.get(edge_id)
        direct_links = direct_support_by_edge.get(edge_id, [])
        support_summary = _support_summary(direct_links)
        annotated_edges.append(
            {
                **edge,
                "raw_component_id": raw_component_id,
                "support_status": _item_status(direct_links, raw_component_id, support_info_by_component),
                **support_summary,
            }
        )
    return annotated_edges


def _annotate_vertices(
    evidence_graph: dict,
    vertex_to_component: dict[str, str],
    support_info_by_component: dict[str, dict],
    support_links: list[dict],
) -> list[dict]:
    direct_support_by_vertex = _direct_support_by_field(support_links, "graph_vertex_id")
    annotated_vertices = []
    for vertex in evidence_graph.get("vertices", []):
        vertex_id = vertex["vertex_id"]
        raw_component_id = vertex_to_component.get(vertex_id)
        direct_links = direct_support_by_vertex.get(vertex_id, [])
        support_summary = _support_summary(direct_links)
        annotated_vertices.append(
            {
                **vertex,
                "raw_component_id": raw_component_id,
                "support_status": _item_status(direct_links, raw_component_id, support_info_by_component),
                **support_summary,
            }
        )
    return annotated_vertices


def _bridge_support_status(
    bridge: dict,
    support_info_by_component: dict[str, dict],
) -> tuple[str, list[str]]:
    component_ids = [bridge.get("from_component_id"), bridge.get("to_component_id")]
    statuses = [
        support_info_by_component.get(component_id, {}).get("support_status", "unsupported")
        for component_id in component_ids
    ]
    if statuses[0] == "best_terminal_supported" and statuses[1] == "best_terminal_supported":
        return "between_best_supported_components", statuses
    if statuses[0] in TERMINAL_SUPPORTED_STATUSES and statuses[1] in TERMINAL_SUPPORTED_STATUSES:
        return "between_supported_components", statuses
    if statuses[0] in PATH_SUPPORTED_STATUSES and statuses[1] in PATH_SUPPORTED_STATUSES:
        return "between_path_supported_components", statuses
    if statuses[0] in PATH_SUPPORTED_STATUSES or statuses[1] in PATH_SUPPORTED_STATUSES:
        return "one_sided_supported", statuses
    return "unsupported", statuses


def _annotate_bridge_candidates(
    evidence_graph: dict,
    support_info_by_component: dict[str, dict],
) -> list[dict]:
    annotated_bridges = []
    for bridge in evidence_graph.get("bridge_candidates", []):
        status, endpoint_statuses = _bridge_support_status(bridge, support_info_by_component)
        endpoint_pin_ids = []
        for component_id in (bridge.get("from_component_id"), bridge.get("to_component_id")):
            endpoint_pin_ids.extend(support_info_by_component.get(component_id, {}).get("support_pin_ids", []))
        annotated_bridges.append(
            {
                **bridge,
                "support_status": status,
                "from_component_support_status": endpoint_statuses[0],
                "to_component_support_status": endpoint_statuses[1],
                "support_pin_ids": _unique(endpoint_pin_ids),
            }
        )
    return annotated_bridges


def _promote_relay_components(
    annotated_components: list[dict],
    support_info_by_component: dict[str, dict],
    annotated_bridges: list[dict],
    min_supported_neighbors: int,
) -> tuple[list[dict], dict[str, dict]]:
    bridge_neighbors: dict[str, list[tuple[str, str]]] = {}
    for bridge in annotated_bridges:
        from_component_id = bridge.get("from_component_id")
        to_component_id = bridge.get("to_component_id")
        if from_component_id is None or to_component_id is None:
            continue
        bridge_id = bridge["bridge_candidate_id"]
        bridge_neighbors.setdefault(from_component_id, []).append((to_component_id, bridge_id))
        bridge_neighbors.setdefault(to_component_id, []).append((from_component_id, bridge_id))

    promoted_components = []
    updated_support_info = {key: {**value} for key, value in support_info_by_component.items()}
    for component in annotated_components:
        component_id = component["raw_component_id"]
        if component.get("support_status") != "unsupported":
            promoted_components.append(component)
            continue

        supported_neighbors = []
        relay_bridge_ids = []
        for neighbor_id, bridge_id in bridge_neighbors.get(component_id, []):
            neighbor_status = support_info_by_component.get(neighbor_id, {}).get("support_status")
            if neighbor_status not in TERMINAL_SUPPORTED_STATUSES:
                continue
            if neighbor_id not in supported_neighbors:
                supported_neighbors.append(neighbor_id)
            if bridge_id not in relay_bridge_ids:
                relay_bridge_ids.append(bridge_id)

        if len(supported_neighbors) < min_supported_neighbors:
            promoted_components.append(component)
            continue

        relay_fields = {
            "support_status": "relay_supported",
            "relay_neighbor_component_ids": supported_neighbors,
            "relay_bridge_candidate_ids": relay_bridge_ids,
            "relay_support_reason": "bridges_multiple_terminal_supported_components",
            "relay_neighbor_count": len(supported_neighbors),
        }
        promoted_component = {**component, **relay_fields}
        promoted_components.append(promoted_component)
        updated_support_info[component_id] = {
            **updated_support_info.get(component_id, {}),
            **relay_fields,
        }

    return promoted_components, updated_support_info


def _supported_subgraph(
    annotated_components: list[dict],
    annotated_edges: list[dict],
    annotated_vertices: list[dict],
    annotated_bridges: list[dict],
) -> dict:
    supported_component_ids = [
        component["raw_component_id"]
        for component in annotated_components
        if component["support_status"] != "unsupported"
    ]
    unsupported_component_ids = [
        component["raw_component_id"]
        for component in annotated_components
        if component["support_status"] == "unsupported"
    ]
    return {
        "supported_raw_component_ids": supported_component_ids,
        "unsupported_raw_component_ids": unsupported_component_ids,
        "supported_edge_ids": [
            edge["edge_id"]
            for edge in annotated_edges
            if edge["support_status"] != "unsupported"
        ],
        "direct_supported_edge_ids": [
            edge["edge_id"]
            for edge in annotated_edges
            if edge["support_status"] in {"direct_best_supported", "direct_candidate_supported"}
        ],
        "supported_vertex_ids": [
            vertex["vertex_id"]
            for vertex in annotated_vertices
            if vertex["support_status"] != "unsupported"
        ],
        "bridge_candidate_ids": [
            bridge["bridge_candidate_id"]
            for bridge in annotated_bridges
            if bridge["support_status"]
            in {"between_best_supported_components", "between_supported_components", "between_path_supported_components"}
        ],
    }


def build_supported_graph(evidence_graph: dict, terminal_attachments: dict, config: dict) -> dict:
    """Mark terminal-supported evidence without changing the production topology."""
    config_values = _config_values(config)
    components_by_id, vertex_to_component, edge_to_component = _raw_component_lookups(evidence_graph)
    support_links = _support_links_from_attachments(terminal_attachments, config_values)
    annotated_components, support_info_by_component = _annotate_raw_components(
        evidence_graph,
        support_links,
        config_values["force_all_components_supported"],
    )
    annotated_bridges = _annotate_bridge_candidates(evidence_graph, support_info_by_component)
    annotated_components, support_info_by_component = _promote_relay_components(
        annotated_components,
        support_info_by_component,
        annotated_bridges,
        config_values["relay_min_supported_neighbors"],
    )
    annotated_edges = _annotate_edges(
        evidence_graph,
        edge_to_component,
        support_info_by_component,
        support_links,
    )
    annotated_vertices = _annotate_vertices(
        evidence_graph,
        vertex_to_component,
        support_info_by_component,
        support_links,
    )
    annotated_bridges = _annotate_bridge_candidates(evidence_graph, support_info_by_component)
    supported_subgraph = _supported_subgraph(
        annotated_components,
        annotated_edges,
        annotated_vertices,
        annotated_bridges,
    )

    return {
        "vertices": annotated_vertices,
        "edges": annotated_edges,
        "raw_components": annotated_components,
        "bridge_candidates": annotated_bridges,
        "support_links": support_links,
        "supported_subgraph": supported_subgraph,
        "stats": {
            "raw_component_count": len(annotated_components),
            "supported_raw_component_count": len(supported_subgraph["supported_raw_component_ids"]),
            "unsupported_raw_component_count": len(supported_subgraph["unsupported_raw_component_ids"]),
            "relay_supported_raw_component_count": sum(
                1 for component in annotated_components if component["support_status"] == "relay_supported"
            ),
            "support_link_count": len(support_links),
            "best_support_link_count": sum(
                1 for link in support_links if link["support_kind"] == "best_terminal_support"
            ),
            "candidate_support_link_count": sum(
                1 for link in support_links if link["support_kind"] == "candidate_terminal_support"
            ),
            "supported_edge_count": len(supported_subgraph["supported_edge_ids"]),
            "direct_supported_edge_count": len(supported_subgraph["direct_supported_edge_ids"]),
            "supported_vertex_count": len(supported_subgraph["supported_vertex_ids"]),
            "supported_bridge_candidate_count": len(supported_subgraph["bridge_candidate_ids"]),
            "known_raw_component_count": len(components_by_id),
        },
        "config": config_values,
    }
