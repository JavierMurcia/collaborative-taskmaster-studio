"""Safe H9-02 diagnostic; initializes a client but never queries Firestore."""

from __future__ import annotations

import json

from infrastructure.firestore import FirestoreSettings, initialize_firestore


def main() -> None:
    runtime = initialize_firestore(FirestoreSettings.from_environment())
    print(json.dumps(runtime.readiness.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
