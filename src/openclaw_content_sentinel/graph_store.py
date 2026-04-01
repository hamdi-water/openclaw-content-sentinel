from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .config import AppConfig
from .research import prompt_is_actionable
from .storage import RunStore
from .utils import dump_json, ensure_dir, load_json, now_utc

try:
    HAS_NEO4J = True
except ImportError:  # pragma: no cover
    HAS_NEO4J = False
    GraphDatabase = None  # type: ignore[assignment]


def _selected_backend(config: AppConfig) -> str:
    candidate = (config.graph_backend or "local").strip().lower()
    return candidate if candidate in {"local", "neo4j"} else "local"


def backend_status(config: AppConfig) -> dict[str, Any]:
    backend = _selected_backend(config)
    available = True
    reason = ""
    target = ""

    if backend == "neo4j":
        target = config.neo4j_uri or ""
        if GraphDatabase is None:
            available = False
            reason = "neo4j driver is not installed"
        elif not config.neo4j_uri or not config.neo4j_username or not config.neo4j_password:
            available = False
            reason = "neo4j credentials are incomplete"
    else:
        target = str(config.graph_local_path)

    return {
        "backend": backend,
        "available": available,
        "reason": reason,
        "target": target,
    }


def load_local_graph(config: AppConfig) -> dict[str, Any]:
    payload = load_json(
        config.graph_local_path,
        default={
            "nodes": [],
            "edges": [],
            "stats": {"runs": 0, "topics": 0, "competitors": 0, "trends": 0},
        },
    )
    payload = payload or {}
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    payload.setdefault("stats", {"runs": 0, "topics": 0, "competitors": 0, "trends": 0})
    return cast(dict[str, Any], payload)


def save_local_graph(config: AppConfig, payload: dict[str, Any]) -> Path:
    ensure_dir(config.graph_local_path.parent)
    dump_json(config.graph_local_path, payload)
    return config.graph_local_path


def _tokenize(value: str) -> list[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in (value or ""))
    return [item for item in normalized.split() if len(item) >= 2]


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _node(node_id: str, kind: str, label: str, **props: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "label": label.strip() or node_id,
        "properties": props,
    }


def _edge(source: str, relation: str, target: str, **props: Any) -> dict[str, Any]:
    return {
        "source": source,
        "relation": relation,
        "target": target,
        "properties": props,
    }


def _graph_payload_for_run(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if not _run_is_graph_eligible(run):
        return {"run_id": run_id, "nodes": [], "edges": [], "skipped": True}

    run_node = _node(
        f"run:{run_id}",
        "run",
        run_id,
        prompt=run.get("prompt", ""),
        status=run.get("status", ""),
        approval_state=run.get("approval_state", ""),
        created_at=run.get("created_at", ""),
        confidence=run.get("confidence", 0),
    )
    nodes = [run_node]
    edges: list[dict[str, Any]] = []

    _extract_competitor_data(run, run_node, nodes, edges)
    _extract_topic_data(run, run_node, nodes, edges)
    _extract_structured_input_data(run, run_node, nodes, edges)
    _extract_trend_data(run, run_node, nodes, edges)
    _extract_article_data(run, run_node, nodes, edges)
    _extract_publish_data(run, run_node, nodes, edges)
    _extract_review_data(run, run_node, nodes, edges)

    return {"run_id": run_id, "nodes": nodes, "edges": edges}


def _extract_competitor_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    competitor_url = run.get("competitor_url", "")
    domain = _domain(competitor_url)
    if domain:
        article = run.get("article") or {}
        c_node = _node(
            f"competitor:{domain}",
            "competitor",
            article.get("title") or domain,
            domain=domain,
            url=competitor_url,
        )
        nodes.append(c_node)
        edges.append(
            _edge(run_node["id"], "REFERENCES_COMPETITOR", c_node["id"], url=competitor_url)
        )


def _extract_topic_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    for keyword in run.get("keywords") or []:
        topic_id = f"topic:{keyword.strip().lower()}"
        nodes.append(_node(topic_id, "topic", keyword, query=keyword))
        edges.append(_edge(run_node["id"], "FOCUSES_ON", topic_id))


def _extract_structured_input_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    daily_input = run.get("daily_input") or {}
    for key, kind, rel in [
        ("topic", "strategic_topic", "CENTERS_ON"),
        ("business_angle", "business_angle", "FRAMES_AS"),
        ("target_audience", "audience", "TARGETS_AUDIENCE"),
        ("key_call_to_action", "call_to_action", "DRIVES_CTA"),
    ]:
        val = str(daily_input.get(key) or "").strip()
        if val:
            node_id = f"{kind}:{val.lower()}"
            nodes.append(_node(node_id, kind, val))
            edges.append(_edge(run_node["id"], rel, node_id))


def _extract_trend_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    for index, signal in enumerate((run.get("trend_signals") or [])[:12], start=1):
        title = str(signal.get("title") or "").strip()
        if not title:
            continue
        t_id = f"trend:{title.lower()}"
        nodes.append(
            _node(
                t_id,
                "trend",
                title,
                source=signal.get("source", ""),
                url=signal.get("url", ""),
                score=signal.get("score", 0),
                rank=index,
            )
        )
        edges.append(_edge(run_node["id"], "OBSERVES_TREND", t_id, rank=index))


def _extract_article_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    article = run.get("article") or {}
    title = str(article.get("title") or "").strip()
    if title:
        a_id = f"article:{run.get('run_id', '')}"
        nodes.append(
            _node(
                a_id,
                "article",
                title,
                url=article.get("url", run.get("competitor_url", "")),
                published_at=article.get("published_at", ""),
            )
        )
        edges.append(_edge(run_node["id"], "ANALYZES_ARTICLE", a_id))


def _extract_publish_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    run_id = run.get("run_id", "")
    for platform in run.get("platform_targets") or []:
        p_id = f"platform:{platform}"
        pub = (run.get("post_results") or {}).get(platform) or {}
        pub_node_id = f"publish:{run_id}:{platform}"
        nodes.append(_node(p_id, "platform", platform))
        nodes.append(
            _node(
                pub_node_id,
                "publish_target",
                f"{platform}:{pub.get('status', 'pending')}",
                platform=platform,
                status=pub.get("status", "pending"),
                url=pub.get("url", ""),
            )
        )
        edges.append(_edge(run_node["id"], "TARGETS_PLATFORM", p_id))
        edges.append(_edge(run_node["id"], "HAS_PUBLISH_TARGET", pub_node_id))
        edges.append(_edge(pub_node_id, "PUBLISHES_TO", p_id))


def _extract_review_data(
    run: dict[str, Any], run_node: dict[str, Any], nodes: list[Any], edges: list[Any]
) -> None:
    review = run.get("quality_gate") or {}
    if review:
        r_id = f"review:{run.get('run_id', '')}"
        nodes.append(
            _node(
                r_id,
                "review",
                r_id,
                publish_ready=review.get("publish_ready", False),
                score=review.get("score", 0),
            )
        )
        edges.append(_edge(run_node["id"], "HAS_REVIEW", r_id))


def _run_is_graph_eligible(run: dict[str, Any]) -> bool:
    if (run.get("simulation") or {}).get("enabled"):
        return False
    if not prompt_is_actionable(str(run.get("prompt") or "").strip()):
        return False
    status = str(run.get("status") or "").strip().lower()
    if status in {"failed", "rejected"}:
        return False
    approval_state = str(run.get("approval_state") or "").strip().lower()
    if approval_state == "rejected":
        return False
    quality_gate = run.get("quality_gate") or {}
    if quality_gate and not quality_gate.get("metrics", {}).get("prompt_actionable", True):
        return False
    return True


def _local_upsert(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    graph = load_local_graph(config)
    nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    edges_by_key = {
        (edge["source"], edge["relation"], edge["target"]): edge for edge in graph.get("edges", [])
    }

    for node in payload["nodes"]:
        nodes_by_id[node["id"]] = node
    for edge in payload["edges"]:
        edges_by_key[(edge["source"], edge["relation"], edge["target"])] = edge

    nodes = list(nodes_by_id.values())
    edges = list(edges_by_key.values())
    graph = {"nodes": nodes, "edges": edges}
    kinds = Counter(node.get("kind", "") for node in nodes)
    graph["stats"] = {
        "runs": kinds.get("run", 0),
        "topics": kinds.get("topic", 0),
        "competitors": kinds.get("competitor", 0),
        "trends": kinds.get("trend", 0),
        "articles": kinds.get("article", 0),
    }
    save_local_graph(config, graph)
    return {
        "backend": "local",
        "target": str(config.graph_local_path),
        "nodes_indexed": len(payload["nodes"]),
        "edges_indexed": len(payload["edges"]),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


def _reset_local_graph(config: AppConfig) -> dict[str, Any]:
    payload = {
        "nodes": [],
        "edges": [],
        "stats": {"runs": 0, "topics": 0, "competitors": 0, "trends": 0, "articles": 0},
    }
    save_local_graph(config, payload)
    return {
        "backend": "local",
        "target": str(config.graph_local_path),
        "reset": True,
    }


def _neo4j_upsert(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    if GraphDatabase is None:
        raise RuntimeError("Neo4j backend requested but neo4j driver is not installed.")
    driver = GraphDatabase.driver(
        str(config.neo4j_uri or ""),
        auth=(str(config.neo4j_username or ""), str(config.neo4j_password or "")),
    )
    try:
        with driver.session(database=config.neo4j_database) as session:
            for node in payload["nodes"]:
                session.run(
                    """
                    MERGE (n:KnowledgeNode {id: $id})
                    SET n.kind = $kind, n.label = $label, n += $properties
                    """,
                    id=node["id"],
                    kind=node["kind"],
                    label=node["label"],
                    properties=node.get("properties", {}),
                )
            for edge in payload["edges"]:
                session.run(
                    """
                    MATCH (a:KnowledgeNode {id: $source})
                    MATCH (b:KnowledgeNode {id: $target})
                    MERGE (a)-[r:RELATED {relation: $relation}]->(b)
                    SET r += $properties
                    """,
                    source=edge["source"],
                    target=edge["target"],
                    relation=edge["relation"],
                    properties=edge.get("properties", {}),
                )
            counts = session.run(
                """
                MATCH (n:KnowledgeNode)
                RETURN count(n) AS total_nodes
                """
            ).single()
            rels = session.run(
                """
                MATCH ()-[r:RELATED]->()
                RETURN count(r) AS total_edges
                """
            ).single()
    finally:
        driver.close()
    return {
        "backend": "neo4j",
        "target": config.neo4j_uri,
        "nodes_indexed": len(payload["nodes"]),
        "edges_indexed": len(payload["edges"]),
        "total_nodes": int(counts["total_nodes"]) if counts else 0,
        "total_edges": int(rels["total_edges"]) if rels else 0,
    }


def _reset_neo4j_graph(config: AppConfig) -> dict[str, Any]:
    if GraphDatabase is None:
        return {"backend": "neo4j", "target": config.neo4j_uri, "reset": False}
    driver = GraphDatabase.driver(
        str(config.neo4j_uri or ""),
        auth=(str(config.neo4j_username or ""), str(config.neo4j_password or "")),
    )
    try:
        with driver.session(database=config.neo4j_database) as session:
            session.run("MATCH (n:KnowledgeNode) DETACH DELETE n")
    finally:
        driver.close()
    return {
        "backend": "neo4j",
        "target": config.neo4j_uri,
        "reset": True,
    }


def reset_graph_store(config: AppConfig) -> dict[str, Any]:
    status = backend_status(config)
    backend = status["backend"] if status["available"] else "local"
    if backend == "neo4j":
        return _reset_neo4j_graph(config)
    return _reset_local_graph(config)


def sync_run_graph(config: AppConfig, run_id: str) -> dict[str, Any]:
    payload = _graph_payload_for_run(config, run_id)
    if payload.get("skipped"):
        status = backend_status(config)
        backend = status["backend"] if status["available"] else "local"
        return {
            "run_id": run_id,
            "backend": backend,
            "target": status["target"],
            "nodes_indexed": 0,
            "edges_indexed": 0,
            "skipped": True,
        }
    status = backend_status(config)
    backend = status["backend"] if status["available"] else "local"
    result = (
        _neo4j_upsert(config, payload) if backend == "neo4j" else _local_upsert(config, payload)
    )
    return {
        "run_id": run_id,
        **result,
    }


def _search_local(config: AppConfig, query: str, top_k: int = 8) -> list[dict[str, Any]]:
    graph = load_local_graph(config)
    query_tokens = set(_tokenize(query))
    edges = graph.get("edges", [])
    nodes_by_id = {node.get("id", ""): node for node in graph.get("nodes", [])}
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        source = edge.get("source", "")
        target = edge.get("target", "")
        source_node = nodes_by_id.get(source, {})
        target_node = nodes_by_id.get(target, {})
        adjacency.setdefault(source, []).append(
            {
                "relation": edge.get("relation", "RELATED"),
                "peer": target,
                "peerLabel": target_node.get("label", target),
                "direction": "outgoing",
            }
        )
        adjacency.setdefault(target, []).append(
            {
                "relation": edge.get("relation", "RELATED"),
                "peer": source,
                "peerLabel": source_node.get("label", source),
                "direction": "incoming",
            }
        )

    scored = []
    for node in graph.get("nodes", []):
        haystack = " ".join(
            [
                node.get("id", ""),
                node.get("label", ""),
                " ".join(f"{key} {value}" for key, value in (node.get("properties") or {}).items()),
            ]
        )
        tokens = set(_tokenize(haystack))
        overlap = len(query_tokens & tokens)
        if not overlap and query_tokens:
            continue
        score = round(overlap / max(1, len(query_tokens) or 1), 4)
        scored.append(
            {
                "id": node.get("id", ""),
                "kind": node.get("kind", ""),
                "label": node.get("label", ""),
                "score": score,
                "properties": node.get("properties", {}),
                "connections": adjacency.get(node.get("id", ""), [])[:6],
            }
        )
    scored.sort(key=lambda item: (item["score"], item["kind"], item["label"]), reverse=True)
    return scored[:top_k]


def _search_neo4j(config: AppConfig, query: str, top_k: int = 8) -> list[dict[str, Any]]:
    if GraphDatabase is None or not config.neo4j_uri:
        return []
    driver = GraphDatabase.driver(
        str(config.neo4j_uri or ""),
        auth=(str(config.neo4j_username or ""), str(config.neo4j_password or "")),
    )
    try:
        with driver.session(database=config.neo4j_database) as session:
            records = session.run(
                """
                MATCH (n:KnowledgeNode)
                WHERE toLower(n.label) CONTAINS toLower($query_str)
                OR toLower(n.id) CONTAINS toLower($query_str)
                OPTIONAL MATCH (n)-[r:RELATED]-(m:KnowledgeNode)
                RETURN n, collect({
                    relation: r.relation, peer: m.id, peerLabel: m.label
                })[0..6] AS links
                LIMIT $top_k
                """,
                query_str=query,
                top_k=top_k,
            )
            return [
                {
                    "id": record["n"]["id"],
                    "kind": record["n"].get("kind", ""),
                    "label": record["n"].get("label", record["n"]["id"]),
                    "score": 1.0,
                    "properties": {
                        key: value
                        for key, value in dict(record["n"]).items()
                        if key not in {"id", "kind", "label"}
                    },
                    "connections": record["links"] or [],
                }
                for record in records
            ]
    finally:
        driver.close()


def search_graph(config: AppConfig, query: str, top_k: int = 8) -> list[dict[str, Any]]:
    status = backend_status(config)
    backend = status["backend"] if status["available"] else "local"
    if backend == "neo4j":
        return _search_neo4j(config, query, top_k=top_k)
    return _search_local(config, query, top_k=top_k)


def load_graph_context(config: AppConfig, run_id: str) -> dict[str, Any]:
    store = RunStore(config)
    run = store.load_run(run_id)
    query = " ".join([run.get("prompt", ""), *list(run.get("keywords") or [])]).strip()
    hits = search_graph(config, query, top_k=8)
    run["graph_hits"] = hits
    run["analysis"] = {
        **dict(run.get("analysis") or {}),
        "knowledge_graph_brief": render_graph_brief(hits),
    }
    run["updated_at"] = now_utc()
    store.save_run(run)
    return run


def render_graph_brief(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No graph relationships were found for the current prompt and competitor context."
    lines = []
    for item in hits[:5]:
        connections = item.get("connections") or []
        peer_labels = []
        for edge in connections[:3]:
            peer_labels.append(
                f"{edge.get('relation', 'RELATED')} -> "
                f"{edge.get('peerLabel') or edge.get('peer', '')}"
            )
        relation_text = "; ".join(peer_labels) if peer_labels else "no linked peers"
        lines.append(
            f"- {item.get('label', item.get('id', 'node'))} "
            f"[{item.get('kind', '')}] | {relation_text}"
        )
    return "\n".join(lines)


def reindex_graph(config: AppConfig, run_ids: list[str] | None = None) -> dict[str, Any]:
    store = RunStore(config)
    candidate_ids = run_ids or [item["run_id"] for item in store.list_runs()]
    if run_ids is None:
        reset_graph_store(config)
    synced = []
    skipped = []
    for run_id in candidate_ids:
        try:
            payload = sync_run_graph(config, run_id)
            if payload.get("nodes_indexed", 0) > 0:
                synced.append(payload)
            else:
                skipped.append(run_id)
        except FileNotFoundError:
            continue
    latest = (
        synced[-1]
        if synced
        else {
            "backend": backend_status(config)["backend"],
            "target": backend_status(config)["target"],
        }
    )
    return {
        "backend": latest.get("backend", "local"),
        "target": latest.get("target", ""),
        "runs_indexed": len(synced),
        "run_ids": [item["run_id"] for item in synced],
        "skipped_run_ids": skipped,
    }
