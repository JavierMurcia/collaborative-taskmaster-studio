from __future__ import annotations

import io
import zipfile

from app.api.router import _chart_aware_reply
from studio.capabilities.datasets import DatasetAnalysisService
from studio.capabilities.documents import DocumentLibrary


def test_csv_is_preserved_as_structured_data_and_generates_a_chart(tmp_path) -> None:
    library = DocumentLibrary(tmp_path)
    record = library.add(
        "owner",
        "ventas.csv",
        b"mes,ventas\nEnero,120\nFebrero,180\nEnero,30\n",
    )

    inspected = library.inspect("owner", record.id)
    dataset = inspected["dataset"]

    assert dataset["sheets"][0]["columns"] == ["mes", "ventas"]
    assert dataset["sheets"][0]["total_rows"] == 3
    artifacts = DatasetAnalysisService().analyze(
        "Crea una gráfica de ventas por mes", (inspected,)
    )
    assert len(artifacts) == 1
    assert artifacts[0].chart_type == "bar"
    assert artifacts[0].columns == ("mes", "ventas")
    assert [(point.x, point.y) for point in artifacts[0].rows] == [
        ("Febrero", 180.0),
        ("Enero", 150.0),
    ]


def test_dataset_analysis_does_not_run_without_an_explicit_data_request(tmp_path) -> None:
    library = DocumentLibrary(tmp_path)
    record = library.add("owner", "datos.csv", b"categoria,valor\nA,4\nB,8\n")

    artifacts = DatasetAnalysisService().analyze(
        "Hola, que puedes hacer?", (library.inspect("owner", record.id),)
    )

    assert artifacts == ()


def test_explicit_demo_request_renders_charts_instead_of_requiring_python() -> None:
    artifacts = DatasetAnalysisService().analyze(
        "Genera gráficos visuales con datos aleatorios", ()
    )

    assert [artifact.chart_type for artifact in artifacts] == ["line", "bar"]
    assert all(artifact.source_document_id is None for artifact in artifacts)
    assert all(artifact.source_name == "Datos simulados" for artifact in artifacts)
    assert artifacts[0].rows == DatasetAnalysisService().analyze(
        "Genera gráficos visuales con datos aleatorios", ()
    )[0].rows


def test_plain_chart_request_renders_demo_instead_of_returning_code() -> None:
    artifacts = DatasetAnalysisService().analyze("genera graficas", ())

    assert len(artifacts) == 2
    assert all(
        artifact.model_dump(mode="json")["rows"][0].keys() == {"x", "y"}
        for artifact in artifacts
    )


def test_multiple_attached_datasets_each_generate_a_chart(tmp_path) -> None:
    library = DocumentLibrary(tmp_path)
    first = library.add("owner", "ventas.csv", b"mes,ventas\nEnero,120\nFebrero,180\n")
    second = library.add("owner", "costos.csv", b"area,costo\nProducto,80\nSoporte,45\n")

    artifacts = DatasetAnalysisService().analyze(
        "Genera graficas para todos los archivos",
        (library.inspect("owner", first.id), library.inspect("owner", second.id)),
    )

    assert [artifact.source_name for artifact in artifacts] == ["ventas.csv", "costos.csv"]
    assert all(
        isinstance(artifact.model_dump(mode="json")["rows"][0], dict)
        for artifact in artifacts
    )
    reply = _chart_aware_reply(
        "Genera graficas para todos los archivos",
        "Aquí tienes código con matplotlib.",
        artifacts,
        (library.inspect("owner", first.id), library.inspect("owner", second.id)),
    )
    assert "código" in reply
    assert "matplotlib" not in reply.casefold()


def test_xlsx_keeps_sheet_names_columns_and_numeric_values(tmp_path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Resumen" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Equipo</t></is></c><c r="B1" t="inlineStr"><is><t>Puntos</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Norte</t></is></c><c r="B2"><v>42</v></c></row></sheetData></worksheet>',
        )
    library = DocumentLibrary(tmp_path)

    record = library.add("owner", "reporte.xlsx", payload.getvalue())
    dataset = library.inspect("owner", record.id)["dataset"]

    assert dataset["sheets"][0]["name"] == "Resumen"
    assert dataset["sheets"][0]["columns"] == ["Equipo", "Puntos"]
    assert dataset["sheets"][0]["rows"] == [["Norte", 42]]
