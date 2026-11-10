"""Phase 1: model-independent evidence collection for a Hugging Face model repository.

    from m2g.evidence import collect_evidence
    bundle = collect_evidence(repo_info, fetcher, rea_report=repo_info.rea)
    bundle.to_json()

Collection only: no task interpretation, no capability classification, no code generation."""
from .collector import collect_evidence
from .context import CollectionContext, EvidenceLimits
from .provenance import Provenance
from .schema import COLLECTED, INACCESSIBLE, INVALID, MISSING, SCHEMA_VERSION, SKIPPED, STATUSES, EvidenceBundle, EvidenceItem
from .tensors import register_tensor_inspector

__all__ = ["collect_evidence", "CollectionContext", "EvidenceLimits", "Provenance", "EvidenceBundle", "EvidenceItem", "STATUSES",
           "COLLECTED", "MISSING", "INACCESSIBLE", "INVALID", "SKIPPED", "SCHEMA_VERSION", "register_tensor_inspector"]
