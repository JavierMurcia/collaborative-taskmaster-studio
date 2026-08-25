from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "collaborative-taskmaster-studio",
    }


def test_home_serves_the_chat_only_experience() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Collaborative Taskmaster Studio" in response.text
    assert "SOCIO COLABORATIVO" in response.text
    assert "Gemini 3.7 Flash" in response.text
    assert 'id="partner-chat-view"' in response.text
    assert 'id="studio-view"' not in response.text
    assert "Confirmar briefing" not in response.text
    assert "Generar proyecto ADK" not in response.text
    assert "Ejecutar 3 escenarios" not in response.text
    assert '/static/app.js?v=20260824-google-workspace' in response.text


def test_identity_uses_same_origin_server_oauth_instead_of_firebase_iframe() -> None:
    script = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert 'window.location.assign("/api/v1/collaborative/auth/google/start")' in script
    assert 'refreshIdentitySession()' in script
    assert '/api/v1/collaborative/auth/refresh' in script
    assert "firebase-auth.js" not in script
    assert "onAuthStateChanged" not in script
    assert "signInWithPopup" not in script


def test_meta_reports_h10_10_with_firestore_disabled() -> None:
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json()["stage"] == "H10-10"
    assert response.json()["cloud_run_identity"] == {
        "status": "declared",
        "account_id": "taskmaster-studio-runtime",
        "purpose": "cloud_run_runtime",
        "user_managed_keys_allowed": False,
        "cloud_verified": False,
        "roles_assigned": False,
    }
    assert response.json()["cloud_run_iam"] == {
        "status": "declared",
        "exact_project_roles": True,
        "roles": [
            "roles/aiplatform.user",
            "roles/datastore.user",
            "roles/secretmanager.secretAccessor",
        ],
        "firestore_database_scoped": True,
        "cloud_verified": False,
        "bindings_applied": False,
    }
    assert response.json()["build_pipeline"] == {
        "status": "declared",
        "region": "us-central1",
        "repository": "collaborative-taskmaster",
        "repository_format": "DOCKER",
        "immutable_tags": True,
        "builder_account_id": "taskmaster-studio-builder",
        "builder_roles": [
                "roles/artifactregistry.writer",
                "roles/logging.logWriter",
                "roles/storage.objectViewer",
        ],
        "cloudbuild_config_verification_phase": "pre_build",
        "cloud_verified": False,
        "resources_applied": False,
        "build_submitted": False,
    }
    assert response.json()["runtime_configuration"] == {
        "status": "declared",
        "environment_variable_count": 27,
        "secret_reference_count": 6,
        "secret_provider": "google_secret_manager",
        "plaintext_secrets_allowed": False,
        "latest_secret_alias_allowed": False,
        "runtime_secret_accessor_required": True,
        "repository_scan_required": True,
        "repository_scan_phase": "pre_build",
        "cloud_verified": False,
        "configuration_applied": False,
        "secret_payloads_created": False,
    }
    assert response.json()["cloud_run_deployment"] == {
        "status": "declared",
        "service": "collaborative-taskmaster-studio",
        "region": "us-central1",
        "image_digest_required": True,
        "scaling_scope": "service",
        "min_instances": 0,
        "max_instances": 1,
        "container_concurrency": 1,
        "revision_min_instances": None,
        "runtime_account_id": "taskmaster-studio-runtime",
        "public_access": True,
        "cloud_verified": False,
        "deployment_executed": False,
        "service_ready": False,
        "public_url": None,
    }
    assert response.json()["firestore_project_repository"] == {
        "implemented": True,
        "active": False,
        "collection": "projects",
        "subcollections": [
            "briefings",
            "revisions",
            "approvals",
            "events",
            "artifacts",
        ],
        "owner_scoped": True,
        "optimistic_concurrency": True,
        "immutable_revisions": True,
        "human_approval_records": True,
        "ordered_audit_events": True,
        "artifact_metadata_only": True,
        "critical_transactions": True,
        "transaction_max_attempts": 5,
    }
    assert response.json()["firestore_indexes"] == {
        "status": "ready",
        "manifest": "indexes.json",
        "required_queries": 1,
        "automatic_single_field_indexes": 1,
        "composite_indexes": 0,
        "cloud_applied": False,
    }
    assert response.json()["firestore_retention"] == {
        "status": "ready",
        "field": "expires_at",
        "retention_days": 7,
        "collection_groups": [
            "approvals",
            "artifacts",
            "briefings",
            "events",
            "projects",
            "revisions",
        ],
        "deletion_window": "typically_within_24_hours",
        "cascade_assumed": False,
        "cloud_applied": False,
    }
    assert response.json()["cloud_calls_enabled"] is False
    assert response.json()["vertex"]["status"] == "disabled"
    assert response.json()["vertex"]["cloud_calls_enabled"] is False
    assert response.json()["model_features"] == {
        "questions": False,
        "briefing": False,
        "specification": False,
        "revision": False,
    }
    assert response.json()["generator"] == "Google ADK templates 1.0.0"
    assert response.json()["laboratory"] == "sandbox local sin credenciales"
    assert response.json()["adk_root"] == {
        "entrypoint": "agents.agent:root_agent",
        "declared_tools": 0,
        "delegation_tools": 2,
        "loading": "lazy",
        "specialists": ["interviewer_agent", "designer_agent"],
    }
    assert response.json()["model_telemetry"] == {
        "outcomes": ["completed", "fallback"],
        "fields": [
            "provider",
            "model",
            "model_version",
            "location",
            "response_id",
            "latency_ms",
            "usage",
        ],
        "sensitive_content_recorded": False,
    }
    assert response.json()["model_limits"] == {
        "max_output_tokens": 8_192,
        "max_questions_per_project": 3,
        "question_limit_fallback": "local_catalog",
    }
    assert response.json()["local_fallback"] == {
        "mode": "deterministic",
        "strategies": [
            "local_catalog",
            "local_parser",
            "deterministic_designer",
            "deterministic_reviewer",
        ],
        "state_preserved": True,
        "audited": True,
    }
    assert response.json()["recorded_contracts"] == {
        "schema_version": "1.0.0",
        "recordings": 5,
        "sanitized": True,
        "live_calls": False,
    }
    assert response.json()["firestore_database"] == {
        "status": "disabled",
        "database_id": "collaborative-taskmaster",
        "location": "us-central1",
        "type": "FIRESTORE_NATIVE",
        "edition": "STANDARD",
        "delete_protection": True,
        "runtime_enabled": False,
        "database_verified": False,
        "repository_active": False,
        "cloud_calls_enabled": False,
        "credentials_source": "none",
    }


def test_canonical_schema_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "schemas" / "taskmaster-specification-1.0.0.json").is_file()
