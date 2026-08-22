from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.models import AuditEvent, Briefing, Project


def test_project_and_briefing_defaults_are_safe() -> None:
    project = Project(id="meeting_planner", name="Meeting planner")
    assert project.state is ProjectState.IDEA
    assert project.briefing == Briefing()
    assert not project.briefing.confirmed


def test_domain_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Project(id="meeting_planner", name="Meeting planner", hidden=True)  # type: ignore[call-arg]


def test_audit_event_is_typed_and_timestamped() -> None:
    event = AuditEvent(
        id="event_created",
        project_id="meeting_planner",
        event_type=AuditEventType.PROJECT_CREATED,
        actor_id="user",
    )
    assert event.occurred_at <= datetime.now(UTC)
    assert event.details == {}
