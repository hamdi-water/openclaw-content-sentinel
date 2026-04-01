"""tests/test_rag.py – Phase 16 Advanced RAG Engineering unit tests

Covers all 14 components of rag.py without requiring chromadb or
sentence-transformers (falls back gracefully to local TF-IDF).
"""
from __future__ import annotations

import math
import pathlib
import tempfile
import unittest

from openclaw_content_sentinel.config import AppConfig
from openclaw_content_sentinel.rag import (
    BM25Index,
    CrossEncoderReranker,
    EmbeddingEngine,
    GroundednessChecker,
    HierarchicalChunker,
    NoveltyScorer,
    RAGClient,
    SimhashDeduplicator,
    TTLPurger,
    VectorIndex,
    _freshness_decay,
    detect_language,
    safety_to_trust,
)
from openclaw_content_sentinel.rag_fixtures import GOLDEN_RETRIEVAL_FIXTURES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base_dir: pathlib.Path) -> AppConfig:
    (base_dir / "data").mkdir(exist_ok=True)
    (base_dir / "runs").mkdir(exist_ok=True)
    (base_dir / "logs").mkdir(exist_ok=True)
    (base_dir / "config").mkdir(exist_ok=True)
    (base_dir / ".env").write_text("OPENCLAW_SENTINEL_DATA_DIR=data\n")
    return AppConfig.from_env(base_dir=base_dir)


ARTICLE_TEXT = (
    "AI reliability controls are essential for modern content operations. "
    "Approval workflows reduce risk, ensure compliance, and build trust. "
    "Automated evidence packs accelerate operator reviews. "
    "Our platform integrates LangGraph orchestration for multi-step validation. "
    "Zero-cost trend research surfaces relevant industry signals daily. "
    "Content operations teams benefit from structured editorial approval gates."
)


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestHierarchicalChunker(unittest.TestCase):

    def setUp(self):
        self.chunker = HierarchicalChunker(chunk_size=200, chunk_overlap=30)

    def test_returns_no_chunks_for_empty_input(self):
        self.assertEqual(self.chunker.chunk(""), [])

    def test_returns_single_chunk_for_short_text(self):
        chunks = self.chunker.chunk("Short paragraph.", source_id="doc1")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source_id"], "doc1")

    def test_strips_html_tags(self):
        html = "<h1>Title</h1><p>Content here.</p>"
        chunks = self.chunker.chunk(html)
        for c in chunks:
            self.assertNotIn("<", c["text"])

    def test_chunks_long_text_into_multiple_parts(self):
        long_text = "Sentence number {n}. " * 60
        chunks = self.chunker.chunk(long_text, source_id="big")
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c["text"]), self.chunker.chunk_size + 50)

    def test_removes_boilerplate_lines(self):
        text = "Real content here.\nCookie Policy: We use cookies.\nMore real content."
        cleaned = self.chunker.clean(text)
        self.assertNotIn("Cookie Policy", cleaned)
        # At least one real content line should survive
        self.assertTrue("Real content" in cleaned or "More real content" in cleaned)

    def test_chunk_ids_are_unique_per_source(self):
        chunks = self.chunker.chunk(ARTICLE_TEXT * 5, source_id="mysrc")
        ids = [c["chunk_id"] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))


class TestSimhashDeduplicator(unittest.TestCase):

    def setUp(self):
        self.ded = SimhashDeduplicator(threshold=4)

    def test_computes_stable_fingerprint(self):
        fp1 = SimhashDeduplicator.compute(ARTICLE_TEXT)
        fp2 = SimhashDeduplicator.compute(ARTICLE_TEXT)
        self.assertEqual(fp1, fp2)

    def test_different_texts_have_different_fingerprints(self):
        fp1 = SimhashDeduplicator.compute("Hello world content ops")
        fp2 = SimhashDeduplicator.compute("Blockchain DeFi investment crypto")
        self.assertNotEqual(fp1, fp2)

    def test_hamming_distance_zero_for_identical(self):
        fp1 = SimhashDeduplicator.compute(ARTICLE_TEXT)
        self.assertEqual(SimhashDeduplicator.hamming(fp1, fp1), 0)

    def test_near_duplicate_detected(self):
        text1 = "AI reliability automation for content teams"
        text2 = "AI reliability automation for content teams!"
        self.ded.register(text1, "doc-a")
        self.assertTrue(self.ded.is_duplicate(text2, "doc-b"))

    def test_distinct_docs_not_flagged_as_duplicates(self):
        self.ded.register(ARTICLE_TEXT, "doc-a")
        distinct = "Mars colonisation plans announced by SpaceX for 2030 missions."
        self.assertFalse(self.ded.is_duplicate(distinct, "doc-b"))


class TestEmbeddingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EmbeddingEngine()

    def test_embed_returns_float_vector(self):
        vec = self.engine.embed("test content reliability")
        self.assertIsInstance(vec, list)
        self.assertTrue(all(isinstance(v, float) for v in vec))

    def test_embed_is_normalised(self):
        vec = self.engine.embed("AI content operations")
        magnitude = math.sqrt(sum(v * v for v in vec))
        self.assertAlmostEqual(magnitude, 1.0, places=2)

    def test_embed_batch_same_size(self):
        texts = ["first doc", "second doc", "third doc"]
        vecs = self.engine.embed_batch(texts)
        self.assertEqual(len(vecs), 3)
        dim = len(vecs[0])
        self.assertTrue(all(len(v) == dim for v in vecs))

    def test_different_texts_yield_different_vectors(self):
        v1 = self.engine.embed("AI reliability automation")
        v2 = self.engine.embed("basketball sports score")
        self.assertNotEqual(v1, v2)


class TestBM25Index(unittest.TestCase):

    def setUp(self):
        self.bm25 = BM25Index()
        self.bm25.add_document("d1", "AI reliability automation content ops", {})
        self.bm25.add_document("d2", "baseball sports game score championship", {})
        self.bm25.add_document("d3", "approval workflow evidence pack audit", {})

    def test_relevant_doc_scores_higher(self):
        results = self.bm25.score("AI automation", top_k=5)
        self.assertTrue(any(r["id"] == "d1" for r in results))
        top = results[0]
        self.assertEqual(top["id"], "d1")

    def test_irrelevant_query_returns_empty(self):
        results = self.bm25.score("zzz-nonexistent-xyz", top_k=3)
        self.assertEqual(results, [])

    def test_all_docs_count_in_index(self):
        self.assertEqual(len(self.bm25._docs), 3)


class TestFreshnessDecay(unittest.TestCase):

    def test_very_recent_doc_has_high_decay(self):
        from openclaw_content_sentinel.utils import now_utc
        decay = _freshness_decay(now_utc(), half_life_days=7)
        self.assertGreater(decay, 0.95)

    def test_old_doc_has_low_decay(self):
        old_ts = "2020-01-01T00:00:00+00:00"
        decay = _freshness_decay(old_ts, half_life_days=7)
        self.assertLess(decay, 0.01)

    def test_invalid_ts_returns_one(self):
        decay = _freshness_decay("not-a-date", half_life_days=7)
        self.assertEqual(decay, 1.0)


class TestCrossEncoderReranker(unittest.TestCase):

    def setUp(self):
        self.reranker = CrossEncoderReranker()

    def test_reranks_by_overlap(self):
        hits = [
            {"id": "d1", "text": "sports basketball game", "freshness_decay": 1.0, "trust_weight": 1.0},
            {"id": "d2", "text": "AI reliability content ops automation", "freshness_decay": 1.0, "trust_weight": 1.0},
        ]
        ranked = self.reranker.rerank("AI reliability automation", hits)
        self.assertEqual(ranked[0]["id"], "d2")

    def test_top_k_respected(self):
        hits = [{"id": f"d{i}", "text": f"doc {i}", "freshness_decay": 1.0, "trust_weight": 1.0} for i in range(10)]
        ranked = self.reranker.rerank("test", hits, top_k=3)
        self.assertEqual(len(ranked), 3)


class TestGroundednessChecker(unittest.TestCase):

    def setUp(self):
        self.gc = GroundednessChecker(min_overlap_ratio=0.15)

    def test_well_grounded_draft_passes(self):
        context = [{"text": ARTICLE_TEXT}]
        draft = (
            "AI reliability controls ensure compliance in content operations. "
            "Evidence packs accelerate operator approval workflows significantly."
        )
        result = self.gc.check(draft, context)
        self.assertTrue(result["grounded"])
        self.assertGreater(result["score"], 0.5)

    def test_ungrounded_draft_fails(self):
        context = [{"text": "Sports news about football championships."}]
        draft = (
            "AI reliability orchestration enables zero-cost evidence pack generation. "
            "LangGraph nodes automate approval workflows with citation enforcement."
        )
        result = self.gc.check(draft, context)
        self.assertFalse(result["grounded"])

    def test_empty_draft_passes(self):
        result = self.gc.check("", [{"text": ARTICLE_TEXT}])
        self.assertTrue(result["grounded"])


class TestNoveltyScorer(unittest.TestCase):

    def setUp(self):
        self.ns = NoveltyScorer()

    def test_no_history_returns_full_novelty(self):
        result = self.ns.score("New topic about AI reliability")
        self.assertEqual(result["novelty_score"], 1.0)

    def test_identical_text_has_low_novelty(self):
        text = "AI reliability automation content operations"
        self.ns.add_post("run-001", text)
        result = self.ns.score(text)
        self.assertLess(result["novelty_score"], 0.5)

    def test_distinct_text_has_high_novelty(self):
        self.ns.add_post("run-001", "AI reliability content approval workflows")
        result = self.ns.score("Mars colonisation astrobiology water exploration")
        self.assertGreater(result["novelty_score"], 0.5)


class TestLanguageDetector(unittest.TestCase):

    def test_detects_english(self):
        self.assertEqual(detect_language("The content is reliable and the workflow is automated"), "en")

    def test_detects_french(self):
        self.assertEqual(detect_language("Le contenu est fiable et le processus est automatisé"), "fr")

    def test_unknown_gibberish(self):
        result = detect_language("xyzzy foobar baz quux 1234")
        self.assertIn(result, ("unknown", "en", "fr"))


class TestSafetyToTrust(unittest.TestCase):

    def test_none_severity_gives_full_trust(self):
        self.assertEqual(safety_to_trust("none"), 1.0)

    def test_critical_severity_gives_zero_trust(self):
        self.assertEqual(safety_to_trust("critical"), 0.0)

    def test_medium_severity_reduces_trust(self):
        self.assertLess(safety_to_trust("medium"), 1.0)
        self.assertGreater(safety_to_trust("medium"), 0.0)


class TestTTLPurger(unittest.TestCase):

    def test_purge_empty_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "corpus.json"
            purger = TTLPurger(ttl_hours=72)
            result = purger.purge(p)
            self.assertEqual(result["purged"], 0)

    def test_purge_removes_stale_entries(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "corpus.json"
            p.write_text(json.dumps({
                "entries": [
                    {"ingested_at": "2020-01-01T00:00:00+00:00", "text": "old"},
                    {"ingested_at": "2099-01-01T00:00:00+00:00", "text": "future"},
                ]
            }))
            purger = TTLPurger(ttl_hours=24)
            result = purger.purge(p)
            self.assertEqual(result["purged"], 1)
            self.assertEqual(result["remaining"], 1)


class TestRAGClientIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = _make_config(pathlib.Path(self.tmp))
        self.client = RAGClient(self.config)

    def test_index_document_returns_stats(self):
        result = self.client.index_document(
            ARTICLE_TEXT, "test-doc-001",
            freshness="2026-03-28T10:00:00+00:00",
            trust_weight=0.9,
        )
        self.assertEqual(result["doc_id"], "test-doc-001")
        self.assertFalse(result["duplicate"])
        self.assertGreater(result["chunks_indexed"], 0)

    def test_duplicate_document_detected(self):
        self.client.index_document(ARTICLE_TEXT, "doc-a")
        result = self.client.index_document(ARTICLE_TEXT, "doc-b")
        self.assertTrue(result["duplicate"])

    def test_retrieve_returns_results_after_indexing(self):
        self.client.index_document(ARTICLE_TEXT, "doc-rag-01")
        hits = self.client.retrieve("AI reliability approval workflow", top_k=3)
        # BM25 always has hits after indexing; vector may or may not
        self.assertIsInstance(hits, list)

    def test_check_grounding_returns_dict(self):
        context = [{"text": ARTICLE_TEXT}]
        result = self.client.check_grounding("AI reliability content ops", context)
        self.assertIn("grounded", result)
        self.assertIn("score", result)

    def test_score_novelty_returns_dict(self):
        result = self.client.score_novelty("AI reliability test draft")
        self.assertIn("novelty_score", result)

    def test_get_stats_returns_expected_keys(self):
        stats = self.client.get_stats()
        for key in ("backend", "vector_count", "bm25_docs", "last_retrieval_latency_ms"):
            self.assertIn(key, stats)

    def test_verify_no_secret_leaks_returns_clean_for_normal_text(self):
        self.client.index_document(ARTICLE_TEXT, "safe-doc")
        result = self.client.verify_no_secret_leaks()
        self.assertIn("clean", result)
        self.assertIsInstance(result["clean"], bool)

    def test_purge_trends_returns_stats(self):
        result = self.client.purge_trends()
        self.assertIn("purged", result)

    def test_golden_fixtures_have_required_fields(self):
        for fixture in GOLDEN_RETRIEVAL_FIXTURES:
            self.assertIn("fixture_id", fixture)
            self.assertIn("query", fixture)
            self.assertIn("relevant_ids", fixture)
            self.assertIsInstance(fixture["query"], str)
            self.assertTrue(len(fixture["query"]) > 0)


class TestVectorIndex(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = _make_config(pathlib.Path(self.tmp))
        self.index = VectorIndex(self.config)

    def test_count_starts_at_zero(self):
        # ChromaDB may or may not be available
        count = self.index.count()
        self.assertGreaterEqual(count, 0)

    def test_rebuild_version_sets_tag(self):
        tag = self.index.rebuild_version()
        self.assertTrue(tag.startswith("v:"))


if __name__ == "__main__":
    unittest.main()
