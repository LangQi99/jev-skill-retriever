# Session-Depth Quality Skill Boost

**Proposal PR:** https://github.com/ChonSong/skill-retriever/pull/NNN
**Branch:** `feat/session-depth-quality-boost`

## Motivation

[humanlayer's SlopCodeBench benchmark](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/benchmarking-opus-5-on-slop-code-bench.md) shows every tested model (Opus 5, Opus 4.8, Sonnet 5) degrades codebase quality over time — cyclomatic complexity always grew, duplication increased up to 4×, and no model reached the final checkpoint of any challenge with zero defects.

Our internal audit found that ~15-20% of turns are vague/continuation prompts where the composer returns empty and the model gets only the generic fallback (no verification skills).

## Idea

Inject a **session-depth multiplier** into the composer's existing quality scoring:

| Turns elapsed | Multiplier | Effect |
|--------------|-----------|--------|
| 1—4         | 1.0×       | Normal behaviour |
| 5—14        | 1.2×       | Verification/testing skills get a bump |
| 15+         | 1.5×       | Stronger push toward quality skills |

Only skills tagged with `has_verification` or whose capability-tree path includes `testing`, `quality`, `refactoring`, or `verification` are affected.

## What Exists

- `src/skill_retriever/compose.py` — `_score_skill()` has quality signal flags
- `skill-usage.jsonl` — tracks which skills the model actually loads
- `pre_llm_call` hook in `__init__.py` fires every turn with user message context
- The hook already has access to session state

## Touch Points

- **`compose.py`** — `_score_skill()` or the composition loop to apply the multiplier
- **`__init__.py`** — `_on_pre_llm_call()` to pass `turn_count` into the composer

## Not Implemented

This is a discussion PR. No code changes.
