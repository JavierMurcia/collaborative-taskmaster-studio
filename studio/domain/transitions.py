"""The only legal project state transitions."""

from studio.domain.enums import ProjectState
from studio.domain.errors import InvalidTransitionError
from studio.domain.models import Project, utc_now

ALLOWED_TRANSITIONS: dict[ProjectState, frozenset[ProjectState]] = {
    ProjectState.IDEA: frozenset({ProjectState.INTERVIEW}),
    ProjectState.INTERVIEW: frozenset({ProjectState.BRIEFING_PENDING}),
    ProjectState.BRIEFING_PENDING: frozenset(
        {ProjectState.INTERVIEW, ProjectState.BRIEFING_CONFIRMED}
    ),
    ProjectState.BRIEFING_CONFIRMED: frozenset({ProjectState.DESIGN_IN_REVIEW}),
    ProjectState.DESIGN_IN_REVIEW: frozenset(
        {ProjectState.DESIGN_IN_REVIEW, ProjectState.DESIGN_APPROVED}
    ),
    ProjectState.DESIGN_APPROVED: frozenset({ProjectState.GENERATING}),
    ProjectState.GENERATING: frozenset({ProjectState.VALIDATING, ProjectState.DESIGN_APPROVED}),
    ProjectState.VALIDATING: frozenset(
        {ProjectState.READY_TO_EXPORT, ProjectState.DESIGN_IN_REVIEW}
    ),
    ProjectState.READY_TO_EXPORT: frozenset({ProjectState.EXPORTED}),
    ProjectState.EXPORTED: frozenset(),
}


def transition_project(project: Project, target: ProjectState) -> Project:
    """Return a new project in the target state or reject the transition."""
    if target not in ALLOWED_TRANSITIONS[project.state]:
        raise InvalidTransitionError(project.state.value, target.value)
    return project.model_copy(update={"state": target, "updated_at": utc_now()}, deep=True)
