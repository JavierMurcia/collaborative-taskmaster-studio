"""Closed vocabularies used by the domain."""

from enum import StrEnum


class ProjectState(StrEnum):
    IDEA = "idea"
    INTERVIEW = "entrevista"
    BRIEFING_PENDING = "briefing_pendiente"
    BRIEFING_CONFIRMED = "briefing_confirmado"
    DESIGN_IN_REVIEW = "diseno_en_revision"
    DESIGN_APPROVED = "diseno_aprobado"
    GENERATING = "generando"
    VALIDATING = "validando"
    READY_TO_EXPORT = "listo_para_exportar"
    EXPORTED = "exportado"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    DRAFT = "draft"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class AutonomyLevel(StrEnum):
    ASSIST = "assist"
    SUPERVISED = "supervised"
    BOUNDED_AUTONOMOUS = "bounded_autonomous"


class TestCategory(StrEnum):
    HAPPY_PATH = "happy_path"
    EDGE_CASE = "edge_case"
    FAILURE = "failure"
    SECURITY = "security"


class ErrorSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class AuditEventType(StrEnum):
    PROJECT_CREATED = "project_created"
    INTERVIEW_ANSWER_RECORDED = "interview_answer_recorded"
    INTERVIEW_ANSWER_CORRECTED = "interview_answer_corrected"
    BRIEFING_READY = "briefing_ready"
    BRIEFING_CONFIRMED = "briefing_confirmed"
    DESIGN_REQUESTED = "design_requested"
    STATE_TRANSITIONED = "state_transitioned"
    BRIEFING_UPDATED = "briefing_updated"
    REVISION_CREATED = "revision_created"
    REVISION_VALIDATION_FAILED = "revision_validation_failed"
    FEEDBACK_RECORDED = "feedback_recorded"
    REVISION_APPROVED = "revision_approved"
    REVISION_REJECTED = "revision_rejected"
    GENERATION_STARTED = "generation_started"
    ARTIFACT_GENERATED = "artifact_generated"
    GENERATION_FAILED = "generation_failed"
    EVALUATION_STARTED = "evaluation_started"
    SCENARIO_COMPLETED = "scenario_completed"
    EVALUATION_COMPLETED = "evaluation_completed"
    PROJECT_EXPORTED = "project_exported"
    MODEL_GENERATION_COMPLETED = "model_generation_completed"
    MODEL_FALLBACK_USED = "model_fallback_used"
    VALIDATION_COMPLETED = "validation_completed"
    AGENT_OUTPUT_DECIDED = "agent_output_decided"
