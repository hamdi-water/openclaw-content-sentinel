"""Phase 16: RAG retrieval test fixtures — 10 edge-case scenarios (§22.4)."""

from __future__ import annotations

GOLDEN_RETRIEVAL_FIXTURES = [
    # 1. Exact keyword match
    {
        "fixture_id": "rag_f01",
        "query": "AI reliability automation content ops",
        "relevant_ids": ["run-001:article"],
        "description": "Exact keyword match on article",
    },
    # 2. Paraphrase / synonym match
    {
        "fixture_id": "rag_f02",
        "query": "machine learning workflow dependability",
        "relevant_ids": ["run-001:article", "run-002:article"],
        "description": "Synonym retrieval — reliability ≈ dependability",
    },
    # 3. Cross-platform draft retrieval
    {
        "fixture_id": "rag_f03",
        "query": "LinkedIn post approval workflow evidence pack",
        "relevant_ids": ["run-001:linkedin_draft"],
        "description": "Platform-specific draft retrieval",
    },
    # 4. Trend signal retrieval
    {
        "fixture_id": "rag_f04",
        "query": "AI content publishing trends 2025",
        "relevant_ids": ["run-001:trend:google-news-rss"],
        "description": "Trend signal retrieval from RAG corpus",
    },
    # 5. Empty context — should return empty, not crash
    {
        "fixture_id": "rag_f05",
        "query": "xyzzy-foobar-nonexistent-term-99",
        "relevant_ids": [],
        "description": "Query with no relevant docs — graceful empty return",
    },
    # 6. Long query (> 200 tokens)
    {
        "fixture_id": "rag_f06",
        "query": " ".join(["operational reliability", "content automation"] * 50),
        "relevant_ids": ["run-001:article"],
        "description": "Very long query — should not crash or truncate badly",
    },
    # 7. French language filtering
    {
        "fixture_id": "rag_f07",
        "query": "fiabilité de l'automatisation du contenu",
        "relevant_ids": ["run-fr-001:article"],
        "description": "French query with language filter=fr",
    },
    # 8. High safety severity — low trust weight — should rank lower
    {
        "fixture_id": "rag_f08",
        "query": "prompt injection security vulnerability",
        "relevant_ids": [],
        "description": "High-risk source should not surface at trust filter 0.5+",
    },
    # 9. Deduplication — exact duplicate should not inflate recall
    {
        "fixture_id": "rag_f09",
        "query": "approval workflow audit trail compliance",
        "relevant_ids": ["run-002:article"],
        "description": "Deduplication — only one instance should be returned",
    },
    # 10. Freshness gate — stale docs should be deprioritized
    {
        "fixture_id": "rag_f10",
        "query": "social media content scheduling",
        "relevant_ids": ["run-003:article"],
        "description": "Freshness decay — docs < 24h old should rank above stale",
    },
]
