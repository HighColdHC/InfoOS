"""Pure schemas and validation for durable intelligence outputs."""

from backend.domain.outputs.types import ContentPlanBody, DecisionBriefBody, EvidenceRef, OpportunityCardBody
from backend.domain.outputs.validation import output_body_schema_info, output_validation_errors, validate_output_body

__all__ = [
    "ContentPlanBody",
    "DecisionBriefBody",
    "EvidenceRef",
    "OpportunityCardBody",
    "output_body_schema_info",
    "output_validation_errors",
    "validate_output_body",
]
