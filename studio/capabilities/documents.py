"""Session-scoped document ingestion and bounded text extraction."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 100_000
MAX_DOCUMENTS_PER_SESSION = 12
TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml"})
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".xlsx", ".pptx"}


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^doc_[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=180)
    suffix: str
    size_bytes: int = Field(ge=1, le=MAX_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(max_length=MAX_EXTRACTED_CHARACTERS)
    truncated: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "characters": len(self.text),
            "truncated": self.truncated,
        }


class DocumentLibrary:
    """Store extracted text only; original uploads never become executable files."""

    def __init__(self, data_directory: Path) -> None:
        self._root = (data_directory / "documents").resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def add(self, owner_session_id: str, filename: str, payload: bytes) -> DocumentRecord:
        safe_name = Path(filename).name.strip()[:180]
        suffix = Path(safe_name).suffix.casefold()
        if not safe_name or suffix not in ALLOWED_SUFFIXES:
            raise DomainError(
                "DOCUMENT_FORMAT_UNSUPPORTED",
                "El formato no está permitido. Usa PDF, DOCX, XLSX, PPTX, TXT, Markdown, CSV, JSON o YAML.",
            )
        if not payload or len(payload) > MAX_UPLOAD_BYTES:
            raise DomainError(
                "DOCUMENT_SIZE_INVALID",
                "El documento debe contener datos y no superar 8 MB.",
            )
        directory = self._owner_directory(owner_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        if len(tuple(directory.glob("doc_*.json"))) >= MAX_DOCUMENTS_PER_SESSION:
            raise DomainError(
                "DOCUMENT_LIMIT_REACHED",
                "La sesión alcanzó el límite de 12 documentos; elimina uno antes de continuar.",
            )
        extracted = _extract_text(suffix, payload)
        clean = _normalize_text(extracted)
        truncated = len(clean) > MAX_EXTRACTED_CHARACTERS
        clean = clean[:MAX_EXTRACTED_CHARACTERS]
        if not clean:
            raise DomainError(
                "DOCUMENT_TEXT_EMPTY",
                "No fue posible extraer texto utilizable del documento.",
            )
        record = DocumentRecord(
            id=f"doc_{uuid4().hex[:16]}",
            name=safe_name,
            suffix=suffix,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            text=clean,
            truncated=truncated,
        )
        (directory / f"{record.id}.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        return record

    def list(self, owner_session_id: str) -> tuple[dict[str, Any], ...]:
        directory = self._owner_directory(owner_session_id)
        if not directory.exists():
            return ()
        records = [self._load(path) for path in directory.glob("doc_*.json")]
        return tuple(record.summary() for record in records if record is not None)

    def inspect(self, owner_session_id: str, document_id: str) -> dict[str, Any]:
        record = self._require(owner_session_id, document_id)
        return {"kind": "document", **record.summary(), "content": record.text[:24_000]}

    def search(self, owner_session_id: str, document_id: str, query: str) -> dict[str, Any]:
        record = self._require(owner_session_id, document_id)
        needle = query.strip().casefold()
        if len(needle) < 2 or len(needle) > 120:
            raise DomainError("DOCUMENT_QUERY_INVALID", "La consulta debe tener entre 2 y 120 caracteres.")
        matches: list[dict[str, Any]] = []
        for line_number, line in enumerate(record.text.splitlines(), start=1):
            if needle in line.casefold():
                matches.append({"line": line_number, "snippet": line[:500]})
                if len(matches) >= 24:
                    break
        return {
            "kind": "document_search",
            "document": record.summary(),
            "query": query.strip(),
            "matches": matches,
            "truncated": len(matches) >= 24,
        }

    def delete(self, owner_session_id: str, document_id: str) -> None:
        self._path(owner_session_id, document_id).unlink(missing_ok=True)

    def _require(self, owner_session_id: str, document_id: str) -> DocumentRecord:
        path = self._path(owner_session_id, document_id)
        record = self._load(path) if path.exists() else None
        if record is None:
            raise DomainError("DOCUMENT_NOT_FOUND", "El documento no existe en esta sesión.")
        return record

    def _path(self, owner_session_id: str, document_id: str) -> Path:
        if not re.fullmatch(r"doc_[a-f0-9]{16}", document_id):
            raise DomainError("DOCUMENT_ID_INVALID", "El identificador del documento no es válido.")
        return self._owner_directory(owner_session_id) / f"{document_id}.json"

    def _owner_directory(self, owner_session_id: str) -> Path:
        key = hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()
        return self._root / key

    @staticmethod
    def _load(path: Path) -> DocumentRecord | None:
        try:
            return DocumentRecord.model_validate_json(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None


def _extract_text(suffix: str, payload: bytes) -> str:
    if suffix in TEXT_SUFFIXES:
        try:
            return payload.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise DomainError("DOCUMENT_ENCODING_INVALID", "El documento de texto debe usar UTF-8.") from error
    if suffix == ".docx":
        return _extract_docx(payload)
    if suffix == ".pdf":
        return _extract_pdf(payload)
    if suffix == ".xlsx":
        return _extract_xlsx(payload)
    if suffix == ".pptx":
        return _extract_pptx(payload)
    raise DomainError("DOCUMENT_FORMAT_UNSUPPORTED", "El formato no está permitido.")


def extract_document_text(filename: str, payload: bytes) -> str:
    """Extract bounded, normalized text from a supported untrusted document."""

    suffix = Path(filename).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise DomainError("DOCUMENT_FORMAT_UNSUPPORTED", "El formato no está permitido.")
    clean = _normalize_text(_extract_text(suffix, payload))
    if not clean:
        raise DomainError(
            "DOCUMENT_TEXT_EMPTY",
            "No fue posible extraer texto utilizable del documento.",
        )
    return clean[:MAX_EXTRACTED_CHARACTERS]


def _extract_docx(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > 300 or sum(item.file_size for item in members) > 20_000_000:
                raise DomainError("DOCUMENT_ARCHIVE_UNSAFE", "El DOCX supera los límites seguros.")
            raw = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as error:
        raise DomainError("DOCUMENT_DOCX_INVALID", "El archivo DOCX no es válido.") from error
    root = ElementTree.fromstring(raw)
    return "\n".join(text for node in root.iter() if node.tag.endswith("}t") and (text := node.text))


def _extract_pdf(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as error:
        raise DomainError("DOCUMENT_PDF_READER_UNAVAILABLE", "El lector PDF no está instalado.") from error
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if len(reader.pages) > 100:
            raise DomainError("DOCUMENT_PDF_TOO_LONG", "El PDF supera el límite de 100 páginas.")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except DomainError:
        raise
    except Exception as error:
        raise DomainError("DOCUMENT_PDF_INVALID", "El archivo PDF no pudo procesarse de forma segura.") from error


def _extract_xlsx(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            _validate_office_archive(archive, "XLSX")
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")) for item in root if item.tag.endswith("}si")]
            sheets: list[str] = []
            names = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            for index, name in enumerate(names, start=1):
                root = ElementTree.fromstring(archive.read(name))
                rows: list[str] = []
                for row in (node for node in root.iter() if node.tag.endswith("}row")):
                    values: list[str] = []
                    for cell in (node for node in row if node.tag.endswith("}c")):
                        cell_type = cell.attrib.get("t", "")
                        value = next((node.text or "" for node in cell.iter() if node.tag.endswith("}v")), "")
                        if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        elif cell_type == "inlineStr":
                            value = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                        values.append(value)
                    if any(values):
                        rows.append("\t".join(values))
                if rows:
                    sheets.append(f"Hoja {index}\n" + "\n".join(rows))
            return "\n\n".join(sheets)
    except (zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise DomainError("DOCUMENT_XLSX_INVALID", "El archivo XLSX no es válido.") from error


def _extract_pptx(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            _validate_office_archive(archive, "PPTX")
            slides: list[str] = []
            names = sorted(
                (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda name: int(re.search(r"(\d+)", Path(name).stem).group(1)),  # type: ignore[union-attr]
            )
            for index, name in enumerate(names, start=1):
                root = ElementTree.fromstring(archive.read(name))
                text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
                if text.strip():
                    slides.append(f"Diapositiva {index}\n{text}")
            return "\n\n".join(slides)
    except (zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise DomainError("DOCUMENT_PPTX_INVALID", "El archivo PPTX no es válido.") from error


def _validate_office_archive(archive: zipfile.ZipFile, label: str) -> None:
    members = archive.infolist()
    if len(members) > 1_500 or sum(item.file_size for item in members) > 40_000_000:
        raise DomainError("DOCUMENT_ARCHIVE_UNSAFE", f"El {label} supera los límites seguros.")


def _normalize_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()
