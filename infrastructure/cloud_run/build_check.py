"""Machine-readable H10-06 plan or read-only cloud verification."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.build import plan_build_pipeline, verify_build_pipeline


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="ID exacto del proyecto")
    parser.add_argument(
        "--image-tag",
        required=True,
        help="Etiqueta inmutable, por ejemplo git-a1b2c3d",
    )
    parser.add_argument("--gcloud", default="gcloud", help="Ruta a gcloud")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Consulta recursos cloud; sin esta bandera solo imprime el plan.",
    )
    args = parser.parse_args(argv)
    result = (
        verify_build_pipeline(
            args.project,
            args.image_tag,
            gcloud=args.gcloud,
        )
        if args.verify
        else plan_build_pipeline(
            args.project,
            args.image_tag,
            gcloud=args.gcloud,
        )
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
