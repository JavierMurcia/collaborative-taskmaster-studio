"""Credential-free deterministic builder entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from adapters.frameworks import (
    AntigravityGenerator,
    FrameworkGeneratorRegistry,
    GenAiSdkGenerator,
    GenkitGenerator,
)
from adapters.google_adk import GoogleAdkGenerator
from studio.domain.models import TaskmasterSpecification


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    request_path = Path(sys.argv[1]).resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        root = Path(request["root"]).resolve()
        destination = Path(request["destination"]).resolve()
        if request_path.parent != root / ".studio-build-requests":
            return 3
        if destination.parent != root or destination.exists():
            return 4
        specification = TaskmasterSpecification.model_validate(request["specification"])
        registry = FrameworkGeneratorRegistry(
            (
                GoogleAdkGenerator(root),
                GenAiSdkGenerator(root),
                AntigravityGenerator(root),
                GenkitGenerator(root),
            )
        )
        bundle = registry.generate(specification, destination)
        evidence = bundle.output_directory / ".studio" / "isolated-builder.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "runtime": "isolated_controlled_builder",
                    "network_tools": False,
                    "credentials_available": False,
                    "contract_sha256": request.get("contract_sha256", ""),
                    "framework": specification.generation.target_framework,
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

