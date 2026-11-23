# Reference verification

Verify citation identity in one bounded batch. A report contains every submitted reference in its original order, including duplicates, source failures, and entries without an observation. The package uses metadata capabilities and has no full-text retrieval step.

## Install on Linux

Use Python 3.11 or newer. `requirements.lock` pins the runtime, test, and build dependency sets with hashes.

```sh
python -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/python -m pytest
```

The distribution is `reference-verification` and the import remains `citrus_refcheck`. Install it in an environment where it owns that import. See the [migration table](references/migration.md) for compatibility with hosts that currently install the support bundle.

## CLI

```sh
reference-verification verify references.csv --fixture responses.json --output saved.json
reference-verification export-snapshot saved.json --format markdown --output report.md
reference-verification export-snapshot saved.json --format xlsx --output report.xlsx
```

`verify` requires exactly one transport option. With `--fixture`, supply JSON keyed by full registry URL, with an optional JSON `[url, params]` key for queries. Values contain `http_status` and `payload`. Use `{}` to exercise offline 404 handling. With `--allow-network`, explicitly authorize metadata calls to Crossref (default exact DOI resolver and title search), DataCite (when selected with `--doi-registry DataCite`) and arXiv (exact arXiv IDs). Select the DOI registry for the submitted bibliography. The CLI uses anonymous public access with redirects disabled and no environment proxy or credential lookup. Hosts can inject their own configured clients and search services via the Python API.

Inputs include JSON records or a prebuilt inventory, pasted text via the API, TXT, Markdown, CSV, TSV, DOCX paragraphs, and text-layer PDF bibliographies. Use `--column-map '{"title":"Article Title","doi":"DOI"}'` and optionally `--encoding` for delimited inputs. Text segmentation is heuristic. PDF OCR and DOCX table extraction are outside the parser's coverage. PDF input means local bibliography extraction from a submitted file.

The default batch uses at most four active queries, 20 request reservations, 30 seconds per batch, and eight seconds per item. Use `--request-budget`, `--concurrency`, `--timeout`, and `--item-timeout` to set limits. Retries default to zero. An explicit `--rate-limit-retries` of one or two retries a 429 only when `Retry-After` fits the remaining deadline and budget. A 429 without retry timing closes that callback's rate scope for the rest of the batch. Rows with an observed 429 are processed and unconfirmed. A skipped row remains unprocessed. There is no automatic resume, fallback search, DOI replacement, or retrieval by opening a search result URL.

`verify` returns JSON on stdout and can save the same result to a new file. Read `batch_processing_status`, `all_entries_processed`, `coverage`, and the per-row results even when the CLI exits successfully. JSON retains exact citations, source positions, and evidence. XLSX carries row results and provenance with literal text cells and a `LongText` sheet when needed. RIS and BibTeX project the submitted bibliography in original order, with row IDs and statuses in notes. Markdown presents a compact review table. Every export uses the saved snapshot.

## Python API

```python
from citrus_refcheck import BatchLimits, Capabilities, export_snapshot, extract_inventory, verify_batch

inventory = extract_inventory(references=[{"citation": "[1] Example work", "title": "Example work"}])
capabilities = Capabilities(check=host.check_network_and_cancel, debit=host.reserve_request,
                            lookup_doi=host.lookup_doi, search=host.search_metadata)
snapshot = verify_batch(inventory, capabilities=capabilities, limits=BatchLimits())
result_json = export_snapshot(snapshot)
```

See the [callback contract](references/host-adapter.md) and the [runnable example](scripts/host_adapter.py).
