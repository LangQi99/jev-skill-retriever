#!/usr/bin/env python3
"""Tests for the vague-prompt gate heuristic (v2 — smarter noun detection).

Run:  python test_vague_gate.py
  or:  python -m pytest test_vague_gate.py
"""

from __future__ import annotations

import sys
import os

# The heuristic module lives under src/skill_retriever/
_SRC = os.path.join(os.path.dirname(__file__), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from skill_retriever.vague_gate import is_vague_prompt, _has_specific_noun


# ===================================================================
# HELPER
# ===================================================================

_tests_run = 0
_tests_passed = 0


def _check(label: str, got: bool, expected: bool) -> None:
    global _tests_run, _tests_passed
    _tests_run += 1
    if got == expected:
        _tests_passed += 1
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  (expected={expected}, got={got})")


# ===================================================================
# VAGUE PROMPTS — continuation and filler edge cases
# ===================================================================

print("=== Vague prompts — continuation / filler (v2 target cases) ===")

# These are the specific failure modes the user reported.
_check('"continue" → vague', is_vague_prompt("continue"), True)
_check('"next" → vague', is_vague_prompt("next"), True)
_check('"proceed" → vague', is_vague_prompt("proceed"), True)
_check('"keep going" → vague', is_vague_prompt("keep going"), True)
_check('"go on" → vague', is_vague_prompt("go on"), True)
_check('"do it" → vague', is_vague_prompt("do it"), True)
_check('"do that" → vague', is_vague_prompt("do that"), True)
_check('"next step" → vague', is_vague_prompt("next step"), True)

# Conversational fillers — zero actionable intent
_check('"ok" → vague', is_vague_prompt("ok"), True)
_check('"okay" → vague', is_vague_prompt("okay"), True)
_check('"sure" → vague', is_vague_prompt("sure"), True)

print()

print("=== Vague prompts — classic short-command cases ===")

# v1-origin cases that should still be vague
_check(
    'make it work (v1 case)',
    is_vague_prompt("make it work"),
    True,
)
_check(
    'use it as it is (v1 case)',
    is_vague_prompt("use it as it is"),
    True,
)
_check(
    'fix this (v1 case)',
    is_vague_prompt("fix this"),
    True,
)
_check(
    '"use" alone (v1 case)',
    is_vague_prompt("use"),
    True,
)

# New vague cases added for v2 robustness
_check(
    'empty string (v2 — empty is always vague)',
    is_vague_prompt(""),
    True,
)
_check(
    'whitespace only (v2)',
    is_vague_prompt("   "),
    True,
)
_check(
    '"hello" (v2 — greeting, no intent)',
    is_vague_prompt("hello"),
    True,
)
_check(
    '"fix the code" (v2 — "code" is an abstract noun)',
    is_vague_prompt("fix the code"),
    True,
)
_check(
    '"improve this" (v2)',
    is_vague_prompt("improve this"),
    True,
)
_check(
    '"make it better" (v2)',
    is_vague_prompt("make it better"),
    True,
)
_check(
    '"change the thing" (v2 — "thing" is abstract)',
    is_vague_prompt("change the thing"),
    True,
)
_check(
    '"run the tests again" (v2 — "tests" is a specific domain noun)',
    is_vague_prompt("run the tests again"),
    False,
)

# ===================================================================
# CLEAR PROMPTS — must NOT be flagged as vague
# ===================================================================

print()
print("=== Clear prompts (must NOT be detected as vague) ===")

# v1-origin cases that should remain NOT vague
_check(
    'deploy the riptide webhook to production using github actions (v1)',
    is_vague_prompt("deploy the riptide webhook to production using github actions"),
    False,
)
_check(
    'refactor the authentication module to use JWT tokens (v1)',
    is_vague_prompt("refactor the authentication module to use JWT tokens"),
    False,
)
_check(
    'set up a CI/CD pipeline for the monorepo using GitHub Actions (v1)',
    is_vague_prompt("set up a CI/CD pipeline for the monorepo using GitHub Actions"),
    False,
)
_check(
    'write unit tests for the UserService class (v1)',
    is_vague_prompt("write unit tests for the UserService class"),
    False,
)

# New specific cases added for v2
_check(
    'fix the authentication bug in the login endpoint (v2)',
    is_vague_prompt("fix the authentication bug in the login endpoint"),
    False,
)
_check(
    'make the __init__.py import lazy (v2 — has specific noun "__init__.py")',
    is_vague_prompt("make the __init__.py import lazy"),
    False,
)
_check(
    'deploy the database migration to staging (v2)',
    is_vague_prompt("deploy the database migration to staging"),
    False,
)
_check(
    'update the stripe integration for the new api version (v2)',
    is_vague_prompt("update the stripe integration for the new api version"),
    False,
)

# ===================================================================
# CONTINUATION / COMPACTION-REHYDRATION DETECTION
# ===================================================================

print()
print("=== Continuation detection (is_continuation_prompt) ===")

from skill_retriever.vague_gate import is_continuation_prompt

# Empty / whitespace → continuation
_check("empty string → continuation", is_continuation_prompt(""), True)
_check("whitespace only → continuation", is_continuation_prompt("   "), True)

# Known tokens → continuation
_check('"continue" → continuation', is_continuation_prompt("continue"), True)
_check('"next" → continuation', is_continuation_prompt("next"), True)
_check('"ok" → continuation', is_continuation_prompt("ok"), True)
_check('"go on" → continuation', is_continuation_prompt("go on"), True)
_check('"proceed" → continuation', is_continuation_prompt("proceed"), True)

# Normal prompts → NOT continuation
_check(
    '"fix the auth bug" → NOT continuation',
    is_continuation_prompt("fix the auth bug"),
    False,
)
_check(
    '"deploy to production using github actions" → NOT continuation',
    is_continuation_prompt("deploy to production using github actions"),
    False,
)

# ===================================================================
# SPECIFIC NOUN DETECTION  (unit-level)
# ===================================================================

print()
print("=== Specific noun detection (_has_specific_noun) ===")

_check(
    "empty list → False",
    _has_specific_noun([]),
    False,
)
_check(
    "continuation token 'continue' → False",
    _has_specific_noun(["continue"]),
    False,
)
_check(
    "abstract noun 'code' → False",
    _has_specific_noun(["code"]),
    False,
)
_check(
    "vague pronoun 'they' → False",
    _has_specific_noun(["they"]),
    False,
)
_check(
    "domain term 'authentication' → True",
    _has_specific_noun(["authentication"]),
    True,
)
_check(
    "technical term 'grafana' → True",
    _has_specific_noun(["grafana"]),
    True,
)
_check(
    "mixed ['continue', 'deploy', 'server'] → True",
    _has_specific_noun(["continue", "deploy", "server"]),
    True,
)

# ===================================================================
# SUMMARY
# ===================================================================

print(f"\n{'='*50}")
print(f"Results:  {_tests_passed}/{_tests_run} passed")
if _tests_passed == _tests_run:
    print("All tests passed  ✓")
    sys.exit(0)
else:
    print(f"FAILURES: {_tests_run - _tests_passed}")
    sys.exit(1)
