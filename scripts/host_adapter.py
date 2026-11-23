"""Example wiring for a host with authorized metadata, file and artifact capabilities."""
import json
from pathlib import Path
from threading import Lock

from citrus_refcheck import (BatchLimits, BudgetExceeded, Capabilities, Observation,
                             export_snapshot, extract_inventory, verify_batch)


def run_reference_verification(host, input_ref, *, limits=BatchLimits()):
    name, content = host.read_input(input_ref)
    inventory = extract_inventory(name=name, content=content, source_ref=input_ref)
    capabilities = Capabilities(check=host.check_network_and_cancel, debit=host.reserve_request,
                                lookup_doi=host.lookup_doi, search=host.search_metadata,
                                lookup_arxiv=getattr(host, 'lookup_arxiv', None), executor=getattr(host, 'executor', None))
    snapshot = verify_batch(inventory, capabilities=capabilities, limits=limits)
    artifact_ref = host.publish('reference-results.json', 'application/json', export_snapshot(snapshot))
    return snapshot, artifact_ref


class FixtureHost:
    def __init__(self):
        self.request_count = 0
        self.request_lock = Lock()
        self.artifacts = {}

    def read_input(self, ref):
        if ref != 'fixture:input':
            raise ValueError('unknown_file')
        return 'input.csv', 'title,authors\n合成参考条目,Example Author\n'.encode()

    def check_network_and_cancel(self):
        pass

    def reserve_request(self, query):
        with self.request_lock:
            if self.request_count >= 1:
                raise BudgetExceeded('request_budget_exhausted')
            self.request_count += 1

    lookup_doi = None

    def search_metadata(self, query, deadline):
        record = {'title': query.value, 'authors': query.authors, 'source_ref': 'fixture:metadata'}
        return Observation('Fixture catalog', 200, (record,), ('fixture:metadata',))

    def publish(self, name, mime_type, content):
        self.artifacts[name] = (mime_type, content)
        return f'fixture:{name}'


if __name__ == '__main__':
    skill = Path(__file__).resolve().parents[1] / 'SKILL.md'
    start, frontmatter, _body = skill.read_text(encoding='utf-8').split('---', 2)
    assert not start and 'name: reference-verification' in frontmatter.splitlines()
    host = FixtureHost()
    result, artifact = run_reference_verification(host, 'fixture:input', limits=BatchLimits(concurrency=1))
    print(json.dumps({'skill': 'reference-verification', 'match': result['entries'][0]['evidence']['match_status'],
                      'requests': host.request_count, 'artifact': artifact}))
