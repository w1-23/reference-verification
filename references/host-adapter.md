# Host adapter

Translate existing host capabilities into the small Python boundary below. Construct callbacks in the authorized request context. The core receives only the inventory, injected capabilities, and finite limits.

| Boundary | Contract |
|---|---|
| Local input | Read an authorized file in the host and supply bytes with its name and opaque provenance to `extract_inventory`. Keep the returned IDs and positions. |
| Lookup | `lookup_doi`, `lookup_arxiv`, and `search` accept `(Query, deadline)`. `deadline` is an absolute `time.monotonic()` deadline covering provider work. `Query.value` is a normalized exact DOI, an exact arXiv ID, or the original-language title. `Query.authors` contains useful submitted author metadata for title search. |
| Observation | Return `Observation(source, http_status, records, evidence_refs, error, retry_after)`. A callback invocation corresponds to one provider request. `records` contains up to 20 normalized metadata dicts with a title, available bibliographic fields, and a real `url` or opaque `source_ref`. An observation-level `evidence_refs` value can also identify the retained response. Represent transport errors with status `0` and a safe error code. Send `404` or a clean 200 with zero records for a source-local miss. Send `429` and `retry_after` in seconds for throttling. Return only records with enough provenance to cite or review. |
| Scope and cancellation | `check()` is thread-safe and raises `AuditStopped('cancelled')`, `AuditStopped('deadline')`, or `AuditStopped('network_disabled')` when blocked. Lookup callbacks must honor the supplied deadline and enforce network permissions at dispatch. Cancellation fences late responses. Completed rows remain available. |
| Global request budget | `debit(Query)` is a thread-safe reservation invoked before each attempted dispatch, including an explicit retry. Raise `BudgetExceeded` to deny a reservation. A reservation is distinct from dispatch. The package's batch reservation still counts if the host denies or cancels a request. |
| Rate and concurrent work | The batch groups identical queries, then uses the existing bounded dispatch pattern with at most four active queries. Provide an existing worker executor to reuse the host's own scheduling. Invoke `verify_batch` outside that executor's workers so it can make progress. Share the same callback object for operations targeting one local throttle bucket. Enforce provider limits across batches and retries in the host. Lookup functions process one request and must not schedule another executor or make hidden fallback requests. |
| Storage and report | Persist the returned JSON snapshot through the host's artifact API. JSON/XLSX contain row evidence. Generate exports from the saved snapshot without calling a lookup. The host owns durable artifact receipts and user/session access. |

`entries[].evidence.processing_status` records whether an observation was received. `match_status` is `matching`, `conflict`, `unconfirmed`, or `unprocessed`. The `reason` distinguishes `rate_limited`, `not_found`, `provider_error`, missing metadata, and work skipped before an accepted observation. `not_found` describes the queried source. Keep extraction coverage (`inventory.extraction`) distinct from batch processing coverage. `all_entries_processed` describes the inventory returned by extraction.

### First-return report view

Call `citrus_refcheck.report_view(saved_snapshot)` and give the model that projection, not a fresh lookup and not the full per-row source receipts. It is offline and never mutates the snapshot. The stable shape is:

```json
{
  "schema_version": "reference-verification-report/1",
  "source_schema_version": "reference-verification/1",
  "batch_finished": true,
  "snapshot_only": true,
  "report_ready": true,
  "coverage_complete": true,
  "batch_processing_status": "complete",
  "stop_reason": "",
  "coverage": {"requested": 10, "actual": 10, "gap": 0},
  "unprocessed_ordinals": [],
  "additional_checks_require_user_request": true,
  "rows": [{
    "record_id": "opaque-stable-id",
    "record_index": 0,
    "ordinal": 1,
    "citation": "short original citation",
    "processing_status": "processed",
    "identity": "matched",
    "reason": "identifier_match",
    "match": {"title": "...", "doi": "...", "source": "..."},
    "field_notes": []
  }]
}
```

`rows` always preserve submitted order and duplicate rows. `processing_status` is `processed` or `unprocessed`; `identity` is `matched`, `conflict`, `unresolved`, or `unprocessed`. `field_notes` entries retain `field`, `submitted`, `registered`, `source`, and `reason`. They are notes, not an automatic correction demand. The compact projection intentionally omits abstracts, full text, and raw provider payloads.

`report_ready` means the current snapshot can be shown: either every entry was processed, or the batch has a terminal partial/cancelled result with an explicit stop reason or per-row reason. `coverage_complete` is stricter and is true only when `all_entries_processed` is true. A `complete` batch may still contain `unresolved` rows; that is a valid report. An `unprocessed` row is a coverage gap, never a negative identity finding.

After the batch returns, `snapshot_only` and `additional_checks_require_user_request` are always true. Do not automatically Search/Fetch, resume, read full text, replace a DOI, or rebuild the audit from the final answer. A pure identity request ends at this report. A mixed request closes only this subtask. Render extra rows or XLSX/RIS/BibTeX from the same saved snapshot. The CLI equivalents are `report-view` and `export-snapshot`.

## CitrusScholar integration notes

Bind the host network switch and cancellation to `check`, the global metering and authorization to `debit`, and the existing authorized Search and bibliographic routes to callbacks. Use host-managed receipts and source records to fill `evidence_refs` and `source_ref`. Refer to the actual source in the report. Map the snapshot to the host's generic completion and artifact APIs. `complete` with `unconfirmed` rows is a valid delivered report. Save and render partial snapshots after a stop. Host integration requires wiring and testing authorized Search output, shared budget and throttle, citation receipts, artifact publication, and the final runtime installation lock. These mappings remain outside the package.

## Independent example

Run `python scripts/host_adapter.py` after installation. It discovers the Skill name in the entry file, calls the installed batch API with a fixture search and file capability, and publishes the snapshot via a fixture artifact capability. This exercises the adapter contract without service credentials.
