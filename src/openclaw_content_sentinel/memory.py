from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, cast

from .config import AppConfig
from .research import prompt_is_actionable
from .storage import RunStore
from .utils import dump_json, ensure_dir, load_json, read_text

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = cast(Any, None)

try:
    import chromadb  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    chromadb = cast(Any, None)

try:
    import faiss  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    faiss = cast(Any, None)


VECTOR_DIMENSIONS = 256


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_]{1,}", (text or "").lower())


def _embed(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in _tokenize(text):
        index = hash(token) % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def _memory_path(config: AppConfig) -> Path:
    return config.data_dir / "memory" / "vector_store.json"


def _faiss_index_path(config: AppConfig) -> Path:
    return config.memory_faiss_dir / "runs.index"


def _faiss_metadata_path(config: AppConfig) -> Path:
    return config.memory_faiss_dir / "runs.metadata.json"


def _selected_backend(config: AppConfig) -> str:
    candidate = (config.memory_backend or "local").strip().lower()
    return candidate if candidate in {"local", "chroma", "faiss"} else "local"


def humanize_memory_hit(hit: dict[str, Any]) -> str:
    title = str(hit.get("title") or "").strip()
    if title and not title.lower().endswith(".md"):
        return title
    kind = str(hit.get("kind") or "").strip().lower()
    mapping = {
        "article_draft": "Previous article draft",
        "linkedin_draft": "Previous LinkedIn draft",
        "facebook_draft": "Previous Facebook draft",
        "x_draft": "Previous X draft",
        "source_summary": "Previous source summary",
        "trend_summary": "Previous trend summary",
    }
    if kind in mapping:
        return mapping[kind]
    run_id = str(hit.get("run_id") or "").strip()
    return run_id or "Historical item"


def backend_status(config: AppConfig) -> dict[str, Any]:
    backend = _selected_backend(config)
    available = True
    reason = ""
    path = ""

    if backend == "chroma":
        path = str(config.memory_chroma_dir)
        if chromadb is None:
            available = False
            reason = "chromadb is not installed"
    elif backend == "faiss":
        path = str(config.memory_faiss_dir)
        if faiss is None or np is None:
            available = False
            reason = "faiss and numpy are required"
    else:
        path = str(_memory_path(config))

    return {
        "backend": backend,
        "available": available,
        "reason": reason,
        "path": path,
        "dimensions": VECTOR_DIMENSIONS,
    }


def load_vector_store(config: AppConfig) -> dict[str, Any]:
    path = _memory_path(config)
    payload = load_json(path, default={"dimensions": VECTOR_DIMENSIONS, "documents": []}) or {}
    payload.setdefault("dimensions", VECTOR_DIMENSIONS)
    payload.setdefault("documents", [])
    return payload


def save_vector_store(config: AppConfig, payload: dict[str, Any]) -> Path:
    path = _memory_path(config)
    ensure_dir(path.parent)
    dump_json(path, payload)
    return path


def _collect_run_documents(config: AppConfig, run_id: str) -> list[dict[str, Any]]:
    store = RunStore(config)
    run = store.load_run(run_id)
    if not _run_is_memory_eligible(run):
        return []
    run_dir = store.run_dir(run_id)
    article = run.get("article") or {}
    outputs = run.get("outputs") or {}
    article_title = str(article.get("title") or "").strip()
    prompt = str(run.get("prompt") or "").strip()
    prompt_headline = " ".join(prompt.split())[:72].strip()
    semantic_subject = article_title or prompt_headline or run_id

    semantic_titles = {
        "article_draft": f"Article draft about {semantic_subject}",
        "linkedin_draft": f"LinkedIn draft about {semantic_subject}",
        "facebook_draft": f"Facebook draft about {semantic_subject}",
        "x_draft": f"X draft about {semantic_subject}",
    }

    documents = [
        {
            "id": f"{run_id}:source-summary",
            "run_id": run_id,
            "kind": "source_summary",
            "title": article.get("title") or run_id,
            "text": article.get("summary") or article.get("clean_text") or "",
        },
        {
            "id": f"{run_id}:daily-brief",
            "run_id": run_id,
            "kind": "daily_brief",
            "title": f"Daily brief for {semantic_subject}",
            "text": "\n".join(
                [
                    f"Prompt: {run.get('prompt', '')}",
                    f"Topic: {((run.get('daily_input') or {}).get('topic') or '')}",
                    f"Business angle: "
                    f"{((run.get('daily_input') or {}).get('business_angle') or '')}",
                    f"Target audience: "
                    f"{((run.get('daily_input') or {}).get('target_audience') or '')}",
                    f"Key CTA: {((run.get('daily_input') or {}).get('key_call_to_action') or '')}",
                ]
            ).strip(),
        },
        {
            "id": f"{run_id}:trend-summary",
            "run_id": run_id,
            "kind": "trend_summary",
            "title": "Trend Summary",
            "text": "\n".join(
                item.get("title", "") for item in (run.get("trend_signals") or [])[:10]
            ),
        },
        {
            "id": f"{run_id}:original-angle",
            "run_id": run_id,
            "kind": "original_angle",
            "title": f"Original angle for {semantic_subject}",
            "text": str((run.get("analysis") or {}).get("original_angle") or "").strip(),
        },
    ]

    file_map = {
        "article_draft": outputs.get("article", "drafts/article.md"),
        "linkedin_draft": outputs.get("linkedin", "drafts/linkedin.md"),
        "facebook_draft": outputs.get("facebook", "drafts/facebook.md"),
        "x_draft": outputs.get("x", "drafts/x.md"),
    }
    for kind, rel_path in file_map.items():
        path = run_dir / rel_path
        if path.exists():
            documents.append(
                {
                    "id": f"{run_id}:{kind}",
                    "run_id": run_id,
                    "kind": kind,
                    "title": semantic_titles.get(kind, path.name),
                    "text": read_text(path),
                }
            )

    extra_file_map = {
        "review_report": outputs.get("review_report", "ops/review_report.md"),
        "hybrid_context": outputs.get("hybrid_context", "artifacts/hybrid_context.md"),
        "graph_context": outputs.get("graph_context", "artifacts/graph_context.md"),
    }
    for kind, rel_path in extra_file_map.items():
        path = run_dir / rel_path
        if path.exists():
            documents.append(
                {
                    "id": f"{run_id}:{kind}",
                    "run_id": run_id,
                    "kind": kind,
                    "title": f"{kind.replace('_', ' ').title()} for {semantic_subject}",
                    "text": read_text(path),
                }
            )
    return [item for item in documents if item.get("text")]


def _run_is_memory_eligible(run: dict[str, Any]) -> bool:
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


def _reset_local(config: AppConfig) -> None:
    save_vector_store(config, {"dimensions": VECTOR_DIMENSIONS, "documents": []})


def _reset_chroma(config: AppConfig) -> None:
    if chromadb is None:
        return
    ensure_dir(config.memory_chroma_dir)
    client = chromadb.PersistentClient(path=str(config.memory_chroma_dir))
    try:
        client.delete_collection(config.memory_collection)
    except Exception:  # noqa: S110
        # Ignore errors if collection already deleted
        pass


def _reset_faiss(config: AppConfig) -> None:
    if faiss is None or np is None:
        return
    ensure_dir(config.memory_faiss_dir)
    index = faiss.IndexFlatIP(VECTOR_DIMENSIONS)
    faiss.write_index(index, str(_faiss_index_path(config)))
    dump_json(_faiss_metadata_path(config), [])


def reset_memory_store(config: AppConfig) -> dict[str, Any]:
    backend = _selected_backend(config)
    status = backend_status(config)
    if not status["available"]:
        backend = "local"
    if backend == "chroma":
        _reset_chroma(config)
        target = str(config.memory_chroma_dir)
    elif backend == "faiss":
        _reset_faiss(config)
        target = str(config.memory_faiss_dir)
    else:
        _reset_local(config)
        target = str(_memory_path(config))
    return {
        "backend": backend,
        "store_path": target,
        "reset": True,
    }


def _sync_local(config: AppConfig, documents: list[dict[str, Any]]) -> dict[str, Any]:
    store = load_vector_store(config)
    existing = {item["id"]: item for item in store.get("documents", [])}
    for document in documents:
        existing[document["id"]] = {
            **document,
            "embedding": _embed(document["text"]),
        }
    payload = {
        "dimensions": VECTOR_DIMENSIONS,
        "documents": list(existing.values()),
    }
    path = save_vector_store(config, payload)
    return {
        "store_path": str(path),
        "total_documents": len(cast(list[Any], payload["documents"])),
    }


def _search_local(
    config: AppConfig, query: str, top_k: int = 5, min_score: float = 0.12
) -> list[dict[str, Any]]:
    payload = load_vector_store(config)
    query_embedding = _embed(query)
    matches = []
    for document in payload.get("documents", []):
        score = _cosine(query_embedding, document.get("embedding", []))
        if score < min_score:
            continue
        matches.append(
            {
                "id": document.get("id", ""),
                "run_id": document.get("run_id", ""),
                "kind": document.get("kind", ""),
                "title": document.get("title", ""),
                "score": round(score, 4),
                "text": (document.get("text", "")[:420]).strip(),
            }
        )
    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:top_k]


def _sync_chroma(config: AppConfig, documents: list[dict[str, Any]]) -> dict[str, Any]:
    if chromadb is None:
        raise RuntimeError("Chroma backend requested but chromadb is not installed.")
    ensure_dir(config.memory_chroma_dir)
    client = chromadb.PersistentClient(path=str(config.memory_chroma_dir))
    collection = client.get_or_create_collection(name=config.memory_collection)
    embeddings = [_embed(document["text"]) for document in documents]
    collection.upsert(
        ids=[document["id"] for document in documents],
        documents=[document["text"] for document in documents],
        metadatas=[
            {
                "run_id": document["run_id"],
                "kind": document["kind"],
                "title": document["title"],
            }
            for document in documents
        ],
        embeddings=cast(Any, embeddings),
    )
    count = collection.count()
    return {
        "store_path": str(config.memory_chroma_dir),
        "total_documents": count,
    }


def _search_chroma(
    config: AppConfig, query: str, top_k: int = 5, min_score: float = 0.12
) -> list[dict[str, Any]]:
    if chromadb is None:
        return []
    client = chromadb.PersistentClient(path=str(config.memory_chroma_dir))
    collection = client.get_or_create_collection(name=config.memory_collection)
    result = collection.query(
        query_embeddings=cast(Any, [_embed(query)]),
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    matches = []
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]
    for idx, doc_id in enumerate(ids):
        distance = float(distances[idx]) if idx < len(distances) else 1.0
        score = round(max(0.0, 1.0 - min(distance, 2.0) / 2.0), 4)
        if score < min_score:
            continue
        metadata = metadatas[idx] if idx < len(metadatas) else {}
        text = documents[idx] if idx < len(documents) else ""
        matches.append(
            {
                "id": doc_id,
                "run_id": metadata.get("run_id", ""),
                "kind": metadata.get("kind", ""),
                "title": metadata.get("title", ""),
                "score": score,
                "text": text[:420].strip(),
            }
        )
    return matches


def _sync_faiss(config: AppConfig, documents: list[dict[str, Any]]) -> dict[str, Any]:
    if faiss is None or np is None:
        raise RuntimeError("FAISS backend requested but faiss/numpy are not installed.")
    ensure_dir(config.memory_faiss_dir)
    embeddings = np.array([_embed(document["text"]) for document in documents], dtype="float32")
    index = faiss.IndexFlatIP(VECTOR_DIMENSIONS)
    if len(embeddings):
        index.add(embeddings)
    faiss.write_index(index, str(_faiss_index_path(config)))
    metadata = []
    for document in documents:
        metadata.append(
            {
                "id": document["id"],
                "run_id": document["run_id"],
                "kind": document["kind"],
                "title": document["title"],
                "text": document["text"],
            }
        )
    dump_json(_faiss_metadata_path(config), metadata)
    return {
        "store_path": str(config.memory_faiss_dir),
        "total_documents": len(metadata),
    }


def _search_faiss(
    config: AppConfig, query: str, top_k: int = 5, min_score: float = 0.12
) -> list[dict[str, Any]]:
    if faiss is None or np is None:
        return []
    index_path = _faiss_index_path(config)
    metadata_path = _faiss_metadata_path(config)
    if not index_path.exists() or not metadata_path.exists():
        return []
    index = faiss.read_index(str(index_path))
    metadata = load_json(metadata_path, default=[]) or []
    if not metadata:
        return []
    query_embedding = np.array([_embed(query)], dtype="float32")
    scores, indices = index.search(query_embedding, min(top_k, len(metadata)))
    matches = []
    for score, index_value in zip(scores[0].tolist(), indices[0].tolist(), strict=False):
        if index_value < 0 or index_value >= len(metadata):
            continue
        if score < min_score:
            continue
        document = metadata[index_value]
        matches.append(
            {
                "id": document.get("id", ""),
                "run_id": document.get("run_id", ""),
                "kind": document.get("kind", ""),
                "title": document.get("title", ""),
                "score": round(float(score), 4),
                "text": document.get("text", "")[:420].strip(),
            }
        )
    return matches


def sync_run_memory(config: AppConfig, run_id: str) -> dict[str, Any]:
    documents = _collect_run_documents(config, run_id)
    backend = _selected_backend(config)
    status = backend_status(config)
    if not status["available"]:
        backend = "local"

    if backend == "chroma":
        payload = _sync_chroma(config, documents)
    elif backend == "faiss":
        payload = _sync_faiss(config, documents)
    else:
        payload = _sync_local(config, documents)

    return {
        "run_id": run_id,
        "backend": backend,
        "documents_indexed": len(documents),
        **payload,
    }


def search_memory(
    config: AppConfig, query: str, top_k: int = 5, min_score: float = 0.12
) -> list[dict[str, Any]]:
    backend = _selected_backend(config)
    status = backend_status(config)
    if not status["available"]:
        backend = "local"

    if backend == "chroma":
        return _search_chroma(config, query, top_k=top_k, min_score=min_score)
    if backend == "faiss":
        return _search_faiss(config, query, top_k=top_k, min_score=min_score)
    return _search_local(config, query, top_k=top_k, min_score=min_score)


def reindex_memory(config: AppConfig, run_ids: list[str] | None = None) -> dict[str, Any]:
    store = RunStore(config)
    candidate_ids = run_ids or [item["run_id"] for item in store.list_runs()]
    if run_ids is None:
        reset_memory_store(config)
    synced = []
    skipped = []
    for run_id in candidate_ids:
        try:
            payload = sync_run_memory(config, run_id)
            if payload.get("documents_indexed", 0) > 0:
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
            "store_path": backend_status(config)["path"],
        }
    )
    return {
        "backend": latest.get("backend", "local"),
        "store_path": latest.get("store_path", ""),
        "runs_indexed": len(synced),
        "run_ids": [item["run_id"] for item in synced],
        "skipped_run_ids": skipped,
    }
