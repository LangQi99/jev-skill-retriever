"""Tests for Jev-backed structured skill recall."""

from unittest.mock import patch

from skill_retriever.jev_recaller import JevRecallConfig, JevSkillRecaller


def _fake_transport(payload, _config):
    if payload["questions"].get("which"):
        names = payload["questions"]["which"]["criteria"]
        probabilities = {
            name: {"pdf": 0.72, "documents": 0.25, "weather": 0.03}[name]
            for name in names
        }
        return {
            "answers": {
                "which": {
                    "type": "choice",
                    "choice": max(probabilities, key=probabilities.get),
                    "probabilities": probabilities,
                }
            }
        }

    answers = {}
    for question_id, question in payload["questions"].items():
        instructions = question["instructions"]
        name = next(name for name in ("pdf", "documents", "weather") if f"'{name}'" in instructions)
        score = {
            "pdf": 0.91,
            "documents": 0.67,
            "weather": 0.08,
        }[name]
        answers[question_id] = {"type": "noul", "noul": score}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {}}


def test_jev_recaller_returns_ranked_multi_skill_bundle():
    config = JevRecallConfig(
        api_key="test", threshold=0.35, shortlist_size=2, batch_size=180
    )
    recaller = JevSkillRecaller(config, transport=_fake_transport)
    skills = [
        {"name": "weather", "description": "Forecast the weather", "tags": []},
        {"name": "documents", "description": "Create Word documents", "tags": []},
        {"name": "pdf", "description": "Read and edit PDF files", "tags": []},
    ]

    result = recaller.recall("Turn this report into a Word document and PDF", skills)

    assert [item["name"] for item in result] == ["pdf", "documents"]
    assert result[0]["load_as"] == "must"
    assert result[1]["load_as"] == "should"
    assert result[0]["score"] == 0.91


def test_jev_recaller_can_reject_every_skill():
    def irrelevant_transport(payload, _config):
        return {
            "answers": {
                question_id: {"type": "noul", "noul": 0.05}
                for question_id in payload["questions"]
            }
        }

    recaller = JevSkillRecaller(
        JevRecallConfig(api_key="test", threshold=0.35),
        transport=irrelevant_transport,
    )
    result = recaller.recall(
        "Explain what a monad is",
        [{"name": "weather", "description": "Forecast weather"}],
    )

    assert result == []


def test_jev_payload_uses_official_choice_and_noul_shapes():
    payloads = []

    def recording_transport(payload, config):
        payloads.append(payload)
        return _fake_transport(payload, config)

    recaller = JevSkillRecaller(
        JevRecallConfig(api_key="test", shortlist_size=2),
        transport=recording_transport,
    )
    recaller.recall(
        "Create a PDF report",
        [
            {"name": "pdf", "description": "Read and edit PDF files"},
            {"name": "documents", "description": "Create Word documents"},
            {"name": "weather", "description": "Forecast weather"},
        ],
    )

    choice = next(payload for payload in payloads if "which" in payload["questions"])
    noul = next(payload for payload in payloads if "which" not in payload["questions"])
    assert isinstance(choice["questions"]["which"]["instructions"], str)
    assert all(question["type"] == "noul" for question in noul["questions"].values())
    assert all(isinstance(question["instructions"], str) for question in noul["questions"].values())


def test_compose_uses_jev_when_key_is_configured():
    bundle = [{"name": "pdf", "load_as": "must", "confidence": "high"}]
    with (
        patch("skill_retriever.compose._flat_index", return_value=[{"name": "pdf"}]),
        patch("skill_retriever.compose.JevRecallConfig.from_env") as config_from_env,
        patch("skill_retriever.compose.JevSkillRecaller") as recaller_cls,
        patch("skill_retriever.compose._log_bundle"),
    ):
        config_from_env.return_value = JevRecallConfig(api_key="test")
        recaller_cls.return_value.recall.return_value = bundle

        from skill_retriever.compose import compose_skills

        assert compose_skills("read this PDF") == bundle
        recaller_cls.return_value.recall.assert_called_once()


def test_jev_config_reads_typesafe_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", "https://example.test")
    monkeypatch.setenv("SKILL_RETRIEVER_JEV_THRESHOLD", "0.42")

    config = JevRecallConfig.from_env()

    assert config.api_key == "secret"
    assert config.endpoint == "https://example.test/v1/systemone"
    assert config.threshold == 0.42
