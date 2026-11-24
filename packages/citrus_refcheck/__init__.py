"""Evidence-oriented reference lookup without a platform/agent dependency."""
__version__ = '1.2.0'
from .lookup import LookupLimits, FixtureTransport, HttpTransport, lookup_evidence
from .compare import hint_differences, identity_sufficient
from .inventory import extract_inventory
from .audit import AuditRepository, AuditStopped, BudgetExceeded, export_snapshot
from .providers import normalize_doi, norm_title
from .verification import BatchLimits, Capabilities, Observation, Query, verify_batch
from .report import report_view

__all__ = ['LookupLimits','FixtureTransport','HttpTransport','lookup_evidence',
           'hint_differences','identity_sufficient','extract_inventory','AuditRepository','AuditStopped','BudgetExceeded',
           'export_snapshot','normalize_doi','norm_title',
           'BatchLimits','Capabilities','Observation','Query','verify_batch','report_view']
