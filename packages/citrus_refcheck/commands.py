"""CLI commands for one-shot verification and export from saved results."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .audit import export_snapshot
from .inventory import extract_inventory
from .standalone import registry_capabilities
from .verification import BatchLimits, verify_batch


def add_verification_commands(subparsers):
    verify = subparsers.add_parser('verify', help='Verify an inventory once')
    verify.add_argument('input', type=Path)
    verify.add_argument('--output', type=Path)
    verify.add_argument('--encoding', default='')
    verify.add_argument('--column-map', type=json.loads)
    transport = verify.add_mutually_exclusive_group(required=True)
    transport.add_argument('--allow-network', action='store_true')
    transport.add_argument('--fixture', type=Path)
    verify.add_argument('--doi-registry', choices=['Crossref', 'DataCite'], default='Crossref')
    verify.add_argument('--concurrency', type=int, default=4)
    verify.add_argument('--request-budget', type=int, default=20)
    verify.add_argument('--timeout', type=float, default=30)
    verify.add_argument('--item-timeout', type=float, default=8)
    verify.add_argument('--rate-limit-retries', type=int, default=0)
    verify.set_defaults(handler=_verify)

    render = subparsers.add_parser('export-snapshot', help='Render a saved result offline')
    render.add_argument('snapshot', type=Path)
    render.add_argument('--format', choices=['json', 'xlsx', 'ris', 'bibtex', 'markdown'], required=True)
    render.add_argument('--output', type=Path, required=True)
    render.set_defaults(handler=_export)


def _load_inventory(args):
    content = args.input.read_bytes()
    if args.input.suffix.lower() == '.json':
        if args.encoding or args.column_map:
            raise ValueError('column_mapping_requires_delimited_input')
        payload = json.loads(content)
        if isinstance(payload, list):
            return extract_inventory(name=args.input.name, references=payload)
        return payload
    return extract_inventory(name=args.input.name, content=content, encoding=args.encoding, column_map=args.column_map)


def _save(path, content):
    with NamedTemporaryFile(dir=path.parent) as output:
        output.write(content)
        output.flush()
        os.link(output.name, path)


def _verify(args):
    inventory = _load_inventory(args)
    limits = BatchLimits(concurrency=args.concurrency, request_budget=args.request_budget, timeout=args.timeout,
                         item_timeout=args.item_timeout, rate_limit_retries=args.rate_limit_retries)
    if args.output and args.output.exists():
        raise FileExistsError(args.output)
    if args.fixture:
        capabilities = registry_capabilities(fixture=json.loads(args.fixture.read_bytes()), doi_registry=args.doi_registry)
        result = verify_batch(inventory, capabilities=capabilities, limits=limits)
    else:
        import httpx
        with httpx.Client(trust_env=False) as client:
            capabilities = registry_capabilities(client=client, doi_registry=args.doi_registry)
            result = verify_batch(inventory, capabilities=capabilities, limits=limits)
    if args.output:
        _save(args.output, export_snapshot(result))
    return result


def _export(args):
    snapshot = json.loads(args.snapshot.read_bytes())
    _save(args.output, export_snapshot(snapshot, format=args.format))
    return {'status': 'saved', 'output': str(args.output), 'coverage': snapshot['coverage']}
