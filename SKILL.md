---
name: reference-verification
description: Verify whether submitted references identify the cited works. Use for a bibliography, uploaded citation list, or DOI and title verification. Return an ordered identity report with evidence, conflicts, and unconfirmed entries.
---

# Reference verification

1. Read the submitted bibliography using an authorized file capability. Keep each original citation, identifier, ordinal, and source position. Disclose uncertain parsing or incomplete extraction.
2. Run one bounded batch through the installed `citrus_refcheck.verify_batch` API or [the CLI script](scripts/verify.py). Supply an authorized DOI metadata capability and an original-title Search capability. Include author information when available and necessary to distinguish works. Prefer an exact DOI or arXiv identifier when provided and check the returned title against the submitted title.
3. Use the returned snapshot to report original citation, status, matched metadata, source, and material differences. Preserve duplicate rows and original ordering. A successful match ends that entry. Treat sustained 429s, source errors, and empty results as unconfirmed. Report work skipped by cancellation, missing capabilities, or exhausted budgets as unprocessed.
4. Finish once all entries have been processed, including entries that could not be confirmed. A partial snapshot can also be reported with its coverage and stop reason. Ask for a separate instruction before running additional searches, correcting the bibliography, or fetching full text. Export saved results without repeating the batch.

See [usage and formats](README.md) and [the optional host adapter](references/host-adapter.md).
