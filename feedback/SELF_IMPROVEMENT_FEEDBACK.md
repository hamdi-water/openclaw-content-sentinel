# Self-Improvement Feature Requests

Inspired by Hermes Agent's learning loop architecture, adapted for the existing OpenClaw Content Sentinel stack. These are features to build on top of the current Python backend + OpenClaw runtime + Groq Llama 3.3 70B setup — no framework swap needed.

---

## 1. Performance Review Loop (every N runs)

Hermes evaluates itself every 15 tool calls. The sentinel should do the same after every N daily runs (e.g., 7 or 14).

**What to build:**
- A `self_review` CLI command and scheduled task that runs after every N completed runs
- It reads the last N run records from the ledger and computes:
  - Average quality gate score
  - Average confidence score
  - Approval rate (approved vs rejected)
  - Post success rate per platform
  - Most common failure reasons from `failure_taxonomy`
  - Which trend sources produced signals that made it into final drafts
- It generates a structured improvement report saved to `workspace-data/reviews/`
- The LLM (via Groq) reads the report and suggests concrete parameter adjustments

**Config changes it can propose:**
- `confidence_threshold` — raise or lower based on false positive/negative rate
- `competitor_overlap_threshold` — tighten if drafts are too similar to sources
- `memory_overlap_threshold` — adjust based on novelty scores
- Feed URL priorities in `default_feeds.json` — demote feeds that never produce useful signals
- Prompt template tweaks in `daily_prompt_template.md`

**Operator approval:** Changes are proposed, not applied automatically. Operator reviews via Telegram or CLI before they take effect.

---

## 2. Skill Creation from Successful Runs

Hermes creates reusable skill documents when it solves a hard problem. The sentinel should do the same when a run scores exceptionally well.

**What to build:**
- After a run scores above a threshold (e.g., quality gate > 0.9, approved on first pass, all platforms posted successfully), extract what made it work:
  - The prompt structure that was used
  - Which trend signals were incorporated
  - The editorial angle that was chosen
  - The hybrid context composition
- Save this as a "winning pattern" document in `workspace-data/patterns/`
- Future runs can retrieve these patterns from memory and use them as reference when the LLM generates new content

**Format:** Markdown files with structured metadata (date, quality score, platforms, keywords, angle summary). Searchable via the existing vector memory system.

---

## 3. Prompt Template Evolution

Hermes uses JEPA (Generic Evolution of Prompt Architectures) to optimize prompts over time. The sentinel can do a simpler version.

**What to build:**
- Track which `daily_input.json` configurations produced the highest-scoring runs
- After every review cycle (feature #1), the LLM analyzes the top 3 and bottom 3 runs and identifies:
  - What prompt phrasing correlated with higher quality
  - What business angles resonated (based on approval speed and post success)
  - What keyword combinations produced better trend signals
- Generate a revised `daily_prompt_template.md` as a candidate
- Store prompt template versions in `workspace-data/prompt-history/` with their associated quality metrics
- Operator picks which template to promote to active

---

## 4. Source Trust Scoring

The project already tracks trend signal sources. It should learn which sources are reliable over time.

**What to build:**
- For each source (RSS feed, Google News, SearXNG, pytrends), track:
  - How often its signals make it into the final curated set
  - How often its signals appear in the published article
  - Whether runs using its signals score higher
- Compute a rolling trust score per source (last 30 days)
- Feed this into `curate_trend_signals()` as a weight multiplier — the existing `_signal_relevance_score` function already supports scoring, just add a trust factor
- Auto-demote sources that consistently produce irrelevant signals
- Surface trust scores in the dashboard

---

## 5. Editorial Memory with Intelligent Forgetting

Hermes uses a four-layer memory system with intelligent forgetting. The sentinel's memory system should do the same.

**What to build:**
- Tag memory documents with a "usefulness score" based on how often they're retrieved and whether the runs that used them scored well
- Implement decay: documents that haven't been retrieved in 30+ days get their relevance weight reduced
- Implement compression: after 60 days, summarize old run memories into condensed "lesson learned" documents and remove the verbose originals
- The existing `TTLPurger` in `rag.py` handles time-based expiry for trends — extend this pattern to the full memory store
- Keep a "core lessons" collection that never expires — these are the distilled insights from the best runs

---

## 6. Failure Pattern Learning

The project already has `failure_taxonomy` on run records. It should learn from failures automatically.

**What to build:**
- After each failed publish or rejected run, log the failure context:
  - Platform, failure reason, browser state, time of day
  - What was different about this run vs successful ones
- After N failures of the same type, the LLM generates a mitigation strategy:
  - For `not_logged_in` failures: suggest browser profile refresh schedule
  - For `rate_limited`: suggest time-of-day adjustments
  - For `rejected` runs: analyze what the operator objected to and adjust quality gate thresholds
- Store mitigation strategies in `workspace-data/mitigations/`
- Apply them automatically where safe (e.g., retry timing), propose them for operator review where not (e.g., quality gate changes)

---

## 7. User Model / Operator Preference Learning

Hermes builds a deepening model of who you are. The sentinel should learn operator preferences.

**What to build:**
- Track operator behavior patterns:
  - How quickly they approve vs reject
  - What they add in approval/rejection notes
  - Which platforms they care about most (based on which failures they fix first)
  - What time of day they're most responsive
- Use this to:
  - Send Telegram previews at optimal times
  - Prioritize the operator action queue based on what they actually act on
  - Pre-fill approval notes with common patterns
  - Adjust the heartbeat check frequency based on operator activity

---

## Implementation Priority

| Feature | Effort | Impact | Priority |
|---|---|---|---|
| 1. Performance review loop | Medium | High | Do first |
| 4. Source trust scoring | Low | Medium | Do second |
| 6. Failure pattern learning | Medium | High | Do third |
| 2. Skill creation from wins | Low | Medium | Do fourth |
| 3. Prompt template evolution | Medium | High | Do fifth |
| 5. Intelligent forgetting | Medium | Medium | Do sixth |
| 7. Operator preference learning | High | Medium | Do last |

All features use the existing stack (Python backend + Groq Llama 3.3 70B + SQLite ledger + vector memory + knowledge graph). No new frameworks or dependencies required.
