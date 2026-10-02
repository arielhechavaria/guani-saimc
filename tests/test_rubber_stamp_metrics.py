"""`saimc.web.rubber_stamp_metrics` — vigilancia del rubber-stamping (slice 5).

Lo que se prueba acá es la agregación pura sobre `LlmReview.rows`: tasa de
"no editado" por campo y agregada, mediana de tiempo por decisión, y el
criterio direccional de la alerta. Casos sintéticos armados a mano, no el
corpus de 300 anuncios — esto es lógica de agregación, no extracción de H0.

Desde A2c la métrica se recalcula a partir de `proposed` y `final` (lo único
que importa de cada fila), no del `edited` guardado: ese campo trataba una fila
borrada como «sin editar». Las expectativas de cada categoría están escritas a
mano, antes que el código.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from saimc.models import Channel, LlmProposal, LlmReview, Platform, Province, RawIngestion
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.web import rubber_stamp_metrics as metrics

AHORA = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
DIA = timedelta(days=1)


def _ingesta(session: Session, sufijo: str, channel_id=None) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT if channel_id is None else SourceType.WHATSAPP_CHANNEL,
        source_reference=None if channel_id is None else "canal",
        channel_id=channel_id,
        file_path=f"raw/{sufijo}.txt",
        content_hash=f"hash-{sufijo}",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=AHORA,
    )
    session.add(ingestion)
    session.flush()
    return ingestion


def _proposal(session: Session, ingestion: RawIngestion) -> LlmProposal:
    proposal = LlmProposal(
        ingestion_id=ingestion.id,
        model="m",
        prompt_version="v",
        status="ok",
        rows=[],
        input_tokens=0,
        output_tokens=0,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _review(
    session: Session,
    *,
    rows: list[dict],
    outcome: str = "curated",
    created_at: datetime = AHORA,
    shown_at: datetime | None = None,
    sufijo: str = "r",
    channel_id=None,
) -> LlmReview:
    ingestion = _ingesta(session, sufijo, channel_id)
    proposal = _proposal(session, ingestion)
    review = LlmReview(
        ingestion_id=ingestion.id,
        proposal_id=proposal.id,
        outcome=outcome,
        rows=rows,
        created_at=created_at,
        shown_at=shown_at,
    )
    session.add(review)
    session.commit()
    return review


def _fila(proposed: dict | None, final: dict | None) -> dict:
    """Una entrada de `LlmReview.rows`. `edited` va a propósito vacío: la
    métrica ya no debe leerlo (era la fuente del bug)."""
    return {
        "proposal_index": 0 if proposed is not None else None,
        "price_history_id": "x" if final is not None else None,
        "proposed": proposed,
        "final": final,
        "edited": [],
    }


_PROPUESTA_COMPLETA = {
    "product_id": "p",
    "price": "100",
    "currency": "CUP",
    "quantity": "1",
    "unit": "lb",
    "province_id": "prov",
    "sale_format": "retail",
    "price_basis": "unit",
    "packaging": None,
}


def _final(**cambios: str | None) -> dict:
    """`final` idéntico a la propuesta completa, salvo los campos pasados."""
    return {**_PROPUESTA_COMPLETA, **cambios}


def _ventana() -> tuple[datetime, datetime]:
    return AHORA - DIA, AHORA + DIA


class TestDesgloseDeCuatroCategorias:
    """Una revisión sintética por categoría, más el artefacto de provincia.
    Cuenta por celda (fila × campo propuesto por el modelo)."""

    def test_sin_cambios(self, session: Session) -> None:
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final())], sufijo="s1")

        total = metrics.outcome_breakdown(session, *_ventana())["total"]

        assert total == {"unchanged": 9, "filled": 0, "corrected": 0, "deleted": 0}

    def test_completo_un_vacio(self, session: Session) -> None:
        # El modelo se abstuvo con el envase (None); el operador lo llenó.
        _review(
            session,
            rows=[_fila(_PROPUESTA_COMPLETA, _final(packaging="bolsa"))],
            sufijo="s2",
        )

        desglose = metrics.outcome_breakdown(session, *_ventana())

        assert desglose["by_field"]["packaging"] == {
            "unchanged": 0, "filled": 1, "corrected": 0, "deleted": 0,
        }
        # Llenar un vacío no corrige nada de lo demás.
        assert desglose["total"] == {"unchanged": 8, "filled": 1, "corrected": 0, "deleted": 0}

    def test_corrigio_un_valor(self, session: Session) -> None:
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final(price="120"))], sufijo="s3")

        desglose = metrics.outcome_breakdown(session, *_ventana())

        assert desglose["by_field"]["price"] == {
            "unchanged": 0, "filled": 0, "corrected": 1, "deleted": 0,
        }
        assert desglose["total"] == {"unchanged": 8, "filled": 0, "corrected": 1, "deleted": 0}

    def test_borro_la_fila(self, session: Session) -> None:
        # `final` es None: el operador borró la fila que el modelo propuso.
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, None)], sufijo="s4")

        total = metrics.outcome_breakdown(session, *_ventana())["total"]

        assert total == {"unchanged": 0, "filled": 0, "corrected": 0, "deleted": 9}

    def test_provincia_fija_del_canal_no_cuenta_como_edicion(self, session: Session) -> None:
        # El modelo propuso "prov", pero el canal fija otra provincia y el
        # formulario la pisa: eso no lo decidió el operador. El campo queda
        # fuera de la métrica, ni corregido ni sin cambios.
        habana = Province(code="LH", name="La Habana")
        session.add(habana)
        session.flush()
        canal = Channel(
            name="canal", platform=Platform.WHATSAPP_CHANNEL,
            province_id=habana.id, is_active=True,
        )
        session.add(canal)
        session.commit()
        _review(
            session,
            rows=[_fila(_PROPUESTA_COMPLETA, _final(province_id=str(habana.id)))],
            sufijo="s5",
            channel_id=canal.id,
        )

        desglose = metrics.outcome_breakdown(session, *_ventana())

        assert desglose["by_field"]["province_id"] == {
            "unchanged": 0, "filled": 0, "corrected": 0, "deleted": 0,
        }
        assert desglose["total"] == {"unchanged": 8, "filled": 0, "corrected": 0, "deleted": 0}

    def test_no_editado_no_cuenta_filas_borradas_como_aceptadas(self, session: Session) -> None:
        # Una fila aceptada y una borrada: la tasa es 9 de 18, no 18 de 18.
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final())], sufijo="s6a")
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, None)], sufijo="s6b")

        rates = metrics.no_edit_rate_by_field(session, *_ventana())

        assert rates["price"] == (1, 2)
        assert metrics._aggregate_rate(rates) == (9, 18)


class TestNoEditRateByField:
    def test_mezcla_de_editado_y_no_editado_por_campo(self, session: Session) -> None:
        # Una fila donde el operador tocó "price", el resto de los campos
        # quedó como propuso el modelo.
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final(price="120"))], sufijo="a")

        rates = metrics.no_edit_rate_by_field(session, *_ventana())

        assert rates["price"] == (0, 1)
        assert rates["product_id"] == (1, 1)
        assert rates["currency"] == (1, 1)

    def test_filas_agregadas_a_mano_no_cuentan_en_el_denominador(
        self, session: Session
    ) -> None:
        # `proposed` es None: fila agregada por el operador, sin propuesta del
        # modelo para comparar.
        _review(session, rows=[_fila(None, _final())], sufijo="b")

        rates = metrics.no_edit_rate_by_field(session, *_ventana())

        assert rates["product_id"] == (0, 0)
        assert rates["price"] == (0, 0)

    def test_ventana_sin_datos_da_todo_en_cero(self, session: Session) -> None:
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final())], sufijo="c")

        rates = metrics.no_edit_rate_by_field(session, AHORA + 10 * DIA, AHORA + 20 * DIA)

        assert all(num == 0 and den == 0 for num, den in rates.values())

    def test_descartados_no_cuentan(self, session: Session) -> None:
        _review(
            session,
            rows=[_fila(_PROPUESTA_COMPLETA, _final())],
            outcome="discarded",
            sufijo="d",
        )

        rates = metrics.no_edit_rate_by_field(session, *_ventana())

        assert all(num == 0 and den == 0 for num, den in rates.values())


class TestAgregadoEsSumaDeTodosLosCamposNoPromedioDeTasas:
    """El agregado tiene que ser (suma de numeradores)/(suma de denominadores)
    de TODOS los campos, no el promedio de las tasas por campo — un campo con
    pocos datos no puede pesar igual que uno con muchos. Distingue las dos
    cuentas a propósito: si `rubber_stamp_alert` promediara tasas en vez de
    sumar, este test da un número distinto y falla."""

    def test_agregado_pondera_por_cantidad_de_datos_no_por_campo(
        self, session: Session
    ) -> None:
        # Fila A: propuesta completa (9 campos), nada editado.
        _review(session, rows=[_fila(_PROPUESTA_COMPLETA, _final())], sufijo="k1")
        # Fila B: propuesta parcial (solo "product_id"), y esa sí se editó.
        _review(
            session,
            rows=[_fila({"product_id": "p"}, _final(product_id="q"))],
            sufijo="k2",
        )

        resultado = metrics.rubber_stamp_alert(session, now=AHORA + DIA)
        actual = resultado["current"]["no_edit_rate"]

        # Suma/suma: 9 campos no editados de A (num=9) + 0 de B, sobre
        # denominador 9 (campos de A) + 1 (product_id de B) = 9/10.
        assert actual == 9 / 10


class TestDecisionTimeStats:
    def test_mediana_y_n_sobre_shown_at_valido(self, session: Session) -> None:
        _review(
            session,
            rows=[],
            shown_at=AHORA - timedelta(seconds=10),
            created_at=AHORA,
            sufijo="e1",
        )
        _review(
            session,
            rows=[],
            shown_at=AHORA - timedelta(seconds=30),
            created_at=AHORA,
            sufijo="e2",
        )

        stats = metrics.decision_time_stats(session, *_ventana())

        assert stats["n"] == 2
        assert stats["median"] == 20.0

    def test_sin_shown_at_no_entra_en_la_muestra(self, session: Session) -> None:
        _review(session, rows=[], shown_at=None, sufijo="f")

        stats = metrics.decision_time_stats(session, *_ventana())

        assert stats["n"] == 0
        assert stats["median"] is None

    def test_duracion_negativa_se_descarta(self, session: Session) -> None:
        # shown_at posterior a created_at: reloj raro / parseo mal hecho.
        _review(
            session,
            rows=[],
            shown_at=AHORA + timedelta(seconds=60),
            created_at=AHORA,
            sufijo="g",
        )

        stats = metrics.decision_time_stats(session, *_ventana())

        assert stats["n"] == 0
        assert stats["median"] is None


class TestRubberStampAlert:
    def _sembrar(
        self,
        session: Session,
        *,
        when: datetime,
        no_edit: bool,
        segundos_decision: float,
        sufijo: str,
    ) -> None:
        _review(
            session,
            rows=[_fila(_PROPUESTA_COMPLETA, _final() if no_edit else _final(price="120"))],
            created_at=when,
            shown_at=when - timedelta(seconds=segundos_decision),
            sufijo=sufijo,
        )

    def test_datos_insuficientes_en_alguna_ventana_no_alerta(self, session: Session) -> None:
        # Solo la ventana actual tiene datos; la anterior queda vacía.
        self._sembrar(session, when=AHORA - DIA, no_edit=True, segundos_decision=5, sufijo="h")

        resultado = metrics.rubber_stamp_alert(session, now=AHORA)

        assert resultado["alert"] is False
        assert resultado["reason"] == "datos_insuficientes"

    def test_alerta_dispara_cuando_sube_no_editado_y_baja_tiempo(
        self, session: Session
    ) -> None:
        vieja = AHORA - 10 * DIA
        nueva = AHORA - DIA
        # Semana anterior: mitad editado, decisiones lentas (60s).
        self._sembrar(session, when=vieja, no_edit=True, segundos_decision=60, sufijo="i1")
        self._sembrar(session, when=vieja, no_edit=False, segundos_decision=60, sufijo="i2")
        # Semana actual: todo sin editar, decisiones rápidas (5s).
        self._sembrar(session, when=nueva, no_edit=True, segundos_decision=5, sufijo="i3")
        self._sembrar(session, when=nueva, no_edit=True, segundos_decision=5, sufijo="i4")

        resultado = metrics.rubber_stamp_alert(session, now=AHORA)

        assert resultado["alert"] is True

    def test_no_alerta_si_solo_una_metrica_empeora(self, session: Session) -> None:
        vieja = AHORA - 10 * DIA
        nueva = AHORA - DIA
        # Sube la tasa de no-editado, pero el tiempo de decisión también sube
        # (no baja) — no cumple las dos condiciones a la vez.
        self._sembrar(session, when=vieja, no_edit=True, segundos_decision=60, sufijo="j1")
        self._sembrar(session, when=vieja, no_edit=False, segundos_decision=60, sufijo="j2")
        self._sembrar(session, when=nueva, no_edit=True, segundos_decision=90, sufijo="j3")
        self._sembrar(session, when=nueva, no_edit=True, segundos_decision=90, sufijo="j4")

        resultado = metrics.rubber_stamp_alert(session, now=AHORA)

        assert resultado["alert"] is False
        assert resultado["reason"] is None
