"""Standalone Antigravity worker executed in its own dependency environment."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from adapters.antigravity.builder import _ConfinedWorkspace, orchestrate_workspace


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    request_path = Path(sys.argv[1]).resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        workspace_root = Path(request["workspace"]).resolve()
        specification = request["specification"]
        contract = request["contract"]
        if request_path.parent != workspace_root / ".studio":
            return 3
        if not isinstance(specification, dict) or not isinstance(contract, dict):
            return 4
        workspace = _ConfinedWorkspace(workspace_root)
        summary = asyncio.run(orchestrate_workspace(workspace, specification, contract))
        if not workspace.operations:
            return 5
        evidence_path = workspace_root / ".studio" / "antigravity-orchestration.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "runtime": "antigravity_sdk",
                    "summary": summary[:4_000],
                    "operations": workspace.operations,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
