# Architecture

> **jev-skill-retriever** is a Hermes Agent plugin that performs structured, multi-label skill recall with Jev and retains the original LLM composer as an automatic fallback.

## High-Level Flow

```
                     ┌─────────────────────────────────────┐
  User Message ─────▶│ pre_llm_call hook                   │
                     │ plugin/__init__.py: _on_pre_llm_call │
                     └──────────┬──────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │ Continuation Handler   │ ← check for compaction rehydration
                    │ (vague_gate.py)        │
                    └───────────┬───────────┘
                                │
                    ┌───────────┴───────────┐
                    │ Early return check     │ ← empty/short message → ret None
                    └───────────┬───────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
   ┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
   │ ★ Path 1:        │  │ ▸ Path 2:    │  │ · Path 3:    │
   │   Jev Recaller   │  │   Legacy LLM │  │   Static     │
   │   (structured)   │  │   Composer   │  │   Fallback   │
   └──────┬───────────┘  └──────┬───────┘  └──────┬───────┘
          │                     │                  │
          └─────────────────────┼──────────────────┘
                                ▼
                    ┌──────────────────────┐
                    │ Hint injection        │
                    │ "[Skill Capability    │
                    │  Chain]" prepended    │
                    │ to user message       │
                    └──────────────────────┘
```

> **Architecture diagram**: [Excalidraw](https://gist.githubusercontent.com/ChonSong/f1f23335282e8dd6951d4dbcf18bf319/raw/skill_retriever.excalidraw) (drag onto [excalidraw.com](https://excalidraw.com))

## 3-Path Pipeline

All three paths run from `_on_pre_llm_call()` in `plugin/__init__.py:312`. They fire in strict precedence order:

### ★ Path 1: Jev Recaller (Structured — Preferred)

**File:** `src/skill_retriever/jev_recaller.py`

1. Load the flat skill index.
2. Rank the catalog with Choice questions, splitting oversized catalogs into concurrent batches.
3. Take a small cross-batch shortlist and ask one independent Noul question per candidate.
4. Apply the absolute Noul threshold and return up to `SKILL_RETRIEVER_JEV_MAX_RESULTS` skills.
5. Validate every answer against its exact candidate ID before injecting hints.

The result is multi-label and may be empty. A valid empty result suppresses static fallback so unrelated skills are not suggested.

### ▸ Path 2: Legacy LLM Composer

**File:** `src/skill_retriever/compose.py` (287 LOC)

Used when `SKILL_RETRIEVER_ENGINE=llm`, when `TYPESAFE_API_KEY` is absent, or when the Jev request fails. A single generative LLM call curates a query-specific skill bundle:

1. **Load flat index** — reads `~/.hermes/skill-retriever-cache/flat_index.json` (~50KB, ~400 skills)
2. **`_pre_filter()`** — cheap keyword match by token overlap against skill name + description + tags. Returns top 50 candidates. Applies:
   - **Quality floor penalty**: Skills missing `has_steps`, `has_verification`, or `has_pitfalls` get 0.5× score
   - **History boost**: Previously-useful skills (from `skill-usage.jsonl`) get score × (1 + usefulness_ratio)
3. **One-shot LLM call** — `SKILL_COMPOSER_PROMPT` formats the 50 candidates, asks the LLM to pick 3-10 with `name`, `load_as` (must/should/consider), `reason`, and `confidence`
4. **`bundle_to_hint_block()`** — formats as a natural-language hint block with ★/▸/· markers and compliance instructions
5. **Auto-log** — each returned skill is logged to `skill-usage.jsonl` via `skill_usage_logger.py` for future feedback

Returns `None` when: flat index missing, pre-filter returns 0 candidates, or LLM call fails.

### ▸ Path 2: Static Capability Chains (Fallback)

**File:** `plugin/__init__.py` — `_INTENT_PATTERNS` and `_CAPABILITY_CHAINS` (lines 114-234)

Keyword pattern matching against a hardcoded intent → skill chain dictionary:

| Intent | Trigger Patterns | Skills in Chain |
|--------|-----------------|-----------------|
| `large_build` | `build a`, `from scratch`, `fullstack`, `scaffold` | 10 (★2 / ▸3 / ·5) |
| `code_review` | `review`, `audit`, `inspect`, `code quality` | 6 (★2 / ▸1 / ·3) |
| `large_refactor` | `refactor`, `rewrite`, `migrate`, `restructure` | 5 (★2 / ▸1 / ·2) |
| `deploy` | `deploy`, `ship`, `release`, `docker` | 4 (★1 / ▸2 / ·1) |

Intent detection (`_detect_intent()`): ordered by specificity — code_review checked before large_build to handle "review this and build" correctly. `large_build` requires both a keyword match AND message > 5 words.

Each chain also injects a **behavioral nudge** (`_BEHAVIORAL_NUDGES`) — one-line workflow advice specific to the intent.

### · Path 3: General Fallback (Last Resort)

**File:** `plugin/__init__.py` — `_CAPABILITY_CHAINS["general"]` (lines 226-233)

Always injects 5 generic skills so the model never gets 0 hints. Skills: `skill-retrieval-system`, `skill-creator-ms`, `skills-list`, `skill-curation`, `skill-router`.

If ALL paths fail (should never happen), a diagnostic message is injected suggesting `skills_list`.

## Continuation Handler

**File:** `src/skill_retriever/vague_gate.py` — `handle_continuation()` (lines 261-332)

Fires BEFORE the empty/short-message early return. Addresses the **dominant failure pattern** (50% of skill-retriever misses per 30-day audit):

1. Detects continuation prompts: `is_continuation_prompt()` — checks empty, <3 chars, or `_CONTINUATION_TOKENS` set
2. Checks `conversation_history[0]` for compaction summary via `is_compaction_summary_message()` from Hermes' `agent.context_compressor`
3. Extracts keywords from the summary text
4. Runs composer `_pre_filter()` against those keywords at `top_k=8`
5. Injects matched skills as a context hint block

Returns `None` when NOT a compaction restart (falls through to normal pipeline).

## Vague-Prompt Deepthink Gate

**File:** `src/skill_retriever/vague_gate.py` — `is_vague_prompt()` (lines 161-202)

Opt-in (env `SKILL_RETRIEVER_DEEPTHINK_GATE=1`). Five static heuristics, no LLM calls:

| # | Heuristic | Detection |
|---|-----------|-----------|
| 1 | Empty/whitespace-only | Always vague |
| 2 | Continuation token/filler | `_CONTINUATION_TOKENS`, `_FILLER_TOKENS` |
| 3 | Word count < 10 AND no specific noun | `_has_specific_noun()` checks against `_VAGUE_NON_SPECIFIC` set |
| 4 | Bare action-verb start + <15 words + no specific noun | `_ACTION_VERB_RE` regex (auto-generated from `_VAGUE_VERBS` + `_MULTI_WORD_VERBS`) |
| 5 | Composer pre_filter returns 0 candidates | Only when Path 1 flat index is available |

When triggered: logs to `skill_retriever_debug.log` with top-3 candidate names, then falls through to normal pipeline. The `clarify` tool cannot be invoked from plugin hooks — logging is the only action.

## Config & LLM Discovery

**File:** `src/skill_retriever/config.py` (204 LOC)

All settings are env-var driven with sensible defaults. Five-level priority chain for LLM credentials:

```
  1. SKILL_RETRIEVER_LLM_MODEL / _API_KEY / _BASE_URL (explicit override)
  2. Hermes config.yaml → model.provider → custom_providers matching
  3. OPENAI_API_KEY + OPENAI_BASE_URL env vars
  4. NOUS_API_KEY + first custom_provider base_url
  5. Fallback: gpt-4o-mini default
```

Key configurable parameters:

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `SKILL_RETRIEVER_DISABLE` | — | Set `1` to disable entirely |
| `TYPESAFE_API_KEY` | — | TypeSafe API key used by Jev |
| `SKILL_RETRIEVER_ENGINE` | `jev` | `jev`, `auto`, or `llm` |
| `SKILL_RETRIEVER_JEV_MODEL` | `jev-1.13.0` | Pinned Jev model |
| `SKILL_RETRIEVER_JEV_THRESHOLD` | `0.35` | Minimum relevance probability |
| `SKILL_RETRIEVER_JEV_SHORTLIST_SIZE` | `12` | Candidates verified by Noul |
| `SKILL_RETRIEVER_JEV_BATCH_SIZE` | `180` | Skills per Choice request |
| `SKILL_RETRIEVER_JEV_MAX_PARALLEL` | `4` | Concurrent Jev batches |
| `SKILL_RETRIEVER_LLM_MODEL` | Hermes config → `gpt-4o-mini` | LLM model override |
| `SKILL_RETRIEVER_CACHE_DIR` | `~/.hermes/skill-retriever-cache` | Cache directory |
| `SKILL_RETRIEVER_TEMPERATURE` | `0.3` | LLM temperature |
| `SKILL_RETRIEVER_BRANCHING_FACTOR` | `3` | Tree branching (CLI only) |
| `SKILL_RETRIEVER_MAX_PARALLEL` | `5` | Max parallel branches (CLI only) |
| `SKILL_RETRIEVER_PRUNE` | `true` | Enable dedup pruning (CLI only) |
| `SKILL_RETRIEVER_MAX_DEPTH` | `5` | Tree max depth (CLI only) |
| `SKILL_RETRIEVER_DEEPTHINK_GATE` | `0` | Enable vague-prompt logging |
| `SKILL_RETRIEVER_TREE_PATH` | Bundled `tree_10000_ship_safe.yaml` | Capability tree path |

### LiteLLM Cache

`ensure_cache()` initializes a disk cache for LiteLLM in `src/skill_retriever/.litellm_cache/` to reduce repeated API calls for identical prompts.

## Quality Signals & Usage Feedback Loop

**File:** `src/skill_retriever/compose.py` (lines 30-132)

Three mechanisms improve retrieval over time:

### 1. Quality Signals
```python
QUALITY_SIGNALS = ("has_steps", "has_verification", "has_pitfalls")
```
Skills missing ≥2 of these signals get a 0.5× score penalty in `_pre_filter()`.

### 2. Usage History
```python
_load_usage_history(window_hours=720)  # 30 days
```
Reads `skill-usage.jsonl` and tracks `useful`/`irrelevant`/`harmful` counts per skill. Previously-successful skills get a score boost in the pre-filter.

### 3. Previously-Useful Context
```python
_find_previously_useful(history, query, top_k=5)
```
Skills with `useful > 0` that match query tokens are injected into the LLM prompt as context, so the composer learns from past success.

### Usage Logging
```python
# auto-logged after every compose:
log_skill_view(name, load_as, confidence)
# manual feedback after response:
log_skill_view(name, outcome_signal='useful'|'irrelevant'|'harmful')
```
**File:** `src/skill_retriever/skill_usage_logger.py` (79 LOC)

## Integration Points

| Point | What | When |
|-------|------|------|
| `pre_llm_call` hook | Inject curated skill bundle | Every LLM turn |
| Subagent dispatch | Inject per-task bundle | `delegate_task()` |
| Deepthink planning | Phase-sectioned coverage | Planning phase |
| Skills logger | JSONL usage tracking | Every compose + optional feedback |

**Subagent binding:** `src/skill_retriever/subagent_binding.py` (98 LOC) — ensures subagents receive per-task skill bundles rather than the parent session's generic bundle.

**Planning with skills:** `src/skill_retriever/planning_with_skills.py` (63 LOC) — generates phase-sectioned skill coverage for complex planning tasks.

## Known Failure Patterns

| Frequency | Pattern | Resolution |
|-----------|---------|------------|
| **50%** | Implicit continuation after compaction | Continuation handler at `__init__.py:321` |
| **~25%** | Fresh vague prompt with <10 words | Logged by deepthink gate; falls to general |
| **~15%** | Legacy composer 0-candidate (no keyword overlap) | Jev evaluates the indexed catalog directly |
| **~10%** | Network/config errors | Caught and logged; non-fatal |

## Plugin Layer

**File:** `plugin/__init__.py` (432 LOC) + `plugin/plugin.yaml` (20 LOC)

Registers a single Hermes hook: `pre_llm_call`. Fires before every LLM turn:

1. Check `SKILL_RETRIEVER_DISABLE=1` — short-circuit
2. Continuation handler — intercept compaction restarts
3. Early return on empty/short messages
4. Vague-prompt gate (opt-in logging)
5. Try Jev → legacy LLM composer → static chains → general fallback → diagnostic
6. Return `{"context": hint_block}` — prepended to user message

**Jev mode:** reads `TYPESAFE_API_KEY`. The legacy LLM fallback still auto-discovers credentials from Hermes config.

## CLI

**Files:** `src/skill_retriever/cli.py`, `src/skill_retriever/cli_compose.py`, `src/skill_retriever/__main__.py`

```bash
skill-retriever rebuild       # Rebuild flat index from skills/ directory
skill-retriever compose        # One-shot composer for a query
skill-retriever info           # Show index statistics
skill-retriever search         # Deep tree search (recursive LLM descent — for analysis, not real-time)
```

## Directory Map

```
skill-retriever/
├── plugin/
│   ├── __init__.py              ← Hermes pre_llm_call hook (432 LOC)
│   └── plugin.yaml              ← Plugin manifest
├── src/
│   └── skill_retriever/
│       ├── __init__.py          ← Public API exports
│       ├── __main__.py          ← CLI entry point
│       ├── cli.py               ← CLI commands (search, rebuild, info)
│       ├── cli_compose.py       ← CLI compose command (65 LOC)
│       ├── jev_recaller.py      ← Jev structured multi-skill recall
│       ├── compose.py           ← Jev routing + legacy LLM fallback
│       ├── config.py            ← Env-based config + LLM discovery (204 LOC)
│       ├── vague_gate.py        ← Continuation handler + deepthink gate (394 LOC)
│       ├── skill_usage_logger.py← JSONL usage tracking (79 LOC)
│       ├── subagent_binding.py  ← Per-task bundle injection (98 LOC)
│       ├── planning_with_skills.py ← Phase-sectioned planning (63 LOC)
│       ├── watchdog.py          ← Daily cron: cache rebuild + log pruning (258 LOC)
│       ├── scanner.py           ← Hermes skills scanner (for plugin)
│       ├── build_flat_index.py  ← Flat index builder
│       ├── search/
│       │   └── searcher.py      ← Core tree search engine (CLI)
│       ├── tree/
│       │   ├── builder.py       ← Tree builder
│       │   ├── prompts.py       ← LLM prompts
│       │   ├── schema.py        ← Data classes
│       │   ├── skill_scanner.py ← Corpus scanner
│       │   └── visualizer.py    ← HTML tree visualization
│       └── capability_tree/     ← Pre-built trees (YAML + HTML)
├── tests/
│   ├── test_vague_gate.py       ← Continuation + heuristics tests (265 LOC)
│   └── ...
├── data/                        ← Skill corpus (gitignored)
├── scripts/
├── ARCHITECTURE.md              ← This file
├── README.md
└── pyproject.toml
```

## Development

### Testing the continuator handler
```bash
cd plugin
python3 -c "
import sys; sys.path.insert(0, 'src')
from skill_retriever.vague_gate import is_continuation_prompt, is_vague_prompt
assert is_continuation_prompt('') and not is_continuation_prompt('deploy the webhook')
assert is_vague_prompt('make it work') and not is_vague_prompt('deploy riptide webhook on port 8788')
"
```

### Running tests
```bash
cd plugin && python3 test_vague_gate.py
```

### Enabling deepthink gate logging
```bash
export SKILL_RETRIEVER_DEEPTHINK_GATE=1
# Vague prompts logged to ~/.hermes/logs/skill_retriever_debug.log
```

### Rebuilding the flat index
```bash
skill-retriever rebuild
# Reads ~/.hermes/skills/ → writes ~/.hermes/skill-retriever-cache/flat_index.json
```
