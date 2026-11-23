"""Run one bounded identity check per distinct query and project it onto each input."""
from concurrent.futures import Executor, ThreadPoolExecutor, as_completed, wait
from contextvars import copy_context
from dataclasses import dataclass, replace
import math
import re
from threading import Event, Lock
import time
from typing import Callable

from .audit import BudgetExceeded
from .compare import _value, compare_fields
from .inventory import MAX_RECORDS, digest
from .lookup import AuditStopped
from .providers import norm_title, normalize_doi
from .verdict import _abbreviated_authors

_MAX_WORKERS = 4
_WORKERS = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix='reference-verification')
_BATCH_STOPS = frozenset({'cancelled', 'canceled', 'deadline', 'request_budget_exhausted', 'network_disabled',
                          'operation_stopped', 'lease_lost'})
_ITEM_STOPS = frozenset({'item_deadline', 'rate_limit_cooldown'})
_EXACT_DOI = re.compile(r'10\.\d{4,9}/\S+')
_ERROR_CODE = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}')
_TEXT_FIELDS = ('title', 'authors', 'year', 'doi', 'arxiv_id', 'journal', 'venue', 'volume', 'issue', 'pages',
                'article_number', 'type', 'source', 'url')


@dataclass(frozen=True)
class Query:
    kind: str
    value: str
    authors: str = ''


@dataclass(frozen=True)
class Observation:
    source: str
    http_status: int
    records: tuple[dict, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error: str = ''
    retry_after: float | None = None


Lookup = Callable[[Query, float], Observation]


@dataclass(frozen=True)
class Capabilities:
    check: Callable[[], None]
    debit: Callable[[Query], None]
    lookup_doi: Lookup | None = None
    search: Lookup | None = None
    lookup_arxiv: Lookup | None = None
    executor: Executor | None = None


@dataclass(frozen=True)
class BatchLimits:
    concurrency: int = _MAX_WORKERS
    request_budget: int = 20
    timeout: float = 30
    item_timeout: float = 8
    rate_limit_retries: int = 0

    def __post_init__(self):
        for value, minimum, maximum in ((self.concurrency, 1, _MAX_WORKERS),
                                        (self.request_budget, 0, 50000),
                                        (self.rate_limit_retries, 0, 2)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError('invalid_batch_limits')
        for value in (self.timeout, self.item_timeout):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 120:
                raise ValueError('invalid_batch_deadline')


def _query(entry):
    if entry.get('parse_status') == 'invalid_row':
        return None
    claims = entry['claims']
    doi = normalize_doi(claims.get('doi'))
    if doi:
        return Query('doi', doi) if _EXACT_DOI.fullmatch(doi) else None
    arxiv = _value(claims, 'arxiv_id')
    if arxiv:
        return Query('arxiv', arxiv)
    title = _value(claims, 'title')
    authors = '' if 'authors' in entry.get('uncertain_fields', ()) else _value(claims, 'authors')
    return Query('search', title, authors) if title else None


def _stop_code(error):
    reason = str(error)
    return reason if reason in _BATCH_STOPS or reason in _ITEM_STOPS else 'operation_stopped'


def _metadata(record):
    fields = {}
    for name in _TEXT_FIELDS:
        if (value := record.get(name)) is None:
            continue
        if type(value) not in (str, int) or len(str(value)) > 8000:
            raise ValueError('invalid_metadata_field')
        fields[name] = str(value)
    if (source_ref := record.get('source_ref')) is not None:
        if type(source_ref) not in (str, dict):
            raise ValueError('invalid_source_ref')
        digest(source_ref)
        fields['source_ref'] = source_ref
    years = record.get('years') or []
    if not isinstance(years, (list, tuple)) or len(years) > 20:
        raise ValueError('invalid_years')
    if any(not isinstance(value, dict) or not isinstance(value.get('kind'), str)
           or type(value.get('year')) not in (str, int) for value in years):
        raise ValueError('invalid_year_metadata')
    fields['years'] = [{'kind': value['kind'], 'year': str(value['year'])} for value in years]
    return fields


def _validated(observation):
    if not isinstance(observation, Observation):
        raise ValueError('invalid_observation')
    if not isinstance(observation.source, str) or not observation.source or len(observation.source) > 200:
        raise ValueError('invalid_source')
    if type(observation.http_status) is not int or not 0 <= observation.http_status <= 599:
        raise ValueError('invalid_status')
    if len(observation.records) > 20 or len(observation.evidence_refs) > 20:
        raise ValueError('too_many_candidates_or_receipts')
    if any(not isinstance(ref, str) or len(ref) > 8000 for ref in observation.evidence_refs):
        raise ValueError('invalid_evidence_ref')
    delay = observation.retry_after
    if type(delay) not in (int, float) or not 0 <= delay < float('inf'):
        delay = None
    records = tuple(_metadata(record) for record in observation.records) if observation.http_status == 200 else ()
    error = observation.error
    if not isinstance(error, str) or (error and not _ERROR_CODE.fullmatch(error)):
        error = 'provider_error'
    return replace(observation, records=records, retry_after=delay, error=error)


class _BatchRequests:
    def __init__(self, capabilities, limits):
        self.capabilities = capabilities
        self.limits = limits
        self.deadline = time.monotonic() + limits.timeout
        self.lock = Lock()
        self.interrupted = Event()
        self.cooldown = {}
        self.reserved = 0
        self.dispatched = 0

    def check(self, deadline):
        if self.interrupted.is_set():
            raise AuditStopped('cancelled')
        self.capabilities.check()
        now = time.monotonic()
        if now >= self.deadline:
            raise AuditStopped('deadline')
        if now >= deadline:
            raise AuditStopped('item_deadline')

    def reserve(self, query, lookup, deadline):
        self.check(deadline)
        with self.lock:
            if self.cooldown.get(id(lookup), 0) > time.monotonic():
                raise AuditStopped('rate_limit_cooldown')
            if self.reserved >= self.limits.request_budget:
                raise BudgetExceeded('request_budget_exhausted')
            self.reserved += 1
        self.capabilities.debit(query)
        self.check(deadline)
        with self.lock:
            if self.cooldown.get(id(lookup), 0) > time.monotonic():
                raise AuditStopped('rate_limit_cooldown')
            self.dispatched += 1

    def run(self, query, lookup):
        deadline = min(self.deadline, time.monotonic() + self.limits.item_timeout)
        observations = []
        for retry in range(self.limits.rate_limit_retries + 1):
            try:
                self.reserve(query, lookup, deadline)
            except BudgetExceeded:
                return observations, 'request_budget_exhausted'
            except AuditStopped as error:
                return observations, _stop_code(error)
            except Exception:
                return observations, 'operation_stopped'
            try:
                observation = _validated(lookup(query, deadline))
            except AuditStopped as error:
                return observations, _stop_code(error)
            except BudgetExceeded:
                return observations, 'request_budget_exhausted'
            except Exception:
                observation = Observation('adapter', 0, error='provider_error')
            delay = observation.retry_after
            if observation.http_status == 429:
                now = time.monotonic()
                until = self.deadline if delay is None or delay >= self.deadline - now else now + delay
                with self.lock:
                    self.cooldown[id(lookup)] = max(self.cooldown.get(id(lookup), 0), until)
            try:
                self.check(deadline)
            except AuditStopped as error:
                return observations, _stop_code(error)
            except Exception:
                return observations, 'operation_stopped'
            observations.append(observation)
            if observation.http_status != 429 or retry == self.limits.rate_limit_retries or delay is None:
                break
            try:
                while True:
                    self.check(deadline)
                    with self.lock:
                        remaining = self.cooldown[id(lookup)] - time.monotonic()
                    if remaining <= 0 or time.monotonic() + remaining >= deadline:
                        break
                    time.sleep(min(remaining, 0.05))
                if remaining > 0:
                    break
            except AuditStopped as error:
                return observations, _stop_code(error)
            except Exception:
                return observations, 'operation_stopped'
        return observations, ''


def _candidate_identity(record):
    for field in ('doi', 'arxiv_id', 'url'):
        value = record.get(field)
        if value:
            return field, normalize_doi(value) if field == 'doi' else value
    return 'source_ref', digest(record['source_ref']) if record.get('source_ref') else None


def _match(entry, query, observation):
    claims = entry['claims']
    uncertain = entry.get('uncertain_fields', ())
    records = [{**row, 'source': row.get('source') or observation.source}
               for row in observation.records if norm_title(row.get('title'))
               and (row.get('url') or row.get('source_ref') or observation.evidence_refs)]
    if not records:
        return None, 'unconfirmed', 'insufficient_metadata'
    title = _value(claims, 'title')
    if query.kind in {'doi', 'arxiv'}:
        field = 'doi' if query.kind == 'doi' else 'arxiv_id'
        direct = [row for row in records if (normalize_doi(row.get(field)) if field == 'doi'
                                             else row.get(field)) == query.value]
        if not direct:
            return None, 'unconfirmed', 'identifier_not_returned'
        titles = {norm_title(row['title']) for row in direct}
        if len(titles) > 1:
            return None, 'conflict', 'source_title_conflict'
        record = direct[0]
        if title:
            title_check = compare_fields({'title': title}, record, uncertain_fields=uncertain)['title']
            if title_check['status'] == 'conflicting':
                return record, 'conflict', 'identifier_title_conflict'
            if title_check['status'] != 'matched':
                return record, 'unconfirmed', 'uncertain_input_title'
        return record, 'matching', 'identifier_match'

    if 'title' in uncertain:
        return None, 'unconfirmed', 'uncertain_input_title'
    matches = {}
    for index, row in enumerate(records):
        fields = compare_fields(claims, row, uncertain_fields=uncertain)
        if fields['title']['status'] != 'matched':
            continue
        authors = fields['authors']
        if _value(claims, 'authors') and not (authors['status'] == 'matched' or (
                'authors' not in uncertain and _abbreviated_authors(authors['submitted'], authors['registered']))):
            continue
        if fields['year']['status'] in {'conflicting', 'uncertain'}:
            continue
        key = _candidate_identity(row)
        matches.setdefault(key if key[1] else ('candidate', index), row)
    if len(matches) == 1:
        return next(iter(matches.values())), 'matching', 'title_match'
    return None, 'unconfirmed', 'ambiguous_match' if matches else 'no_sufficient_match'


def _row_result(entry, query, observations, stop):
    receipts = [{'source': item.source, 'http_status': item.http_status, 'error': item.error,
                 'retry_after': item.retry_after, 'evidence_refs': list(item.evidence_refs)}
                for item in observations]
    record, differences = None, []
    status, reason = 'unprocessed', stop
    if observations:
        status = 'unconfirmed'
        observed = observations[-1]
        if observed.http_status == 429:
            reason = 'rate_limited'
        elif observed.error or observed.http_status not in (200, 404):
            reason = 'provider_error'
        elif observed.http_status == 404 or not observed.records:
            reason = 'not_found'
        else:
            record, status, reason = _match(entry, query, observed)
        if record:
            fields = compare_fields(entry['claims'], record, uncertain_fields=entry.get('uncertain_fields', ()))
            differences = [dict(value, field=field) for field, value in fields.items()
                           if value['status'] in {'conflicting', 'uncertain'}]
    return {'processing_status': 'processed' if observations else 'unprocessed', 'match_status': status,
            'reason': reason, 'match': record, 'sources': receipts, 'differences': differences}


def verify_batch(inventory, *, capabilities: Capabilities, limits=BatchLimits()):
    """Verify an explicit inventory once. All observations, including 429s, are reportable."""
    requests = _BatchRequests(capabilities, limits)
    entries = inventory['entries']
    if (not isinstance(entries, list) or len(entries) > MAX_RECORDS or any(
            not isinstance(entry, dict) or not isinstance(entry.get('record_id'), str)
            or not isinstance(entry.get('citation'), str) or not isinstance(entry.get('claims'), dict)
            or type(entry.get('record_index')) is not int for entry in entries)):
        raise ValueError('invalid_inventory')
    if len({entry['record_id'] for entry in entries}) != len(entries):
        raise ValueError('duplicate_record_id')
    lookups = {'doi': capabilities.lookup_doi, 'arxiv': capabilities.lookup_arxiv, 'search': capabilities.search}
    if not callable(capabilities.check) or not callable(capabilities.debit):
        raise TypeError('check_and_debit_callbacks_required')
    rows = [dict(entry, evidence=None) for entry in entries]
    groups = {}
    for index, entry in enumerate(entries):
        query = _query(entry)
        if query and lookups[query.kind]:
            groups.setdefault(query, []).append(index)
        else:
            rows[index]['evidence'] = _row_result(entry, query, (), 'capability_unavailable' if query else 'invalid_input')

    executor = capabilities.executor or _WORKERS
    pending = iter(groups)
    workers = {}
    stop = ''

    def dispatch():
        query = next(pending, None)
        if query is not None:
            future = executor.submit(copy_context().run, requests.run, query, lookups[query.kind])
            workers[future] = query

    def collect(future, query):
        observations, reason = future.result()
        for index in groups[query]:
            rows[index]['evidence'] = _row_result(entries[index], query, observations, reason)
        return reason

    try:
        for _ in range(min(len(groups), limits.concurrency)):
            dispatch()
        while workers:
            future = next(as_completed(workers))
            reason = collect(future, workers.pop(future))
            if reason in {'cancelled', 'canceled'} or (reason in _BATCH_STOPS and not stop):
                stop = reason
            if not stop:
                dispatch()
    except KeyboardInterrupt:
        requests.interrupted.set()
        stop = 'cancelled'
        for future, query in workers.items():
            if future.done() and not future.cancelled():
                collect(future, query)
    finally:
        for future in workers:
            future.cancel()
        if workers:
            wait(workers)
    for index, row in enumerate(rows):
        if row['evidence'] is None:
            row['evidence'] = _row_result(entries[index], None, (), stop or 'not_started')
    processed = sum(row['evidence']['processing_status'] == 'processed' for row in rows)
    gap = len(rows) - processed
    status = 'cancelled' if stop in {'cancelled', 'canceled'} else 'partial' if gap else 'complete'
    return {'schema_version': 'reference-verification/1', 'inventory': inventory, 'entries': rows,
            'batch_processing_status': status, 'all_entries_processed': not gap, 'stop_reason': stop,
            'coverage': {'requested': len(rows), 'actual': processed, 'gap': gap},
            'budget': {'limit': limits.request_budget, 'reserved': requests.reserved, 'dispatched': requests.dispatched}}
