from __future__ import annotations

from pathlib import Path

import pytest

from studio.capabilities.workspace import WorkspaceReader


def test_workspace_reader_lists_and_reads_allowed_text(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("Guía segura", encoding="utf-8")
    reader = WorkspaceReader(tmp_path)

    listing = reader.inspect(".")
    document = reader.inspect("docs/guide.md")

    assert listing["entries"] == [{"name": "docs", "kind": "directory"}]
    assert document["content"] == "Guía segura"
    assert len(document["sha256"]) == 64


@pytest.mark.parametrize(
    "path",
    ["../outside.md", ".env", "credentials.json", "secret-notes.md", "private.key"],
)
def test_workspace_reader_blocks_escape_and_sensitive_paths(tmp_path: Path, path: str) -> None:
    if ".." not in path:
        (tmp_path / path).write_text("sensitive", encoding="utf-8")
    reader = WorkspaceReader(tmp_path)

    with pytest.raises(PermissionError):
        reader.inspect(path)


def test_workspace_reader_blocks_large_files(tmp_path: Path) -> None:
    (tmp_path / "large.md").write_text("x" * 20, encoding="utf-8")
    reader = WorkspaceReader(tmp_path, max_bytes=10)

    with pytest.raises(PermissionError):
        reader.inspect("large.md")


def test_workspace_reader_searches_recursively_with_bounded_snippets(tmp_path: Path) -> None:
    (tmp_path / "studio").mkdir()
    (tmp_path / "studio" / "service.py").write_text(
        "class CollaborativeService:\n    pass\n", encoding="utf-8"
    )
    reader = WorkspaceReader(tmp_path)

    result = reader.search("CollaborativeService")

    assert result["kind"] == "search"
    assert result["searched_files"] == 1
    assert result["matches"] == [
        {
            "path": "studio/service.py",
            "line": 1,
            "snippet": "class CollaborativeService:",
        }
    ]


def test_workspace_reader_redacts_likely_secrets_before_egress(tmp_path: Path) -> None:
    (tmp_path / "settings.py").write_text(
        'api_key = "super-secret-value"\nname = "safe"\n', encoding="utf-8"
    )
    reader = WorkspaceReader(tmp_path)

    result = reader.inspect("settings.py")

    assert "super-secret-value" not in result["content"]
    assert "[REDACTED]" in result["content"]
    assert result["redactions"] == 1
