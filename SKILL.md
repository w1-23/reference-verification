---
name: reference-verification
description: Verify whether submitted references identify the cited works. Use for a bibliography, uploaded citation list, or DOI and title verification. Return an ordered identity report with evidence, conflicts, and unconfirmed entries.
---

# Reference verification

1. Read the submitted bibliography using an authorized file capability. Keep each original citation, identifier, ordinal, and source position. Disclose uncertain parsing or incomplete extraction. Preserve duplicate rows.
2. Run **one** bounded batch through the installed `citrus_refcheck.verify_batch` API or [the CLI script](scripts/verify.py). Supply an authorized DOI metadata capability and an original-title Search capability. Include author information when available and necessary to distinguish works. Prefer an exact DOI or arXiv identifier; check the returned title against the submitted title. Do not add a second provider, worker pool, full-text step, or automatic resume.
3. Render the returned snapshot with `citrus_refcheck.report_view`. Keep processing on one axis (`processed` / `unprocessed`) and identity on the other (`matched` / `conflict` / `unresolved` / `unprocessed`). Preserve original order and citation IDs. A successful match ends that entry. A 429 or provider error is `processed · unresolved`, not nonexistence. A row skipped before an observation is `unprocessed`; report its stop reason. Every explicit conflict must retain the submitted value, registered value, matched work, and source. Author abbreviation, online/print year, page/article-number, or venue variants are notes; do not turn them into automatic field correction.
4. Stop when the batch returns. A complete or partial snapshot is reportable, with coverage and stop reason separate from identity. Pure identity verification ends here; in a mixed task only close the reference-verification subtask. Do **not** run generic Search/Fetch, reread a full receipt, correct the bibliography, or fetch full text without a new explicit user instruction. If the user asks for more rows than the first compact return, render them from the saved snapshot without network access. Export XLSX/RIS/BibTeX only when explicitly requested or when reusing an existing snapshot.

The following is the report shape, not a claim about any real reference:

| # | Original citation (short) | Identity | Matched work and source | Necessary differences |
|---|---|---|---|---|
| 1 | [1] Alice Example, Synthetic work… | processed · matched | Synthetic work · Alice Example · 2024 · 10.1234/example · Fixture registry | — |
| 2 | [2] Submitted title… | processed · conflict (identifier_title_conflict) | Registered work · 10.1234/example · Fixture registry | title: Submitted title → Registered work [Fixture registry] |
| 3 | [3] Rate-limited item… | processed · unresolved (rate_limited) | — | — |
| 4 | [4] Not attempted… | unprocessed (request_budget_exhausted) | — | — |

Do not answer “all ten are real” or “no fabrication” in place of the per-row identity result. See [usage and formats](README.md) and [the host adapter](references/host-adapter.md).
