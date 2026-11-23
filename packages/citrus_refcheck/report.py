"""Human-readable and bibliographic projections of a saved snapshot."""
from html import escape
import re

from .compare import _value


def _cell(value):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return escape(text, quote=False).replace('|', '&#124;').replace('`', '&#96;').replace('[', '&#91;')


def report_markdown(snapshot):
    lines = ['| # | Original citation | Status | Matched metadata | Source | Differences |',
             '|---|---|---|---|---|---|']
    for entry in snapshot['entries']:
        result = entry['evidence']
        record = result['match'] or {}
        match = ' · '.join(str(record[key]) for key in ('title', 'authors', 'year', 'doi', 'arxiv_id') if record.get(key))
        sources = ', '.join(dict.fromkeys(source['source'] for source in result['sources']))
        if record.get('url'):
            sources = f"{sources} ({record['url']})"
        differences = ' · '.join(f"{item['field']} ({item['submitted']} → {item['registered']})"
                                 for item in result['differences'])
        cells = [str(entry['record_index'] + 1), _cell(entry['citation']),
                 _cell(f"{result['match_status']} ({result['reason']})"), _cell(match), _cell(sources), _cell(differences)]
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
