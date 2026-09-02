"""Retro-completado de `price_basis` en las filas anteriores al campo.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el inferidor.

Lo que se prueba es la regla, no un veredicto sobre una fila concreta: entre
las dos lecturas posibles del mismo número se elige la que queda
proporcionalmente más cerca de la mediana de las filas **no ambiguas** de su
propio vecindario. Sin corte de precio, sin margen mínimo y sin muestra
mínima — §12.6 prohíbe inventar cualquiera de los tres. Cuando no hay
vecindario contra el que contrastar **no hay veredicto**, que es distinto de
un veredicto por defecto.

El par que gobierna el diseño son los dos primeros casos: «saco de 110 lb a
33.000» y «picadillo 40 lb a 525» tienen la misma forma —cantidad mayor a
uno, precio y unidad— y lecturas opuestas. Cualquier regla que mire la
cantidad para decidir acierta uno y falla el otro
(`engine.py:_declared_price_basis`, docs/decisions.md 2026-08-25).
"""

import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from saimc.basis_backfill import apply_backfill, infer_basis, plan_backfill
from saimc.models import PriceHistory, Product, Province, SaleFormat
from tests.test_cells import _ingestion, _precio

RETAIL = SaleFormat.RETAIL.value
WHOLESALE = SaleFormat.WHOLESALE.value


class TestInferBasis:
    """La regla pura, sin base de datos."""

    def test_elige_total_cuando_el_lote_encaja_con_el_vecindario(self) -> None:
        """«Sacos de 110 lb a 33.000 cup», vecindario en 310 CUP/lb.

        Leída como unitaria la fila declara 33.000/lb y se aparta ~106 veces.
        Leída como total da 300/lb, a 1,03 de la mediana. Gana `total`.
        """
        resultado = infer_basis(
            Decimal("33000"),
            Decimal("110"),
            row_unit="lb",
            target_unit="lb",
            reference=Decimal("310"),
        )

        assert resultado == "total"

    def test_elige_unit_con_la_misma_forma_y_respuesta_opuesta(self) -> None:
        """«Picadillo de pollo americano 40 lb a 525», vecindario en 500 CUP/lb.

        Misma forma que el saco —cantidad 40, unidad lb— y la respuesta es la
        contraria: 525 se aparta 1,05 veces de la mediana, mientras que leerlo
        como total daría 13,125/lb, a 38 veces. Gana `unit`.

        Este caso existe para que ninguna regla que mire la cantidad pueda
        pasar la suite.
        """
        resultado = infer_basis(
            Decimal("525"),
            Decimal("40"),
            row_unit="lb",
            target_unit="lb",
            reference=Decimal("500"),
        )

        assert resultado == "unit"

    def test_convierte_la_unidad_antes_de_comparar(self) -> None:
        """Fila en kg contra un vecindario en lb: «arroz de 50 kg a 31.000».

        Como total son 620 CUP/kg, que en libras son 620 × 0,45359237 =
        281,23 — a 1,10 de la mediana de 310. Como unitaria serían 31.000
        CUP/kg = 14.061,36 CUP/lb, a 45 veces. Gana `total`.

        Sin la conversión las dos lecturas se compararían contra una mediana
        de otra unidad y el resultado no significaría nada.
        """
        resultado = infer_basis(
            Decimal("31000"),
            Decimal("50"),
            row_unit="kg",
            target_unit="lb",
            reference=Decimal("310"),
        )

        assert resultado == "total"

    def test_sin_vecindario_no_hay_veredicto(self) -> None:
        """`None` es «no se sabe», no un valor por defecto."""
        resultado = infer_basis(
            Decimal("33000"),
            Decimal("110"),
            row_unit="lb",
            target_unit="lb",
            reference=None,
        )

        assert resultado is None

    def test_unidad_no_convertible_no_produce_veredicto(self) -> None:
        """`caja` no dice cuánto contiene: las dos lecturas son incomparables."""
        resultado = infer_basis(
            Decimal("23352"),
            Decimal("4"),
            row_unit="caja",
            target_unit="lb",
            reference=Decimal("310"),
        )

        assert resultado is None

    def test_cantidad_uno_no_es_candidata(self) -> None:
        """Con cantidad uno, unitario y total son el mismo número."""
        resultado = infer_basis(
            Decimal("310"),
            Decimal("1"),
            row_unit="lb",
            target_unit="lb",
            reference=Decimal("310"),
        )

        assert resultado is None

    def test_precio_no_positivo_no_produce_veredicto(self) -> None:
        """Una distancia proporcional contra cero no ordena nada."""
        resultado = infer_basis(
            Decimal("0"),
            Decimal("110"),
            row_unit="lb",
            target_unit="lb",
            reference=Decimal("310"),
        )

        assert resultado is None


def _arroz(session: Session) -> tuple[Product, Province]:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
    habana = Province(code="hab", name="La Habana")
    session.add_all([arroz, habana])
    session.commit()
    return arroz, habana


class TestPlanBackfill:
    """Qué filas entran al plan y contra qué vecindario se resuelven."""

    def test_propone_la_fila_sin_basis_contra_las_no_ambiguas_de_su_celda(
        self, session: Session
    ) -> None:
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-plan")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        plan = plan_backfill(session)

        assert len(plan) == 1
        entrada = plan[0]
        assert entrada.basis == "total"
        assert entrada.reference == Decimal("310")
        assert entrada.reference_count == 3
        assert entrada.product_name == "Arroz"

    def test_una_celda_sin_filas_no_ambiguas_no_produce_plan(
        self, session: Session
    ) -> None:
        """Inventarle un veredicto sería exactamente lo que §12.6 prohíbe."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-sin-ref")
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        assert plan_backfill(session) == []

    def test_la_fila_ya_resuelta_no_se_toca(self, session: Session) -> None:
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-resuelta")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL, price_basis="total",
        )
        session.commit()

        assert plan_backfill(session) == []

    def test_una_fila_unit_mal_curada_no_envenena_el_vecindario(
        self, session: Session
    ) -> None:
        """Caso real: `24800 / 40 lb` de Pollo marcado `unit` por error.

        Si esa fila votara la referencia, la mediana de la celda saltaría de
        ~710 a ~12.755 CUP/lb y la regla escribiría `unit` sobre filas que son
        `total`. `price_basis == 'unit'` con `quantity != 1` es justo la forma
        que se puede haber curado mal, así que no cuenta como referencia —
        misma definición que `detect_basis_suspects`.
        """
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([pollo, santiago])
        session.commit()
        origen = _ingestion(session, "hash-veneno")

        # Única referencia legítima: un lote de 10 lb a 7.100, basis total -> 710/lb.
        _precio(
            session, product=pollo, province=santiago, price="7100", source=origen,
            unit="lb", quantity="10", sale_format=RETAIL, price_basis="total",
        )
        # Fila mal curada: 24.800 por caja de 40 lb, marcada unit.
        _precio(
            session, product=pollo, province=santiago, price="24800", source=origen,
            unit="lb", quantity="40", sale_format=RETAIL, price_basis="unit",
        )
        # Candidata: 7.300 por 10 lb, sin basis. La lectura correcta es total (730/lb).
        _precio(
            session, product=pollo, province=santiago, price="7300", source=origen,
            unit="lb", quantity="10", sale_format=RETAIL,
        )
        session.commit()

        plan = plan_backfill(session)

        assert len(plan) == 1
        assert plan[0].basis == "total"
        assert plan[0].reference == Decimal("710")
        assert plan[0].reference_count == 1

    def test_el_vecindario_no_cruza_el_formato_de_venta(
        self, session: Session
    ) -> None:
        """Mayorista es más barato por unidad: es economía real, no ruido.

        Las tres filas minoristas no pueden servir de referencia a una fila
        mayorista — fundirlas afirmaría una equivalencia que `cells.py` separa
        deliberadamente desde el 2026-08-12.
        """
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-formato")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="26500", source=origen,
            unit="lb", quantity="110", sale_format=WHOLESALE,
        )
        session.commit()

        assert plan_backfill(session) == []


class TestApplyBackfill:
    """La escritura y su registro de procedencia."""

    def test_escribe_el_basis_y_deja_el_rastro_en_el_log(
        self, session: Session, tmp_path: Path
    ) -> None:
        """El valor inferido queda distinguible del curado, fuera de la fila.

        Es el mismo criterio que `data/llm-assist-log.jsonl` (design.md, D6):
        la procedencia vive en un log de solo-anexado y no en una columna
        nueva, así no hace falta migración para que el dato siga siendo
        auditable.
        """
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-apply")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        log = tmp_path / "basis-backfill.jsonl"
        escritas = apply_backfill(session, plan_backfill(session), log_path=log)

        assert escritas == 1
        registro = json.loads(log.read_text(encoding="utf-8").strip())
        assert registro["basis"] == "total"
        assert registro["source"] == "inferred_vs_curated_median"
        assert registro["reference_count"] == 3
        assert "price_id" in registro

    def test_el_log_es_de_solo_anexado(
        self, session: Session, tmp_path: Path
    ) -> None:
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-anexado")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        log = tmp_path / "basis-backfill.jsonl"
        log.write_text('{"price_id": "previo"}\n', encoding="utf-8")
        apply_backfill(session, plan_backfill(session), log_path=log)

        lineas = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(lineas) == 2
        assert json.loads(lineas[0])["price_id"] == "previo"

    def test_no_cuenta_ni_pisa_la_fila_que_el_operador_resolvio_mientras_tanto(
        self, session: Session, tmp_path: Path
    ) -> None:
        """Lo curado gana siempre sobre lo inferido, y el conteo lo refleja."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-carrera")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        plan = plan_backfill(session)
        assert len(plan) == 1

        # El operador la cura entre el plan y la escritura, y en el otro sentido.
        fila = session.get(PriceHistory, plan[0].price_id)
        assert fila is not None
        fila.price_basis = "unit"
        session.commit()

        log = tmp_path / "basis-backfill.jsonl"
        assert apply_backfill(session, plan, log_path=log) == 0
        assert session.get(PriceHistory, plan[0].price_id).price_basis == "unit"
        assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""

    def test_correrlo_dos_veces_no_duplica_nada(
        self, session: Session, tmp_path: Path
    ) -> None:
        """La segunda corrida no encuentra candidatas: ya no hay basis nulo."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-idempotente")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="33000", source=origen,
            unit="lb", quantity="110", sale_format=RETAIL,
        )
        session.commit()

        log = tmp_path / "basis-backfill.jsonl"
        assert apply_backfill(session, plan_backfill(session), log_path=log) == 1
        assert apply_backfill(session, plan_backfill(session), log_path=log) == 0
        assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1
