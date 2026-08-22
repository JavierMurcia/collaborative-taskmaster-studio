"""Run the H10-09 controlled Cloud Run smoke journey."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.smoke import run_smoke


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="URL HTTPS exacta del servicio")
    parser.add_argument(
        "--functional",
        action="store_true",
        help="Crea y vuelve a leer un proyecto aislado en la persistencia desplegada.",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    result = run_smoke(
        args.url,
        functional=args.functional,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
