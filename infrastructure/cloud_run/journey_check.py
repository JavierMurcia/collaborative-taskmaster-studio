"""Execute the H10-10 deployed end-to-end demo journey."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from infrastructure.cloud_run.journey import run_demo_journey


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="URL HTTPS exacta del servicio")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)
    result = run_demo_journey(args.url, timeout_seconds=args.timeout)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
