"""Session-scoped document ingestion and bounded text extraction."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from studio.capabilities.datasets import DatasetSnapshot, parse_dataset, parse_dataset_path
from studio.domain.errors import DomainError
from studio.ports.model_gateway import ModelMedia

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_LARGE_UPLOAD_BYTES = 600 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_LARGE_UPLOAD_BYTES = 1200 * 1024 * 1024
MAX_MULTIMODAL_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 100_000
MAX_DOCUMENTS_PER_SESSION = 12
TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml"})
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".xlsx", ".pptx"} | IMAGE_MIME_TYPES.keys()


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^doc_[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=180)
    suffix: str
    size_bytes: int = Field(ge=1, le=MAX_LARGE_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(max_length=MAX_EXTRACTED_CHARACTERS)
    truncated: bool = False
    dataset: DatasetSnapshot | None = None
    media: ModelMedia | None = None

    def summary(self) -> dict[str, Any]:
        summary = {
            "id": self.id,
            "name": self.name,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "characters": len(self.text),
            "truncated": self.truncated,
        }
        if self.dataset is not None:
            summary["dataset"] = self.dataset.summary()
        if self.media is not None:
            summary["media_type"] = self.media.mime_type
        return summary


class LargeUploadState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^large_[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(gt=MAX_UPLOAD_BYTES, le=MAX_LARGE_UPLOAD_BYTES)
    received_bytes: int = Field(ge=0, le=MAX_LARGE_UPLOAD_BYTES)
    created_at: float = Field(gt=0)


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
                "El formato no está permitido. Usa documentos compatibles o imágenes PNG, JPG y WEBP.",
            )
        if not payload or len(payload) > MAX_UPLOAD_BYTES:
            raise DomainError(
                "DOCUMENT_SIZE_INVALID",
                "El documento debe contener datos y no superar 25 MB.",
            )
        directory = self._owner_directory(owner_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        if len(tuple(directory.glob("doc_*.json"))) >= MAX_DOCUMENTS_PER_SESSION:
            raise DomainError(
                "DOCUMENT_LIMIT_REACHED",
                "La sesión alcanzó el límite de 12 documentos; elimina uno antes de continuar.",
            )
        dataset = parse_dataset(suffix, payload)
        media = _image_media(suffix, payload) if suffix in IMAGE_MIME_TYPES else None
        extracted = (
            f"Imagen adjunta: {safe_name}. Gemini puede analizar sus píxeles directamente."
            if media is not None
            else _dataset_text(dataset)
            if suffix == ".xlsx" and dataset is not None
            else _extract_text(suffix, payload)
        )
        return self._store_record(
            directory,
            safe_name=safe_name,
            suffix=suffix,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            extracted=extracted,
            dataset=dataset,
            media=media,
        )

    def add_path(self, owner_session_id: str, filename: str, path: Path) -> DocumentRecord:
        """Finalize a large CSV/XLSX from disk using bounded, streaming inspection."""

        safe_name = Path(filename).name.strip()[:180]
        suffix = Path(safe_name).suffix.casefold()
        size_bytes = path.stat().st_size if path.exists() else 0
        if suffix not in {".csv", ".xlsx"}:
            raise DomainError(
                "DOCUMENT_LARGE_FORMAT_UNSUPPORTED",
                "Las cargas mayores de 25 MB admiten CSV y XLSX.",
            )
        if not size_bytes or size_bytes > MAX_LARGE_UPLOAD_BYTES:
            raise DomainError(
                "DOCUMENT_SIZE_INVALID",
                "El archivo debe contener datos y no superar 600 MB.",
            )
        directory = self._owner_directory(owner_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        if len(tuple(directory.glob("doc_*.json"))) >= MAX_DOCUMENTS_PER_SESSION:
            raise DomainError(
                "DOCUMENT_LIMIT_REACHED",
                "La sesión alcanzó el límite de 12 documentos; elimina uno antes de continuar.",
            )
        try:
            dataset = parse_dataset_path(suffix, path)
            if suffix == ".xlsx":
                if dataset is None:
                    raise DomainError(
                        "DOCUMENT_XLSX_LIMIT_REACHED",
                        "El XLSX no contiene hojas legibles dentro de los límites de análisis.",
                    )
                extracted = _dataset_text(dataset)
            else:
                extracted = _read_text_prefix(path)
        except DomainError:
            raise
        except UnicodeDecodeError as error:
            raise DomainError(
                "DOCUMENT_ENCODING_INVALID", "El archivo CSV debe usar UTF-8."
            ) from error
        except (zipfile.BadZipFile, ElementTree.ParseError) as error:
            raise DomainError(
                "DOCUMENT_XLSX_INVALID", "El archivo XLSX no es válido."
            ) from error
        return self._store_record(
            directory,
            safe_name=safe_name,
            suffix=suffix,
            size_bytes=size_bytes,
            sha256=_sha256_path(path),
            extracted=extracted,
            dataset=dataset,
            media=None,
        )

    def _store_record(
        self,
        directory: Path,
        *,
        safe_name: str,
        suffix: str,
        size_bytes: int,
        sha256: str,
        extracted: str,
        dataset: DatasetSnapshot | None,
        media: ModelMedia | None,
    ) -> DocumentRecord:
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
            size_bytes=size_bytes,
            sha256=sha256,
            text=clean,
            truncated=truncated,
            dataset=dataset,
            media=media,
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
        payload: dict[str, Any] = {
            "kind": "document",
            **record.summary(),
            "content": record.text[:24_000],
        }
        if record.dataset is not None:
            payload["dataset"] = record.dataset.model_dump(mode="json")
        if record.media is not None:
            payload["media"] = record.media.model_dump(mode="json")
        return payload

    def media(
        self, owner_session_id: str, document_ids: tuple[str, ...]
    ) -> tuple[ModelMedia, ...]:
        result: list[ModelMedia] = []
        total_bytes = 0
        for document_id in document_ids:
            record = self._require(owner_session_id, document_id)
            if record.media is not None:
                total_bytes += record.size_bytes
                if total_bytes > MAX_MULTIMODAL_BYTES:
                    raise DomainError(
                        "DOCUMENT_MEDIA_LIMIT_REACHED",
                        "Las imágenes adjuntas no pueden superar 16 MB en conjunto.",
                    )
                result.append(record.media)
        return tuple(result)

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


class LargeUploadManager:
    """Assemble same-origin chunks on disk before bounded dataset inspection."""

    def __init__(self, root: Path, documents: DocumentLibrary) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._documents = documents

    def start(self, owner_session_id: str, filename: str, size_bytes: int) -> LargeUploadState:
        safe_name = Path(filename).name.strip()[:180]
        if Path(safe_name).suffix.casefold() not in {".csv", ".xlsx"}:
            raise DomainError(
                "DOCUMENT_LARGE_FORMAT_UNSUPPORTED",
                "Las cargas mayores de 25 MB admiten CSV y XLSX.",
            )
        if not MAX_UPLOAD_BYTES < size_bytes <= MAX_LARGE_UPLOAD_BYTES:
            raise DomainError(
                "DOCUMENT_SIZE_INVALID",
                "La carga grande debe superar 25 MB y no exceder 600 MB.",
            )
        directory = self._owner_directory(owner_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        for owner_directory in self._root.iterdir():
            if owner_directory.is_dir():
                self._prune(owner_directory)
        active = len(tuple(directory.glob("large_*.json")))
        if len(self._documents.list(owner_session_id)) + active >= MAX_DOCUMENTS_PER_SESSION:
            raise DomainError(
                "DOCUMENT_LIMIT_REACHED",
                "La sesión alcanzó el límite de 12 documentos; elimina uno antes de continuar.",
            )
        if self._active_declared_bytes() + size_bytes > MAX_ACTIVE_LARGE_UPLOAD_BYTES:
            raise DomainError(
                "DOCUMENT_UPLOAD_CAPACITY_REACHED",
                "El servicio está procesando otras cargas grandes; inténtalo nuevamente en unos minutos.",
            )
        state = LargeUploadState(
            id=f"large_{uuid4().hex[:16]}",
            name=safe_name,
            size_bytes=size_bytes,
            received_bytes=0,
            created_at=time.time(),
        )
        self._write_state(directory, state)
        self._part_path(directory, state.id).touch(exist_ok=False)
        return state

    def append(
        self,
        owner_session_id: str,
        upload_id: str,
        offset: int,
        payload: bytes,
    ) -> LargeUploadState:
        directory = self._owner_directory(owner_session_id)
        state = self._require(directory, upload_id)
        if not payload or len(payload) > MAX_UPLOAD_CHUNK_BYTES:
            raise DomainError(
                "DOCUMENT_CHUNK_SIZE_INVALID",
                "Cada bloque debe contener datos y no superar 8 MB.",
            )
        if offset != state.received_bytes:
            raise DomainError(
                "DOCUMENT_CHUNK_OFFSET_CONFLICT",
                "La posición del bloque no coincide con la carga recibida.",
            )
        if offset + len(payload) > state.size_bytes:
            raise DomainError(
                "DOCUMENT_CHUNK_OVERFLOW", "El bloque supera el tamaño declarado del archivo."
            )
        with self._part_path(directory, upload_id).open("ab") as target:
            target.write(payload)
        updated = state.model_copy(update={"received_bytes": offset + len(payload)})
        self._write_state(directory, updated)
        return updated

    def complete(self, owner_session_id: str, upload_id: str) -> DocumentRecord:
        directory = self._owner_directory(owner_session_id)
        state = self._require(directory, upload_id)
        part_path = self._part_path(directory, upload_id)
        if state.received_bytes != state.size_bytes or part_path.stat().st_size != state.size_bytes:
            raise DomainError(
                "DOCUMENT_UPLOAD_INCOMPLETE", "La carga todavía no ha recibido todos sus bloques."
            )
        try:
            return self._documents.add_path(owner_session_id, state.name, part_path)
        finally:
            part_path.unlink(missing_ok=True)
            self._state_path(directory, upload_id).unlink(missing_ok=True)

    def cancel(self, owner_session_id: str, upload_id: str) -> None:
        directory = self._owner_directory(owner_session_id)
        self._validate_id(upload_id)
        self._part_path(directory, upload_id).unlink(missing_ok=True)
        self._state_path(directory, upload_id).unlink(missing_ok=True)

    def _require(self, directory: Path, upload_id: str) -> LargeUploadState:
        self._validate_id(upload_id)
        try:
            return LargeUploadState.model_validate_json(
                self._state_path(directory, upload_id).read_text("utf-8")
            )
        except (OSError, ValueError) as error:
            raise DomainError(
                "DOCUMENT_UPLOAD_NOT_FOUND", "La carga grande no existe o ya terminó."
            ) from error

    def _owner_directory(self, owner_session_id: str) -> Path:
        return self._root / hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_id(upload_id: str) -> None:
        if not re.fullmatch(r"large_[a-f0-9]{16}", upload_id):
            raise DomainError(
                "DOCUMENT_UPLOAD_ID_INVALID", "El identificador de carga no es válido."
            )

    @staticmethod
    def _state_path(directory: Path, upload_id: str) -> Path:
        return directory / f"{upload_id}.json"

    @staticmethod
    def _part_path(directory: Path, upload_id: str) -> Path:
        return directory / f"{upload_id}.part"

    def _write_state(self, directory: Path, state: LargeUploadState) -> None:
        self._state_path(directory, state.id).write_text(
            state.model_dump_json(), encoding="utf-8"
        )

    def _active_declared_bytes(self) -> int:
        total = 0
        for state_path in self._root.glob("*/large_*.json"):
            try:
                total += int(json.loads(state_path.read_text("utf-8")).get("size_bytes", 0))
            except (OSError, ValueError, TypeError):
                continue
        return total

    @staticmethod
    def _prune(directory: Path) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for state_path in directory.glob("large_*.json"):
            try:
                payload = json.loads(state_path.read_text("utf-8"))
                if float(payload.get("created_at", 0)) >= cutoff:
                    continue
            except (OSError, ValueError, TypeError):
                pass
            state_path.with_suffix(".part").unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)


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


def _dataset_text(dataset: DatasetSnapshot | None) -> str:
    if dataset is None:
        return ""
    sections: list[str] = []
    for sheet in dataset.sheets:
        lines = ["\t".join(sheet.columns)]
        lines.extend(
            "\t".join("" if value is None else str(value) for value in row)
            for row in sheet.rows
        )
        sections.append(f"Hoja: {sheet.name}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _read_text_prefix(path: Path) -> str:
    with path.open("rb") as source:
        payload = source.read(MAX_EXTRACTED_CHARACTERS * 4)
    return payload.decode("utf-8-sig", errors="ignore")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _image_media(suffix: str, payload: bytes) -> ModelMedia:
    mime_type = IMAGE_MIME_TYPES[suffix]
    valid = (
        payload.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix == ".png"
        else payload.startswith(b"\xff\xd8\xff")
        if suffix in {".jpg", ".jpeg"}
        else len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP"
    )
    if not valid:
        raise DomainError("DOCUMENT_IMAGE_INVALID", "La imagen no coincide con su formato o está dañada.")
    return ModelMedia(
        mime_type=mime_type,
        data_base64=base64.b64encode(payload).decode("ascii"),
    )


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
        return _dataset_text(parse_dataset(".xlsx", payload))
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
