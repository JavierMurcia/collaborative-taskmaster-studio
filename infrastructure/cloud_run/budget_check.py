"""Machine-readable H10-12 budget plan or read-only verification."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.budget import plan_budget, verify_budget


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto")
    parser.add_argument(
        "--billing-account",
        required=True,
        help="ID de cuenta en formato 000000-000000-000000",
    )
    parser.add_argument("--gcloud", default="gcloud", help="Ruta a gcloud")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Consulta Cloud Billing; sin esta bandera solo imprime el plan.",
    )
    args = parser.parse_args(argv)
    result = (
        verify_budget(
            args.project,
            args.billing_account,
            gcloud=args.gcloud,
        )
        if args.verify
        else plan_budget(
            args.project,
            args.billing_account,
            gcloud=args.gcloud,
        )
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
