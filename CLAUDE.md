# Skill Retriever

Composer-based dynamic skill curation for Hermes Agent. Walks a YAML capability tree once to build a flat index (~50KB, ~400 skills), then uses a single LLM call to curate a query-specific bundle of 3-20 skills.

- **Plugin**: Hermes Agent plugin (plugin.yaml + __init__.py)
- **CLI**: `skill-retriever` command (src/skill_retriever/)
- **Mode**: Flat index pre-filter → single LLM curation → 1 call/query

## Local test

```
source .env 2>/dev/null
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

## Workability

- Caches the flat index to disk between runs.
- Fails gracefully when flat index is missing (rebuilds on next query).
- Silently unavailable if dependencies (litellm) aren't installed.
