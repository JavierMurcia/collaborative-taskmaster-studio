"""Machine-readable plan or read-only verification for runtime IAM bindings."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.iam import plan_runtime_iam, verify_runtime_iam


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto Google Cloud")
    parser.add_argument("--gcloud", default="gcloud", help="Ruta al ejecutable gcloud")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Consulta IAM. Sin esta bandera solo muestra el plan.",
    )
    args = parser.parse_args(argv)
    result = (
        verify_runtime_iam(args.project, gcloud=args.gcloud)
        if args.verify
        else plan_runtime_iam(args.project, gcloud=args.gcloud)
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
