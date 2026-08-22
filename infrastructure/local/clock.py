"""Clock implementations for production-local and deterministic tests."""

from dataclasses import dataclass
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FrozenClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        self.current = value
