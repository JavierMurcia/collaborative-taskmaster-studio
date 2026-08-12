"""Validated in-memory evidence store for the Sentinel MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from simulator.state import Evidence


class MemoryDisposition(str, Enum):
    STORED = "stored"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class MemoryRecord:
    evidence: Evidence
    disposition: MemoryDisposition
    reason: str

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["disposition"] = self.disposition.value
        return data


class ValidatedMemory:
    """Stores only evidence that has a traceable, trusted origin."""

    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []

    def ingest(self, evidence: Evidence) -> MemoryRecord:
        if evidence.trust != "verified":
            record = MemoryRecord(evidence, MemoryDisposition.QUARANTINED, "Evidence source is not verified.")
        else:
            record = MemoryRecord(evidence, MemoryDisposition.STORED, "Evidence has verified provenance.")
        self.records.append(record)
        return record

    @property
    def stored(self) -> list[MemoryRecord]:
        return [record for record in self.records if record.disposition is MemoryDisposition.STORED]

    @property
    def quarantined(self) -> list[MemoryRecord]:
        return [record for record in self.records if record.disposition is MemoryDisposition.QUARANTINED]
