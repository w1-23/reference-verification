# Capability migration

Baseline `d275430d3ef01bf9558ccd85204d1bdede631989`.

| Baseline | Independent Skill |
|---|---|
| `packages/citrus_refcheck` in `citrusscholar-support` | Reuse the import `citrus_refcheck` from the `reference-verification` distribution. Two distributions providing that same import cannot safely own it in one environment. Move host requirements together or install in a separate worker environment. |
| `extract_inventory`, `compare_fields`, registry mappers, `project_verdict` | Retained. The new identity report uses the same inventory, metadata mappers and conservative comparison rules. The fuller legacy verdict remains callable for saved facts. |
| `research/reference_audit.py::run_audit` bounded completion loop | Ported four-worker bounded dispatch and context propagation. Group at query level before submitting work. Use `verify_batch` with an injected executor when the host has one. |
| Bounded batch completion | `verify_batch` waits with the remaining deadline, cancels its own workers, and drops late futures unless `on_row` already persisted their row. |
| Provider chains and optional resume | New entry runs one identifier or title route. Additional checking is an explicit host/user operation. |
| `LookupLimits`, `lookup_evidence`, `AuditRepository`, legacy CLI | Retained for compatibility. Existing standalone SQLite audits can still use old `snapshot` and `export`. The Skill path uses in-memory batches and explicit snapshot storage. |
| Host reference audit JSON/XLSX/RIS/BibTeX and report UI | `reference-verification/1` snapshot has every inventory row and its processing/match outcome. XLSX columns and JSON shape are versioned independently from the old server API. RIS/BibTeX remain lossy projections of submitted claims. Markdown is available for direct reporting. |
| Host private state, authorization, Search, and artifacts | Supply capabilities using [the adapter contract](references/host-adapter.md). Host interprets completion and delivers artifacts. |

## Host capability mapping

| Host responsibility | Independent entry point |
|---|---|
| Read authorized source material | Pass supplied content and source provenance to `extract_inventory`. Preserve `record_id`, input order, and original citation text. |
| Look up bibliographic metadata | Inject `Capabilities.lookup_doi`, `Capabilities.search`, and optionally `Capabilities.lookup_arxiv`. Return `Observation` with the source, status, metadata, evidence references, and retry timing when supplied. |
| Authorize, cancel, and meter requests | Bind `Capabilities.check` to the host's authorization and cancellation state. Bind `Capabilities.debit` to the shared request budget. Lookups honor the supplied deadline. Reuse the host scheduler through `Capabilities.executor` where available. |
| Deliver an audit result | Persist the `verify_batch` snapshot. Use its processing and identity fields separately. An observed 429 is processed and unconfirmed. A skipped lookup remains unprocessed. Generate exports from the stored snapshot with `export_snapshot`. |

See the [host adapter contract](references/host-adapter.md) and [fixture host example](scripts/host_adapter.py) for callback signatures and a standalone run.
