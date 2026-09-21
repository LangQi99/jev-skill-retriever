"""jev-skill-retriever — Jev-powered skill retrieval for Hermes Agent.

Wires one behaviour via the Hermes plugin system:

    pre_llm_call hook — on each user query, ranks the installed skill catalog
    with Jev, verifies a shortlist, and injects the relevant skill hints into
    the user message as natural-language instructions.

Modes:
    Jev (default): Uses TYPESAFE_API_KEY for structured recall.
    LLM fallback: Borrows Hermes' active OpenAI-compatible credentials.

Quirks:
    - pre_llm_call context is PREPENDED to the user message, not the system prompt.
    - Cannot intercept skill_view or available_skills — we inject instructions
      telling the LLM which skills to manually load via skill_view(name).
    - The LLM has final authority to ignore hints.

Env:
    SKILL_RETRIEVER_DISABLE=1        — disable entirely
    TYPESAFE_API_KEY                 — TypeSafe API key used by Jev
    SKILL_RETRIEVER_ENGINE           — jev (default), auto, or llm
    SKILL_RETRIEVER_JEV_MODEL        — pinned Jev model (default: jev-1.13.0)
    SKILL_RETRIEVER_CACHE_DIR        — override cache dir (default: ~/.hermes/skill-retriever-cache)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DISABLE_ENV = "SKILL_RETRIEVER_DISABLE"

# Lazy-loaded singletons
_searcher = None
_scanner = None


def _get_scanner():
    """Lazy-load the skill scanner function."""
    global _scanner
    if _scanner is None:
        from skill_retriever.scanner import scan_hermes_skills
        _scanner = scan_hermes_skills
    return _scanner


def _get_searcher():
    """Lazy-load the Searcher singleton from skill-retriever.

    On first call, initializes the Searcher with the capability tree
    and loads skill metadata. The tree is built once and cached to disk.

    LLM credentials are read from the environment (borrow-mode):
        OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
    or the skill-retriever-specific env vars:
        SKILL_RETRIEVER_LLM_API_KEY, SKILL_RETRIEVER_LLM_BASE_URL,
        SKILL_RETRIEVER_LLM_MODEL
    """
    global _searcher
    if _searcher is not None:
        return _searcher

    from skill_retriever.search.searcher import Searcher
    from skill_retriever.config import (
        CAPABILITY_TREE_PATH,
        LLM_MODEL as _cfg_model,
        LLM_API_KEY as _cfg_key,
        LLM_BASE_URL as _cfg_url,
    )

    # Use the pre-built tree from skill_retriever/data or fall back to
    # the bundled tree in the retriever package.
    tree_path = os.environ.get(
        "SKILL_RETRIEVER_TREE_PATH",
        str(CAPABILITY_TREE_PATH),
    )

    # Read LLM config from config.py (which auto-discovers Hermes provider)
    # with env var override on top.
    model = os.environ.get("SKILL_RETRIEVER_LLM_MODEL", _cfg_model)
    api_key = os.environ.get("SKILL_RETRIEVER_LLM_API_KEY", _cfg_key)
    base_url = os.environ.get("SKILL_RETRIEVER_LLM_BASE_URL", _cfg_url)

    logger.info(
        "skill-retriever: initializing searcher (model=%s, tree=%s)",
        model, tree_path,
    )

    try:
        _searcher = Searcher(
            tree_path=tree_path,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        # If using a custom base_url and the model lacks a provider prefix,
        # wrap it with "openai/" so litellm routes through the OpenAI-compatible
        # adapter instead of trying to match a built-in provider.
        if _searcher and _searcher.base_url and _searcher.model and "/" not in _searcher.model:
            _searcher.model = f"openai/{_searcher.model}"
    except Exception as e:
        logger.warning(
            "skill-retriever: failed to initialize searcher: %s. "
            "Skill hints will be disabled.",
            e,
        )
        _searcher = None

    return _searcher


# ── Composer ────────────────────────────────────────────────────────────

def _get_composer_flat_index_path():
    """Return the path to the flat index cache."""
    from pathlib import Path
    return Path.home() / ".hermes" / "skill-retriever-cache" / "flat_index.json"


# Hook lives below after _build_hint_block etc.


# ── Intent classification ──────────────────────────────────────────────

# Intent detection: keyword pattern → intent label.
# Intents trigger capability-chain bundles that the retriever injects
# regardless of individual embedding scores. This compensates for the
# fact that "subagent-driven-development" doesn't semantically match
# "build a web app" even though it's the correct next step.
_INTENT_PATTERNS: dict[str, list[str]] = {
    "large_build": [
        "build a", "create a", "from scratch", "fully featured",
        "complete app", "full application", "new project", "new repo",
        "scaffold", "boilerplate", "greenfield", "fullstack",
        "monorepo", "many features", "multi-page", "complex app",
    ],
    "code_review": [
        "review", "audit", "inspect", "evaluate", "assess",
        "code quality", "security audit", "check for", "find bugs",
        "qa", "quality assurance",
    ],
    "large_refactor": [
        "refactor", "rewrite", "restructure", "clean up", "reorganize",
        "split up", "decouple", "extract module", "migrate",
        "upgrade all", "across many files",
    ],
    "deploy": [
        "deploy", "ship", "release", "publish", "launch",
        "go live", "production", "docker", "containerize",
    ],
}

# Capability chain bundles: intent → [(skill_name, loading_priority, why)]
# Priority: 1=must load, 2=should load, 3=consider
_CAPABILITY_CHAINS: dict[str, list[tuple[str, int, str]]] = {
    "large_build": [
        ("writing-plans", 1, "architecture and phased planning before any code"),
        ("subagent-driven-development", 1, "parallel delegation for multi-file builds"),
        ("codebase-ingestion", 2, "index the repo for semantic code search"),
        ("code-quality-audit", 2, "language-agnostic quality checks before commits"),
        ("test-driven-development", 2, "TDD: tests first, then code, then refactor"),
        ("search-first", 3, "research existing tools before building custom"),
        ("web-app-factory", 3, "repeatable web app build workflow"),
        ("docker-patterns", 3, "containerize if deploying"),
        ("deployment-patterns", 3, "CI/CD pipelines, health checks, rollback"),
        ("systematic-debugging", 3, "root cause analysis when builds fail"),
        ("deep-think", 4, "loop-based structured reasoning for hard problems"),
        ("spike", 4, "throwaway experiments to validate ideas before building"),
        ("git-workflow", 4, "branch strategy, commits, PR conventions"),
        ("benchmark", 4, "measure performance baselines before and after changes"),
        ("context-engineering", 4, "optimize context window usage for large builds"),
    ],
    "code_review": [
        ("code-quality-audit", 1, "structured code quality and security checks"),
        ("requesting-code-review", 1, "pre-commit security scan and quality gates"),
        ("simplify-code", 2, "parallel 3-agent cleanup of recent changes"),
        ("fec-e2e-testing", 2, "real-browser E2E tests with Playwright"),
        ("playwright-best-practices", 3, "battle-tested testing patterns"),
        ("dogfood", 3, "exploratory QA and bug hunting"),
        ("test-master", 3, "comprehensive test strategy and coverage analysis"),
        ("browser-automation", 3, "automate browser interactions for visual QA"),
        ("systematic-debugging", 4, "4-phase root cause for any bugs found"),
        ("deep-think", 4, "loop-based reasoning on complex code decisions"),
        ("code-refactoring", 4, "refactoring patterns and techniques"),
        ("context-engineering", 4, "prompt optimization for code analysis"),
        ("benchmark", 4, "measure performance impact of code changes"),
        ("langsmith-observability", 5, "trace LLM calls during code review"),
        ("assistant-avatar-extension", 5, "build browser extension for code review"),
    ],
    "large_refactor": [
        ("codebase-exploration", 1, "semantic search to understand the codebase"),
        ("simplify-code", 1, "parallel review for reuse, quality, efficiency"),
        ("subagent-driven-development", 2, "parallel subagents for independent files"),
        ("test-driven-development", 2, "tests before and after refactoring"),
        ("systematic-debugging", 3, "4-phase root cause if the refactor exposes bugs"),
        ("code-refactoring", 3, "refactoring patterns and techniques"),
        ("test-master", 3, "verify test coverage during and after refactor"),
        ("deep-think", 4, "loop-based structured reasoning for complex changes"),
        ("context-engineering", 4, "optimize context for large codebase refactors"),
        ("benchmark", 4, "measure before/after performance of refactored code"),
        ("spike", 4, "throwaway experiments to test refactor approaches"),
        ("search-first", 4, "research existing patterns before refactoring"),
        ("git-workflow", 4, "commit strategy for large refactors"),
        ("code-quality-audit", 4, "quality gate before and after refactor"),
        ("langgraph", 5, "graph-based workflows for complex refactor orchestration"),
    ],
    "deploy": [
        ("deployment-patterns", 1, "CI/CD, Docker, health checks, rollback"),
        ("cloudflare-tunnel", 2, "expose local services via Cloudflare"),
        ("code-quality-audit", 2, "pre-deploy quality gate"),
        ("canary-watch", 3, "post-deploy monitoring for regressions"),
        ("docker-patterns", 3, "container best practices for production"),
        ("systematic-debugging", 3, "diagnose deployment failures"),
        ("benchmark", 4, "measure performance after deploy"),
        ("git-workflow", 4, "tagging, releases, deployment branches"),
        ("web-quality-audit", 4, "Core Web Vitals and accessibility checks"),
        ("ui-qa-pipeline", 4, "visual regression testing post-deploy"),
        ("cloudflare-tunnel-persistence", 5, "persistent tunnels with monitoring"),
        ("nextjs-deployment-patches", 5, "fix Next.js deployment issues"),
        ("vercel-deployment", 5, "Vercel-specific deployment patterns"),
        ("devops", 5, "infrastructure and ops best practices"),
        ("inngest", 5, "serverless job queues for background deploy tasks"),
    ],
    "general": [
        ("skill-retrieval-system", 1, "understand how skill retrieval works and why it matters"),
        ("skill-creator-ms", 2, "create, update, and curate agent skills"),
        ("skills-list", 2, "discover all available skills"),
        ("skill-curation", 2, "curate and maintain the skill library"),
        ("skill-router", 2, "route tasks to the right skill"),
        ("planning-and-task-breakdown", 4, "break complex work into ordered steps"),
    ],
}

# Behavioral nudge snippets: one-line imperatives per intent
_BEHAVIORAL_NUDGES: dict[str, str] = {
    "large_build": (
        "For multi-file builds: plan the architecture first, "
        "delegate parallel workstreams via delegate_task, "
        "then run a quality gate before committing."
    ),
    "code_review": (
        "For reviews: load the quality-audit skill, run its checks, "
        "then use browser or e2e tests to verify, not just manual inspection."
    ),
    "large_refactor": (
        "For refactors: index the codebase first for semantic search, "
        "run tests before touching code, delegate independent files in parallel, "
        "run the full test suite after each batch."
    ),
    "deploy": (
        "For deploys: verify all tests pass, run pre-deploy quality checks, "
        "ensure health-check endpoints exist, monitor for regressions after shipping."
    ),
    "general": (
        "For general tasks: check skill-retrieval-system first to understand "
        "how retrieval works, then use skills-list to discover relevant skills."
    ),
}


def _detect_intent(message: str) -> str | None:
    """Classify user query into an intent label via keyword pattern matching.

    Returns None when no intent is detected (skip injection).
    Multiple intents: first match wins, ordered by specificity.
    """
    msg_lower = message.lower()
    # Code review before general build — "review this code and build" should catch review
    for intent in ("code_review", "large_refactor", "deploy"):
        for pattern in _INTENT_PATTERNS[intent]:
            if pattern in msg_lower:
                return intent
    # "large_build" requires at least ONE build keyword AND either scope or scale signal
    build_hit = any(p in msg_lower for p in _INTENT_PATTERNS["large_build"])
    has_scope = len(message.split()) > 5  # short queries aren't "large" builds
    if build_hit and has_scope:
        return "large_build"
    return None


def _build_hint_block(
    skills: list[tuple[str, int, str]], intent: str | None
) -> str:
    """Format a capability chain into a natural-language hint block.

    Includes a behavioral nudge when intent is recognized.
    """
    parts = [
        "[Skill Capability Chain]",
        "",
        "These skills form a complete workflow for this type of task.",
        "Load the priority-1 skills first, then others as needed.",
        "Call skill_view('<name>') to load each one.",
        "",
    ]
    for name, pri, why in skills:
        marker = {1: "★", 2: "▸", 3: "·"}[pri]
        parts.append(f"  {marker} **{name}** — {why}")
    parts.append("")

    if intent and intent in _BEHAVIORAL_NUDGES:
        parts.append(f"[Workflow note] {_BEHAVIORAL_NUDGES[intent]}")
        parts.append("")

    return "\n".join(parts)


# ── Hook ────────────────────────────────────────────────────────────────

def _on_pre_llm_call(*, user_message: str = "", **_kwargs) -> dict | None:
    """Run skill retrieval and inject hints into the user message.

    Primary pathway: Jev Choice ranking + Noul shortlist verification.
    Fallback: Legacy LLM composer, then static capability chains.
    """
    if os.environ.get(_DISABLE_ENV, "").lower() in ("1", "true", "yes"):
        return None

    # ── Continuation / compaction-rehydration handler ────────────────
    # Must fire BEFORE the empty/short-message early return because
    # continuation prompts ("continue", "next", "") are the dominant
    # failure pattern after context compaction (50% of skill-retriever
    # misses according to the 30-day audit; see Issue #71058 / PR #71077
    # for the upstream compaction seam).
    try:
        from skill_retriever.vague_gate import handle_continuation

        _continuation_result = handle_continuation(
            conversation_history=_kwargs.get("conversation_history") or [],
            user_message=user_message or "",
        )
        if _continuation_result is not None:
            return _continuation_result
    except Exception:
        logger.debug("skill-retriever: continuation handler unavailable (non-fatal)")

    if not user_message or not user_message.strip():
        return None
    if len(user_message.strip()) < 10:
        return None

    # ── Vague-prompt deepthink gate (opt-in) ────────────────────────
    if os.environ.get("SKILL_RETRIEVER_DEEPTHINK_GATE", "0").lower() in ("1", "true", "yes"):
        _gate_vague = False
        try:
            from skill_retriever.vague_gate import (
                is_vague_prompt,
                log_vague_prompt,
                _get_top_candidate_names,
            )

            # Static heuristics (1 & 2)
            _gate_vague = is_vague_prompt(user_message)

            # Heuristic 3: composer pre_filter returns 0 candidates
            if not _gate_vague:
                try:
                    from skill_retriever.compose import _flat_index, _pre_filter
                    skills = _flat_index()
                    if skills:
                        _gate_vague = not _pre_filter(skills, user_message, top_k=1)
                except Exception:
                    pass  # non-fatal — fall through

            if _gate_vague:
                cats = _get_top_candidate_names(user_message)
                log_vague_prompt(user_message, cats)
                logger.info(
                    "skill-retriever: vague-prompt gate triggered – logged to skill_retriever_debug.log"
                )
        except Exception:
            logger.debug("skill-retriever: vague-prompt gate unavailable (non-fatal)")

    try:
        # ── Path 1: Jev recall, with legacy LLM fallback ───────────────
        try:
            from skill_retriever.compose import compose_skills, bundle_to_hint_block
            bundle = compose_skills(user_message)
            if bundle is not None:
                hint_block = bundle_to_hint_block(bundle)
                if not hint_block:
                    hint_block = (
                        "[Skill Recall]\n\n"
                        "Skill recall evaluated the installed catalog and found no skill "
                        "that clearly applies to this request. Use your own judgment if "
                        "the request explicitly names a skill.\n"
                    )
                logger.info(
                    "jev-skill-retriever: recall returned %d skills",
                    len(bundle),
                )
                return {"context": hint_block}
        except Exception as e:
            logger.debug("skill-retriever composer failed: %s", e)

        # ── Path 2: Static capability chains (fallback) ────────────────
        intent = _detect_intent(user_message)
        chain_skills = _CAPABILITY_CHAINS.get(intent, []) if intent else []

        if chain_skills:
            hint_block = _build_hint_block(chain_skills, intent)
            logger.info(
                "skill-retriever: fallback intent=%s chain_skills=%d",
                intent, len(chain_skills),
            )
            return {"context": hint_block}

        # ── Path 3: General fallback (always inject something) ────────
        # Even on composer failure or non-matched intent, inject general
        # chain so the agent knows retrieval is alive but curator is down.
        general_skills = _CAPABILITY_CHAINS.get("general", [])
        if general_skills:
            hint_block = _build_hint_block(general_skills, "general")
            logger.warning(
                "skill-retriever: composer failed and no specific intent — injecting general fallback"
            )
            return {"context": hint_block}

        # ── Last resort: diagnostic (should never happen) ──────────────
        logger.warning("skill-retriever: all paths exhausted — injecting diagnostic")
        return {
            "context": (
                "[Skill Retriever] ⚠️ No curated bundle available.\n"
                "Composer and capability chains both failed.\n"
                "Run `skills_list` to find skills manually."
            )
        }

    except Exception as e:
        logger.debug("skill-retriever hook failed (non-fatal): %s", e)
        return None


def register(ctx) -> None:
    """Register the pre_llm_call hook with the Hermes plugin system."""
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    logger.info("skill-retriever plugin registered (pre_llm_call hook)")
