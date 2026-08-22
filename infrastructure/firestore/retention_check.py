"""Offline, machine-readable check for the H9-09 retention declaration."""

from __future__ import annotations

import json

from infrastructure.firestore.config import FirestoreSettings
from infrastructure.firestore.retention import (
    DemoRetentionPolicy,
    verify_retention_manifest,
)


def main() -> None:
    settings = FirestoreSettings.from_environment()
    policy = DemoRetentionPolicy(settings.demo_retention_days)
    print(json.dumps(verify_retention_manifest(policy).as_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
