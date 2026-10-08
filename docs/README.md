# Documentation

Start at [`architecture/overview.md`](architecture/overview.md): it links to every
current subsystem document and ends with a source-of-truth map into the code.

```text
architecture/   how the system works now (current implementation only)
workflows/      end-to-end data flows as implemented (robot ingestion)
development/    running it locally, commands, the test surface, the reference environment
adr/            architecture decision records: why a decision was made (README.md maps
                the paths they cite to the current layout)
history/        point-in-time studies and benchmarks; evidence, not current truth
```

Rules of the hierarchy:

- `architecture/`, `workflows/` and `development/` describe the system **as it exists**
  and are updated when the system changes. Code, tests and `make help` win over any
  document.
- `adr/` records decisions and their reasoning. An ADR is not rewritten to match the
  code; a changed decision is superseded by a new ADR (ADR-004 is superseded by
  [ADR-009](adr/009-job-centric-execution-and-durable-boundaries.md)).
- `history/` is never current truth. Hits there for removed components are intentional.
- There is no roadmap document: future work lives in issues and pull requests, and what
  the platform does not do today is in
  [`architecture/limitations.md`](architecture/limitations.md).
