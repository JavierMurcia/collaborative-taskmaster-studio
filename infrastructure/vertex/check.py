"""Safe local diagnostic for H8-02; it never refreshes tokens or calls Vertex AI."""

from __future__ import annotations

import json

from infrastructure.vertex import VertexSettings, inspect_vertex_readiness


def main() -> None:
    settings = VertexSettings.from_environment()
    readiness = inspect_vertex_readiness(settings)
    print(json.dumps(readiness.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
