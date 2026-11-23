"""Evidence-oriented reference lookup without a platform/agent dependency."""
__version__ = '1.0.0'
from .lookup import LookupLimits, FixtureTransport, HttpTransport, lookup_evidence
from .compare import hint_differences
from .inventory import extract_inventory
from .audit import AuditRepository, AuditStopped, BudgetExceeded, export_snapshot
from .providers import normalize_doi, norm_title
from .verification import BatchLimits, Capabilities, Observation, Query, verify_batch

__all__ = ['LookupLimits','FixtureTransport','HttpTransport','lookup_evidence',
           'hint_differences','extract_inventory','AuditRepository','AuditStopped','BudgetExceeded',
           'export_snapshot','normalize_doi','norm_title',
           'BatchLimits','Capabilities','Observation','Query','verify_batch']
