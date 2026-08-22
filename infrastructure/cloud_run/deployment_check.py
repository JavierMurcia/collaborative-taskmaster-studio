"""Machine-readable H10-08 plan or read-only Cloud Run verification."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.deployment import plan_deployment, verify_deployment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto")
    parser.add_argument(
        "--image-digest",
        required=True,
        help="Digest SHA-256 sin el prefijo sha256:",
    )
    parser.add_argument("--gcloud", default="gcloud", help="Ruta a gcloud")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Consulta Cloud Run; sin esta bandera solo imprime el plan.",
    )
    args = parser.parse_args(argv)
    result = (
        verify_deployment(
            args.project,
            args.image_digest,
            gcloud=args.gcloud,
        )
        if args.verify
        else plan_deployment(
            args.project,
            args.image_digest,
            gcloud=args.gcloud,
        )
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
