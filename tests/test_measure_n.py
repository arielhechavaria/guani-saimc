"""`python -m saimc.validation.measure_n` (slice C3, control C1).

Caso 10 de `docs/spec-slice-C3.md`, «Tests primero». Lo esperado sale de la
especificación: valores todos iguales dan error 0 para todo k; la misma semilla
da lo mismo; una celda de 19 filas no entra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, Product, Province, RawIngestion, SaleFormat
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.validation.measure_n import (
    MIN_CELL_ROWS,
    SAMPLE_SIZES,
    SUBSAMPLES,
    execute,
    measure,
    print_report,
)

#: 20 precios distintos, de 100 a 290.
VARIED = [Decimal(100 + 10 * i) for i in range(20)]


class TestMeasure:
    def test_las_constantes_son_las_de_la_especificacion(self) -> None:
        assert MIN_CELL_ROWS == 20
        assert list(SAMPLE_SIZES) == list(range(2, 16))
        assert SUBSAMPLES == 1000

    def test_valores_todos_iguales_dan_error_cero_para_todo_k(self) -> None:
        results = measure([[Decimal("100")] * 20])

        assert [r.k for r in results] == list(range(2, 16))
        assert all(r.median_error == 0 and r.p90_error == 0 for r in results)
        assert all(r.cells == 1 for r in results)

    def test_con_la_misma_semilla_dos_corridas_dan_lo_mismo(self) -> None:
        assert measure([VARIED]) == measure([VARIED])

    def test_no_depende_del_orden_en_que_llegan_las_filas(self) -> None:
        # La base no garantiza el orden: el resultado no puede colgar de él.
        assert measure([VARIED]) == measure([list(reversed(VARIED))])

    def test_otra_semilla_da_otra_cosa(self) -> None:
        assert measure([VARIED], seed=1) != measure([VARIED], seed=2)

    def test_una_celda_de_19_filas_no_entra(self) -> None:
        assert measure([VARIED[:19]]) == []
        assert all(r.cells == 1 for r in measure([VARIED, VARIED[:19]]))

    def test_el_error_no_crece_con_k(self) -> None:
        # Con 15 de 20 valores la mediana de la submuestra no puede alejarse
        # tanto como con 2: el p90 del error en k=15 es menor que en k=2.
        results = {r.k: r for r in measure([VARIED])}

        assert results[15].p90_error < results[2].p90_error
        assert results[2].p90_error > 0


class TestPrintReport:
    def test_sin_celdas_avisa_y_no_inventa_una_tabla(self) -> None:
        lines: list[str] = []
        print_report([], total_cells=3, out=lines.append)

        text = "\n".join(lines)
        assert "Ninguna celda llega a 20 filas" in text
        assert "k=" not in text and "p90" not in text

    def test_con_celdas_informa_una_linea_por_k_y_no_fija_n(self) -> None:
        lines: list[str] = []
        print_report(measure([VARIED]), total_cells=1, out=lines.append)

        text = "\n".join(lines)
        assert "p90" in text
        assert sum(1 for line in lines if line.lstrip().split(" ")[0].isdigit()) == 14
        assert "No fija N" in text


class TestExecute:
    """El comando contra la base: lee las celdas públicas y no escribe nada."""

    def test_con_la_base_vacia_avisa_y_sale_bien(self, session: Session) -> None:
        lines: list[str] = []

        assert execute(session, out=lines.append) == 0
        assert "Ninguna celda llega a 20 filas" in "\n".join(lines)

    def test_mide_una_celda_de_20_filas_y_no_toca_ninguna_fila(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        scu = Province(code="scu", name="Santiago de Cuba")
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/n.txt",
            content_hash="hash-n",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        session.add_all([arroz, scu, origen])
        session.flush()
        for _ in range(20):
            session.add(
                PriceHistory(
                    provenance="human",
                    product_id=arroz.id,
                    province_id=scu.id,
                    price=Decimal("100"),
                    currency=Currency.CUP.value,
                    unit="lb",
                    quantity=Decimal("1"),
                    sale_format=SaleFormat.RETAIL.value,
                    published_date=date.today(),
                    source_id=origen.id,
                )
            )
        session.commit()
        lines: list[str] = []

        assert execute(session, out=lines.append) == 0

        text = "\n".join(lines)
        assert "Celdas con 20 filas o más: 1." in text
        # Todos los precios iguales: error 0 en las 14 líneas.
        assert text.count("0,0%") == 28
        assert not session.dirty and not session.new
