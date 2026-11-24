from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier, Event, Lock
import time
from unittest.mock import patch

import docx
import httpx
import openpyxl
import pymupdf
import pytest

from citrus_refcheck import (AuditStopped, BatchLimits, BudgetExceeded, Capabilities, Observation,
                             export_snapshot, extract_inventory, identity_sufficient, report_view, verify_batch)
from citrus_refcheck.standalone import registry_capabilities


def _noop(*_args):
    pass


def _doi(name):
    return f'10.1234/{name}'


def _work(name, *, title=None, doi=None, authors='Alice Example'):
    return {'title': title or f'Synthetic work {name}', 'doi': doi or _doi(name), 'authors': authors,
            'source_ref': f'fixture:{name}'}


def _entry(name, *, title=None, doi=None, authors='Alice Example'):
    citation = f'[{name}] Submitted citation\r\n'
    return {'citation': citation, 'claims': {'title': title or f'Synthetic work {name}',
                                            'doi': _doi(name) if doi is None else doi, 'authors': authors}}


def test_ten_entries_first_return_with_three_throttles_and_real_overlap():
    entries = [_entry('a'), _entry('a duplicate', title='Synthetic work a', doi='https://doi.org/10.1234/A'),
               _entry('b', title='原始题名', doi=''), _entry('wrong', title='Unrelated submitted title'),
               _entry('missing'), _entry('error'), _entry('c'), _entry('rate1'), _entry('rate2'), _entry('rate3')]
    inventory = extract_inventory(references=entries)
    lock = Lock()
    start_throttled = Barrier(3, timeout=5)
    calls, reservations = [], []
    active = peak = 0
    request_context = ContextVar('request_context', default='missing')
    request_context.set('request-1')

    def lookup(query, deadline):
        nonlocal active, peak
        assert request_context.get() == 'request-1' and deadline > time.monotonic()
        with lock:
            calls.append(query)
            active += 1
            peak = max(peak, active)
        try:
            if query.value in {_doi('rate1'), _doi('rate2'), _doi('rate3')}:
                start_throttled.wait()
                return Observation('Fixture registry', 429, error='http_429')
            if query.kind == 'search':
                assert query.value == '原始题名' and query.authors == 'Alice Example'
                return Observation('Fixture Search', 200, (_work('b', title=query.value),))
            name = query.value.split('/')[1]
            if name == 'missing':
                return Observation('Fixture registry', 404)
            if name == 'error':
                return Observation('Fixture registry', 503, error='http_503')
            return Observation('Fixture registry', 200, (_work(name),), ('fixture:receipt',))
        finally:
            with lock:
                active -= 1

    def debit(query):
        with lock:
            reservations.append(query)

    with ThreadPoolExecutor(max_workers=4) as executor, patch('socket.socket', side_effect=AssertionError('network')):
        capabilities = Capabilities(_noop, debit, lookup_doi=lookup, search=lookup, executor=executor)
        snapshot = verify_batch(inventory, capabilities=capabilities)
    assert snapshot['batch_processing_status'] == 'complete'
    assert snapshot['all_entries_processed'] and snapshot['coverage'] == {'requested': 10, 'actual': 10, 'gap': 0}
    assert [row['record_id'] for row in snapshot['entries']] == [row['record_id'] for row in inventory['entries']]
    assert [row['citation'] for row in snapshot['entries']] == [entry['citation'] for entry in entries]
    assert len({row['record_id'] for row in snapshot['entries']}) == 10
    results = [row['evidence'] for row in snapshot['entries']]
    assert [result['reason'] for result in results] == ['identifier_match', 'identifier_match', 'title_match',
        'identifier_title_conflict', 'not_found', 'provider_error', 'identifier_match', *(['rate_limited'] * 3)]
    assert results[3]['match_status'] == 'conflict' and results[3]['differences'][0]['field'] == 'title'
    assert all(result['match_status'] == 'unconfirmed' for result in results[-3:])
    assert all(result['sources'][0]['http_status'] == 429 for result in results[-3:])
    assert 3 <= peak <= 4
    assert len(calls) == 9 and Counter(calls) == Counter(reservations) and len(set(calls)) == 9
    assert snapshot['budget']['dispatched'] == 9
    view = report_view(snapshot)
    assert view['report_ready'] and view['coverage_complete']
    assert len(view['rows']) == 10 and [row['ordinal'] for row in view['rows']] == list(range(1, 11))
    assert [row['identity'] for row in view['rows']].count('unresolved') == 5
    assert all(row['match']['source'] for row in view['rows'] if row['identity'] == 'matched')
    assert len(calls) == 9


def test_unprocessed_rate_cooldown_and_no_fallback():
    entries = [_entry('one'), _entry('two')]
    calls = []

    def limited(query, deadline):
        calls.append(query)
        return Observation('Fixture registry', 429, retry_after=60)

    snapshot = verify_batch(extract_inventory(references=entries), limits=BatchLimits(concurrency=1),
                            capabilities=Capabilities(_noop, _noop, lookup_doi=limited, search=limited))
    first, second = [row['evidence'] for row in snapshot['entries']]
    assert first['match_status'] == 'unconfirmed' and first['reason'] == 'rate_limited'
    assert second['match_status'] == 'unprocessed' and second['reason'] == 'rate_limit_cooldown'
    assert snapshot['batch_processing_status'] == 'partial'
    assert len(calls) == 1


def test_opt_in_retry_waits_and_reserves_again():
    inventory = extract_inventory(references=[_entry('one')])
    calls, reservations = [], []

    def lookup(query, deadline):
        calls.append(time.monotonic())
        if len(calls) == 1:
            return Observation('Fixture registry', 429, retry_after=0.03)
        return Observation('Fixture registry', 200, (_work('one'),))

    result = verify_batch(inventory, limits=BatchLimits(rate_limit_retries=1),
                          capabilities=Capabilities(_noop, reservations.append, lookup_doi=lookup))
    assert calls[1] - calls[0] >= 0.03 and len(reservations) == 2
    evidence = result['entries'][0]['evidence']
    assert [source['http_status'] for source in evidence['sources']] == [429, 200]
    assert evidence['match_status'] == 'matching'


@pytest.mark.parametrize('stopped', ['cancelled', 'network_disabled', 'request_budget_exhausted'])
def test_global_stop_preserves_observations(stopped):
    inventory = extract_inventory(references=[_entry(name) for name in ('one', 'two', 'three')])
    block = Event()
    dispatched = []

    def check():
        if block.is_set() and stopped != 'request_budget_exhausted':
            raise AuditStopped(stopped)

    def debit(query):
        if stopped == 'request_budget_exhausted' and dispatched:
            raise BudgetExceeded(stopped)

    def lookup(query, deadline):
        dispatched.append(query.value)
        if query.value == _doi('two'):
            block.set()
        return Observation('Fixture registry', 200, (_work(query.value.split('/')[1]),))

    result = verify_batch(inventory, limits=BatchLimits(concurrency=1),
                          capabilities=Capabilities(check, debit, lookup_doi=lookup))
    reasons = [row['evidence']['reason'] for row in result['entries']]
    assert reasons == ['identifier_match', stopped, stopped]
    assert result['coverage'] == {'requested': 3, 'actual': 1, 'gap': 2}
    assert result['batch_processing_status'] == ('cancelled' if stopped == 'cancelled' else 'partial')
    assert len(dispatched) == (1 if stopped == 'request_budget_exhausted' else 2)


def test_deadline_and_local_budget_are_separate_limits():
    inventory = extract_inventory(references=[_entry('one'), _entry('two')])
    calls = []

    def lookup(query, deadline):
        calls.append(query.value)
        time.sleep(max(0, deadline - time.monotonic()) + 0.005)
        return Observation('Fixture registry', 404)

    capabilities = Capabilities(_noop, _noop, lookup_doi=lookup)
    result = verify_batch(inventory, capabilities=capabilities,
                          limits=BatchLimits(concurrency=1, request_budget=1, timeout=1, item_timeout=0.02))
    assert calls == [_doi('one')]
    assert [row['evidence']['reason'] for row in result['entries']] == ['item_deadline', 'request_budget_exhausted']
    result = verify_batch(inventory, capabilities=capabilities,
                          limits=BatchLimits(concurrency=1, timeout=0.02, item_timeout=1))
    assert calls == [_doi('one'), _doi('one')]
    assert [row['evidence']['reason'] for row in result['entries']] == ['deadline', 'deadline']


def test_source_errors_and_ambiguous_candidates_are_not_confirmation():
    title = 'Synthetic homonym'
    inventory = extract_inventory(references=[_entry('a', title=title, doi='')])
    records = (_work('a', title=title), _work('b', title=title))
    result = verify_batch(inventory, capabilities=Capabilities(_noop, _noop,
                          search=lambda *_: Observation('Fixture Search', 200, records)))
    assert result['entries'][0]['evidence']['reason'] == 'ambiguous_match'
    result = verify_batch(inventory, capabilities=Capabilities(_noop, _noop))
    assert result['entries'][0]['evidence']['reason'] == 'capability_unavailable'


def test_metadata_http_transport_has_no_fallback_redirect_or_fulltext():
    urls = []

    def respond(request):
        urls.append(str(request.url))
        return httpx.Response(429, headers={'Retry-After': '120'})

    inventory = extract_inventory(references=[_entry('one'), _entry('two')])
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        result = verify_batch(inventory, capabilities=registry_capabilities(client=client),
                              limits=BatchLimits(concurrency=1, rate_limit_retries=2))
    assert urls == ['https://api.crossref.org/works/10.1234/one']
    first, second = [row['evidence'] for row in result['entries']]
    assert first['sources'][0]['retry_after'] == 120
    assert second['reason'] == 'rate_limit_cooldown'


def test_exports_from_saved_snapshot_retaining_literal_text_and_original_doi():
    original = '=1+1' + '🧪' * 17000
    inventory = extract_inventory(references=[dict(_entry('wrong'), citation=original), _entry('untouched')])
    capabilities = Capabilities(_noop, _noop, lookup_doi=lambda *_: Observation('Fixture registry', 200, (_work('wrong', title='Unrelated title'),)))
    snapshot = verify_batch(inventory, capabilities=capabilities, limits=BatchLimits(request_budget=1, concurrency=1))
    with patch('socket.socket', side_effect=AssertionError('network')), patch.object(Capabilities, 'lookup_doi', side_effect=AssertionError('lookup')):
        saved = json.loads(export_snapshot(snapshot))
        workbook = openpyxl.load_workbook(BytesIO(export_snapshot(saved, format='xlsx')))
        ris = export_snapshot(saved, format='ris').decode()
        bibtex = export_snapshot(saved, format='bibtex').decode()
        markdown = export_snapshot(saved, format='markdown').decode()
    assert workbook['References']['B2'].data_type == 's'
    first = workbook['References']['B2'].value
    continuation = next(row[3] for row in workbook['LongText'].iter_rows(min_row=2, values_only=True) if row[1] == 'original')
    assert first + continuation == original
    assert [workbook['References'][f'G{row}'].value for row in (2, 3)] == ['conflict', 'unprocessed']
    assert 'DO  - 10.1234/wrong' in ris and 'doi = {10.1234/wrong}' in bibtex
    assert ris.count('ER  - ') == bibtex.count('@misc') == 2
    assert 'identifier_title_conflict' in markdown and 'request_budget_exhausted' in markdown
    assert saved['entries'][0]['citation'] == original


@pytest.mark.parametrize('name', ['test.txt', 'test.md', 'test.docx', 'test.pdf', 'test.csv', 'test.tsv'])
def test_extract_supported_formats(name):
    text = 'References\n[1] A. Example (2026). Synthetic work. Fictional Journal. doi:10.1234/one\n'
    if name.endswith('.docx'):
        document = docx.Document()
        for line in text.splitlines():
            document.add_paragraph(line)
        content = BytesIO()
        document.save(content)
        raw = content.getvalue()
    elif name.endswith('.pdf'):
        with pymupdf.open() as document:
            document.new_page().insert_text((60, 60), text)
            raw = document.tobytes()
    elif name.endswith(('.csv', '.tsv')):
        delimiter = ',' if name.endswith('.csv') else '\t'
        raw = f'title{delimiter}doi\r\nSynthetic work{delimiter}10.1234/one\r\n'.encode()
    else:
        raw = text.encode()
    result = extract_inventory(name=name, content=raw)
    assert len(result['entries']) == 1
    assert result['entries'][0]['claims']['doi'] == '10.1234/one'


def test_invalid_provider_metadata_degrades_only_its_own_result():
    inventory = extract_inventory(references=[_entry('one'), _entry('two')])

    def lookup(query, deadline):
        if query.value == _doi('one'):
            return Observation('Fixture registry', 200, ({'title': 'Invalid', 'years': [{'kind': {}}]},))
        record = dict(_work('two'), abstract='unneeded fixture body')
        return Observation('Fixture registry', 200, (record,))

    snapshot = verify_batch(inventory, capabilities=Capabilities(_noop, _noop, lookup_doi=lookup))
    first, second = [row['evidence'] for row in snapshot['entries']]
    assert snapshot['all_entries_processed'] and first['reason'] == 'provider_error'
    assert second['match_status'] == 'matching' and 'abstract' not in second['match']


def test_public_transport_does_not_follow_metadata_redirect():
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://example.invalid/fulltext.pdf'})

    inventory = extract_inventory(references=[_entry('one')])
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        snapshot = verify_batch(inventory, capabilities=registry_capabilities(client=client))
    assert calls == ['https://api.crossref.org/works/10.1234/one']
    assert snapshot['entries'][0]['evidence']['reason'] == 'provider_error'


def test_export_command_uses_no_clobber_snapshot_output(tmp_path, capsys):
    from citrus_refcheck.cli import main

    inventory = extract_inventory(references=[_entry('one')])
    snapshot = verify_batch(inventory, capabilities=Capabilities(_noop, _noop))
    source = tmp_path / 'saved.json'
    source.write_bytes(export_snapshot(snapshot))
    destination = tmp_path / 'report.json'
    argv = ['export-snapshot', str(source), '--format', 'json', '--output', str(destination)]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'saved'
    assert destination.read_bytes() == source.read_bytes()
    assert main(argv) == 1
    assert json.loads(capsys.readouterr().out)['errors'][0]['code'] == 'output_exists'
    assert destination.read_bytes() == source.read_bytes()
    assert set(tmp_path.iterdir()) == {source, destination}

def test_on_row_reports_each_entry_before_the_batch_returns():
    entries = [_entry('a'), _entry('b'), _entry('missing')]
    inventory = extract_inventory(references=entries)
    seen = []

    def lookup(query, deadline):
        name = query.value.split('/')[1]
        if name == 'missing':
            return Observation('Fixture registry', 404)
        return Observation('Fixture registry', 200, (_work(name),))

    snapshot = verify_batch(inventory, capabilities=Capabilities(_noop, _noop, lookup_doi=lookup),
                            on_row=lambda row: seen.append((row['record_id'], row['evidence']['processing_status'])))
    assert len(seen) == len(entries)
    assert {row['record_id'] for row in snapshot['entries']} == {record_id for record_id, _ in seen}
    assert all(status == 'processed' for _, status in seen)


def test_identity_sufficient_accepts_doi_and_exact_title_but_rejects_conflicts():
    claims = {'title': 'Synthetic work a', 'doi': '10.1234/a', 'authors': 'Alice Example'}
    assert identity_sufficient(claims, _work('a'))
    assert not identity_sufficient(claims, _work('b'))
    assert not identity_sufficient({**claims, 'title': 'Unrelated work'}, _work('a'))
    work = {**_work('a'), 'year': '2024'}
    title_only = {'title': 'Synthetic work a', 'authors': 'Alice Example', 'year': '2024'}
    assert identity_sufficient(title_only, work)
    assert not identity_sufficient({**title_only, 'year': '1999'}, work)
    assert not identity_sufficient({**title_only, 'title': 'Unrelated'}, work)


_FIXTURES = Path(__file__).parent / 'fixtures'


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding='utf-8'))


def test_complete_batch_replay_has_compact_report_without_new_work():
    snapshot = _fixture('report_complete_batch.json')
    before = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    with patch('socket.socket', side_effect=AssertionError('network')):
        view = report_view(snapshot)
        markdown = export_snapshot(snapshot, format='markdown').decode()
    assert json.dumps(snapshot, ensure_ascii=False, sort_keys=True) == before
    assert view['schema_version'] == 'reference-verification-report/1'
    assert view['batch_finished'] and view['snapshot_only']
    assert view['report_ready'] and view['coverage_complete']
    assert view['additional_checks_require_user_request']
    assert view['coverage'] == {'requested': 4, 'actual': 4, 'gap': 0}
    assert [row['ordinal'] for row in view['rows']] == [1, 2, 3, 4]
    assert [row['identity'] for row in view['rows']] == ['matched', 'conflict', 'unresolved', 'unresolved']
    assert all(row['processing_status'] == 'processed' for row in view['rows'])
    assert view['rows'][0]['match']['source'] == 'Fixture registry'
    conflict = view['rows'][1]['field_notes'][0]
    assert conflict['field'] == 'title' and conflict['submitted'] and conflict['registered']
    assert conflict['source'] == 'Fixture registry'
    assert all('abstract' not in (row['match'] or {}) for row in view['rows'])
    assert '| # | Original citation | Identity | Matched work / source | Necessary differences |' in markdown
    assert 'identifier_title_conflict' in markdown and 'not_found' in markdown


def test_partial_batch_replay_is_reportable_but_not_complete():
    snapshot = _fixture('report_partial_batch.json')
    with patch('socket.socket', side_effect=AssertionError('network')):
        view = report_view(snapshot)
    assert view['batch_finished'] and view['snapshot_only']
    assert view['report_ready'] is True
    assert view['coverage_complete'] is False
    assert view['batch_processing_status'] == 'partial'
    assert view['coverage'] == {'requested': 3, 'actual': 2, 'gap': 1}
    assert view['unprocessed_ordinals'] == [3]
    assert [(row['processing_status'], row['identity'], row['reason']) for row in view['rows']] == [
        ('processed', 'matched', 'identifier_match'),
        ('processed', 'unresolved', 'rate_limited'),
        ('unprocessed', 'unprocessed', 'rate_limit_cooldown'),
    ]
    assert view['additional_checks_require_user_request'] is True


def test_report_view_and_snapshot_export_share_saved_identity_snapshot(tmp_path, capsys):
    from citrus_refcheck.cli import main

    source = _FIXTURES / 'report_partial_batch.json'
    output = tmp_path / 'report-view.json'
    with patch('socket.socket', side_effect=AssertionError('network')):
        assert main(['report-view', str(source), '--output', str(output)]) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding='utf-8'))
    assert printed == saved
    assert saved['coverage_complete'] is False and saved['unprocessed_ordinals'] == [3]
    assert set(saved['rows'][0]) == {'record_id', 'record_index', 'ordinal', 'citation', 'processing_status',
                                     'identity', 'reason', 'match', 'field_notes'}
