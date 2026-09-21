<p align="center">
  <img src="logo.png" alt="Skill Retriever" height="130">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
</p>

# Jev Skill Retriever

> **基于 Jev 的 Hermes Agent 技能召回插件 ｜ Jev-powered skill retrieval for Hermes Agent.**

Jev evaluates the installed skill catalog with structured yes/no judgments and returns zero or more relevant skills. The original generative LLM composer remains available as a fallback.

Jev 对已安装技能并行进行结构化适用性判断，返回零个或多个相关技能；原有通用 LLM Composer 保留为故障回退。

## Why Jev?

- Multi-label by design: a request may need no skill, one skill, or several skills.
- Structured output: no generated JSON to parse or repair.
- Progressive disclosure: Choice ranks the compact catalog, then independent Noul questions verify a small shortlist.
- Safe fallback: missing credentials, timeouts, and API errors fall back to the existing LLM composer.

See the [TypeSafe introduction](https://docs.typesafe.ai/introduction) and [skill suggestion cookbook](https://docs.typesafe.ai/cookbooks/skill_suggestion).

## How It Works

```
User Query → Jev scores installed skills → threshold + top-K → inject hints
                         ↘ failure → legacy LLM composer
```

Each recalled skill gets:

| Field | Meaning |
|-------|---------|
| `name` | Skill name (call `skill_view(name)` to load) |
| `load_as` | `must` ★ / `should` ▸ / `consider` · |
| `confidence` | `high` / `medium` / `low` |
| `score` | Jev Noul relevance probability |
| `reason` | Existing skill description, used as a deterministic explanation |

### Hint Block (injected into user message)

```
[Skill Capability Chain]

These skills are curated for this query.
Call skill_view('<name>') to load each one.

  ★ cloudflare-tunnel — Tunnel deployment + credential management
  ▸ infrastructure-as-code — Terraform for Cloudflare tunnels
  · devops — Broader deployment workflows
```

## Quick Start

```bash
git clone https://github.com/LangQi99/jev-skill-retriever.git
cd jev-skill-retriever
pip install -e .
export TYPESAFE_API_KEY="your-key"
bash scripts/install.sh
```

No plugin development needed — the Hermes plugin is registered automatically.

## CLI

```bash
# Rebuild the flat index (after adding new skills)
jev-skill-retriever rebuild

# Compose a bundle for a query
jev-skill-retriever compose "deploy a cloudflare tunnel"

# Show index info
jev-skill-retriever info
```

## Integration Points

| Point | What | When |
|-------|------|------|
| `pre_llm_call` hook | Inject curated bundle | Every turn |
| Subagent dispatch | Inject per-task bundle | `delegate_task()` |
| Deepthink planning | Phase-sectioned coverage | Planning phase |
| Skills logger | JSONL usage tracking | Every compose |

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full 3-path pipeline, plugin wiring, continuation handler, vague-prompt gate, quality feedback loop, and failure patterns.

> 📐 [Architecture diagram (Excalidraw)](https://gist.github.com/ChonSong/f1f23335282e8dd6951d4dbcf18bf319) — drag the raw `.excalidraw` file onto [excalidraw.com](https://excalidraw.com) to view.

## System Requirements

- Hermes Agent v0.18+
- Python 3.10+
- ~50MB for flat index
- TypeSafe API key for Jev; an OpenAI-compatible endpoint is optional for fallback

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `SKILL_RETRIEVER_DISABLE` | — | Set `1` to disable |
| `TYPESAFE_API_KEY` | — | TypeSafe API key used by Jev |
| `TYPESAFE_ENDPOINT` | `https://api.typesafe.ai` | TypeSafe API base URL |
| `SKILL_RETRIEVER_ENGINE` | `jev` | `jev`, `auto`, or `llm` |
| `SKILL_RETRIEVER_JEV_MODEL` | `jev-1.13.0` | Pinned Jev model |
| `SKILL_RETRIEVER_JEV_THRESHOLD` | `0.35` | Minimum score for suggesting a skill |
| `SKILL_RETRIEVER_JEV_MAX_RESULTS` | `10` | Maximum suggested skills |
| `SKILL_RETRIEVER_JEV_SHORTLIST_SIZE` | `12` | Candidates verified with independent Noul questions |
| `SKILL_RETRIEVER_JEV_BATCH_SIZE` | `180` | Skills ranked per Choice request |
| `SKILL_RETRIEVER_JEV_MAX_PARALLEL` | `4` | Concurrent Jev requests |
| `SKILL_RETRIEVER_LLM_MODEL` | from Hermes config | LLM model override |
| `SKILL_RETRIEVER_LLM_API_KEY` | from Hermes config | API key |
| `SKILL_RETRIEVER_LLM_BASE_URL` | from Hermes config | Base URL |
| `SKILL_RETRIEVER_TEMPERATURE` | `0.3` | LLM temperature |

## Project Structure

```
skill-retriever/
├── plugin/                 # Hermes plugin (pre_llm_call hook)
├── src/
│   ├── skill_retriever/    # Core engine
│   │   ├── cli_compose.py  # CLI (rebuild, compose, info)
│   │   ├── jev_recaller.py # Structured multi-skill recall with Jev
│   │   ├── compose.py      # Jev routing + legacy LLM fallback
│   │   ├── config.py       # LLM discovery (borrow Hermes config)
│   │   ├── build_flat_index.py
│   │   ├── search/         # Tree search (CLI only, not for real-time)
│   │   ├── subagent_binding.py  # Per-task bundle injection
│   │   ├── planning_with_skills.py  # Phase-sectioned bundles
│   │   └── skill_usage_logger.py  # JSONL tracking
│   ├── scanner.py  # Hermes skills scanner
├── data/                   # Skill corpus (gitignored)
├── tests/                  # pytest suite
└── ARCHITECTURE.md
```

## Trust & Safety

- Jev output is validated against the exact indexed candidates.
- Results are thresholded and capped before prompt injection.
- A valid empty result is preserved as “no skill applies”; it does not trigger an unrelated fallback chain.
- API failures are non-fatal and fall back to the original composer.

## License

MIT. Community skills may have separate licenses.
