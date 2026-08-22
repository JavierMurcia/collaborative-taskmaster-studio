"""Declarative project-scoped Cloud Billing budget for H10-12."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DEFINITION_PATH = Path(__file__).with_name("budget.json")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
BILLING_ACCOUNT_PATTERN = re.compile(r"^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$")
EXPECTED_THRESHOLDS = ((0.5, "current-spend"), (0.8, "current-spend"), (1.0, "current-spend"))
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    units: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class ThresholdRule:
    percent: float
    basis: str


@dataclass(frozen=True, slots=True)
class BudgetDefinition:
    schema_version: str
    required_service: str
    display_name: str
    calendar_period: str
    amount: BudgetAmount
    credit_types_treatment: str
    ownership_scope: str
    project_scope: bool
    threshold_rules: tuple[ThresholdRule, ...]
    default_iam_recipients: bool
    project_owner_recipients: bool
    monitoring_notification_channels: tuple[str, ...]
    pubsub_topic: str | None
    automatic_spend_actions: bool


@dataclass(frozen=True, slots=True)
class BudgetResult:
    status: str
    project_id: str
    billing_account: str
    definition: BudgetDefinition
    prerequisite_commands: tuple[tuple[str, ...], ...]
    create_command: tuple[str, ...]
    verify_commands: tuple[tuple[str, ...], ...]
    cloud_verified: bool
    budget_created: bool
    budget_name: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "billing_account": self.billing_account,
            "definition": {
                "schema_version": self.definition.schema_version,
                "required_service": self.definition.required_service,
                "display_name": self.definition.display_name,
                "calendar_period": self.definition.calendar_period,
                "amount": {
                    "units": self.definition.amount.units,
                    "currency_code": self.definition.amount.currency_code,
                },
                "credit_types_treatment": self.definition.credit_types_treatment,
                "ownership_scope": self.definition.ownership_scope,
                "project_scope": self.definition.project_scope,
                "threshold_rules": [
                    {"percent": rule.percent, "basis": rule.basis}
                    for rule in self.definition.threshold_rules
                ],
                "default_iam_recipients": self.definition.default_iam_recipients,
                "project_owner_recipients": self.definition.project_owner_recipients,
                "monitoring_notification_channels": list(
                    self.definition.monitoring_notification_channels
                ),
                "pubsub_topic": self.definition.pubsub_topic,
                "automatic_spend_actions": self.definition.automatic_spend_actions,
            },
            "prerequisite_commands": [list(command) for command in self.prerequisite_commands],
            "create_command": list(self.create_command),
            "verify_commands": [list(command) for command in self.verify_commands],
            "cloud_verified": self.cloud_verified,
            "budget_created": self.budget_created,
            "budget_name": self.budget_name,
        }


def load_budget_definition(path: Path = DEFINITION_PATH) -> BudgetDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "required_service",
        "display_name",
        "calendar_period",
        "amount",
        "credit_types_treatment",
        "ownership_scope",
        "project_scope",
        "threshold_rules",
        "default_iam_recipients",
        "project_owner_recipients",
        "monitoring_notification_channels",
        "pubsub_topic",
        "automatic_spend_actions",
    }
    if set(payload) != expected:
        raise ValueError("La declaración de presupuesto contiene campos desconocidos o ausentes.")
    definition = BudgetDefinition(
        schema_version=str(payload["schema_version"]),
        required_service=str(payload["required_service"]),
        display_name=str(payload["display_name"]),
        calendar_period=str(payload["calendar_period"]),
        amount=BudgetAmount(**cast(dict[str, Any], payload["amount"])),
        credit_types_treatment=str(payload["credit_types_treatment"]),
        ownership_scope=str(payload["ownership_scope"]),
        project_scope=bool(payload["project_scope"]),
        threshold_rules=tuple(
            ThresholdRule(**item)
            for item in cast(list[dict[str, Any]], payload["threshold_rules"])
        ),
        default_iam_recipients=bool(payload["default_iam_recipients"]),
        project_owner_recipients=bool(payload["project_owner_recipients"]),
        monitoring_notification_channels=tuple(
            cast(list[str], payload["monitoring_notification_channels"])
        ),
        pubsub_topic=cast(str | None, payload["pubsub_topic"]),
        automatic_spend_actions=bool(payload["automatic_spend_actions"]),
    )
    _validate_definition(definition)
    return definition


def plan_budget(
    project_id: str,
    billing_account: str,
    *,
    gcloud: str = "gcloud",
) -> BudgetResult:
    return _result(
        "planned",
        project_id,
        billing_account,
        load_budget_definition(),
        gcloud=gcloud,
        cloud_verified=False,
        budget_name=None,
    )


def verify_budget(
    project_id: str,
    billing_account: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> BudgetResult:
    planned = _result(
        "verified",
        project_id,
        billing_account,
        load_budget_definition(),
        gcloud=gcloud,
        cloud_verified=True,
        budget_name=None,
    )
    outputs = [
        runner(command, check=True, capture_output=True, text=True)
        for command in planned.verify_commands
    ]
    _assert_api_enabled(json.loads(outputs[0].stdout or "[]"), planned.definition)
    _assert_project_billing(json.loads(outputs[1].stdout or "{}"), billing_account)
    budgets = cast(list[dict[str, Any]], json.loads(outputs[2].stdout or "[]"))
    matching = [
        budget
        for budget in budgets
        if budget.get("displayName") == planned.definition.display_name
    ]
    if len(matching) != 1:
        raise RuntimeError("Debe existir exactamente un presupuesto H10-12.")
    _assert_budget(matching[0], planned)
    return _result(
        "verified",
        project_id,
        billing_account,
        planned.definition,
        gcloud=gcloud,
        cloud_verified=True,
        budget_name=str(matching[0].get("name", "")),
    )


def _result(
    status: str,
    project_id: str,
    billing_account: str,
    definition: BudgetDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
    budget_name: str | None,
) -> BudgetResult:
    _validate_project_id(project_id)
    _validate_billing_account(billing_account)
    create = (
        gcloud,
        "billing",
        "budgets",
        "create",
        f"--billing-account={billing_account}",
        f"--display-name={definition.display_name}",
        f"--budget-amount={definition.amount.units}{definition.amount.currency_code}",
        f"--calendar-period={definition.calendar_period}",
        f"--filter-projects=projects/{project_id}",
        f"--credit-types-treatment={definition.credit_types_treatment}",
        f"--ownership-scope={definition.ownership_scope}",
        *(f"--threshold-rule=percent={rule.percent}" for rule in definition.threshold_rules),
        "--quiet",
    )
    return BudgetResult(
        status=status,
        project_id=project_id,
        billing_account=billing_account,
        definition=definition,
        prerequisite_commands=(
            (
                gcloud,
                "services",
                "enable",
                definition.required_service,
                f"--project={project_id}",
                "--quiet",
            ),
        ),
        create_command=create,
        verify_commands=(
            (
                gcloud,
                "services",
                "list",
                "--enabled",
                f"--project={project_id}",
                f"--filter=config.name={definition.required_service}",
                "--format=json",
            ),
            (
                gcloud,
                "billing",
                "projects",
                "describe",
                project_id,
                "--format=json",
            ),
            (
                gcloud,
                "billing",
                "budgets",
                "list",
                f"--billing-account={billing_account}",
                f"--filter=displayName={definition.display_name}",
                "--format=json",
            ),
        ),
        cloud_verified=cloud_verified,
        budget_created=False,
        budget_name=budget_name,
    )


def _assert_api_enabled(payload: object, definition: BudgetDefinition) -> None:
    services = cast(list[dict[str, Any]], payload)
    if not any(
        item.get("config", {}).get("name") == definition.required_service
        and item.get("state") == "ENABLED"
        for item in services
    ):
        raise RuntimeError("La API de presupuestos no está habilitada.")


def _assert_project_billing(payload: object, billing_account: str) -> None:
    project = cast(dict[str, Any], payload)
    if project.get("billingEnabled") is not True or project.get("billingAccountName") != (
        f"billingAccounts/{billing_account}"
    ):
        raise RuntimeError("El proyecto no utiliza la cuenta de facturación declarada.")


def _assert_budget(payload: dict[str, Any], result: BudgetResult) -> None:
    definition = result.definition
    budget_filter = cast(dict[str, Any], payload.get("budgetFilter", {}))
    amount = cast(dict[str, Any], payload.get("amount", {})).get("specifiedAmount", {})
    notifications = cast(dict[str, Any], payload.get("notificationsRule", {}))
    actual_thresholds = sorted(
        (
            float(rule.get("thresholdPercent", -1)),
            str(rule.get("spendBasis", "CURRENT_SPEND")).lower().replace("_", "-"),
        )
        for rule in cast(list[dict[str, Any]], payload.get("thresholdRules", []))
    )
    expected_thresholds = sorted(
        (rule.percent, rule.basis) for rule in definition.threshold_rules
    )
    expected = {
        "projects": [f"projects/{result.project_id}"],
        "calendarPeriod": "MONTH",
        "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
        "currencyCode": definition.amount.currency_code,
        "units": str(definition.amount.units),
        "ownershipScope": "ALL_USERS",
        "disableDefaultIamRecipients": False,
        "enableProjectLevelRecipients": True,
        "pubsubTopic": None,
    }
    actual = {
        "projects": budget_filter.get("projects"),
        "calendarPeriod": budget_filter.get("calendarPeriod"),
        "creditTypesTreatment": budget_filter.get("creditTypesTreatment"),
        "currencyCode": amount.get("currencyCode"),
        "units": str(amount.get("units", "")),
        "ownershipScope": payload.get("ownershipScope"),
        "disableDefaultIamRecipients": notifications.get(
            "disableDefaultIamRecipients", False
        ),
        "enableProjectLevelRecipients": notifications.get(
            "enableProjectLevelRecipients", False
        ),
        "pubsubTopic": notifications.get("pubsubTopic"),
    }
    drift = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if drift or actual_thresholds != expected_thresholds:
        raise RuntimeError("El presupuesto Cloud Billing diverge de H10-12.")


def _validate_definition(definition: BudgetDefinition) -> None:
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión del presupuesto no está soportada.")
    if (
        definition.required_service != "billingbudgets.googleapis.com"
        or definition.display_name != "sentinel-mvp-20k-cop"
        or definition.calendar_period != "month"
        or definition.amount != BudgetAmount(units=20_000, currency_code="COP")
        or definition.credit_types_treatment != "exclude-all-credits"
        or definition.ownership_scope != "all-users"
        or not definition.project_scope
    ):
        raise ValueError("El alcance o importe del presupuesto H10-12 no es válido.")
    thresholds = tuple((rule.percent, rule.basis) for rule in definition.threshold_rules)
    if thresholds != EXPECTED_THRESHOLDS:
        raise ValueError("Los umbrales del presupuesto H10-12 no son exactos.")
    if (
        not definition.default_iam_recipients
        or not definition.project_owner_recipients
        or definition.monitoring_notification_channels
        or definition.pubsub_topic is not None
        or definition.automatic_spend_actions
    ):
        raise ValueError("H10-12 exige alertas por correo sin automatizaciones de gasto.")


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto Google Cloud no es válido.")


def _validate_billing_account(billing_account: str) -> None:
    if not BILLING_ACCOUNT_PATTERN.fullmatch(billing_account):
        raise ValueError("El ID de la cuenta de facturación no es válido.")
