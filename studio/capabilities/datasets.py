"""Bounded, deterministic dataset inspection and chart generation."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import random
import re
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

MAX_DATASET_ROWS = 2_500
MAX_DATASET_COLUMNS = 40
MAX_CHART_POINTS = 24
MAX_XLSX_MEMBERS = 10_000
MAX_XLSX_EXPANDED_BYTES = 4_000_000_000
MAX_SHARED_STRINGS = 200_000
MAX_SHARED_STRING_CHARACTERS = 10_000_000
MAX_ANALYSIS_ARTIFACTS = 8
CHART_PALETTE = (
    "#8b7cf6",
    "#55d4df",
    "#ff7aa2",
    "#f3b65a",
    "#6ee7a8",
    "#7ca8ff",
    "#c792ff",
    "#ff9f68",
)

CellValue = str | int | float | bool | None


class DatasetSheet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    columns: tuple[str, ...] = Field(min_length=1, max_length=MAX_DATASET_COLUMNS)
    rows: tuple[tuple[CellValue, ...], ...] = Field(max_length=MAX_DATASET_ROWS)
    total_rows: int = Field(ge=0)
    truncated: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.total_rows,
            "columns": list(self.columns),
            "truncated": self.truncated,
        }


class DatasetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sheets: tuple[DatasetSheet, ...] = Field(min_length=1, max_length=24)

    def summary(self) -> dict[str, Any]:
        return {"kind": "dataset", "sheets": [sheet.summary() for sheet in self.sheets]}


class ChartPoint(BaseModel):
    """Firestore-safe chart point; Firestore rejects arrays nested inside arrays."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: CellValue
    y: CellValue


class ChartMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=60)


class ChartArtifact(BaseModel):
    """A small chart contract that is safe to persist with a conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["chart"] = "chart"
    title: str = Field(min_length=1, max_length=160)
    chart_type: Literal["bar", "horizontal_bar", "line", "area", "pie", "donut", "scatter"]
    columns: tuple[str, str]
    rows: tuple[ChartPoint, ...] = Field(min_length=1, max_length=MAX_CHART_POINTS)
    source_document_id: str | None = Field(default=None, pattern=r"^doc_[a-f0-9]{16}$")
    source_name: str = Field(min_length=1, max_length=180)
    sheet: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    variant: Literal["comparison", "trend", "composition", "distribution", "correlation"] = (
        "comparison"
    )
    palette: tuple[str, ...] = Field(default=CHART_PALETTE, min_length=1, max_length=8)
    metrics: tuple[ChartMetric, ...] = Field(default=(), max_length=4)
    insights: tuple[str, ...] = Field(default=(), max_length=3)


class DatasetAnalysisService:
    """Create chart artifacts from attached structured data without arbitrary code."""

    _CHART_TERMS = (
        "gráfic",
        "grafic",
        "chart",
        "visualiza",
        "diagrama",
        "histograma",
    )
    _REQUEST_TERMS = _CHART_TERMS + (
        "tendencia",
        "distribución",
        "distribucion",
        "correlación",
        "correlacion",
        "analiza",
        "analice",
        "comparar",
        "compara",
        "revisa",
        "profund",
        "dashboard",
        "panel",
        "colorid",
    )
    _SYNTHETIC_TERMS = (
        "aleatori",
        "simulad",
        "demostración",
        "demostracion",
        "demo",
        "ejemplo",
    )

    def analyze(
        self, message: str, documents: tuple[dict[str, Any], ...]
    ) -> tuple[ChartArtifact, ...]:
        normalized = message.casefold()
        if not any(term in normalized for term in self._REQUEST_TERMS):
            return ()
        dataset_documents = tuple(
            item for item in documents if isinstance(item.get("dataset"), dict)
        )
        if not dataset_documents:
            if self.requests_chart(message) or any(
                term in normalized for term in self._SYNTHETIC_TERMS
            ):
                return _build_demo_charts(message)
            return ()
        artifacts: list[ChartArtifact] = []
        deep_request = any(
            term in normalized
            for term in ("profund", "complet", "detall", "complej", "dashboard", "panel")
        )
        per_document = (
            4
            if len(dataset_documents) == 1 and deep_request
            else 3
            if len(dataset_documents) == 1
            else 2
        )
        for document in dataset_documents[:8]:
            snapshot = DatasetSnapshot.model_validate(document["dataset"])
            sheet = _select_sheet(snapshot, normalized)
            artifacts.extend(
                _build_charts(
                    sheet,
                    normalized,
                    document_id=str(document["id"]),
                    document_name=str(document.get("name") or "dataset"),
                    limit=per_document,
                )
            )
            if len(artifacts) >= MAX_ANALYSIS_ARTIFACTS:
                break
        return tuple(artifacts[:MAX_ANALYSIS_ARTIFACTS])

    @classmethod
    def requests_chart(cls, message: str) -> bool:
        normalized = message.casefold()
        return any(term in normalized for term in cls._CHART_TERMS)


def _build_demo_charts(message: str) -> tuple[ChartArtifact, ...]:
    """Render deterministic demonstration charts without executing model code."""

    seed = int.from_bytes(hashlib.sha256(message.encode("utf-8")).digest()[:8], "big")
    generator = random.Random(seed)
    latency = tuple(
        ChartPoint(x=f"Muestra {index}", y=generator.randint(82, 148)) for index in range(1, 13)
    )
    success = tuple(
        ChartPoint(x=label, y=round(generator.uniform(88.0, 99.4), 1))
        for label in ("API", "Datos", "Búsqueda", "Agentes", "Reportes")
    )
    return (
        ChartArtifact(
            title="Latencia por muestra",
            chart_type="line",
            columns=("Muestra", "Latencia (ms)"),
            rows=latency,
            source_name="Datos simulados",
            sheet="Demostración",
            description="Serie simulada para demostrar una visualización temporal.",
        ),
        ChartArtifact(
            title="Tasa de éxito por módulo",
            chart_type="bar",
            columns=("Módulo", "Tasa de éxito (%)"),
            rows=success,
            source_name="Datos simulados",
            sheet="Demostración",
            description="Comparación simulada del desempeño entre módulos.",
        ),
    )


def parse_dataset(suffix: str, payload: bytes) -> DatasetSnapshot | None:
    if suffix == ".csv":
        return DatasetSnapshot(sheets=(_parse_csv(payload),))
    if suffix == ".xlsx":
        sheets = _parse_xlsx(payload)
        return DatasetSnapshot(sheets=tuple(sheets)) if sheets else None
    return None


def parse_dataset_path(suffix: str, path: Path) -> DatasetSnapshot | None:
    """Inspect large structured files from disk without loading the original into memory."""

    if suffix == ".csv":
        return DatasetSnapshot(sheets=(_parse_csv_path(path),))
    if suffix == ".xlsx":
        with zipfile.ZipFile(path) as archive:
            sheets = _parse_xlsx_archive(archive)
        return DatasetSnapshot(sheets=tuple(sheets)) if sheets else None
    return None


def _parse_csv(payload: bytes) -> DatasetSheet:
    text = payload.decode("utf-8-sig")
    sample = text[:8_192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    raw_rows: list[list[str]] = []
    total_nonempty = 0
    for row in csv.reader(io.StringIO(text), dialect):
        if not any(str(value).strip() for value in row):
            continue
        total_nonempty += 1
        if len(raw_rows) <= MAX_DATASET_ROWS:
            raw_rows.append(row)
    sheet = _sheet_from_rows("Datos", raw_rows)
    total_rows = max(0, total_nonempty - 1)
    return sheet.model_copy(
        update={
            "total_rows": total_rows,
            "truncated": total_rows > MAX_DATASET_ROWS,
        }
    )


def _parse_csv_path(path: Path) -> DatasetSheet:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        sample = source.read(8_192)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        raw_rows: list[list[str]] = []
        truncated = False
        for row in csv.reader(source, dialect):
            if not any(str(value).strip() for value in row):
                continue
            if len(raw_rows) <= MAX_DATASET_ROWS:
                raw_rows.append(row)
            else:
                truncated = True
                break
    sheet = _sheet_from_rows("Datos", raw_rows)
    return sheet.model_copy(update={"truncated": truncated or sheet.truncated})


def _parse_xlsx(payload: bytes) -> list[DatasetSheet]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return _parse_xlsx_archive(archive)


def _parse_xlsx_archive(archive: zipfile.ZipFile) -> list[DatasetSheet]:
    members = archive.infolist()
    if (
        len(members) > MAX_XLSX_MEMBERS
        or sum(item.file_size for item in members) > MAX_XLSX_EXPANDED_BYTES
    ):
        return []
    shared = _xlsx_shared_strings(archive)
    names = _xlsx_sheet_names(archive)
    paths = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=lambda value: int(re.search(r"sheet(\d+)", value).group(1)),  # type: ignore[union-attr]
    )
    result: list[DatasetSheet] = []
    for index, path in enumerate(paths[:24]):
        rows: list[list[CellValue]] = []
        truncated = False
        with archive.open(path) as source:
            for _, row in ElementTree.iterparse(source, events=("end",)):
                if not row.tag.endswith("}row"):
                    continue
                if len(rows) > MAX_DATASET_ROWS:
                    truncated = True
                    row.clear()
                    break
                values = _xlsx_row_values(row, shared)
                if any(value not in (None, "") for value in values):
                    rows.append(values)
                row.clear()
        if rows:
            sheet = _sheet_from_rows(
                names[index] if index < len(names) else f"Hoja {index + 1}", rows
            )
            result.append(sheet.model_copy(update={"truncated": truncated or sheet.truncated}))
    return result


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    shared: list[str] = []
    characters = 0
    with archive.open("xl/sharedStrings.xml") as source:
        for _, item in ElementTree.iterparse(source, events=("end",)):
            if not item.tag.endswith("}si"):
                continue
            value = "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
            shared.append(value[:500])
            characters += len(shared[-1])
            item.clear()
            if len(shared) >= MAX_SHARED_STRINGS or characters >= MAX_SHARED_STRING_CHARACTERS:
                break
    return shared


def _xlsx_row_values(row: ElementTree.Element, shared: list[str]) -> list[CellValue]:
    values: list[CellValue] = []
    expected_column = 0
    for cell in (node for node in row if node.tag.endswith("}c")):
        reference = cell.attrib.get("r", "")
        column_index = _column_index(reference) if reference else expected_column
        while len(values) < min(column_index, MAX_DATASET_COLUMNS):
            values.append(None)
        if column_index >= MAX_DATASET_COLUMNS:
            continue
        cell_type = cell.attrib.get("t", "")
        raw = next((node.text or "" for node in cell.iter() if node.tag.endswith("}v")), "")
        if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
            value: CellValue = shared[int(raw)]
        elif cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
        elif cell_type == "b":
            value = raw == "1"
        else:
            value = _coerce_value(raw)
        values.append(value)
        expected_column = column_index + 1
    return values


def _xlsx_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    if "xl/workbook.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    return [
        (node.attrib.get("name") or f"Hoja {index + 1}")[:100]
        for index, node in enumerate(node for node in root.iter() if node.tag.endswith("}sheet"))
    ]


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    result = 0
    for character in letters.group(0):
        result = result * 26 + ord(character) - 64
    return max(0, result - 1)


def _sheet_from_rows(name: str, raw_rows: list[list[Any]]) -> DatasetSheet:
    nonempty = [
        row[:MAX_DATASET_COLUMNS] for row in raw_rows if any(str(value).strip() for value in row)
    ]
    if not nonempty:
        return DatasetSheet(name=name, columns=("Valor",), rows=(), total_rows=0)
    width = min(MAX_DATASET_COLUMNS, max(len(row) for row in nonempty))
    headers = _headers(nonempty[0], width)
    body = nonempty[1:]
    rows = tuple(
        tuple(_coerce_value(row[index] if index < len(row) else None) for index in range(width))
        for row in body[:MAX_DATASET_ROWS]
    )
    return DatasetSheet(
        name=name[:100] or "Datos",
        columns=headers,
        rows=rows,
        total_rows=len(body),
        truncated=len(body) > MAX_DATASET_ROWS,
    )


def _headers(values: list[Any], width: int) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    headers: list[str] = []
    for index in range(width):
        base = (
            str(values[index] if index < len(values) else "").strip()[:80] or f"Columna {index + 1}"
        )
        count = seen.get(base.casefold(), 0) + 1
        seen[base.casefold()] = count
        headers.append(base if count == 1 else f"{base} ({count})")
    return tuple(headers)


def _coerce_value(value: Any) -> CellValue:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    clean = str(value).strip()[:500]
    if not clean:
        return None
    normalized = clean.replace(" ", "")
    if re.fullmatch(r"-?\d+", normalized):
        try:
            return int(normalized)
        except ValueError:
            return clean
    if re.fullmatch(r"-?(?:\d+\.\d+|\d+,\d+)", normalized):
        try:
            return float(normalized.replace(",", "."))
        except ValueError:
            return clean
    return clean


def _select_sheet(snapshot: DatasetSnapshot, message: str) -> DatasetSheet:
    return next(
        (sheet for sheet in snapshot.sheets if sheet.name.casefold() in message), snapshot.sheets[0]
    )


def _build_charts(
    sheet: DatasetSheet,
    message: str,
    *,
    document_id: str,
    document_name: str,
    limit: int,
) -> tuple[ChartArtifact, ...]:
    """Build a compact analytical dashboard from the most informative column shapes."""

    primary = _build_chart(
        sheet,
        message,
        document_id=document_id,
        document_name=document_name,
    )
    if primary is None:
        return ()
    kinds = [_column_kind(sheet.rows, index) for index in range(len(sheet.columns))]
    numeric = [index for index, kind in enumerate(kinds) if kind == "number"]
    dimensions = [index for index, kind in enumerate(kinds) if kind != "number"]
    candidates: list[ChartArtifact | None] = [primary]
    if len(numeric) >= 2:
        correlation = _build_correlation_chart(
            sheet,
            numeric,
            message,
            document_id=document_id,
            document_name=document_name,
        )
        if primary.chart_type == "scatter" and correlation is not None:
            candidates[0] = correlation
        else:
            candidates.append(correlation)
    if dimensions and numeric:
        composition = _build_composition_chart(
            sheet,
            dimensions,
            numeric,
            message,
            document_id=document_id,
            document_name=document_name,
        )
        if primary.chart_type in {"pie", "donut"} and composition is not None:
            candidates[0] = composition.model_copy(update={"chart_type": primary.chart_type})
        else:
            candidates.append(composition)
    if numeric:
        distribution = _build_distribution_chart(
            sheet,
            numeric,
            message,
            document_id=document_id,
            document_name=document_name,
        )
        if any(term in message for term in ("distrib", "histograma")):
            candidates.insert(1, distribution)
        else:
            candidates.append(distribution)
    result: list[ChartArtifact] = []
    signatures: set[tuple[str, tuple[str, str]]] = set()
    for artifact in candidates:
        if artifact is None:
            continue
        signature = (artifact.chart_type, artifact.columns)
        if signature in signatures:
            continue
        result.append(artifact)
        signatures.add(signature)
        if len(result) >= limit:
            break
    return tuple(result)


def _build_chart(
    sheet: DatasetSheet, message: str, *, document_id: str, document_name: str
) -> ChartArtifact | None:
    if not sheet.rows or len(sheet.columns) < 2:
        return None
    kinds = [_column_kind(sheet.rows, index) for index in range(len(sheet.columns))]
    mentioned = [index for index, name in enumerate(sheet.columns) if name.casefold() in message]
    numeric = [index for index, kind in enumerate(kinds) if kind == "number"]
    dimensions = [index for index, kind in enumerate(kinds) if kind != "number"]
    chart_type: Literal["bar", "horizontal_bar", "line", "area", "pie", "donut", "scatter"] = "bar"
    if any(term in message for term in ("dispers", "scatter", "correl")) and len(numeric) >= 2:
        chart_type = "scatter"
    elif any(term in message for term in ("área", "area")):
        chart_type = "area"
    elif any(
        term in message
        for term in ("línea", "linea", "line", "tendencia", "evolución", "evolucion")
    ):
        chart_type = "line"
    elif any(term in message for term in ("horizontal", "ranking")):
        chart_type = "horizontal_bar"
    elif any(term in message for term in ("donut", "anillo", "rosquilla")):
        chart_type = "donut"
    elif any(term in message for term in ("pastel", "torta", "pie", "circular")):
        chart_type = "pie"
    if chart_type == "scatter":
        x = next((item for item in mentioned if item in numeric), numeric[0])
        y = next((item for item in mentioned if item in numeric and item != x), numeric[1])
        points = tuple(
            ChartPoint(x=float(row[x]), y=float(row[y]))
            for row in sheet.rows
            if _is_number(row[x]) and _is_number(row[y])
        )[:MAX_CHART_POINTS]
        if not points:
            return None
        columns = (sheet.columns[x], sheet.columns[y])
        description = f"Relación entre {columns[0]} y {columns[1]} con {len(points)} observaciones."
    else:
        dimension = next(
            (item for item in mentioned if item in dimensions), dimensions[0] if dimensions else 0
        )
        measure = next(
            (item for item in mentioned if item in numeric), numeric[0] if numeric else None
        )
        if chart_type == "bar" and kinds[dimension] == "date":
            chart_type = "line"
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in sheet.rows:
            label = str(row[dimension] if row[dimension] not in (None, "") else "Sin valor")[:80]
            if measure is None:
                grouped[label].append(1.0)
            elif _is_number(row[measure]):
                grouped[label].append(float(row[measure]))
        average = any(term in message for term in ("promedio", "media", "average"))
        values = [
            (label, sum(items) / len(items) if average else sum(items))
            for label, items in grouped.items()
            if items
        ]
        if chart_type in {"pie", "donut"}:
            values = [item for item in values if item[1] > 0]
        if chart_type in {"line", "area"}:
            values.sort(key=lambda item: _date_sort_key(item[0]))
        else:
            values.sort(key=lambda item: item[1], reverse=True)
        points = tuple(
            ChartPoint(x=label, y=round(value, 4)) for label, value in values[:MAX_CHART_POINTS]
        )
        if not points:
            return None
        metric = sheet.columns[measure] if measure is not None else "Registros"
        columns = (sheet.columns[dimension], metric)
        operation = "promedio" if average else "total" if measure is not None else "conteo"
        description = f"{operation.capitalize()} de {metric} agrupado por {columns[0]}."
    title = f"{columns[1]} por {columns[0]}"
    artifact = ChartArtifact(
        title=title,
        chart_type=chart_type,
        columns=columns,
        rows=points,
        source_document_id=document_id,
        source_name=document_name,
        sheet=sheet.name,
        description=description,
    )
    return _decorate_chart(artifact, sheet)


def _build_correlation_chart(
    sheet: DatasetSheet,
    numeric: list[int],
    message: str,
    *,
    document_id: str,
    document_name: str,
) -> ChartArtifact | None:
    mentioned = [index for index, name in enumerate(sheet.columns) if name.casefold() in message]
    x = next((item for item in mentioned if item in numeric), numeric[0])
    y = next((item for item in mentioned if item in numeric and item != x), numeric[1])
    pairs = [
        (float(row[x]), float(row[y]))
        for row in sheet.rows
        if x < len(row) and y < len(row) and _is_number(row[x]) and _is_number(row[y])
    ]
    sampled = _even_sample(pairs, MAX_CHART_POINTS)
    if len(sampled) < 3:
        return None
    coefficient = _pearson(pairs)
    strength = (
        "fuerte" if abs(coefficient) >= 0.7 else "moderada" if abs(coefficient) >= 0.4 else "débil"
    )
    direction = "positiva" if coefficient >= 0 else "negativa"
    return ChartArtifact(
        title=f"Relación entre {sheet.columns[x]} y {sheet.columns[y]}",
        chart_type="scatter",
        columns=(sheet.columns[x], sheet.columns[y]),
        rows=tuple(ChartPoint(x=round(a, 4), y=round(b, 4)) for a, b in sampled),
        source_document_id=document_id,
        source_name=document_name,
        sheet=sheet.name,
        description="Diagrama de dispersión con línea de tendencia para detectar relaciones entre métricas.",
        variant="correlation",
        metrics=(
            ChartMetric(label="Correlación", value=f"{coefficient:.2f}"),
            ChartMetric(label="Observaciones", value=str(len(pairs))),
            ChartMetric(
                label=f"Media {sheet.columns[x]}"[:40],
                value=_compact_number(_mean(a for a, _ in pairs)),
            ),
            ChartMetric(
                label=f"Media {sheet.columns[y]}"[:40],
                value=_compact_number(_mean(b for _, b in pairs)),
            ),
        ),
        insights=(f"La relación observada es {strength} y {direction}.",),
    )


def _build_composition_chart(
    sheet: DatasetSheet,
    dimensions: list[int],
    numeric: list[int],
    message: str,
    *,
    document_id: str,
    document_name: str,
) -> ChartArtifact | None:
    mentioned = [index for index, name in enumerate(sheet.columns) if name.casefold() in message]
    dimension = next((item for item in mentioned if item in dimensions), dimensions[0])
    measure = next((item for item in mentioned if item in numeric), numeric[0])
    grouped: dict[str, float] = defaultdict(float)
    for row in sheet.rows:
        if dimension >= len(row) or measure >= len(row) or not _is_number(row[measure]):
            continue
        label = str(row[dimension] if row[dimension] not in (None, "") else "Sin valor")[:80]
        grouped[label] += float(row[measure])
    ranked = sorted(
        ((label, value) for label, value in grouped.items() if value > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    if not 2 <= len(ranked) <= 80:
        return None
    visible = ranked[:8]
    remainder = sum(value for _, value in ranked[8:])
    if remainder:
        visible.append(("Otros", remainder))
    total = sum(value for _, value in ranked)
    leader, leader_value = ranked[0]
    share = (leader_value / total * 100) if total else 0
    return ChartArtifact(
        title=f"Composición de {sheet.columns[measure]}",
        chart_type="donut",
        columns=(sheet.columns[dimension], sheet.columns[measure]),
        rows=tuple(ChartPoint(x=label, y=round(value, 4)) for label, value in visible),
        source_document_id=document_id,
        source_name=document_name,
        sheet=sheet.name,
        description=f"Participación relativa de {sheet.columns[measure]} por {sheet.columns[dimension]}.",
        variant="composition",
        metrics=(
            ChartMetric(label="Total", value=_compact_number(total)),
            ChartMetric(label="Categorías", value=str(len(ranked))),
            ChartMetric(label="Líder", value=leader[:60]),
            ChartMetric(label="Participación líder", value=f"{share:.1f}%"),
        ),
        insights=(f"{leader} concentra el {share:.1f}% del total analizado.",),
    )


def _build_distribution_chart(
    sheet: DatasetSheet,
    numeric: list[int],
    message: str,
    *,
    document_id: str,
    document_name: str,
) -> ChartArtifact | None:
    mentioned = [index for index, name in enumerate(sheet.columns) if name.casefold() in message]
    measure = next((item for item in mentioned if item in numeric), numeric[0])
    values = sorted(
        float(row[measure]) for row in sheet.rows if measure < len(row) and _is_number(row[measure])
    )
    if len(values) < 4:
        return None
    minimum, maximum = values[0], values[-1]
    if math.isclose(minimum, maximum):
        points = (ChartPoint(x=_compact_number(minimum), y=len(values)),)
    else:
        bucket_count = min(10, max(5, round(math.sqrt(len(values)))))
        width = (maximum - minimum) / bucket_count
        counts = [0] * bucket_count
        for value in values:
            index = min(bucket_count - 1, int((value - minimum) / width))
            counts[index] += 1
        points = tuple(
            ChartPoint(
                x=f"{_compact_number(minimum + index * width)}–{_compact_number(minimum + (index + 1) * width)}",
                y=count,
            )
            for index, count in enumerate(counts)
        )
    median = (
        values[len(values) // 2]
        if len(values) % 2
        else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
    )
    return ChartArtifact(
        title=f"Distribución de {sheet.columns[measure]}",
        chart_type="bar",
        columns=("Rango", "Frecuencia"),
        rows=points,
        source_document_id=document_id,
        source_name=document_name,
        sheet=sheet.name,
        description=f"Histograma para revelar concentración y dispersión de {sheet.columns[measure]}.",
        variant="distribution",
        metrics=(
            ChartMetric(label="Mínimo", value=_compact_number(minimum)),
            ChartMetric(label="Mediana", value=_compact_number(median)),
            ChartMetric(label="Promedio", value=_compact_number(_mean(values))),
            ChartMetric(label="Máximo", value=_compact_number(maximum)),
        ),
        insights=(
            f"El rango observado abarca de {_compact_number(minimum)} a {_compact_number(maximum)}.",
        ),
    )


def _decorate_chart(artifact: ChartArtifact, sheet: DatasetSheet) -> ChartArtifact:
    values = [float(point.y) for point in artifact.rows if _is_number(point.y)]
    if not values:
        return artifact
    leader = max(
        artifact.rows, key=lambda point: float(point.y) if _is_number(point.y) else -math.inf
    )
    variant = {
        "line": "trend",
        "area": "trend",
        "pie": "composition",
        "donut": "composition",
        "scatter": "correlation",
    }.get(artifact.chart_type, "comparison")
    return artifact.model_copy(
        update={
            "variant": variant,
            "metrics": (
                ChartMetric(label="Filas analizadas", value=str(sheet.total_rows)),
                ChartMetric(label="Total", value=_compact_number(sum(values))),
                ChartMetric(label="Promedio", value=_compact_number(_mean(values))),
                ChartMetric(label="Máximo", value=_compact_number(max(values))),
            ),
            "insights": (
                f"{leader.x} presenta el valor más alto: {_compact_number(float(leader.y))}.",
            ),
        }
    )


def _even_sample(values: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    if len(values) <= limit:
        return values
    return [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]


def _pearson(values: list[tuple[float, float]]) -> float:
    if len(values) < 2:
        return 0.0
    mean_x = _mean(a for a, _ in values)
    mean_y = _mean(b for _, b in values)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in values)
    denominator = math.sqrt(
        sum((a - mean_x) ** 2 for a, _ in values) * sum((b - mean_y) ** 2 for _, b in values)
    )
    return numerator / denominator if denominator else 0.0


def _mean(values: Any) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else 0.0


def _compact_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f} mil M"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f} M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f} mil"
    if math.isclose(value, round(value)):
        return f"{value:,.0f}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date_sort_key(value: str) -> tuple[int, str]:
    try:
        return (0, datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat())
    except ValueError:
        return (1, value.casefold())


def _column_kind(rows: tuple[tuple[CellValue, ...], ...], index: int) -> str:
    values = [row[index] for row in rows[:200] if index < len(row) and row[index] not in (None, "")]
    if values and sum(_is_number(value) for value in values) / len(values) >= 0.8:
        return "number"
    if values and sum(_is_date(value) for value in values) / len(values) >= 0.8:
        return "date"
    return "text"


def _is_number(value: CellValue) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_date(value: CellValue) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False
