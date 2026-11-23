"""Explicit offline fixtures or metadata requests to selected public registries."""
from urllib.parse import quote

from . import providers
from .lookup import FixtureTransport, HttpTransport
from .verification import Capabilities, Observation


def _noop(*_args):
    pass


class _RegistryLookup:
    def __init__(self, transport_factory, *, doi_registry):
        self.transport_factory = transport_factory
        self.doi_registry = doi_registry

    def __call__(self, query, deadline):
        transport = self.transport_factory(deadline)
        with providers.using_transport(transport):
            if query.kind == 'search':
                source = 'Crossref'
                search_terms = ' '.join(filter(None, (query.value, query.authors)))
                status, records, error = providers._crossref_search(search_terms)
            elif query.kind == 'arxiv':
                source = 'arXiv'
                status, records, error = providers._arxiv_by_id(query.value)
            else:
                source = self.doi_registry
                lookup = providers._crossref_work if source == 'Crossref' else providers._datacite_work
                status, records, error = lookup(quote(query.value, safe='/'))
        return Observation(source, status if type(status) is int else 0, tuple(records), error=error,
                           retry_after=getattr(transport, 'retry_after', None))


def registry_capabilities(*, fixture=None, client=None, doi_registry='Crossref', check=_noop, debit=_noop):
    """A fixture mapping or a caller-owned HTTP client is required explicitly."""
    if (fixture is None) == (client is None) or doi_registry not in {'Crossref', 'DataCite'}:
        raise ValueError('specify_one_transport_and_a_known_registry')
    if fixture is not None:
        def transport_factory(_deadline):
            return FixtureTransport(fixture)
    else:
        def transport_factory(deadline):
            return HttpTransport(client=client, deadline=deadline, check=check)

    doi = _RegistryLookup(transport_factory, doi_registry=doi_registry)
    search = doi if doi_registry == 'Crossref' else _RegistryLookup(transport_factory, doi_registry='Crossref')
    arxiv = _RegistryLookup(transport_factory, doi_registry=doi_registry)
    return Capabilities(check, debit, lookup_doi=doi, search=search, lookup_arxiv=arxiv)
