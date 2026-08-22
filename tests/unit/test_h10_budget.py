"""H10-12 project budget and alert declaration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from infrastructure.cloud_run.budget import (
    load_budget_definition,
    plan_budget,
    verify_budget,
)
from infrastructure.cloud_run.budget_check import main

PROJECT_ID = "sentinel-taskmaster-dev"
BILLING_ACCOUNT = "ABCDEF-123456-7890AB"


class FakeGcloud:
    def __init__(
        self,
        *,
        services: object | None = None,
        project: object | None = None,
        budgets: object | None = None,
    ) -> None:
        self.responses = [
            services if services is not None else _services(),
            project if project is not None else _project(),
            budgets if budgets is not None else [_budget()],
        ]
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        call = tuple(command)
        self.calls.append(call)
        assert check and capture_output and text
        return subprocess.CompletedProcess(
            call,
            0,
            stdout=json.dumps(self.responses[len(self.calls) - 1]),
            stderr="",
        )


def _services() -> list[dict[str, object]]:
    return [
        {
            "config": {"name": "billingbudgets.googleapis.com"},
            "state": "ENABLED",
        }
    ]


def _project() -> dict[str, object]:
    return {
        "billingAccountName": f"billingAccounts/{BILLING_ACCOUNT}",
        "billingEnabled": True,
        "name": PROJECT_ID,
        "projectId": PROJECT_ID,
    }


def _budget() -> dict[str, object]:
    return {
        "name": f"billingAccounts/{BILLING_ACCOUNT}/budgets/budget-123",
        "displayName": "sentinel-mvp-20k-cop",
        "budgetFilter": {
            "projects": [f"projects/{PROJECT_ID}"],
            "calendarPeriod": "MONTH",
            "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
        },
        "amount": {
            "specifiedAmount": {"currencyCode": "COP", "units": "20000"}
        },
        "thresholdRules": [
            {"thresholdPercent": 0.5, "spendBasis": "CURRENT_SPEND"},
            {"thresholdPercent": 0.8, "spendBasis": "CURRENT_SPEND"},
            {"thresholdPercent": 1.0, "spendBasis": "CURRENT_SPEND"},
        ],
        "notificationsRule": {
            "disableDefaultIamRecipients": False,
            "enableProjectLevelRecipients": True,
        },
        "ownershipScope": "ALL_USERS",
    }


def test_definition_is_project_scoped_conservative_and_non_automatic() -> None:
    definition = load_budget_definition()

    assert definition.amount.units == 20_000
    assert definition.amount.currency_code == "COP"
    assert definition.calendar_period == "month"
    assert definition.project_scope is True
    assert [(rule.percent, rule.basis) for rule in definition.threshold_rules] == [
        (0.5, "current-spend"),
        (0.8, "current-spend"),
        (1.0, "current-spend"),
    ]
    assert definition.default_iam_recipients is True
    assert definition.project_owner_recipients is True
    assert definition.pubsub_topic is None
    assert definition.automatic_spend_actions is False


def test_plan_is_offline_and_never_claims_budget_creation() -> None:
    result = plan_budget(PROJECT_ID, BILLING_ACCOUNT)

    assert result.status == "planned"
    assert result.cloud_verified is False
    assert result.budget_created is False
    assert result.budget_name is None


def test_create_command_has_exact_scope_amount_and_thresholds() -> None:
    command = plan_budget(PROJECT_ID, BILLING_ACCOUNT).create_command

    assert command[:4] == ("gcloud", "billing", "budgets", "create")
    assert f"--billing-account={BILLING_ACCOUNT}" in command
    assert "--budget-amount=20000COP" in command
    assert "--calendar-period=month" in command
    assert f"--filter-projects=projects/{PROJECT_ID}" in command
    assert "--credit-types-treatment=exclude-all-credits" in command
    assert "--ownership-scope=all-users" in command
    assert [item for item in command if item.startswith("--threshold-rule=")] == [
        "--threshold-rule=percent=0.5",
        "--threshold-rule=percent=0.8",
        "--threshold-rule=percent=1.0",
    ]
    serialized = " ".join(command).lower()
    assert "pubsub" not in serialized
    assert "disable-default-iam-recipients" not in serialized


def test_verify_accepts_exact_budget_and_is_read_only() -> None:
    fake = FakeGcloud()
    result = verify_budget(PROJECT_ID, BILLING_ACCOUNT, runner=fake)

    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.budget_created is False
    assert result.budget_name == (
        f"billingAccounts/{BILLING_ACCOUNT}/budgets/budget-123"
    )
    assert len(fake.calls) == 3
    assert not any("create" in call for call in fake.calls)


def test_verify_requires_exactly_one_named_budget() -> None:
    with pytest.raises(RuntimeError, match="exactamente un presupuesto"):
        verify_budget(PROJECT_ID, BILLING_ACCOUNT, runner=FakeGcloud(budgets=[]))


def test_verify_rejects_unscoped_or_drifted_budget() -> None:
    budget = _budget()
    budget["budgetFilter"] = {
        "projects": [],
        "calendarPeriod": "MONTH",
        "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
    }

    with pytest.raises(RuntimeError, match="diverge"):
        verify_budget(
            PROJECT_ID,
            BILLING_ACCOUNT,
            runner=FakeGcloud(budgets=[budget]),
        )


def test_verify_rejects_wrong_billing_account() -> None:
    project = _project()
    project["billingAccountName"] = "billingAccounts/000000-000000-000000"

    with pytest.raises(RuntimeError, match="cuenta de facturación"):
        verify_budget(
            PROJECT_ID,
            BILLING_ACCOUNT,
            runner=FakeGcloud(project=project),
        )


def test_offline_cli_emits_machine_readable_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--project", PROJECT_ID, "--billing-account", BILLING_ACCOUNT]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["definition"]["amount"] == {
        "units": 20_000,
        "currency_code": "COP",
    }
    assert payload["cloud_verified"] is False
    assert payload["budget_created"] is False


@pytest.mark.parametrize(
    ("project_id", "billing_account"),
    [
        ("INVALID", BILLING_ACCOUNT),
        (PROJECT_ID, ""),
        (PROJECT_ID, "123"),
        (PROJECT_ID, "abcdef-123456-7890ab"),
    ],
)
def test_invalid_identifiers_are_rejected_before_cloud_calls(
    project_id: str,
    billing_account: str,
) -> None:
    fake = FakeGcloud()
    with pytest.raises(ValueError):
        verify_budget(project_id, billing_account, runner=fake)
    assert fake.calls == []


def test_definition_file_is_versioned() -> None:
    payload = json.loads(
        Path("infrastructure/cloud_run/budget.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == "1.0.0"


def test_evidence_records_verified_controls_without_billing_identifier() -> None:
    payload = json.loads(
        Path("infrastructure/cloud_run/budget-evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["milestone"] == "H10-12"
    assert payload["result"] == "passed"
    assert payload["project_scope_verified"] is True
    assert payload["thresholds_percent"] == [50, 80, 100]
    assert payload["automatic_spend_limit"] is False
    assert payload["billing_account_identifier_stored"] is False
    assert "billing_account" not in payload
