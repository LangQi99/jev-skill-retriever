"""Jev-backed skill recall for Hermes Agent.

A Choice question cheaply ranks the catalog, then independent Noul questions
score a small shortlist. The second stage makes recall multi-label: zero, one,
or several skills can pass the configured relevance threshold without asking a
generative model to produce or parse a JSON bundle.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import math
import os
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class JevRecallError(RuntimeError):
    """Raised when Jev cannot produce a valid recall result."""


@dataclass(frozen=True)
class JevRecallConfig:
    api_key: str
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-1.13.0"
    threshold: float = 0.35
    must_threshold: float = 0.75
    should_threshold: float = 0.55
    max_results: int = 10
    shortlist_size: int = 12
    batch_size: int = 180
    max_parallel: int = 4
    timeout: float = 8.0
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> "JevRecallConfig":
        endpoint = os.environ.get(
            "TYPESAFE_ENDPOINT", "https://api.typesafe.ai"
        ).rstrip("/")
        if not endpoint.endswith("/v1/systemone"):
            endpoint += "/v1/systemone"
        return cls(
            api_key=os.environ.get("TYPESAFE_API_KEY", "").strip(),
            endpoint=endpoint,
            model=os.environ.get("SKILL_RETRIEVER_JEV_MODEL", "jev-1.13.0"),
            threshold=float(os.environ.get("SKILL_RETRIEVER_JEV_THRESHOLD", "0.35")),
            must_threshold=float(
                os.environ.get("SKILL_RETRIEVER_JEV_MUST_THRESHOLD", "0.75")
            ),
            should_threshold=float(
                os.environ.get("SKILL_RETRIEVER_JEV_SHOULD_THRESHOLD", "0.55")
            ),
            max_results=int(os.environ.get("SKILL_RETRIEVER_JEV_MAX_RESULTS", "10")),
            shortlist_size=int(
                os.environ.get("SKILL_RETRIEVER_JEV_SHORTLIST_SIZE", "12")
            ),
            batch_size=int(os.environ.get("SKILL_RETRIEVER_JEV_BATCH_SIZE", "180")),
            max_parallel=int(os.environ.get("SKILL_RETRIEVER_JEV_MAX_PARALLEL", "4")),
            timeout=float(os.environ.get("SKILL_RETRIEVER_JEV_TIMEOUT", "8")),
            max_retries=int(os.environ.get("SKILL_RETRIEVER_JEV_MAX_RETRIES", "1")),
        )


Transport = Callable[[dict, JevRecallConfig], dict]


def _http_transport(payload: dict, config: JevRecallConfig) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        config.endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "jev-skill-retriever/0.4",
        },
        method="POST",
    )

    for attempt in range(config.max_retries + 1):
        try:
            with urlopen(request, timeout=config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt >= config.max_retries:
                detail = error.read().decode("utf-8", errors="replace")[:300]
                raise JevRecallError(
                    f"TypeSafe API returned HTTP {error.code}: {detail}"
                ) from error
            retry_after = error.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 2.0) if retry_after else 0.25 * (2**attempt)
            except ValueError:
                delay = 0.25 * (2**attempt)
            time.sleep(delay)
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            if attempt >= config.max_retries:
                raise JevRecallError(f"TypeSafe API request failed: {error}") from error
            time.sleep(0.25 * (2**attempt))

    raise JevRecallError("TypeSafe API request failed")


class JevSkillRecaller:
    """Recall applicable skills using Jev's structured Noul primitive."""

    def __init__(
        self,
        config: JevRecallConfig,
        transport: Transport = _http_transport,
    ) -> None:
        if not config.api_key:
            raise JevRecallError("TYPESAFE_API_KEY is not configured")
        if not 0 <= config.threshold <= 1:
            raise ValueError("Jev threshold must be between 0 and 1")
        if (
            config.shortlist_size < 1
            or config.batch_size < 1
            or config.max_parallel < 1
        ):
            raise ValueError("Jev shortlist size, batch size, and parallelism must be positive")
        self.config = config
        self.transport = transport

    def recall(
        self,
        query: str,
        skills: list[dict],
        recent_context: str = "",
    ) -> list[dict]:
        candidates = [skill for skill in skills if skill.get("name")]
        if not candidates:
            return []

        batches = [
            candidates[start : start + self.config.batch_size]
            for start in range(0, len(candidates), self.config.batch_size)
        ]
        if len(candidates) <= self.config.shortlist_size:
            shortlist = candidates
        else:
            workers = min(self.config.max_parallel, len(batches))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                batch_rankings = list(
                    pool.map(
                        lambda batch: self._rank_batch(query, recent_context, batch),
                        batches,
                    )
                )
            shortlist = self._interleave_rankings(batch_rankings)

        scored = self._score_shortlist(query, recent_context, shortlist)
        scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
        return [
            self._bundle_item(skill, score)
            for score, skill in scored
            if score >= self.config.threshold
        ][: self.config.max_results]

    def _rank_batch(
        self,
        query: str,
        recent_context: str,
        skills: list[dict],
    ) -> list[dict]:
        by_name = {str(skill["name"]): skill for skill in skills}
        criteria = {
            name: self._skill_summary(skill)
            for name, skill in by_name.items()
        }

        payload = {
            "state": {
                "request": query,
                "recent_context": recent_context[-2000:],
            },
            "model": self.config.model,
            "questions": {
                "which": {
                    "type": "choice",
                    "instructions": (
                        "Which skill is the best one to load for the user's latest request? "
                        "Judge the requested action and workflow, not superficial word overlap."
                    ),
                    "criteria": criteria,
                }
            },
        }
        response = self.transport(payload, self.config)
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise JevRecallError("TypeSafe API response is missing answers")
        answer = answers.get("which")
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if not isinstance(probabilities, dict):
            raise JevRecallError("TypeSafe API response is missing Choice probabilities")

        ranked = []
        for name, probability in probabilities.items():
            if name not in by_name or not isinstance(probability, (int, float)):
                continue
            ranked.append((float(probability), by_name[name]))
        if not ranked:
            raise JevRecallError("TypeSafe API returned no valid Choice candidates")
        ranked.sort(key=lambda item: (-item[0], item[1].get("name", "")))
        return [skill for _, skill in ranked]

    def _interleave_rankings(self, rankings: list[list[dict]]) -> list[dict]:
        """Take candidates round-robin so every catalog batch is represented."""
        shortlist = []
        depth = 0
        while len(shortlist) < self.config.shortlist_size:
            added = False
            for ranking in rankings:
                if depth < len(ranking):
                    shortlist.append(ranking[depth])
                    added = True
                    if len(shortlist) == self.config.shortlist_size:
                        break
            if not added:
                break
            depth += 1
        return shortlist

    def _score_shortlist(
        self,
        query: str,
        recent_context: str,
        skills: list[dict],
    ) -> list[tuple[float, dict]]:
        questions = {
            f"fits::{index}": {
                "type": "noul",
                "instructions": (
                    f"Does the skill '{skill['name']}' materially help fulfill the user's "
                    "specific request? Judge what the skill actually does, not just its name. "
                    "Answer no for a neighboring product, a different operation, or a skill "
                    f"that is unnecessary. Skill: {self._skill_summary(skill)}"
                ),
            }
            for index, skill in enumerate(skills)
        }
        payload = {
            "state": {
                "request": query,
                "recent_context": recent_context[-2000:],
            },
            "model": self.config.model,
            "questions": questions,
        }
        response = self.transport(payload, self.config)
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise JevRecallError("TypeSafe API response is missing answers")

        results = []
        for index, skill in enumerate(skills):
            answer = answers.get(f"fits::{index}")
            if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
                raise JevRecallError(
                    f"TypeSafe API response is missing fits::{index} Noul answer"
                )
            score = float(answer["noul"])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise JevRecallError(f"TypeSafe API returned invalid Noul score: {score}")
            results.append((score, skill))
        return results

    @staticmethod
    def _skill_summary(skill: dict) -> str:
        description = str(skill.get("description", ""))[:500]
        tags = ", ".join(str(tag) for tag in skill.get("tags", [])[:8])
        return f"{description} Tags: {tags}" if tags else description

    def _bundle_item(self, skill: dict, score: float) -> dict:
        if score >= self.config.must_threshold:
            load_as = "must"
        elif score >= self.config.should_threshold:
            load_as = "should"
        else:
            load_as = "consider"

        if score >= 0.80:
            confidence = "high"
        elif score >= 0.55:
            confidence = "medium"
        else:
            confidence = "low"

        description = str(skill.get("description", "")).strip()
        return {
            "name": skill["name"],
            "load_as": load_as,
            "reason": description[:180] or "Jev found this skill relevant to the request.",
            "confidence": confidence,
            "score": round(score, 4),
        }
