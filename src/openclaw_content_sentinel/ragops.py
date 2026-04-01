"""Phase 16: RAGOps reporting."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .graph_store import backend_status as graph_backend_status
from .graph_store import load_local_graph, search_graph
from .memory import backend_status as memory_backend_status
from .memory import load_vector_store, search_memory
from .rag import RAGClient
from .rag_fixtures import GOLDEN_RETRIEVAL_FIXTURES
from .storage import RunStore
from .utils import dump_json, ensure_dir, write_text
from .workflow import build_proof_readiness_payload


def _candidate_queries(config: AppConfig) -> list[str]:
    store = RunStore(config)
    queries: list[str] = []
    for run in store.list_runs():
        if not build_proof_readiness_payload(run).get("proof_eligible"):
            continue
        topic = str((run.get("daily_input") or {}).get("topic") or "").strip()
        business_angle = str((run.get("daily_input") or {}).get("business_angle") or "").strip()
        keywords = " ".join((run.get("keywords") or [])[:3]).strip()
        query = " ".join(part for part in [topic, business_angle, keywords] if part).strip()
        if query:
            queries.append(query)
        if len(queries) >= 5:
            break
    return queries


def _build_rag_client_stats(config: AppConfig) -> dict[str, Any]:
    """Collect Phase 16 RAGClient stats for the RAGOps report."""
    client = None
    try:
        client = RAGClient(config)
        stats = client.get_stats()
        secret_check = client.verify_no_secret_leaks()
        purge_result = client.purge_trends()

        # Recall@5 on golden fixtures if we have indexed data
        recall: dict[str, Any] = {"recall_at_k": 0.0, "hit_count": 0, "total_queries": 0, "k": 5}
        if stats.get("bm25_docs", 0) > 0:
            recall = client.evaluate_recall(GOLDEN_RETRIEVAL_FIXTURES, k=5)

        return {
            "backend": stats.get("backend", "disabled"),
            "embedding_backend": stats.get("embedding_backend", "tfidf"),
            "vector_count": stats.get("vector_count", 0),
            "bm25_docs": stats.get("bm25_docs", 0),
            "index_version": stats.get("index_version", ""),
            "last_retrieval_latency_ms": stats.get("last_retrieval_latency_ms", 0.0),
            "snippet_quota": stats.get("snippet_quota", 5),
            "novelty_history_size": stats.get("novelty_history_size", 0),
            "secret_leaks_clean": secret_check.get("clean", True),
            "ttl_purge": purge_result,
            "recall": recall,
        }
    except Exception as exc:
        return {"error": str(exc), "vector_count": 0}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: S110
                # Ignore close errors on shutdown
                pass


def build_ragops_report(config: AppConfig) -> dict[str, Any]:
    memory_status = memory_backend_status(config)
    graph_status = graph_backend_status(config)
    vector_store = load_vector_store(config)
    graph_payload = (
        load_local_graph(config)
        if graph_status["backend"] == "local"
        else {"nodes": [], "edges": []}
    )
    kind_counter = Counter(item.get("kind", "") for item in vector_store.get("documents", []))
    graph_kind_counter = Counter(item.get("kind", "") for item in graph_payload.get("nodes", []))
    queries = _candidate_queries(config)

    shadow_eval: list[dict[str, Any]] = []
    for query in queries:
        memory_hits = search_memory(config, query, top_k=3)
        graph_hits = search_graph(config, query, top_k=3)
        shadow_eval.append(
            {
                "query": query,
                "memory_hit_count": len(memory_hits),
                "graph_hit_count": len(graph_hits),
                "memory_top_ids": [item.get("id", "") for item in memory_hits],
                "graph_top_ids": [item.get("id", "") for item in graph_hits],
            }
        )

    rag_client_stats = _build_rag_client_stats(config)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "memory_backend": memory_status,
        "graph_backend": graph_status,
        "index_manifest": {
            "vector_documents": len(vector_store.get("documents", [])),
            "vector_kinds": dict(kind_counter),
            "graph_nodes": len(graph_payload.get("nodes", [])),
            "graph_edges": len(graph_payload.get("edges", [])),
            "graph_kinds": dict(graph_kind_counter),
        },
        "shadow_eval": {
            "query_count": len(shadow_eval),
            "queries": shadow_eval,
        },
        "phase16": {
            "chunker": "HierarchicalChunker",
            "deduplication": "SimhashDeduplicator(threshold=4)",
            "embedding_backend": rag_client_stats.get("embedding_backend", "tfidf"),
            "retrieval": "HybridBM25+Vector(RRF)+FreshnessDecay+TrustWeight",
            "reranker": "CrossEncoderReranker(heuristic)",
            "grounding": "GroundednessChecker(citation_enforcement)",
            "novelty": "NoveltyScorer(cosine_vs_history)",
            "ttl_purge": rag_client_stats.get("ttl_purge", {}),
            "recall_at_5": rag_client_stats.get("recall", {}).get("recall_at_k", 0.0),
            "secret_leaks_clean": rag_client_stats.get("secret_leaks_clean", True),
            "vector_count": rag_client_stats.get("vector_count", 0),
            "bm25_docs": rag_client_stats.get("bm25_docs", 0),
            "last_retrieval_latency_ms": rag_client_stats.get("last_retrieval_latency_ms", 0.0),
            "snippet_quota": rag_client_stats.get("snippet_quota", 5),
            "golden_fixture_count": len(GOLDEN_RETRIEVAL_FIXTURES),
        },
    }


def render_ragops_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RAGOps Report",
        "",
        f"Generated at: {report['generated_at']}",
        f"Memory backend: {report['memory_backend']['backend']} | "
        f"available={report['memory_backend']['available']}",
        f"Graph backend: {report['graph_backend']['backend']} | "
        f"available={report['graph_backend']['available']}",
        "",
        "## Index Manifest",
        "",
        f"- Vector documents: {report['index_manifest']['vector_documents']}",
        f"- Graph nodes: {report['index_manifest']['graph_nodes']}",
        f"- Graph edges: {report['index_manifest']['graph_edges']}",
        "",
        "## Phase 16 — Advanced RAG Engineering",
        "",
    ]
    p16 = report.get("phase16", {})
    lines += [
        f"- Chunker: {p16.get('chunker', '')}",
        f"- Deduplication: {p16.get('deduplication', '')}",
        f"- Embedding backend: {p16.get('embedding_backend', '')}",
        f"- Retrieval: {p16.get('retrieval', '')}",
        f"- Reranker: {p16.get('reranker', '')}",
        f"- Grounding: {p16.get('grounding', '')}",
        f"- Novelty scorer: {p16.get('novelty', '')}",
        f"- Snippet quota (anti-contamination): {p16.get('snippet_quota', '')}",
        f"- Recall@5: {p16.get('recall_at_5', 0.0)}",
        f"- Secret leaks clean: {p16.get('secret_leaks_clean', True)}",
        f"- Vector count: {p16.get('vector_count', 0)}",
        f"- BM25 docs: {p16.get('bm25_docs', 0)}",
        f"- Last retrieval latency (ms): {p16.get('last_retrieval_latency_ms', 0.0)}",
        f"- Golden fixture count: {p16.get('golden_fixture_count', 0)}",
        "",
        "## Shadow Eval",
        "",
    ]
    for item in report["shadow_eval"]["queries"]:
        lines.append(
            f"- {item['query']} | memory_hits={item['memory_hit_count']} "
            f"| graph_hits={item['graph_hit_count']}"
        )
    lines.append("")
    return "\n".join(lines)


def write_ragops_report(config: AppConfig) -> dict[str, Any]:
    report = build_ragops_report(config)
    out_dir = ensure_dir(config.data_dir / "ragops")
    json_path = out_dir / "ragops-report.json"
    md_path = out_dir / "ragops-report.md"
    dump_json(json_path, report)
    write_text(md_path, render_ragops_markdown(report))
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "report": report,
    }
