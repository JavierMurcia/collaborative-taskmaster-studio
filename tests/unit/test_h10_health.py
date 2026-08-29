"""H10-03 health probes remain local, minimal, and fail closed."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness_only_reports_that_the_process_is_responding() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "alive",
        "service": "collaborative-taskmaster-studio",
    }


def test_startup_reports_completed_application_composition() -> None:
    response = client.get("/health/startup")

    assert response.status_code == 200
    assert response.json() == {
        "status": "started",
        "service": "collaborative-taskmaster-studio",
        "checks": {"application": "ready"},
    }


def test_readiness_reports_application_and_persistence_without_cloud_details() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "collaborative-taskmaster-studio",
        "checks": {
            "application": "ready",
            "persistence": "ready",
            "orchestration": "ready",
        },
    }
    serialized = response.text.casefold()
    assert "credential" not in serialized
    assert "vertex" not in serialized
    assert "firestore" not in serialized


def test_startup_fails_closed_when_composition_is_not_complete() -> None:
    previous = app.state.startup_complete
    app.state.startup_complete = False
    try:
        response = client.get("/health/startup")
    finally:
        app.state.startup_complete = previous

    assert response.status_code == 503
    assert response.json()["status"] == "not_started"
    assert response.json()["checks"] == {"application": "not_ready"}


def test_readiness_fails_closed_when_persistence_is_unavailable() -> None:
    previous = app.state.persistence_ready
    app.state.persistence_ready = False
    try:
        response = client.get("/health/ready")
    finally:
        app.state.persistence_ready = previous

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "collaborative-taskmaster-studio",
        "checks": {
            "application": "ready",
            "persistence": "not_ready",
            "orchestration": "ready",
        },
    }


def test_readiness_fails_closed_when_external_orchestration_is_unavailable() -> None:
    previous = app.state.orchestration_ready
    app.state.orchestration_ready = False
    try:
        response = client.get("/health/ready")
    finally:
        app.state.orchestration_ready = previous

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "application": "ready",
        "persistence": "ready",
        "orchestration": "not_ready",
    }
