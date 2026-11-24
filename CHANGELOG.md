# Changelog

## 1.2.0

- Added `report_view(snapshot)`, an offline compact projection with separate processing and identity axes, preservation of original order and citation IDs, explicit necessary-difference notes, and a snapshot-only stop contract.
- Markdown reports now use the same projection, so saved-report rendering and first-return host rows cannot diverge.
- Added `report-view` CLI rendering from a saved snapshot without lookups, plus complete and partial replay fixtures.

## 1.1.0

- Bounded batch waiting: `verify_batch` now waits with a deadline and discards late futures instead of blocking on an unresponsive callback.
- `verify_batch(..., on_row=...)` reports each evidence row as soon as it completes so hosts can persist partial progress in short transactions.
- Exported `identity_sufficient` so hosts reuse the package's own conservative match rule for early stop instead of duplicating it.
- Clarified metering: one `debit()` per real outbound provider request; logical queries are no longer billed as requests.

## 1.0.0

- Extracted the bibliographic inventory, metadata registry mappings, comparisons, exports and verdict fixtures from CitrusScholar `d275430`.
- Added deduplicated bounded verification with explicit capability, budget and cancellation hooks, and separate processing and identity outcomes.
- Added offline export from saved snapshots, standalone CLI and an executable host adapter example.
