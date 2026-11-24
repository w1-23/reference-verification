"""Compact, offline projections of a saved reference-verification snapshot."""
from html import escape
import re

from .compare import _value

_REPORT_SCHEMA = 'reference-verification-report/1'
_IDENTITY = {'matching': 'matched', 'conflict': 'conflict', 'unconfirmed': 'unresolved',
             'unprocessed': 'unprocessed'}


def _cell(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return escape(text, quote=False).replace('|', '&#124;').replace('`', '&#96;').replace('[', '&#91;')


def _short_citation(value, limit):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + '…'


def _compact_match(record, fallback_source=''):
    if not isinstance(record, dict):
        return None
    fields = ('title', 'authors', 'year', 'doi', 'arxiv_id', 'journal', 'volume', 'issue', 'pages',
              'article_number', 'source', 'url')
    match = {field: _short_citation(record[field], 300) for field in fields
             if record.get(field) not in (None, '')}
    if not match.get('source') and fallback_source:
        match['source'] = fallback_source
    return match or None


def _field_notes(result, source):
    notes = []
    for difference in result.get('differences') or ():
        if not isinstance(difference, dict):
            continue
        notes.append({'field': str(difference.get('field') or ''),
                      'submitted': _short_citation(difference.get('submitted'), 500),
                      'registered': _short_citation(difference.get('registered'), 500),
                      'source': _short_citation(source, 300),
                      'reason': str(difference.get('reason') or '')})
    return notes


def report_view(snapshot, *, citation_limit=240):
    """Project a saved snapshot into the compact host/model report.

    The kernel's snapshot remains authoritative.  This function performs no
    lookup and mutates nothing.  Processing and identity stay on separate axes;
    ``report_ready`` says the current snapshot can be shown, while
    ``coverage_complete`` says every submitted row actually received an
    observation.  Additional checks after any batch require a new explicit user
    request.
    """
    if type(citation_limit) is not int or citation_limit < 32:
        raise ValueError('invalid_citation_limit')
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != 'reference-verification/1':
        raise ValueError('invalid_verification_snapshot')
    entries = snapshot.get('entries')
    if not isinstance(entries, list):
        raise ValueError('invalid_verification_snapshot')

    rows = []
    for fallback_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get('claims'), dict):
            raise ValueError('invalid_verification_snapshot')
        result = entry.get('evidence')
        if not isinstance(result, dict):
            result = {'processing_status': 'unprocessed', 'match_status': 'unprocessed',
                      'reason': 'not_started', 'match': None, 'sources': [], 'differences': []}
        processing = result.get('processing_status')
        if processing not in {'processed', 'unprocessed'}:
            processing = 'unprocessed'
        match_status = result.get('match_status') if processing == 'processed' else 'unprocessed'
        identity = _IDENTITY.get(match_status, 'unresolved')
        sources = result.get('sources') if isinstance(result.get('sources'), list) else []
        source = ''
        if sources and isinstance(sources[0], dict):
            source = str(sources[0].get('source') or '')
        record = _compact_match(result.get('match'), source)
        if record and record.get('source'):
            source = record['source']
        record_index = entry.get('record_index', fallback_index)
        rows.append({'record_id': str(entry.get('record_id') or ''),
                     'record_index': record_index,
                     'ordinal': fallback_index + 1,
                     'citation': _short_citation(entry.get('citation'), citation_limit),
                     'processing_status': processing,
                     'identity': identity,
                     'reason': str(result.get('reason') or ''),
                     'match': record,
                     'field_notes': _field_notes(result, source)})

    processed = sum(row['processing_status'] == 'processed' for row in rows)
    coverage = snapshot.get('coverage')
    if not isinstance(coverage, dict):
        coverage = {'requested': len(rows), 'actual': processed, 'gap': len(rows) - processed}
    batch_status = snapshot.get('batch_processing_status')
    all_processed = snapshot.get('all_entries_processed') is True
    stop_reason = str(snapshot.get('stop_reason') or '')
    explicit_terminal_stop = batch_status in {'partial', 'cancelled'} and (
        bool(stop_reason) or all(bool(row['reason']) for row in rows))
    report_ready = bool(rows) and (all_processed or explicit_terminal_stop)
    return {'schema_version': _REPORT_SCHEMA,
            'source_schema_version': snapshot['schema_version'],
            'batch_finished': True,
            'snapshot_only': True,
            'report_ready': report_ready,
            'coverage_complete': all_processed,
            'batch_processing_status': batch_status,
            'stop_reason': stop_reason,
            'coverage': {'requested': int(coverage.get('requested', len(rows))),
                         'actual': int(coverage.get('actual', processed)),
                         'gap': int(coverage.get('gap', len(rows) - processed))},
            'unprocessed_ordinals': [row['ordinal'] for row in rows
                                     if row['processing_status'] == 'unprocessed'],
            'additional_checks_require_user_request': True,
            'rows': rows}


def _status_text(row):
    status = f"{row['processing_status']} · {row['identity']}"
    if row['reason'] and (row['identity'] != 'matched' or row['processing_status'] == 'unprocessed'):
        status += f" ({row['reason']})"
    return status


def _matched_text(row):
    match = row.get('match') or {}
    values = [match.get(field) for field in ('title', 'authors', 'year', 'doi', 'arxiv_id', 'journal')]
    text = ' · '.join(value for value in values if value)
    if match.get('source'):
        text = f"{text} · {match['source']}" if text else match['source']
    if match.get('url'):
        text = f"{text} ({match['url']})" if text else match['url']
    return text


def _notes_text(row):
    return ' · '.join(
        f"{note['field']}: {note['submitted']} → {note['registered']}"
        + (f" [{note['source']}]" if note.get('source') else '')
        for note in row['field_notes'])


def report_markdown(snapshot):
    view = report_view(snapshot)
    lines = ['| # | Original citation | Identity | Matched work / source | Necessary differences |',
             '|---|---|---|---|---|']
    for row in view['rows']:
        cells = [str(row['ordinal']), _cell(row['citation']), _cell(_status_text(row)),
                 _cell(_matched_text(row)), _cell(_notes_text(row))]
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n'


def bibliography_projection(entries, format):
    def clean(value):
        return re.sub(r'[\x00-\x1f]+', ' ', str(value))

    def bib(value):
        return clean(value).replace('\\', '\\textbackslash{}').replace('{', '\\{').replace('}', '\\}')

    fields = ('title', 'authors', 'year', 'journal', 'volume', 'issue', 'pages', 'doi')
    parts = []
    for index, entry in enumerate(entries, 1):
        claims = entry['claims']
        result = entry['evidence']
        record_id = entry['record_id']
        note = f"record_id={record_id}, status={result['match_status']}, full evidence in JSON/XLSX"
        values = [_value(claims, field) for field in fields]
        if format == 'ris':
            tags = ('TI', 'AU', 'PY', 'JO', 'VL', 'IS', 'SP', 'DO')
            lines = [f'{tag}  - {clean(value)}' for tag, value in zip(tags, values) if value]
            parts.append('\n'.join(['TY  - GEN', *lines, f'N1  - {clean(note)}', 'ER  - ', '']))
        else:
            tags = ('title', 'author', 'year', 'journal', 'volume', 'number', 'pages', 'doi')
            lines = [f'  {tag} = {{{bib(value)}}}' for tag, value in zip(tags, values) if value]
            body = ',\n'.join([*lines, f'  note = {{{bib(note)}}}'])
            parts.append(f'@misc{{ref_{index},\n{body}\n}}\n')
    return '\n'.join(parts).encode('utf-8')
