"""Bounded Firestore transaction execution for critical repository mutations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from studio.domain.errors import DomainError

ResultT = TypeVar("ResultT")


class TransactionRetryExhaustedError(RuntimeError):
    """Signal that the SDK consumed every retry after aborted commits."""


class Transaction(Protocol):
    def create(self, reference: object, document_data: dict[str, Any]) -> object: ...

    def update(
        self,
        reference: object,
        field_updates: dict[str, Any],
        option: object | None = None,
    ) -> object: ...


class TransactionClient(Protocol):
    def transaction(self, *, max_attempts: int = 5) -> object: ...


class TransactionExecutor(Protocol):
    max_attempts: int

    def execute(
        self,
        client: TransactionClient,
        operation: Callable[[Transaction], ResultT],
    ) -> ResultT: ...


@dataclass(frozen=True, slots=True)
class FirestoreTransactionExecutor:
    """Run the SDK transaction decorator with a finite retry budget."""

    max_attempts: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise DomainError(
                "FIRESTORE_TRANSACTION_ATTEMPTS_INVALID",
                "Los intentos de transacción Firestore deben estar entre 1 y 10.",
                context={"min": 1, "max": 10},
            )

    def execute(
        self,
        client: TransactionClient,
        operation: Callable[[Transaction], ResultT],
    ) -> ResultT:
        from google.cloud import firestore

        transaction = client.transaction(max_attempts=self.max_attempts)
        wrapped = firestore.transactional(operation)
        try:
            return cast(ResultT, wrapped(cast(Any, transaction)))
        except ValueError as error:
            cause = error.__cause__
            if cause is not None and cause.__class__.__name__ == "Aborted":
                raise TransactionRetryExhaustedError from error
            raise
