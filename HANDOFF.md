# Handoff

## State

- Active development — core retrieval pipeline is stable.
- Flat index rebuild and LLM curation pass integration tests pass.
- Plugin registered with Hermes Agent.

## Next Actions

- [ ] Benchmark query latency across flat index sizes
- [ ] OpenTelemetry tracing for curation LLM calls
- [ ] Fallback strategies when LLM curation fails

## Verify Before Ship

- [ ] `python3 -m pytest tests/ -v` passes
- [ ] `ruff check .` passes
