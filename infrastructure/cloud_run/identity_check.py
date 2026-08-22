"""Machine-readable plan or read-only verification for the Cloud Run identity."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.identity import (
    plan_runtime_identity,
    verify_runtime_identity,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto Google Cloud")
    parser.add_argument("--gcloud", default="gcloud", help="Ruta al ejecutable gcloud")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Consulta la cuenta y sus claves. Sin esta bandera solo muestra el plan.",
    )
    args = parser.parse_args(argv)
    result = (
        verify_runtime_identity(args.project, gcloud=args.gcloud)
        if args.verify
        else plan_runtime_identity(args.project, gcloud=args.gcloud)
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
