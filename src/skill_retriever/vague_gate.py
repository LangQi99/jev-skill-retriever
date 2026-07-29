"""
Vague-prompt detection for the skill-retriever deepthink gate.

Pure heuristic functions — no LLM calls, no Hermes dependencies.
Can be imported standalone for testing.

Heuristics (any match → vague):

  1. Empty or whitespace-only → always vague.
  2. Continuation token / phrase → always vague.
  3. Word count < 10 AND **no** specific noun phrase found.
  4. Starts with a bare action verb AND word count < 15 AND *no*
     specific noun phrase (prevents flagging "fix the auth module").

The core improvement over v1 is in ``_has_specific_noun``: instead of
treating *any* word >3 chars that isn't a stopword or vague verb as a
"specific noun", it now checks against a comprehensive categorization
of tokens that carry zero actionable intent — continuation markers
("continue", "next"), fillers ("okay", "sure"), vague pronouns ("it",
"that"), and abstract nouns ("code", "stuff", "thing").
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Optional


# ── Semantic categories for word classification ─────────────────────

# Tokens that signal the user wants to continue a prior conversation
# rather than start a new task.  These carry zero actionable intent.
_CONTINUATION_TOKENS: frozenset[str] = frozenset({
    "continue", "next", "proceed", "resume", "go", "goes", "ongoing",
    "onward", "further", "again", "more", "another",
})

# Explicit continuation / acknowledgement phrases matched as whole prompts.
_CONTINUATION_PHRASES: frozenset[str] = frozenset({
    "go on", "keep going", "carry on", "move on", "as is", "as it is",
    "do that", "same again", "like before",
})

# Conversational fillers — agreement, acknowledgement, minimal responses
# that carry no actionable request.
_FILLER_TOKENS: frozenset[str] = frozenset({
    "yes", "yeah", "yep", "yup", "ok", "okay", "k", "sure", "alright",
    "right", "fine", "cool", "good", "great", "nice", "gotcha",
    "understood", "roger",
})

# Pronouns and deictic / placeholder words used in vague references.
# "Fix it", "change that", "make this" — the subject is a pronoun
# that points to something outside the prompt itself.
_VAGUE_PRONOUNS: frozenset[str] = frozenset({
    "it", "that", "this", "those", "these", "them", "they",
    "there", "here", "everywhere",
    "everyone", "everybody", "someone", "somebody",
    "anyone", "anybody", "nobody",
})

# Abstract / generic nouns that look like subjects but carry minimal
# actionable intent.  "Fix the thing", "update the code", "change stuff".
# These are the words that the v1 heuristic misclassified as "specific nouns".
_VAGUE_ABSTRACT_NOUNS: frozenset[str] = frozenset({
    "thing", "things", "stuff", "something", "anything", "nothing",
    "everything", "code", "codes", "app", "apps",
    "fix", "fixup", "tweak", "tweaks", "adjustment", "adjustments",
    "improvement", "improvements", "update", "updates", "upgrade",
    "upgrades", "change", "changes", "modification", "modifications",
    "work", "works", "job", "tasks", "task", "item", "items", "step",
    "steps", "part", "parts", "piece", "pieces", "bit", "bits",
    "issue", "issues", "problem", "problems", "bug", "bugs", "error",
    "errors", "todo", "todos", "some", "any", "few", "many",
    # Evaluative adjectives that don't name a real subject
    "better", "worse", "good", "great", "nice", "cool", "fine",
    "minor", "simple", "quick", "fast", "easy", "hard", "tricky",
})

# Standard stopwords — tokens that carry no semantic intent in isolation.
_VAGUE_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "because", "but", "and",
    "or", "if", "while", "about", "up", "it", "its", "this", "that",
    "these", "those",
    # Politeness / meta markers added for v2
    "please", "hi", "hello", "hey",
})

# Short action verbs — a prompt starting with one of these and being
# short (< 15 words) is likely a terse / underspecified command.
_VAGUE_VERBS: frozenset[str] = frozenset({
    "use", "fix", "make", "improve", "optimize", "build", "do", "run",
    "check", "test", "get", "set", "put", "add", "remove", "change",
    "update", "modify", "create", "write", "read", "find", "show",
    "tell", "help", "setup", "deploy",
})

# Multi-word phrasal verbs that the compiled regex must handle as a unit.
_MULTI_WORD_VERBS: frozenset[str] = frozenset({
    "set up", "fix up", "clean up", "clean out",
})

# The combined set of EVERYTHING that does NOT count as a specific noun.
# A word longer than 3 chars that is NOT in this set is presumed to be
# a domain-specific term (camelCase, technical jargon, file paths, etc.).
_VAGUE_NON_SPECIFIC: frozenset[str] = (
    _CONTINUATION_TOKENS
    | _FILLER_TOKENS
    | _VAGUE_PRONOUNS
    | _VAGUE_ABSTRACT_NOUNS
    | _VAGUE_STOPWORDS
    | _VAGUE_VERBS
    | _MULTI_WORD_VERBS
)

# Compiled regex for Heuristic 4 — action-verb leading match.
# Auto-generated from _VAGUE_VERBS ∪ _MULTI_WORD_VERBS so it stays in sync.
# Verbs are sorted longest-first so multi-word variants (e.g. "set up")
# are tried before their component single words ("set").
_ACTION_VERB_RE: re.Pattern[str] = re.compile(
    r"^(?:"
    + "|".join(
        re.escape(v)
        for v in sorted(
            _VAGUE_VERBS | _MULTI_WORD_VERBS,
            key=lambda x: (-len(x), x),
        )
    )
    + r")\b",
    re.IGNORECASE,
)


# ── Public API ─────────────────────────────────────────────────────


def _has_specific_noun(words: list[str]) -> bool:
    """Return True if any word looks like a specific noun / subject phrase.

    A word counts as "specific" when it is longer than 3 characters and
    is NOT in the combined ``_VAGUE_NON_SPECIFIC`` set (stopwords, vague
    verbs, continuation tokens, filler, pronouns, abstract nouns).
    """
    for w in words:
        clean = w.strip(".,!?;:'\"()[]{}").lower()
        if len(clean) > 3 and clean not in _VAGUE_NON_SPECIFIC:
            return True
    return False


def is_vague_prompt(message: str) -> bool:
    """Detect whether *message* is vague using static heuristics.

    Returns ``True`` when the prompt is likely underspecified (no clear
    intent).  No LLM calls, no disk I/O — pure set-lookup + regex logic.

    Heuristics (any match → vague):

      1. Empty or whitespace-only → *always* vague.
      2. Continuation token / phrase → *always* vague.
      3. Word count < 10 AND **no** specific noun phrase found.
      4. Starts with a bare action verb AND word count < 15 AND *no*
         specific noun phrase (prevents incorrectly flagging prompts
         that have a clear domain noun but a vague leading verb).
    """
    msg = message.strip()
    if not msg:
        return True  # Heuristic 1

    msg_lower = msg.lower()
    words = msg.split()
    wc = len(words)

    # Heuristic 2 — continuation tokens and filler-only prompts
    if wc <= 3:
        stripped = msg_lower.strip()
        if stripped in _CONTINUATION_TOKENS:
            return True
        if stripped in _CONTINUATION_PHRASES:
            return True
        if stripped in _FILLER_TOKENS:
            return True

    # Heuristic 3 — very short and no specific noun
    if wc < 10 and not _has_specific_noun(words):
        return True

    # Heuristic 4 — bare action-verb lead-in, short query, no specific noun
    if _ACTION_VERB_RE.match(msg) and wc < 15 and not _has_specific_noun(words):
        return True

    return False


# ── Candidate extraction for logs ────────────────────────────────


def _get_top_candidate_names(message: str, top_n: int = 3) -> list[str]:
    """Return the top-N candidate skill names from the composer's pre-filter.

    Calls ``_pre_filter`` which is cheap keyword matching — no LLM call.
    Returns an empty list on any failure (flat index missing, import
    error, etc.).
    """
    try:
        from skill_retriever.compose import _flat_index, _pre_filter

        skills = _flat_index()
        if not skills:
            return []

        candidates = _pre_filter(skills, message, top_k=top_n)
        names: list[str] = []
        for c in candidates:
            # Format: "- skill-name: description [tags: ...]"
            if c.startswith("- "):
                name_part = c[2:].split(":", 1)[0].strip()
                names.append(name_part)
        return names
    except Exception:  # noqa: BLE001 — intentionally broad, best-effort
        return []


# ── Logging ──────────────────────────────────────────────────────

_DEBUG_LOG_PATH = Path.home() / ".hermes" / "logs" / "skill_retriever_debug.log"


# ── Continuation / compaction-rehydration handler ────────────────

_CONTINUATION_TOKENS_V1 = frozenset({
    "continue", "next", "more", "keep", "keep going", "yes", "go",
    "go on", "ok", "okay", "sure", "yep", "yeah", "proceed", "alright",
})


def is_continuation_prompt(message: str) -> bool:
    """Return True when *message* looks like a continuation token.

    Continuation prompts ("continue", "next", "", " ") carry no
    actionable intent for skill retrieval — the model already has
    sufficient context from the transcript.  These are the dominant
    failure pattern after context compaction.
    """
    msg = (message or "").strip()
    if not msg or len(msg) < 3:
        return True  # empty / whitespace-only is always continuation
    return msg.lower() in _CONTINUATION_TOKENS_V1


def handle_continuation(
    *,
    conversation_history: list,
    user_message: str,
) -> dict | None:
    """Detect compaction-rehydrated sessions and inject skills from the summary.

    After context compaction the session restarts with a compaction summary
    as the first history message.  The model has context, but the skill
    retriever sees an empty / short / continuation prompt and early-returns
    with zero skills.  This intercepts that case by:

    1. Detecting that ``conversation_history[0]`` is a compaction summary.
    2. Extracting keywords from the summary text.
    3. Running the composer's cheap keyword pre-filter against those
       keywords to find relevant skills.
    4. Injecting the found skills as a context hint block.

    Returns ``None`` when this is NOT a compaction restart (fall through
    to the normal pipeline).  Returns ``{"context": hint_block}`` when
    continuation skills are found.
    """
    # Step 1: Only intercept continuation-like prompts
    if not is_continuation_prompt(user_message):
        return None

    # Step 2: Any compaction summary in the conversation?
    try:
        from agent.context_compressor import is_compaction_summary_message
    except ImportError:
        return _maybe_short_prompt_log(user_message)

    history = conversation_history or []
    first_msg = history[0] if history else None
    if not first_msg or not is_compaction_summary_message(first_msg):
        return _maybe_short_prompt_log(user_message)

    # Step 3: Extract readable text from the summary message
    summary_text = _extract_message_text(first_msg)
    if not summary_text or len(summary_text) < 20:
        return None

    # Step 4: Use the composer pre-filter to find relevant skills
    try:
        from skill_retriever.compose import _flat_index, _pre_filter
        import logging
        logger = logging.getLogger(__name__)

        skills = _flat_index()
        if not skills:
            return None

        candidates = _pre_filter_safe(skills, summary_text, top_k=8)
        if not candidates:
            return None

        logger.info(
            "skill-retriever: compaction restart detected — injected %d skills from summary",
            len(candidates),
        )

        hint = (
            "─── Skills relevant to your ongoing session ───\n"
            + "\n".join(candidates)
            + "\n────────────────────────────────────────────"
        )
        return {"context": hint}
    except Exception:
        logging.getLogger(__name__).debug(
            "skill-retriever continuation handler failed (non-fatal)"
        )
        return None


def _maybe_short_prompt_log(user_message: str) -> dict | None:
    """Log a very short prompt that isn't a compaction restart."""
    msg = (user_message or "").strip()
    if msg and len(msg.split()) < 5:
        log_vague_prompt(msg, ["continuation (no compaction detected)"])
    return None


def _extract_message_text(msg: dict | str) -> str:
    """Extract plain text content from a message dict or string."""
    if isinstance(msg, str):
        return msg
    if not isinstance(msg, dict):
        return ""

    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return " ".join(parts)
    return ""


def _pre_filter_safe(skills: list[dict], query: str, top_k: int = 8) -> list[str]:
    """Safely run ``_pre_filter`` with a fallback if the module is unavailable."""
    try:
        from skill_retriever.compose import _pre_filter
        return _pre_filter(skills, query, top_k=top_k) or []
    except Exception:
        return []


def log_vague_prompt(
    prompt: str,
    categories: Optional[list[str]] = None,
) -> None:
    """Log a vague-prompt detection to ``skill_retriever_debug.log``.

    Exact format::

        [YYYY-MM-DD HH:MM:SS] Vague Prompt: "<prompt>" | would_clarify: <cats> | action: fell_through
    """
    _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cats_str = ", ".join(categories[:3]) if categories else "(unknown)"
    line = (
        f"[{ts}] Vague Prompt: \"{prompt}\""
        f" | would_clarify: {cats_str}"
        f" | action: fell_through\n"
    )
    try:
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass
