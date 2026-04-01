"""
Phase 16: Advanced RAG Engineering — Knowledge Plane (§30.1–§30.6, §16.1, §22.3–§22.4).

==========================================================================================

This module implements a full-stack Retrieval-Augmented Generation layer for
the OpenClaw Content Sentinel, including:

  • HierarchicalChunker  – semantic, HTML-aware, overlap-safe text splitter (§30.3)
  • SimhashDeduplicator  – 64-bit Simhash-based near-duplicate detection (§30.2)
  • EmbeddingEngine      – zero-cost local CPU embeddings (sentence-transformers or TF-IDF fallback)
  • VectorIndex          – ChromaDB persistent store with brand-partitioned collections (§30.1)
  • BM25Index            – keyword sparse index for hybrid retrieval
  • HybridRetriever      – BM25 + vector RRF fusion with freshness decay and trust weights (§30.4)
  • CrossEncoderReranker – heuristic cross-encoder re-scorer (§30.4)
  • GroundednessChecker  – citation presence and snippet quota enforcement (§30.5)
  • NoveltyScorer        – cosine similarity against historical post index (§30.6)
  • LanguageFilter       – fr/en language detection and routing
  • TTLPurger            – time-based corpus expiry for corpus_trends (§30.1)
  • RAGClient            – clean public abstraction for worker consumption
"""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from .cache_engine import TieredCache
from .config import AppConfig
from .monitoring import monitor
from .utils import dump_json, ensure_dir, load_json, now_utc

try:
    import chromadb  # type: ignore
except Exception:  # pragma: no cover
    chromadb = None

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


# ---------------------------------------------------------------------------
# §30.3 — Phase 16.1: Semantic HTML-Aware Hierarchical Chunker
# ---------------------------------------------------------------------------


class HierarchicalChunker:
    """
    Hierarchical chunker for semantic text splitting.

    Splits content at semantic boundaries (headers → paragraphs → sentences)
    with configurable chunk size and overlap.  HTML tags are stripped first.
    Anti-boilerplate patterns (navigation, cookie banners, ads) are removed
    before chunking (§Trafilatura-equivalent extraction hardening).
    """

    # Boilerplate patterns to strip before chunking
    _BOILERPLATE_RE = re.compile(
        r"(cookie[s]?\s+(policy|consent|notice|banner)|privacy\s+policy|"
        r"terms\s+of\s+service|all\s+rights\s+reserved|copyright\s+©|"
        r"subscribe\s+to\s+our\s+newsletter|click\s+here\s+to|"
        r"sign\s+up\s+for|follow\s+us\s+on|share\s+this\s+(article|post))",
        re.IGNORECASE,
    )
    _HTML_TAG_RE = re.compile(r"<[^>]+>")
    _WHITESPACE_RE = re.compile(r"\s+")

    def __init__(self, chunk_size: int = 900, chunk_overlap: int = 150) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def clean(self, html_or_text: str) -> str:
        """Strip HTML, remove boilerplate per line, then collapse whitespace."""
        # Strip HTML tags
        text = self._HTML_TAG_RE.sub(" ", html_or_text)
        # Filter boilerplate lines
        lines = [ln for ln in text.splitlines() if not self._BOILERPLATE_RE.search(ln)]
        # Collapse whitespace per line
        cleaned_lines = []
        for ln in lines:
            cleaned_ln = self._WHITESPACE_RE.sub(" ", ln).strip()
            if cleaned_ln:
                cleaned_lines.append(cleaned_ln)
        return "\n".join(cleaned_lines).strip()

    def chunk(
        self,
        html_or_text: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        source_id: str = "",
    ) -> list[dict[str, Any]]:
        """Return a list[Any] of chunk dicts: {text, chunk_id, char_offset, source_id}."""
        size = chunk_size or self.chunk_size
        overlap = chunk_overlap or self.chunk_overlap
        text = self.clean(html_or_text)
        if not text:
            return []

        raw_chunks = self._split(text, size, overlap, ["\n\n", "\n", ". ", " ", ""])
        result = []
        offset = 0
        for i, chunk in enumerate(raw_chunks):
            cid = f"{source_id}:ch{i}" if source_id else f"ch{i}"
            result.append(
                {
                    "text": chunk,
                    "chunk_id": cid,
                    "char_offset": offset,
                    "source_id": source_id,
                }
            )
            offset += len(chunk)
        return result

    def _split(self, text: str, size: int, overlap: int, seps: list[str]) -> list[str]:
        if len(text) <= size:
            return [text] if text.strip() else []

        sep = seps[0]
        remaining_seps = seps[1:]

        parts = list(text) if sep == "" else text.split(sep)
        chunks: list[str] = []
        buf: list[str] = []
        buf_len = 0

        for part in parts:
            if buf and buf_len + len(part) + len(sep) > size:
                chunks.append(sep.join(buf))
                # Trim buffer to overlap size
                while buf and buf_len > overlap:
                    removed = buf.pop(0)
                    buf_len -= len(removed) + len(sep)

            if len(part) > size:
                if buf:
                    chunks.append(sep.join(buf))
                    buf, buf_len = [], 0
                chunks.extend(self._split(part, size, overlap, remaining_seps))
            else:
                buf.append(part)
                buf_len += len(part) + (len(sep) if buf_len > 0 else 0)

        if buf:
            chunks.append(sep.join(buf))

        return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# §30.2 — Phase 16.2: Simhash Near-Duplicate Detector
# ---------------------------------------------------------------------------


class SimhashDeduplicator:
    """
    Simhash fingerprinting for content-level near-duplicate detection.

    64-bit Simhash fingerprinting for content-level near-duplicate detection.
    Hamming distance ≤ threshold (default 4) → considered duplicate.
    """

    def __init__(self, threshold: int = 4) -> None:
        self.threshold = threshold
        self._seen: dict[str, int] = {}  # source_id → fingerprint

    @staticmethod
    def compute(text: str) -> int:
        """Compute a 64-bit Simhash fingerprint from 3-gram features."""
        tokens = re.findall(r"\w+", text.lower())
        features: Counter[str] = Counter()
        for i in range(len(tokens) - 2):
            features[" ".join(tokens[i : i + 3])] += 1

        v = [0] * 64
        for feat, weight in features.items():
            h = int(hashlib.sha256(feat.encode()).hexdigest(), 16)
            for i in range(64):
                v[i] += weight if (h >> i) & 1 else -weight

        fingerprint = 0
        for i in range(64):
            if v[i] >= 0:
                fingerprint |= 1 << i
        return fingerprint

    @staticmethod
    def hamming(a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    def is_duplicate(self, text: str, source_id: str = "") -> bool:
        fp = self.compute(text)
        for existing_id, existing_fp in self._seen.items():
            if existing_id == source_id:
                continue
            if self.hamming(fp, existing_fp) <= self.threshold:
                return True
        return False

    def register(self, text: str, source_id: str) -> int:
        fp = self.compute(text)
        if source_id:
            self._seen[source_id] = fp
        return fp


# ---------------------------------------------------------------------------
# §30.3 — Phase 16.3: Embedding Engine (zero-cost, local CPU)
# ---------------------------------------------------------------------------


class EmbeddingEngine:
    """
    Local embedding compute engine.

    Zero-cost local embedding compute.

    Priority:
      1. sentence-transformers (all-MiniLM-L6-v2) — best quality, offline
      2. TF-IDF sparse vector (pure stdlib) — always available, no cost

    The backend is set at init and stays consistent throughout the session.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model = None
        self._backend = "tfidf"
        self._vocab: dict[str, int] = {}
        self._idf: dict[str, float] = {}

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            self._model = SentenceTransformer(model_name)
            self._backend = "sentence_transformers"
        except Exception:
            # Fallback to TF-IDF if sentence_transformers is missing
            self._model = None

    @property
    def backend(self) -> str:
        return self._backend

    def embed(self, text: str) -> list[float]:
        if self._backend == "sentence_transformers" and self._model is not None:
            vec = self._model.encode(text, normalize_embeddings=True)
            return [float(v) for v in vec]
        return self._tfidf_embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._backend == "sentence_transformers" and self._model is not None:
            vecs = self._model.encode(texts, normalize_embeddings=True)
            return [[float(v) for v in row] for row in vecs]
        return [self._tfidf_embed(t) for t in texts]

    def _tfidf_embed(self, text: str) -> list[float]:
        """Deterministic TF-IDF vector in a 512-dim space using hash trick."""
        dim = 512
        tokens = re.findall(r"\w+", text.lower())
        tf: Counter[str] = Counter(tokens)
        vector = [0.0] * dim
        for token, count in tf.items():
            # Use sha256 for security (S324 compliance)
            idx = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dim
            vector[idx] += count
        norm = sum(v * v for v in vector) ** 0.5
        return [v / norm for v in vector] if norm > 0 else vector


# ---------------------------------------------------------------------------
# §30.4 — Phase 16.4: BM25 Sparse Index
# ---------------------------------------------------------------------------


class BM25Index:
    """
    Okapi BM25 sparse retrieval index.

    k1=1.5, b=0.75 per standard literature.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[dict[str, Any]] = []  # {id, text, tokens, metadata}
        self._df: Counter[str] = Counter()  # document frequency
        self._avgdl: float = 0.0

    def add_document(self, doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        tokens = re.findall(r"\w+", text.lower())
        for tok in set(tokens):
            self._df[tok] += 1
        self._docs.append(
            {"id": doc_id, "text": text, "tokens": tokens, "metadata": metadata or {}}
        )
        total = sum(len(d["tokens"]) for d in self._docs)
        self._avgdl = total / len(self._docs)

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        results = self.score(query, top_k=top_k)
        if results:
            monitor.rag_retrieval_hits.inc()
            monitor.export()
        return results

    def score(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        q_tokens = re.findall(r"\w+", query.lower())
        n = len(self._docs)
        scores: list[tuple[float, dict]] = []

        for doc in self._docs:
            tf_counts: Counter[str] = Counter(doc["tokens"])
            dl = len(doc["tokens"])
            s = 0.0
            for qt in q_tokens:
                tf = tf_counts.get(qt, 0)
                df = self._df.get(qt, 0)
                if df == 0:
                    continue
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / max(self._avgdl, 1))
                )
                s += idf * tf_norm
            scores.append((s, doc))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [
            {"id": d["id"], "text": d["text"], "metadata": d["metadata"], "bm25_score": s}
            for s, d in scores[:top_k]
            if s > 0
        ]


# ---------------------------------------------------------------------------
# §30.4 — Phase 16.5: Hybrid Retriever (BM25 + Vector, RRF Fusion)
# ---------------------------------------------------------------------------


def _freshness_decay(freshness_str: str, half_life_days: int = 7) -> float:
    """Exponential decay: docs older than half_life_days get progressively lower scores."""
    try:
        ts = datetime.fromisoformat(freshness_str.replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - ts).total_seconds() / 86400
        return math.exp(-math.log(2) * age_days / half_life_days)
    except Exception:
        return 1.0


def _rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion score."""
    return 1.0 / (k + rank + 1)


class HybridRetriever:
    """
    Fuses BM25 + vector results using Reciprocal Rank Fusion (RRF).

    Applies freshness decay and trust weight multipliers (§30.4, §16.1).
    """

    def __init__(
        self,
        bm25: BM25Index,
        collection: Any,  # ChromaDB collection or None
        half_life_days: int = 7,
        snippet_quota: int = 5,  # §30.5 anti-contamination
    ) -> None:
        self.bm25 = bm25
        self.collection = collection
        self.half_life_days = half_life_days
        self.snippet_quota = snippet_quota

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        lang_filter: str | None = None,  # "fr" | "en" | None
        min_trust: float = 0.0,  # §16.1
        min_freshness_score: float = 0.0,  # decay gate
    ) -> list[dict[str, Any]]:
        """Return top_k de-duplicated, fused, decayed, trust-weighted hits."""
        t0 = time.perf_counter()
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}

        # --- BM25 leg ---
        bm25_hits = self.bm25.score(query, top_k=top_k * 3)
        for rank, hit in enumerate(bm25_hits):
            did = hit["id"]
            scores[did] = scores.get(did, 0.0) + _rrf_score(rank)
            meta[did] = hit["metadata"]
            meta[did]["text"] = hit["text"]

        # --- Vector leg ---
        if self.collection is not None:
            try:
                vec_results = self.collection.query(query_texts=[query], n_results=top_k * 3)
                for rank, (did, doc_text, mdata) in enumerate(
                    zip(
                        vec_results["ids"][0],
                        vec_results["documents"][0],
                        vec_results["metadatas"][0],
                        strict=False,
                    )
                ):
                    base_id = did.split(":ch")[0]
                    scores[base_id] = scores.get(base_id, 0.0) + _rrf_score(rank)
                    if base_id not in meta:
                        try:
                            meta[base_id] = mdata
                            meta[base_id]["text"] = doc_text
                        except Exception:  # noqa: S110
                            # Skip unreadable document segments
                            pass
            except Exception:  # noqa: S110
                pass

        # --- Apply freshness decay + trust weight ---
        final_scores: dict[str, float] = {}
        for did, score in scores.items():
            m = meta.get(did, {})
            freshness_str = str(m.get("freshness", "") or m.get("freshness_ts", "") or "")
            decay = _freshness_decay(freshness_str, self.half_life_days) if freshness_str else 1.0
            trust = float(m.get("trust_weight", 1.0))

            # Filters
            if decay < min_freshness_score:
                continue
            if trust < min_trust:
                continue
            if lang_filter:
                detected = m.get("lang") or ""
                if detected and detected != lang_filter:
                    continue

            final_scores[did] = score * decay * trust

        # --- Ranked results (snippet quota § 30.5) ---
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for did, score in ranked[: self.snippet_quota]:
            m = meta.get(did, {})
            results.append(
                {
                    "id": did,
                    "text": m.get("text", ""),
                    "score": round(score, 6),
                    "freshness_decay": round(
                        _freshness_decay(str(m.get("freshness", "") or ""), self.half_life_days), 4
                    ),
                    "trust_weight": float(m.get("trust_weight", 1.0)),
                    "source_id": m.get("source_id", did),
                    "freshness": m.get("freshness", ""),
                    "lang": m.get("lang", ""),
                    "source_safety_severity": m.get("source_safety_severity", "none"),
                    "doc_id": did,
                    "chunk_id": m.get("chunk_id", ""),
                    "retrieval_latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            )

        return results


# ---------------------------------------------------------------------------
# §30.4 — Phase 16.6: Heuristic Cross-Encoder Reranker
# ---------------------------------------------------------------------------


class CrossEncoderReranker:
    """
    Heuristic reranker that approximates cross-encoder scoring without a model.

    Scoring = token overlap ratio × (1 + freshness decay bonus)
    A real cross-encoder (ms-marco-MiniLM) can be swapped in later.
    """

    def rerank(
        self, query: str, hits: list[dict[str, Any]], top_k: int | None = None
    ) -> list[dict[str, Any]]:
        q_tokens = set(re.findall(r"\w+", query.lower()))
        ranked = []
        for hit in hits:
            doc_tokens = set(re.findall(r"\w+", (hit.get("text") or "").lower()))
            overlap = len(q_tokens & doc_tokens) / max(len(q_tokens), 1)
            freshness_bonus = float(hit.get("freshness_decay", 1.0))
            trust_bonus = float(hit.get("trust_weight", 1.0))
            rerank_score = overlap * freshness_bonus * trust_bonus
            ranked.append({**hit, "rerank_score": round(rerank_score, 6)})

        ranked.sort(key=lambda h: h["rerank_score"], reverse=True)
        return ranked[:top_k] if top_k else ranked


# ---------------------------------------------------------------------------
# §30.5 — Phase 16.7: Groundedness Checker (Citation Enforcement)
# ---------------------------------------------------------------------------


class ContextDiversityScorer:
    """Ref §30.3: Ensure non-redundant RAG hits."""

    def score(self, hits: list[dict[str, Any]]) -> float:
        if not hits:
            return 1.0
        unique_tokens = set()
        total_tokens = 0
        for h in hits:
            toks = set(re.findall(r"\w+", (h.get("text") or "").lower()))
            unique_tokens.update(toks)
            total_tokens += len(toks)
        return round(len(unique_tokens) / max(total_tokens, 1), 3)


class GroundednessChecker:
    """
    Verify that claims in the draft are supported by retrieved snippets.

    Flags ungrounded sentences (§30.5).
    """

    def __init__(self, min_overlap_ratio: float = 0.15) -> None:
        self.min_overlap_ratio = min_overlap_ratio

    def check(self, draft: str, context_hits: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Check groundedness of the draft against context hits.

        Returns:
            grounded: bool — whether the draft passes the grounding gate
            score: float [0,1]
        ungrounded_sentences: list[str]
        citation_count: int.

        """
        context_tokens: set[str] = set()
        for hit in context_hits:
            context_tokens.update(re.findall(r"\w+", (hit.get("text") or "").lower()))

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", draft) if len(s.strip()) > 30]
        if not sentences:
            return {"grounded": True, "score": 1.0, "ungrounded_sentences": [], "citation_count": 0}

        grounded_count = 0
        ungrounded: list[str] = []
        for sent in sentences:
            s_tokens = set(re.findall(r"\w+", sent.lower()))
            overlap = len(s_tokens & context_tokens) / max(len(s_tokens), 1)
            if overlap >= self.min_overlap_ratio:
                grounded_count += 1
            else:
                ungrounded.append(sent[:120])

        score = grounded_count / len(sentences)
        return {
            "grounded": score >= 0.6,
            "score": round(score, 3),
            "ungrounded_sentences": ungrounded[:5],
            "citation_count": len(context_hits),
            "sentences_checked": len(sentences),
        }


# ---------------------------------------------------------------------------
# §30.6 — Phase 16.8: Novelty Scorer (against historical post index)
# ---------------------------------------------------------------------------


class NoveltyScorer:
    """
    Novelty scorer for draft verification.

    Computes a novelty score for a draft by comparing it against a corpus
    of already-published post text.  Score = 1 − max_cosine_similarity.
    Uses TF-IDF cosine as a zero-cost metric.
    """

    def __init__(self) -> None:
        self._history: list[tuple[str, list[float]]] = []  # (run_id, vector)
        self._engine = EmbeddingEngine.__new__(EmbeddingEngine)
        EmbeddingEngine.__init__(self._engine, "all-MiniLM-L6-v2")

    def add_post(self, run_id: str, text: str) -> None:
        vec = self._engine.embed(text)
        self._history.append((run_id, vec))

    def score(self, draft: str) -> dict[str, Any]:
        """Return {novelty_score, most_similar_run_id, max_similarity}."""
        if not self._history:
            return {"novelty_score": 1.0, "most_similar_run_id": None, "max_similarity": 0.0}
        q_vec = self._engine.embed(draft)
        max_sim = 0.0
        most_similar = None
        for run_id, h_vec in self._history:
            dot = sum(a * b for a, b in zip(q_vec, h_vec, strict=False))
            sim = max(0.0, min(1.0, dot))
            if sim > max_sim:
                max_sim = sim
                most_similar = run_id
        novelty = round(1.0 - max_sim, 4)
        return {
            "novelty_score": novelty,
            "most_similar_run_id": most_similar,
            "max_similarity": round(max_sim, 4),
        }


# ---------------------------------------------------------------------------
# §30.1 — Phase 16.9: TTL Purger (corpus_trends eviction)
# ---------------------------------------------------------------------------


class TTLPurger:
    """
    Remove stale entries from the local JSON-based trend corpus.

    corpus_trends is short-lived (§30.1 default TTL = 72h).
    """

    def __init__(self, ttl_hours: int = 72) -> None:
        self.ttl_hours = ttl_hours

    def purge(self, corpus_path: Path) -> dict[str, Any]:
        """Evict entries older than TTL. Returns purge statistics."""
        if not corpus_path.exists():
            return {"purged": 0, "remaining": 0, "path": str(corpus_path)}

        data = load_json(corpus_path, default={})
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            return {"purged": 0, "remaining": 0, "path": str(corpus_path)}

        cutoff = datetime.now(UTC) - timedelta(hours=self.ttl_hours)
        kept: list[dict[str, Any]] = []
        purged = 0
        for entry in entries:
            ts_str = str(entry.get("ingested_at") or entry.get("freshness") or "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    kept.append(entry)
                else:
                    purged += 1
            except Exception:
                kept.append(entry)  # Keep entries with unparseable timestamps

        data["entries"] = kept
        data["last_purge"] = now_utc()
        dump_json(corpus_path, data)

        return {"purged": purged, "remaining": len(kept), "path": str(corpus_path)}


# ---------------------------------------------------------------------------
# §22.4 — Phase 16.10: Recall@K Evaluator (golden set)
# ---------------------------------------------------------------------------


class RecallEvaluator:
    """
    Measure recall@K on a configurable golden retrieval set.

    Golden set: list[Any] of {query, relevant_ids} pairs stored in a JSON fixture.
    """

    def evaluate(
        self,
        retriever: Any,
        golden_pairs: list[dict[str, Any]],
        k: int = 5,
    ) -> dict[str, Any]:
        """
        Calculate recall@K metrics.

        golden_pairs: [{query: str, relevant_ids: list[str]}]
        Returns {recall_at_k, hit_count, total_queries}.
        """
        hits = 0
        for pair in golden_pairs:
            query = pair.get("query", "")
            relevant = {str(r) for r in (pair.get("relevant_ids") or [])}
            if not query or not relevant:
                continue
            results = retriever.retrieve(query, top_k=k)
            returned_ids = {r.get("id", "") for r in results}
            if returned_ids & relevant:
                hits += 1

        total = len(golden_pairs)
        return {
            "recall_at_k": round(hits / max(total, 1), 4),
            "hit_count": hits,
            "total_queries": total,
            "k": k,
        }


# ---------------------------------------------------------------------------
# §30.1 — Phase 16.11: VectorIndex (ChromaDB persistent, brand-partitioned)
# ---------------------------------------------------------------------------


class VectorIndex:
    """
    ChromaDB-backed persistent vector index.

    Includes brand/project partitioning and rebuild versioning (§30.1).
    Falls back gracefully if chromadb is not installed.
    """

    def __init__(self, config: AppConfig, collection_name: str | None = None) -> None:
        self.config = config
        self._collection_name = collection_name or config.memory_collection
        self._client = None
        self._collection = None
        self._version_tag: str = ""
        self._backend = "disabled"
        self._embedding_engine = EmbeddingEngine()

        try:
            import chromadb  # type: ignore

            persist_path = str(config.memory_chroma_dir)
            ensure_dir(Path(persist_path))
            self._client = chromadb.PersistentClient(path=persist_path)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._backend = self._embedding_engine.backend
        except ImportError:
            pass

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def collection(self) -> Any:
        return self._collection

    def upsert(self, chunks: list[dict[str, Any]]) -> int:
        """
        Upsert chunk dicts {chunk_id, text, source_id, metadata}.

        Returns number of documents upserted.
        """
        if self._collection is None:
            return 0

        ids, documents, metadatas = [], [], []
        for c in chunks:
            cid = str(c.get("chunk_id") or uuid.uuid4())
            text = str(c.get("text") or "")
            if not text.strip():
                continue
            ids.append(cid)
            documents.append(text)
            m: dict[str, Any] = dict(c.get("metadata") or {})
            m["source_id"] = str(c.get("source_id") or "")
            m["chunk_id"] = cid
            m["indexed_at"] = now_utc()
            # Stringify all metadata values (ChromaDB requirement)
            metadatas.append({k: str(v) for k, v in m.items()})

        if ids:
            self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def get_version_tag(self) -> str:
        return self._version_tag

    def rebuild_version(self) -> str:
        """Stamp a new version tag on rebuild."""
        self._version_tag = f"v:{now_utc()}"
        return self._version_tag

    def count(self) -> int:
        if self._collection is None:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def delete_by_source(self, source_id: str) -> int:
        """Remove all chunks for a given source (for TTL or update flows)."""
        if self._collection is None:
            return 0
        try:
            res = self._collection.get(where={"source_id": source_id})
            ids = res.get("ids", [])
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)
        except Exception:
            return 0

    def close(self) -> None:
        """Explicitly release ChromaDB file handles (important on Windows)."""
        try:
            if self._client is not None:
                # chromadb PersistentClient does not expose a close() but
                # resetting the reference releases the SQLite handle.
                self._collection = None
        except Exception:  # noqa: S110
            # Ignore cleanup errors on system shutdown
            pass

    def __del__(self) -> None:
        """Trigger graceful closure on destruction."""
        self.close()


# ---------------------------------------------------------------------------
# §30.5 — Phase 16.12: Language Detector
# ---------------------------------------------------------------------------


def detect_language(text: str) -> str:
    """
    Detect language based on stop-word frequency.

    Returns "fr", "en", or "unknown".
    """
    fr_stops = {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "et",
        "en",
        "un",
        "une",
        "que",
        "qui",
        "pas",
        "avec",
        "sur",
        "mais",
        "ou",
        "donc",
        "car",
        "pour",
        "dans",
        "est",
        "sont",
        "au",
        "aux",
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "ils",
        "elles",
        "ce",
        "cette",
        "ces",
        "mon",
        "ton",
        "son",
        "ma",
        "ta",
        "sa",
        "notre",
        "votre",
        "leur",
        "tout",
        "plus",
        "très",
        "bien",
    }
    en_stops = {
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "it",
        "that",
        "he",
        "was",
        "for",
        "on",
        "are",
        "with",
        "as",
        "at",
        "be",
        "by",
        "from",
        "or",
        "an",
        "this",
        "have",
        "but",
        "his",
        "they",
        "she",
        "you",
        "we",
        "do",
        "its",
        "not",
        "been",
        "has",
        "will",
        "their",
        "if",
        "can",
        "which",
        "would",
    }
    tokens = re.findall(r"\b\w+\b", text.lower())
    fr_count = sum(1 for t in tokens if t in fr_stops)
    en_count = sum(1 for t in tokens if t in en_stops)

    if fr_count == 0 and en_count == 0:
        return "unknown"
    return "fr" if fr_count > en_count else "en"


# ---------------------------------------------------------------------------
# §16.1 — Phase 16.13: Trust-Weight Source Safety Integration
# ---------------------------------------------------------------------------

_SAFETY_TRUST: dict[str, float] = {
    "none": 1.0,
    "low": 0.9,
    "medium": 0.7,
    "high": 0.3,
    "critical": 0.0,
}


def safety_to_trust(severity: str) -> float:
    """Convert source_safety_severity to trust_weight (§16.1)."""
    return _SAFETY_TRUST.get(str(severity).lower(), 0.8)


# ---------------------------------------------------------------------------
# §30.1 — Phase 16.14: RAGClient Abstraction
# ---------------------------------------------------------------------------


class RAGClient:
    """
    Clean public interface for all RAG operations, consumed by worker nodes.

    Usage (from langgraph_workflow.py or any worker):
        client = RAGClient(config)
        hits = client.retrieve("AI reliability automation", top_k=5)
        grounding = client.check_grounding(draft, hits)
        novelty = client.score_novelty(draft)
    """

    def __init__(self, config: AppConfig, brand_id: str = "default") -> None:
        self.config = config
        self.brand_id = brand_id

        self.chunker = HierarchicalChunker(chunk_size=900, chunk_overlap=150)
        self.deduplicator = SimhashDeduplicator(threshold=4)
        self.embedding_engine = EmbeddingEngine()
        self.vector_index = VectorIndex(
            config, collection_name=f"{config.memory_collection}_{brand_id}"
        )
        self.bm25 = BM25Index()
        self.retriever = HybridRetriever(
            bm25=self.bm25,
            collection=self.vector_index.collection,
            half_life_days=7,
            snippet_quota=5,
        )
        self.reranker = CrossEncoderReranker()
        self.grounder = GroundednessChecker(min_overlap_ratio=0.15)
        self.novelty = NoveltyScorer()
        self.ttl_purger = TTLPurger(ttl_hours=72)
        self.recall_eval = RecallEvaluator()

        # Phase 22 Performance: Tiered Caching
        self.retrieval_cache = TieredCache(
            config, namespace="rag_retrieval", capacity=500, ttl=86400
        )
        self.grounding_cache = TieredCache(
            config, namespace="rag_grounding", capacity=500, ttl=172800
        )

        # Statistics for RAGOps reporting
        self._last_retrieval_latency_ms: float = 0.0
        self._index_version: str = ""

    # ---- Indexing ---

    def index_document(
        self,
        text: str,
        source_id: str,
        freshness: str = "",
        trust_weight: float = 1.0,
        lang: str = "",
        source_safety_severity: str = "none",
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Process and index a single document.

        Returns {doc_id, chunks_indexed, duplicate, lang}.
        """
        if not text.strip():
            return {"doc_id": source_id, "chunks_indexed": 0, "duplicate": False, "lang": "unknown"}

        if not lang:
            lang = detect_language(text)
        trust = min(trust_weight, safety_to_trust(source_safety_severity))

        if self.deduplicator.is_duplicate(text, source_id):
            return {"doc_id": source_id, "chunks_indexed": 0, "duplicate": True, "lang": lang}

        self.deduplicator.register(text, source_id)

        chunks = self.chunker.chunk(text, source_id=source_id)
        enriched: list[dict[str, Any]] = []
        for c in chunks:
            metadata = {
                "source_id": source_id,
                "freshness": freshness or now_utc(),
                "trust_weight": str(trust),
                "lang": lang,
                "source_safety_severity": source_safety_severity,
                **(extra_metadata or {}),
            }
            enriched.append({**c, "metadata": metadata})
            # Also add to BM25
            self.bm25.add_document(c["chunk_id"], c["text"], metadata)

        n_indexed = self.vector_index.upsert(enriched)
        return {"doc_id": source_id, "chunks_indexed": n_indexed, "duplicate": False, "lang": lang}

    def index_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """
        Index all content from a completed run.

        Includes article text, platform drafts, and trend signals.
        """
        run_id = run.get("run_id", "")
        article = run.get("article") or {}
        freshness = run.get("created_at", now_utc())
        safety = (run.get("analysis") or {}).get("source_safety", {}).get("severity", "none")
        trust = safety_to_trust(safety)

        results: list[dict[str, Any]] = []

        article_text = article.get("clean_text") or article.get("raw_text") or ""
        if article_text:
            results.append(
                self.index_document(
                    article_text,
                    f"{run_id}:article",
                    freshness=freshness,
                    trust_weight=trust,
                    source_safety_severity=safety,
                    extra_metadata={"run_id": run_id, "kind": "article"},
                )
            )

        for draft_key in ("article_draft", "x_draft", "linkedin_draft", "facebook_draft"):
            draft_text = (run.get("outputs") or {}).get(draft_key, "")
            if draft_text:
                results.append(
                    self.index_document(
                        draft_text,
                        f"{run_id}:{draft_key}",
                        freshness=freshness,
                        trust_weight=trust,
                        extra_metadata={"run_id": run_id, "kind": draft_key},
                    )
                )
                # Register for novelty tracking
                self.novelty.add_post(run_id, draft_text)

        for sig in run.get("trend_signals") or []:
            title = sig.get("title") or ""
            desc = sig.get("description") or ""
            sig_text = f"{title} {desc}".strip()
            if sig_text:
                results.append(
                    self.index_document(
                        sig_text,
                        f"{run_id}:trend:{sig.get('source', '')}",
                        freshness=sig.get("published_at") or freshness,
                        trust_weight=0.7,
                        extra_metadata={"run_id": run_id, "kind": "trend_signal"},
                    )
                )

        version = self.vector_index.rebuild_version()
        return {
            "run_id": run_id,
            "index_version": version,
            "indexed_items": results,
            "total_chunks": sum(r.get("chunks_indexed", 0) for r in results),
        }

    # ---- Retrieval ---

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        lang_filter: str | None = None,
        min_trust: float = 0.0,
        rerank: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Execute full retrieval pipeline.

        Includes hybrid search, freshness decay, trust filters, and reranking.
        """
        t0 = time.perf_counter()
        cache_key = {"query": query, "top_k": top_k, "lang": lang_filter, "trust": min_trust}
        cached = self.retrieval_cache.get(cache_key)
        if cached:
            self._last_retrieval_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return cast(list[dict[str, Any]], cached)

        hits = self.retriever.retrieve(
            query, top_k=top_k * 2, lang_filter=lang_filter, min_trust=min_trust
        )
        if rerank:
            hits = self.reranker.rerank(query, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        self.retrieval_cache.set(cache_key, hits)
        self._last_retrieval_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        if hits:
            monitor.record_rag_hit()
            monitor.export()
        return hits

    # ---- Grounding & Novelty ---

    def check_grounding(self, draft: str, docs: list[dict[Any]]) -> dict[str, Any]:
        result = self.grounder.check(draft, docs)
        if not result.get("grounded", True):
            monitor.record_rag_failure()
            monitor.export()
        return result

    def score_novelty(self, draft: str) -> dict[str, Any]:
        return self.novelty.score(draft)

    # ---- Maintenance ---

    def purge_trends(self) -> dict[str, Any]:
        corpus_path = self.config.data_dir / "corpus_trends.json"
        return self.ttl_purger.purge(corpus_path)

    def evaluate_recall(self, golden_pairs: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
        return self.recall_eval.evaluate(self, golden_pairs, k)

    def verify_no_secret_leaks(self) -> dict[str, Any]:
        """
        Scan indexed documents for potential API keys or tokens.

        Returns {clean: bool, violations: list[str]}.
        """
        secret_patterns = [
            r"sk-[A-Za-z0-9]{20,}",  # OpenAI keys
            r"AKIA[0-9A-Z]{16}",  # AWS keys
            r"eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+",  # JWT tokens
            r"(?i)(api_?key|api_?token|secret_?key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
            r"(?i)bot_?token\s*[:=]\s*['\"]?[0-9]+:[A-Za-z0-9_\-]{30,}",
        ]
        compiled = [re.compile(p) for p in secret_patterns]
        violations = []
        if self.vector_index.collection is not None:
            try:
                results = self.vector_index.collection.get(limit=200)
                for doc in results.get("documents") or []:
                    for pat in compiled:
                        if pat.search(str(doc or "")):
                            violations.append(str(doc[:60]) + "…")
                            break
            except Exception:  # noqa: S110
                pass
        return {"clean": len(violations) == 0, "violations": violations[:5]}

    def check_integrity(self) -> dict[str, Any]:
        """Check for obvious rule violations in the corpus."""
        violations: list[str] = []
        patterns = [r"copyright\s+©", r"all\s+rights\s+reserved", r"terms\s+of\s+service"]
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

        if self.vector_index.collection is not None:
            try:
                results = self.vector_index.collection.get(limit=200)
                for doc in results.get("documents") or []:
                    for pat in compiled:
                        if pat.search(str(doc or "")):
                            violations.append(str(doc[:60]) + "…")
                            break
            except Exception:  # noqa: S110
                pass

        return {"clean": len(violations) == 0, "violations": violations[:5]}

    def get_stats(self) -> dict[str, Any]:
        """Return index statistics for gap tracker / RAGOps."""
        return {
            "backend": self.vector_index.backend,
            "index_version": self.vector_index.get_version_tag(),
            "vector_count": self.vector_index.count(),
            "bm25_docs": len(self.bm25._docs),
            "last_retrieval_latency_ms": self._last_retrieval_latency_ms,
            "snippet_quota": self.retriever.snippet_quota,
            "embedding_backend": self.embedding_engine.backend,
            "novelty_history_size": len(self.novelty._history),
        }

    def close(self) -> None:
        """Release all file handles. Call explicitly when done (especially on Windows)."""
        if hasattr(self, "vector_index"):
            self.vector_index.close()

    def __del__(self) -> None:
        """Trigger graceful closure on destruction."""
        self.close()
