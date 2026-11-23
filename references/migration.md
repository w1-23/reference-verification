# Migration from d275430

| Baseline | Independent Skill |
|---|---|
| `packages/citrus_refcheck` in `citrusscholar-support` | Reuse the import `citrus_refcheck` from the `reference-verification` distribution. Two distributions providing that same import cannot safely own it in one environment. Move host requirements together or install in a separate worker environment. |
| `extract_inventory`, `compare_fields`, registry mappers, `project_verdict` | Retained. The new identity report uses the same inventory, metadata mappers and conservative comparison rules. The fuller legacy verdict remains callable for saved facts. |
| `research/reference_audit.py::run_audit` bounded completion loop | Ported four-worker bounded dispatch and context propagation. Group at query level before submitting work. Use `verify_batch` with an injected executor when the host has one. |
| Provider chains and optional resume | New entry runs one identifier or title route. Additional checking is an explicit host/user operation. |
| `LookupLimits`, `lookup_evidence`, `AuditRepository`, legacy CLI | Retained for compatibility. Existing standalone SQLite audits can still use old `snapshot` and `export`. The Skill path uses in-memory batches and explicit snapshot storage. |
| Host reference audit JSON/XLSX/RIS/BibTeX and report UI | `reference-verification/1` snapshot has every inventory row and its processing/match outcome. XLSX columns and JSON shape are versioned independently from the old server API. RIS/BibTeX remain lossy projections of submitted claims. Markdown is available for direct reporting. |
| Host private state, authorization, Search, and artifacts | Supply capabilities using [the adapter contract](host-adapter.md). Host interprets completion and delivers artifacts. |
