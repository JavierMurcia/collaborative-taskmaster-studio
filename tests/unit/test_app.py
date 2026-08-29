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
    assert "RADAR DE PROYECTOS · SOCIO COLABORATIVO" in response.text
    assert "¿Qué proyecto deberíamos construir?" in response.text
    assert "GitHub" in response.text
    assert "Google Drive" in response.text
    assert "Web verificada" in response.text
    assert "Gemini 3.7 Flash" in response.text
    assert 'id="partner-chat-view"' in response.text
    assert 'id="studio-view"' not in response.text
    assert "Confirmar briefing" not in response.text
    assert "Generar proyecto ADK" not in response.text
    assert "Ejecutar 3 escenarios" not in response.text
    assert 'id="taskmaster-studio-access"' in response.text
    assert "Taskmaster Studio" in response.text
    assert '/static/styles.css?v=20260827-full-connection-icons-v16' in response.text
    assert '/static/app.js?v=20260827-full-connection-icons-v16' in response.text
    assert 'id="partner-typing"' not in response.text
    assert "Gemini 3.7 Flash diseña · El Ingeniero construye con aprobación · Sin efectos externos" not in response.text
    assert 'id="conversation-title"' not in response.text
    assert 'class="chat-model-chip"' not in response.text
    assert "Ir al taller" in response.text
    assert response.text.index('id="agent-library-title"') < response.text.index('id="conversation-library-title"')
    assert 'class="builder-grid-preview"' not in response.text
    assert 'id="builder-live-board"' not in response.text
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


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


def test_taskmaster_chat_keeps_the_composer_docked_below_a_scrolling_transcript() -> None:
    root = Path(__file__).parents[2]
    script = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (root / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "conversation.scrollTo({ top: conversation.scrollHeight" in script
    assert 'scrollIntoView({ behavior: "smooth", block: "end" })' not in script
    assert "body.taskmaster-studio-mode.chat-active .partner-conversation" in styles
    assert "body.taskmaster-studio-mode.chat-active #partner-message-form" in styles
    assert "overflow-y:auto" in styles
    assert "flex:0 0 auto" in styles


def test_new_chat_reserves_an_independent_conversation_before_first_message() -> None:
    script = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    reset_start = script.index("function resetPartnerChat()")
    reset_end = script.index("function readPartnerConversations", reset_start)
    reset_implementation = script[reset_start:reset_end]

    assert "state.activeConversationId = newConversationId()" in reset_implementation
    assert "state.activeConversationId = null" not in reset_implementation


def test_first_message_transitions_before_it_is_sent() -> None:
    script = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    create_start = script.index("async function createProject(event)")
    create_end = script.index("async function sendPartnerMessage", create_start)
    create_implementation = script[create_start:create_end]

    assert "await transitionWelcomeToConversation()" in create_implementation
    assert create_implementation.index("await transitionWelcomeToConversation()") < create_implementation.index("await sendPartnerMessage(message)")
    assert 'chat.classList.add("chat-entering")' in script


def test_first_message_chat_rises_from_below_without_moving_entry_composer() -> None:
    styles = (Path(__file__).parents[2] / "app" / "static" / "styles.css").read_text(
        encoding="utf-8"
    )

    assert "@keyframes welcome-composer-out{to{opacity:0;filter:blur(2px)}}" in styles
    assert "@keyframes conversation-content-in{from{opacity:0;transform:translateY(54px)}" in styles
    assert "@keyframes composer-dock-in{from{opacity:0;transform:translateY(120px)" in styles
    assert "translateY(-28vh)" not in styles


def test_taskmaster_first_message_reuses_the_same_canvas_without_transition() -> None:
    root = Path(__file__).parents[2]
    script = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (root / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    transition_start = script.index("async function transitionWelcomeToConversation()")
    transition_end = script.index("async function sendPartnerMessage", transition_start)
    transition = script[transition_start:transition_end]
    assert 'if (state.entryMode === "builder")' in transition
    assert transition.index('if (state.entryMode === "builder")') < transition.index('document.body.classList.add("chat-transitioning")')
    assert "body.taskmaster-studio-mode #main-content{" in styles
    assert "body.taskmaster-studio-mode #welcome-view{" in styles
    assert "background-image:none" in styles


def test_first_builder_response_clears_stale_markup_and_reveals_the_real_draft() -> None:
    root = Path(__file__).parents[2]
    script = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (root / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    transition_start = script.index("async function transitionWelcomeToConversation()")
    transition_end = script.index("async function sendPartnerMessage", transition_start)
    transition = script[transition_start:transition_end]
    assert transition.index("renderPartnerConversation()") < transition.index("showPartnerChat()")
    assert "revealResponse: true" in script
    assert 'item.revealResponse ? " response-arrival" : ""' in script
    assert "if (item.revealResponse) item.revealResponse = false" in script
    assert ".assistant-turn.response-arrival{animation:assistant-response-in" in styles
    assert ".assistant-turn.response-arrival .agent-draft-card{animation:draft-response-in" in styles


def test_taskmaster_conversation_stays_on_the_grid_without_status_board() -> None:
    root = Path(__file__).parents[2]
    script = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (root / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'const builderCanvas = $("#main-content")' in script
    assert "renderBuilderBoard" not in script
    assert ".builder-live-board" not in styles
    assert "body.taskmaster-studio-mode.chat-active #main-content" in styles
    assert "body.taskmaster-studio-mode.chat-active .partner-chat-view:not([hidden])" in styles
    assert "grid-template-columns:minmax(24px,1fr) minmax(0,872px) minmax(24px,1fr)" in styles
    assert "body.taskmaster-studio-mode.chat-active .partner-conversation>.partner-turn" in styles


def test_chat_keeps_the_draft_approval_footer_visible_and_names_the_builder() -> None:
    root = Path(__file__).parents[2]
    script = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (root / "app" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "body.chat-active>footer{display:none}" in styles
    assert "body.chat-active footer{display:none}" not in styles
    assert 'state.buildRuntime = payload.build_orchestration?.runtime || ""' in script
    assert "Constructor: Antigravity SDK" in script
    assert "Aprobar diseño y construir en laboratorio" in script


def test_startup_keeps_the_entry_chat_instead_of_opening_latest_history() -> None:
    script = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    state_declaration = script[script.index("const state = "):script.index("const buildPollers")]
    memory_start = script.index("async function loadConversationMemory()")
    memory_end = script.index("function renderConversationHistory", memory_start)
    memory_implementation = script[memory_start:memory_end]

    assert "activeConversationId: null" in state_declaration
    assert "partnerMessages: []" in state_declaration
    assert "if (!state.activeConversationId && state.partnerConversations.length)" not in memory_implementation


def test_brand_button_returns_home_without_creating_a_conversation() -> None:
    script = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    home_start = script.index("function openChatHome()")
    home_end = script.index("function enableComposerKeyboard", home_start)
    home_implementation = script[home_start:home_end]

    assert "state.activeConversationId = null" in home_implementation
    assert "showWelcome()" in home_implementation
    assert "resetPartnerChat()" not in home_implementation
    assert "newConversationId()" not in home_implementation
    assert '$("#home-button").addEventListener("click", openChatHome)' in script


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
                "roles/storage.objectUser",
                "roles/cloudtasks.enqueuer",
                "roles/secretmanager.secretAccessor",
            ],
            "firestore_database_scoped": True,
            "storage_bucket_scoped": True,
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
        "environment_variable_count": 35,
        "secret_reference_count": 8,
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
