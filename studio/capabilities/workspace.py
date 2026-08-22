"""Confined, read-only workspace access for the collaborative partner."""

from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path
from typing import Any

ALLOWED_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".toml", ".xml",
        ".html", ".css", ".py", ".js", ".ts", ".tsx", ".jsx",
    }
)
DENIED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".ssh", ".gnupg"}
)
DENIED_NAMES = frozenset(
    {".env", "credentials.json", "application_default_credentials.json", "id_rsa", "id_ed25519"}
)
DENIED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})
MAX_DIRECTORY_ENTRIES = 120
MAX_SEARCHED_FILES = 200
MAX_SEARCH_MATCHES = 30
MAX_MAPPED_FILES = 500
MAX_STATS_BYTES = 8_388_608
HARD_MAX_BYTES = 262_144
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[\"']?([^\s\"']{8,})"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([a-z0-9._~+/-]{8,})"),
)


class WorkspaceReader:
    """List or read allowed text without ever leaving an assigned root."""

    def __init__(self, root: str | Path, *, max_bytes: int = 16_384) -> None:
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise PermissionError("La raíz del workspace no puede ser un enlace simbólico.")
        self.root = raw_root.resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError("El workspace debe ser un directorio.")
        self.max_bytes = max(1, min(int(max_bytes), HARD_MAX_BYTES))

    def inspect(self, relative_path: str = ".") -> dict[str, Any]:
        target = self._resolve(relative_path)
        if target.is_dir():
            return self._list_directory(target)
        return self._read_text(target)

    def read_text(self, relative_path: str) -> dict[str, Any]:
        return self._read_text(self._resolve(relative_path))

    def search(self, query: str, relative_path: str = ".") -> dict[str, Any]:
        clean_query = str(query).strip()
        if len(clean_query) < 2 or len(clean_query) > 120:
            raise ValueError("La búsqueda debe contener entre 2 y 120 caracteres.")
        target = self._resolve(relative_path)
        if not target.is_dir():
            raise NotADirectoryError("La búsqueda requiere un directorio como alcance.")

        matches: list[dict[str, Any]] = []
        searched_files = 0
        needle = clean_query.casefold()
        for current_root, directory_names, file_names in os.walk(target, followlinks=False):
            current = Path(current_root)
            directory_names[:] = [
                name
                for name in sorted(directory_names, key=str.casefold)
                if not (current / name).is_symlink()
                and name.casefold() not in DENIED_DIRECTORIES
            ]
            for file_name in sorted(file_names, key=str.casefold):
                if searched_files >= MAX_SEARCHED_FILES or len(matches) >= MAX_SEARCH_MATCHES:
                    return self._search_result(target, clean_query, matches, searched_files, True)
                candidate = current / file_name
                if candidate.is_symlink() or candidate.suffix.casefold() not in ALLOWED_SUFFIXES:
                    continue
                try:
                    self._check_sensitive(candidate)
                except PermissionError:
                    continue
                if candidate.stat().st_size > self.max_bytes:
                    continue
                searched_files += 1
                try:
                    text = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                safe_text, _ = _redact_sensitive_text(text)
                for line_number, line in enumerate(safe_text.splitlines(), start=1):
                    if needle in line.casefold():
                        matches.append(
                            {
                                "path": candidate.relative_to(self.root).as_posix(),
                                "line": line_number,
                                "snippet": line.strip()[:300],
                            }
                        )
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            return self._search_result(
                                target, clean_query, matches, searched_files, True
                            )
        return self._search_result(target, clean_query, matches, searched_files, False)

    def map_project(self, relative_path: str = ".") -> dict[str, Any]:
        """Build a bounded structural map without returning file contents."""

        target = self._resolve(relative_path)
        if not target.is_dir():
            raise NotADirectoryError("El mapa requiere un directorio como alcance.")
        suffix_counts: dict[str, int] = {}
        directories: dict[str, int] = {}
        manifests: list[str] = []
        tests: list[str] = []
        files: list[str] = []
        total_lines = 0
        nonblank_lines = 0
        line_count_files = 0
        line_count_skipped = 0
        stats_bytes = 0
        truncated = False
        manifest_names = {"pyproject.toml", "package.json", "dockerfile", "cloudbuild.yaml", "readme.md"}
        for current_root, directory_names, file_names in os.walk(target, followlinks=False):
            current = Path(current_root)
            directory_names[:] = [
                name for name in sorted(directory_names, key=str.casefold)
                if not (current / name).is_symlink() and name.casefold() not in DENIED_DIRECTORIES
            ]
            relative_directory = current.relative_to(self.root).as_posix() or "."
            accepted = 0
            for file_name in sorted(file_names, key=str.casefold):
                candidate = current / file_name
                if candidate.is_symlink() or candidate.suffix.casefold() not in ALLOWED_SUFFIXES:
                    continue
                try:
                    self._check_sensitive(candidate)
                except PermissionError:
                    continue
                if len(files) >= MAX_MAPPED_FILES:
                    truncated = True
                    break
                path = candidate.relative_to(self.root).as_posix()
                files.append(path)
                accepted += 1
                size = candidate.stat().st_size
                if size <= HARD_MAX_BYTES and stats_bytes + size <= MAX_STATS_BYTES:
                    try:
                        text = candidate.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        line_count_skipped += 1
                    else:
                        safe_text, _ = _redact_sensitive_text(text)
                        lines = safe_text.splitlines()
                        total_lines += len(lines)
                        nonblank_lines += sum(1 for line in lines if line.strip())
                        line_count_files += 1
                        stats_bytes += size
                else:
                    line_count_skipped += 1
                suffix = candidate.suffix.casefold() or "[sin extensión]"
                suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
                if candidate.name.casefold() in manifest_names:
                    manifests.append(path)
                if "test" in candidate.name.casefold() or "tests" in candidate.parts:
                    tests.append(path)
            if accepted:
                directories[relative_directory] = accepted
            if truncated:
                break
        return {
            "kind": "project_map",
            "path": target.relative_to(self.root).as_posix() or ".",
            "total_files": len(files),
            "total_lines": total_lines,
            "nonblank_lines": nonblank_lines,
            "line_count_files": line_count_files,
            "line_count_skipped": line_count_skipped,
            "line_count_truncated": truncated or line_count_skipped > 0,
            "file_types": dict(sorted(suffix_counts.items(), key=lambda item: (-item[1], item[0]))),
            "directories": dict(sorted(directories.items(), key=lambda item: (-item[1], item[0]))[:40]),
            "manifests": manifests[:30],
            "tests": tests[:60],
            "sample_files": files[:120],
            "truncated": truncated,
        }

    def related(self, relative_path: str) -> dict[str, Any]:
        """Find direct imports and bounded reverse textual references for a source file."""

        target = self._resolve(relative_path)
        payload = self._read_text(target)
        content = str(payload["content"])
        imports: list[str] = []
        if target.suffix.casefold() == ".py":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
            except SyntaxError:
                pass
        else:
            imports.extend(
                match.group(1) or match.group(2)
                for match in re.finditer(
                    r"(?:from\s+['\"]([^'\"]+)['\"]|require\(['\"]([^'\"]+)['\"]\))",
                    content,
                )
            )
        reverse = self.search(target.stem, ".")
        referenced_by = sorted({
            str(item["path"]) for item in reverse["matches"]
            if item["path"] != payload["path"]
        })[:40]
        return {
            "kind": "relations",
            "path": payload["path"],
            "imports": sorted(set(imports))[:80],
            "referenced_by": referenced_by,
            "search_truncated": reverse["truncated"],
        }

    def _resolve(self, relative_path: str) -> Path:
        value = str(relative_path).strip() or "."
        if "\x00" in value:
            raise PermissionError("La ruta contiene caracteres no permitidos.")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("La ruta debe permanecer dentro del workspace.")
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("No se permite seguir enlaces simbólicos.")
        target = current.resolve(strict=True)
        try:
            target.relative_to(self.root)
        except ValueError as error:
            raise PermissionError("La ruta escapó del workspace.") from error
        self._check_sensitive(target)
        return target

    def _check_sensitive(self, target: Path) -> None:
        parts = tuple(part.casefold() for part in target.relative_to(self.root).parts)
        if any(part in DENIED_DIRECTORIES for part in parts):
            raise PermissionError("El directorio solicitado está bloqueado.")
        name = target.name.casefold()
        if name in DENIED_NAMES or name.startswith(".env.") or name.startswith("secret"):
            raise PermissionError("El archivo solicitado está clasificado como sensible.")
        if target.suffix.casefold() in DENIED_SUFFIXES:
            raise PermissionError("No se permite leer material criptográfico.")

    def _list_directory(self, target: Path) -> dict[str, Any]:
        entries: list[dict[str, str]] = []
        truncated = False
        for child in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
            if len(entries) >= MAX_DIRECTORY_ENTRIES:
                truncated = True
                break
            if child.is_symlink():
                continue
            try:
                self._check_sensitive(child)
            except PermissionError:
                continue
            entries.append(
                {"name": child.name, "kind": "directory" if child.is_dir() else "file"}
            )
        return {
            "kind": "directory",
            "path": target.relative_to(self.root).as_posix() or ".",
            "entries": entries,
            "truncated": truncated,
        }

    def _read_text(self, target: Path) -> dict[str, Any]:
        if not target.is_file():
            raise IsADirectoryError("La ruta no corresponde a un archivo legible.")
        if target.suffix.casefold() not in ALLOWED_SUFFIXES:
            raise PermissionError("El formato solicitado no está permitido para lectura textual.")
        size = target.stat().st_size
        if size > self.max_bytes:
            raise PermissionError("El archivo supera el límite de lectura configurado.")
        payload = target.read_bytes()
        if b"\x00" in payload:
            raise PermissionError("El archivo parece binario y fue rechazado.")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PermissionError("El archivo no contiene texto UTF-8 válido.") from error
        safe_content, redactions = _redact_sensitive_text(content)
        return {
            "kind": "file",
            "path": target.relative_to(self.root).as_posix(),
            "content": safe_content,
            "size_bytes": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "redactions": redactions,
        }

    def _search_result(
        self,
        target: Path,
        query: str,
        matches: list[dict[str, Any]],
        searched_files: int,
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "kind": "search",
            "path": target.relative_to(self.root).as_posix() or ".",
            "query": query,
            "matches": matches,
            "searched_files": searched_files,
            "truncated": truncated,
        }


def _redact_sensitive_text(content: str) -> tuple[str, int]:
    redactions = 0
    safe_content = content
    for pattern in SENSITIVE_VALUE_PATTERNS:
        if pattern.groups == 3:
            safe_content, count = pattern.subn(r"\1\2[REDACTED]", safe_content)
        else:
            safe_content, count = pattern.subn(r"\1[REDACTED]", safe_content)
        redactions += count
    return safe_content, redactions
