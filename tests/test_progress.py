"""Progreso hacia la meta de Fase 0 — casos sintéticos.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el módulo.

Nada de lo que se mide en este archivo es un umbral inventado. Los dos únicos
números que se comparan contra un objetivo salen del documento rector:
`spec.md` §9 fija «500 registros validados de 3–5 productos en 2 provincias»,
y §12.5 fija la tasa de descarte objetivo en «<30%». Todo lo demás —ritmo,
huecos, cobertura— es medición cruda sin veredicto, mismo criterio que
`cells.py` (§12.6).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from saimc.models import (
    Channel,
    CurationEvent,
    Currency,
    LlmProposal,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.models.channel import Platform
from saimc.models.raw_ingestion import (
    ContentType,
    DiscardReason,
    ProcessingStatus,
    SourceType,
)
from saimc.progress import (
    DISCARD_RATE_OBJECTIVE,
    PAUSE_CAP_SECONDS,
    PHASE0_TARGET_ROWS,
    compute_channel_intake,
    compute_coverage,
    compute_daily_activity,
    compute_intake_health,
    compute_open_gaps,
    compute_pace,
    compute_phase0_progress,
    compute_weekly_automation,
    decision_seconds,
    oldest_pending_age_days,
)


def _ingestion(
    session: Session,
    hash_: str,
    status: ProcessingStatus = ProcessingStatus.CURATED,
    discard_reason: str | None = None,
) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        processing_status=status,
        discard_reason=discard_reason,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    return row


def _precio(
    session: Session,
    *,
    product: Product,
    province: Province | None,
    source: RawIngestion,
    price: str = "100",
    unit: str | None = "lb",
    sale_format: str | None = SaleFormat.RETAIL.value,
    currency: str = Currency.CUP.value,
    price_cup: str | None = None,
    created_at: datetime | None = None,
    published_date: date | None = None,
) -> None:
    session.add(
        PriceHistory(
            provenance="human",
            product_id=product.id,
            province_id=province.id if province else None,
            price=Decimal(price),
            currency=currency,
            source_id=source.id,
            unit=unit,
            sale_format=sale_format,
            price_cup=Decimal(price_cup) if price_cup is not None else None,
            created_at=created_at or datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            published_date=published_date,
        )
    )


def _producto(session: Session, name: str, unit: str = "lb", *, is_pilot: bool = True) -> Product:
    # `is_pilot` por default en True: casi todos estos tests hablan del
    # conjunto foco. Desde el 2026-08-15 el foco vive en la base y no en una
    # tupla fija, así que cada test declara los pilotos que necesita.
    row = Product(name=name, category="varios", unit=unit, aliases=[], is_pilot=is_pilot)
    session.add(row)
    session.flush()
    return row


def _provincia(session: Session, name: str) -> Province:
    # `code` es NOT NULL en el modelo; se deriva del nombre para no repetirlo
    # en cada llamada, no tiene significado en estos tests.
    code = "".join(word[0] for word in name.split()).lower()
    row = Province(code=code, name=name)
    session.add(row)
    session.flush()
    return row


class TestPhase0Progress:
    def test_cuenta_filas_contra_la_meta_de_500(self, session: Session) -> None:
        """spec.md §9: «500 registros validados»."""
        arroz = _producto(session, "Arroz")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-progreso-filas")
        for _ in range(3):
            _precio(session, product=arroz, province=santiago, source=origen)
        session.commit()

        progress = compute_phase0_progress(session)

        assert progress.rows == 3
        assert progress.target_rows == PHASE0_TARGET_ROWS == 500
        assert progress.rows_remaining == 497

    def test_separa_pilotos_con_dato_de_los_que_faltan(self, session: Session) -> None:
        arroz = _producto(session, "Arroz")
        _producto(session, "Pollo")
        _producto(session, "Aceite")
        _producto(session, "Azúcar blanca")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-progreso-pilotos")
        _precio(session, product=arroz, province=santiago, source=origen)
        session.commit()

        progress = compute_phase0_progress(session)

        assert progress.products_with_data == ("Arroz",)
        # Pollo existe en el catálogo pero sin una sola fila cargada.
        assert progress.products_missing == ("Aceite", "Azúcar blanca", "Pollo")

    def test_separa_provincias_objetivo_con_dato_de_las_que_faltan(
        self, session: Session
    ) -> None:
        """spec.md §9 nombra las dos: La Habana y Santiago de Cuba."""
        arroz = _producto(session, "Arroz")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-progreso-provincias")
        _precio(session, product=arroz, province=santiago, source=origen)
        session.commit()

        progress = compute_phase0_progress(session)

        assert progress.provinces_with_data == ("Santiago de Cuba",)
        assert progress.provinces_missing == ("La Habana",)

    def test_base_vacia_no_rompe(self, session: Session) -> None:
        progress = compute_phase0_progress(session)

        assert progress.rows == 0
        assert progress.rows_remaining == 500
        assert progress.products_with_data == ()
        assert progress.provinces_with_data == ()

    def test_una_fila_de_producto_no_piloto_no_cuenta_como_piloto(
        self, session: Session
    ) -> None:
        """El catálogo tiene 50+ productos por altas al vuelo; la meta de §9
        habla de productos bandera, no de cualquier alta."""
        gomitas = _producto(session, "Gomitas FRULLY", is_pilot=False)
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-progreso-no-piloto")
        _precio(session, product=gomitas, province=santiago, source=origen)
        session.commit()

        progress = compute_phase0_progress(session)

        # La fila suma al total de registros...
        assert progress.rows == 1
        # ...pero no marca ningún producto bandera como cubierto.
        assert progress.products_with_data == ()


class TestIntakeHealth:
    def test_calcula_tasa_de_descarte_sobre_lo_decidido(self, session: Session) -> None:
        """3 descartados sobre 4 decididos = 75%. Lo pendiente no entra en el
        denominador: todavía no se decidió nada sobre eso."""
        _ingestion(session, "hash-salud-curado-1", ProcessingStatus.CURATED)
        _ingestion(session, "hash-salud-desc-1", ProcessingStatus.DISCARDED)
        _ingestion(session, "hash-salud-desc-2", ProcessingStatus.DISCARDED)
        _ingestion(session, "hash-salud-desc-3", ProcessingStatus.DISCARDED)
        _ingestion(session, "hash-salud-pend-1", ProcessingStatus.PENDING)
        session.commit()

        health = compute_intake_health(session)

        assert health.curated == 1
        assert health.discarded == 3
        assert health.pending == 1
        assert health.decided == 4
        assert health.discard_rate == 0.75

    def test_marca_cuando_supera_el_objetivo_de_la_especificacion(
        self, session: Session
    ) -> None:
        """spec.md §12.5 fija la tasa de descarte objetivo en <30%. El objetivo
        es del documento, no inventado acá."""
        assert DISCARD_RATE_OBJECTIVE == 0.30

        _ingestion(session, "hash-obj-curado-1", ProcessingStatus.CURATED)
        _ingestion(session, "hash-obj-desc-1", ProcessingStatus.DISCARDED)
        session.commit()

        health = compute_intake_health(session)

        assert health.discard_rate == 0.5
        assert health.above_objective is True

    def test_dentro_del_objetivo_no_marca(self, session: Session) -> None:
        for i in range(9):
            _ingestion(session, f"hash-ok-curado-{i}", ProcessingStatus.CURATED)
        _ingestion(session, "hash-ok-desc-1", ProcessingStatus.DISCARDED)
        session.commit()

        health = compute_intake_health(session)

        assert health.discard_rate == 0.1
        assert health.above_objective is False

    def test_sin_nada_decidido_la_tasa_es_desconocida(self, session: Session) -> None:
        """Cero decididos no es «0% de descarte»: es que no hay qué medir."""
        _ingestion(session, "hash-solo-pendiente", ProcessingStatus.PENDING)
        session.commit()

        health = compute_intake_health(session)

        assert health.decided == 0
        assert health.discard_rate is None
        assert health.above_objective is False

    def test_el_descarte_fuera_de_foco_sale_del_numerador_y_del_denominador(
        self, session: Session
    ) -> None:
        """spec.md §12.5: la tasa mide «sin información económica extraíble». Un
        anuncio con producto y precio, descartado solo por no ser piloto, es
        útil — no cuenta ni arriba ni abajo de la fracción. Se reporta aparte."""
        _ingestion(session, "hash-ff-cur-1", ProcessingStatus.CURATED)
        _ingestion(session, "hash-ff-cur-2", ProcessingStatus.CURATED)
        _ingestion(
            session, "hash-ff-nodata", ProcessingStatus.DISCARDED, DiscardReason.NO_DATA
        )
        for i in range(3):
            _ingestion(
                session, f"hash-ff-off-{i}", ProcessingStatus.DISCARDED,
                DiscardReason.OFF_FOCUS,
            )
        session.commit()

        health = compute_intake_health(session)

        assert health.discarded == 4          # los 4 descartes, para "Descarte por canal"
        assert health.discarded_off_focus == 3
        # sin dato: 1, sobre lo decidido menos lo fuera de foco: (2 + 1) = 3.
        assert health.discard_rate == pytest.approx(1 / 3)
        assert health.off_focus_rate == pytest.approx(3 / 6)
        assert health.above_objective is True   # 33% > 30%

    def test_descarte_sin_motivo_registrado_cuenta_como_sin_dato(
        self, session: Session
    ) -> None:
        """Los 378 descartes anteriores a este cambio quedan en NULL. Se leen
        como «sin dato» —lo conservador—: nunca se los saca de la tasa sin
        saber que corresponde."""
        _ingestion(session, "hash-null-cur", ProcessingStatus.CURATED)
        _ingestion(session, "hash-null-desc", ProcessingStatus.DISCARDED)  # reason NULL
        session.commit()

        health = compute_intake_health(session)

        assert health.discarded_off_focus == 0
        assert health.discard_rate == 0.5

    def test_solo_descartes_fuera_de_foco_no_deja_nada_que_medir(
        self, session: Session
    ) -> None:
        _ingestion(
            session, "hash-onlyff-1", ProcessingStatus.DISCARDED, DiscardReason.OFF_FOCUS
        )
        _ingestion(
            session, "hash-onlyff-2", ProcessingStatus.DISCARDED, DiscardReason.OFF_FOCUS
        )
        session.commit()

        health = compute_intake_health(session)

        assert health.discarded == 2
        assert health.discarded_off_focus == 2
        assert health.discard_rate is None       # decidido - fuera de foco = 0
        assert health.off_focus_rate == 1.0


class TestDailyActivity:
    def test_agrupa_por_dia_de_carga_y_rellena_los_vacios(
        self, session: Session
    ) -> None:
        """El eje es `created_at` —cuándo el operador cargó la fila—, no
        `published_date`: lo que se mide acá es el ritmo de trabajo."""
        arroz = _producto(session, "Arroz")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-actividad")
        hoy = date(2026, 8, 13)

        for _ in range(2):
            _precio(
                session,
                product=arroz,
                province=santiago,
                source=origen,
                created_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
            )
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=origen,
            created_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        )
        session.commit()

        activity = compute_daily_activity(session, days=3, hoy=hoy)

        # Tres días, del más viejo al más nuevo, con el hueco del 12 explícito.
        assert [(d.day, d.rows) for d in activity] == [
            (date(2026, 8, 11), 1),
            (date(2026, 8, 12), 0),
            (date(2026, 8, 13), 2),
        ]

    def test_ignora_lo_anterior_a_la_ventana(self, session: Session) -> None:
        arroz = _producto(session, "Arroz")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-actividad-vieja")
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=origen,
            created_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        )
        session.commit()

        activity = compute_daily_activity(session, days=3, hoy=date(2026, 8, 13))

        assert sum(d.rows for d in activity) == 0
        assert len(activity) == 3

    def test_base_vacia_devuelve_la_ventana_en_cero(self, session: Session) -> None:
        activity = compute_daily_activity(session, days=7, hoy=date(2026, 8, 13))

        assert len(activity) == 7
        assert all(d.rows == 0 for d in activity)


class TestPace:
    def test_promedia_la_ventana_y_extrapola_lo_que_falta(self) -> None:
        """28 filas en 14 días = 2.0/día. Faltando 472, a ese ritmo son 236
        días. Es aritmética, no un pronóstico."""
        activity = [
            type("D", (), {"day": date(2026, 8, 1) + timedelta(days=i), "rows": 2})()
            for i in range(14)
        ]

        pace = compute_pace(activity, rows_remaining=472)

        assert pace.per_day == 2.0
        assert pace.days_to_target == 236

    def test_sin_actividad_no_hay_extrapolacion(self) -> None:
        """Cero por día no da «infinitos días»: da que no se puede estimar."""
        activity = [
            type("D", (), {"day": date(2026, 8, 1) + timedelta(days=i), "rows": 0})()
            for i in range(7)
        ]

        pace = compute_pace(activity, rows_remaining=500)

        assert pace.per_day == 0.0
        assert pace.days_to_target is None

    def test_meta_alcanzada_no_extrapola(self) -> None:
        activity = [
            type("D", (), {"day": date(2026, 8, 1) + timedelta(days=i), "rows": 5})()
            for i in range(7)
        ]

        pace = compute_pace(activity, rows_remaining=0)

        assert pace.days_to_target == 0

    def test_redondea_hacia_arriba(self) -> None:
        """10 filas en 7 días ≈ 1.43/día; faltando 10, son 7 días, no 6."""
        activity = [
            type("D", (), {"day": date(2026, 8, 1) + timedelta(days=i), "rows": r})()
            for i, r in enumerate([2, 2, 2, 2, 1, 1, 0])
        ]

        pace = compute_pace(activity, rows_remaining=10)

        assert pace.days_to_target == 7


class TestOpenGaps:
    def test_cuenta_lo_que_falta_responder(self, session: Session) -> None:
        arroz = _producto(session, "Arroz")
        santiago = _provincia(session, "Santiago de Cuba")
        origen = _ingestion(session, "hash-huecos")

        # Una fila completa: no es hueco de nada.
        _precio(session, product=arroz, province=santiago, source=origen)
        # Sin provincia.
        _precio(session, product=arroz, province=None, source=origen)
        # Sin unidad.
        _precio(session, product=arroz, province=santiago, source=origen, unit=None)
        # Sin formato de venta.
        _precio(
            session, product=arroz, province=santiago, source=origen, sale_format=None
        )
        # En USD sin tasa resuelta.
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=origen,
            currency=Currency.USD.value,
            price_cup=None,
        )
        # En USD pero ya convertida: no es hueco.
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=origen,
            currency=Currency.USD.value,
            price_cup="24000",
        )
        session.commit()

        gaps = {gap.key: gap.count for gap in compute_open_gaps(session)}

        assert gaps == {
            "province": 1,
            "unit": 1,
            "sale_format": 1,
            "unconverted": 1,
        }

    def test_cada_hueco_enlaza_al_historial_filtrado(self, session: Session) -> None:
        """El hueco sirve si se puede ir a arreglarlo: el contrato de
        `/curation/history` es que un parámetro vacío filtra por NULL."""
        gaps = {gap.key: gap.query for gap in compute_open_gaps(session)}

        assert gaps["province"] == "province="
        assert gaps["unit"] == "unit="
        assert gaps["sale_format"] == "sale_format="
        assert gaps["unconverted"] == "unconverted=1"

    def test_base_vacia_devuelve_los_cuatro_en_cero(self, session: Session) -> None:
        gaps = compute_open_gaps(session)

        assert len(gaps) == 4
        assert all(gap.count == 0 for gap in gaps)


class TestCoverage:
    def test_devuelve_la_grilla_completa_incluidos_los_ceros(
        self, session: Session
    ) -> None:
        """El hueco es el dato: una celda en cero dice exactamente qué falta
        capturar para cumplir §9 (3–5 productos en 2 provincias)."""
        arroz = _producto(session, "Arroz")
        for otro in ("Aceite", "Azúcar blanca", "Pollo"):
            _producto(session, otro)
        santiago = _provincia(session, "Santiago de Cuba")
        _provincia(session, "La Habana")
        origen = _ingestion(session, "hash-cobertura")
        _precio(session, product=arroz, province=santiago, source=origen)
        _precio(session, product=arroz, province=santiago, source=origen)
        session.commit()

        coverage = compute_coverage(session)

        # 4 productos piloto × 2 provincias objetivo.
        assert len(coverage) == 8
        por_celda = {(c.product_name, c.province_name): c.rows for c in coverage}
        assert por_celda[("Arroz", "Santiago de Cuba")] == 2
        assert por_celda[("Arroz", "La Habana")] == 0
        assert por_celda[("Pollo", "Santiago de Cuba")] == 0

    def test_ignora_provincias_fuera_de_la_meta(self, session: Session) -> None:
        arroz = _producto(session, "Arroz")
        holguin = _provincia(session, "Holguín")
        origen = _ingestion(session, "hash-cobertura-fuera")
        _precio(session, product=arroz, province=holguin, source=origen)
        session.commit()

        coverage = compute_coverage(session)

        assert all(c.province_name != "Holguín" for c in coverage)
        assert all(c.rows == 0 for c in coverage)


# --- Descarte por canal --------------------------------------------------------


def _canal(session: Session, name: str, *, is_active: bool = True) -> Channel:
    row = Channel(name=name, platform=Platform.WHATSAPP_CHANNEL, is_active=is_active)
    session.add(row)
    session.flush()
    return row


def _ingestion_de_canal(
    session: Session,
    hash_: str,
    status: ProcessingStatus,
    channel: Channel | None,
) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        processing_status=status,
        channel_id=channel.id if channel is not None else None,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    return row


class TestDescartePorCanal:
    """De dónde viene lo que se descarta.

    La tarjeta global de `compute_intake_health` dice cuánto se tira; ésta dice
    de quién. Es el desglose que la propia vista de control recomendaba mirar y
    que no existía.

    Mismo criterio de umbrales que el resto del módulo: el único veredicto es el
    «<30%» de `spec.md` §12.5, reusado tal cual. No se inventa ninguna marca
    propia por canal.
    """

    def test_separa_los_conteos_por_canal(self, session: Session) -> None:
        ruidoso = _canal(session, "Ofertas Habana")
        bueno = _canal(session, "Agro Santiago")

        # Ruidoso: 1 curado, 3 descartados, 2 pendientes.
        _ingestion_de_canal(session, "ch-r-cur-1", ProcessingStatus.CURATED, ruidoso)
        _ingestion_de_canal(session, "ch-r-des-1", ProcessingStatus.DISCARDED, ruidoso)
        _ingestion_de_canal(session, "ch-r-des-2", ProcessingStatus.DISCARDED, ruidoso)
        _ingestion_de_canal(session, "ch-r-des-3", ProcessingStatus.DISCARDED, ruidoso)
        _ingestion_de_canal(session, "ch-r-pen-1", ProcessingStatus.PENDING, ruidoso)
        _ingestion_de_canal(session, "ch-r-pen-2", ProcessingStatus.PENDING, ruidoso)
        # Bueno: 3 curados, 1 descartado.
        _ingestion_de_canal(session, "ch-b-cur-1", ProcessingStatus.CURATED, bueno)
        _ingestion_de_canal(session, "ch-b-cur-2", ProcessingStatus.CURATED, bueno)
        _ingestion_de_canal(session, "ch-b-cur-3", ProcessingStatus.CURATED, bueno)
        _ingestion_de_canal(session, "ch-b-des-1", ProcessingStatus.DISCARDED, bueno)
        session.commit()

        intake = compute_channel_intake(session)
        por_nombre = {c.channel_name: c for c in intake}

        assert por_nombre["Ofertas Habana"].curated == 1
        assert por_nombre["Ofertas Habana"].discarded == 3
        assert por_nombre["Ofertas Habana"].pending == 2
        assert por_nombre["Ofertas Habana"].decided == 4
        assert por_nombre["Ofertas Habana"].discard_rate == 0.75
        assert por_nombre["Agro Santiago"].curated == 3
        assert por_nombre["Agro Santiago"].discarded == 1
        assert por_nombre["Agro Santiago"].pending == 0
        assert por_nombre["Agro Santiago"].discard_rate == 0.25

    def test_ordena_por_descartes_absolutos_no_por_tasa(self, session: Session) -> None:
        """Un 100% sobre dos anuncios no cuesta una hora de curaduría; 30
        descartes sí. Lo que se ordena es el trabajo perdido, no el porcentaje."""
        goteo = _canal(session, "Goteo")
        torrente = _canal(session, "Torrente")

        # Goteo: tasa perfecta de descarte (100%) sobre 2 anuncios.
        _ingestion_de_canal(session, "ch-g-des-1", ProcessingStatus.DISCARDED, goteo)
        _ingestion_de_canal(session, "ch-g-des-2", ProcessingStatus.DISCARDED, goteo)
        # Torrente: 5 descartados sobre 10 decididos — tasa menor, daño mayor.
        for i in range(5):
            _ingestion_de_canal(
                session, f"ch-t-des-{i}", ProcessingStatus.DISCARDED, torrente
            )
        for i in range(5):
            _ingestion_de_canal(
                session, f"ch-t-cur-{i}", ProcessingStatus.CURATED, torrente
            )
        session.commit()

        intake = compute_channel_intake(session)

        assert [c.channel_name for c in intake] == ["Torrente", "Goteo"]
        assert intake[0].discarded == 5
        assert intake[0].discard_rate == 0.5
        assert intake[1].discarded == 2
        assert intake[1].discard_rate == 1.0

    def test_la_captura_manual_es_una_fila_propia(self, session: Session) -> None:
        """Las capturas sin canal son la mitad del trabajo del operador. Dejarlas
        fuera del desglose escondería justamente lo que él controla."""
        canal = _canal(session, "Ofertas Habana")
        _ingestion_de_canal(session, "ch-c-des-1", ProcessingStatus.DISCARDED, canal)
        _ingestion_de_canal(session, "ch-m-des-1", ProcessingStatus.DISCARDED, None)
        _ingestion_de_canal(session, "ch-m-des-2", ProcessingStatus.DISCARDED, None)
        _ingestion_de_canal(session, "ch-m-cur-1", ProcessingStatus.CURATED, None)
        session.commit()

        intake = compute_channel_intake(session)
        manual = [c for c in intake if c.channel_name is None]

        assert len(manual) == 1
        assert manual[0].discarded == 2
        assert manual[0].curated == 1
        assert manual[0].platform is None
        assert manual[0].is_active is None

    def test_omite_canales_que_no_trajeron_nada(self, session: Session) -> None:
        """Un canal recién dado de alta en /catalog no tiene descarte que mostrar:
        no tiene nada. Una fila de ceros afirmaría que se midió algo."""
        activo = _canal(session, "Con dato")
        _canal(session, "Sin dato")
        _ingestion_de_canal(session, "ch-a-cur-1", ProcessingStatus.CURATED, activo)
        session.commit()

        intake = compute_channel_intake(session)

        assert [c.channel_name for c in intake] == ["Con dato"]

    def test_incluye_canales_inactivos_y_los_marca(self, session: Session) -> None:
        """Apagar un canal en /catalog no borra lo que ya costó curar. El
        histórico sigue explicando la tasa global, así que sigue visible."""
        apagado = _canal(session, "Apagado", is_active=False)
        _ingestion_de_canal(session, "ch-ap-des-1", ProcessingStatus.DISCARDED, apagado)
        session.commit()

        intake = compute_channel_intake(session)

        assert len(intake) == 1
        assert intake[0].channel_name == "Apagado"
        assert intake[0].is_active is False

    def test_sin_decididos_no_hay_tasa(self, session: Session) -> None:
        """Solo pendientes: nada se decidió, así que no hay nada que medir.
        Mismo criterio que `IntakeHealth.discard_rate`, no un 0% inventado."""
        nuevo = _canal(session, "Recién llegado")
        _ingestion_de_canal(session, "ch-n-pen-1", ProcessingStatus.PENDING, nuevo)
        session.commit()

        intake = compute_channel_intake(session)

        assert intake[0].pending == 1
        assert intake[0].decided == 0
        assert intake[0].discard_rate is None
        assert intake[0].above_objective is False

    def test_reusa_el_objetivo_de_la_especificacion(self, session: Session) -> None:
        """El único veredicto por canal es el «<30%» de §12.5, el mismo de la
        tarjeta global. Ningún umbral nuevo entra por acá."""
        malo = _canal(session, "Sobre el objetivo")
        bueno = _canal(session, "Bajo el objetivo")
        # 1 de 2 decididos = 50% > 30%.
        _ingestion_de_canal(session, "ch-ma-des-1", ProcessingStatus.DISCARDED, malo)
        _ingestion_de_canal(session, "ch-ma-cur-1", ProcessingStatus.CURATED, malo)
        # 1 de 5 decididos = 20% < 30%.
        _ingestion_de_canal(session, "ch-bu-des-1", ProcessingStatus.DISCARDED, bueno)
        for i in range(4):
            _ingestion_de_canal(
                session, f"ch-bu-cur-{i}", ProcessingStatus.CURATED, bueno
            )
        session.commit()

        por_nombre = {c.channel_name: c for c in compute_channel_intake(session)}

        assert por_nombre["Sobre el objetivo"].above_objective is True
        assert por_nombre["Bajo el objetivo"].above_objective is False

    def test_los_conteos_por_canal_cuadran_con_la_tarjeta_global(
        self, session: Session
    ) -> None:
        """El desglose explica la tarjeta de arriba; si no sumara lo mismo,
        estaría contando otra cosa y las dos vistas se contradirían."""
        uno = _canal(session, "Uno")
        dos = _canal(session, "Dos")
        _ingestion_de_canal(session, "ch-s-cur-1", ProcessingStatus.CURATED, uno)
        _ingestion_de_canal(session, "ch-s-des-1", ProcessingStatus.DISCARDED, uno)
        _ingestion_de_canal(session, "ch-s-pen-1", ProcessingStatus.PENDING, dos)
        _ingestion_de_canal(session, "ch-s-des-2", ProcessingStatus.DISCARDED, dos)
        _ingestion_de_canal(session, "ch-s-des-3", ProcessingStatus.DISCARDED, None)
        session.commit()

        intake = compute_channel_intake(session)
        global_ = compute_intake_health(session)

        assert sum(c.curated for c in intake) == global_.curated
        assert sum(c.discarded for c in intake) == global_.discarded
        assert sum(c.pending for c in intake) == global_.pending


# --- A4a: panel por canal que sirve para decidir ---------------------------------


def _ingestion_a4a(
    session: Session,
    hash_: str,
    status: ProcessingStatus,
    channel: Channel | None,
    *,
    content_type: str = ContentType.TEXT,
    discard_reason: str | None = None,
) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=content_type,
        processing_status=status,
        discard_reason=discard_reason,
        channel_id=channel.id if channel is not None else None,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    return row


def _evento(
    session: Session,
    ingestion: RawIngestion,
    *,
    action: str = "curated",
    mode: str = "individual",
    segundos: float | None = 60.0,
    detail: dict | None = None,
) -> None:
    decidido = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    session.add(
        CurationEvent(
            ingestion_id=ingestion.id,
            action=action,
            mode=mode,
            decided_at=decidido,
            shown_at=None if segundos is None else decidido - timedelta(seconds=segundos),
            detail=detail,
        )
    )


class TestDuracionDeUnaDecision:
    """El tiempo humano que le cuesta una decisión, con las pausas fuera.

    Una pausa absurda (el operador se levantó a mitad de un anuncio) no es
    tiempo de curaduría: se excluye de la suma en vez de inflarla (A2a dejó
    el recorte para el momento de medir).
    """

    def test_devuelve_la_diferencia_entre_mostrado_y_decidido(self) -> None:
        mostrado = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        decidido = mostrado + timedelta(seconds=90)

        assert decision_seconds(mostrado, decidido) == 90.0

    def test_sin_hora_de_vista_no_hay_duracion(self) -> None:
        decidido = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

        assert decision_seconds(None, decidido) is None

    def test_una_vista_posterior_a_la_decision_no_hay_duracion(self) -> None:
        decidido = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

        assert decision_seconds(decidido + timedelta(seconds=5), decidido) is None

    def test_la_pausa_absurda_se_excluye_pero_el_tope_exacto_cuenta(self) -> None:
        decidido = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

        tope = timedelta(seconds=PAUSE_CAP_SECONDS)
        assert decision_seconds(decidido - tope, decidido) == PAUSE_CAP_SECONDS
        assert decision_seconds(decidido - tope - timedelta(seconds=1), decidido) is None

    def test_el_tope_de_pausa_se_mide_por_anuncio_no_por_lote(self) -> None:
        # Un lote de 161 imágenes revisado en 2.018 s (caso real del
        # 2026-10-02) pasa los 15 minutos en total, pero es trabajo, no pausa:
        # cada anuncio costó 2018 / 161 s.
        mostrado = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        decidido = mostrado + timedelta(seconds=2018)

        assert decision_seconds(mostrado, decidido, batch_size=161) == 2018 / 161

    def test_un_lote_con_mas_de_quince_minutos_por_anuncio_es_pausa(self) -> None:
        mostrado = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        decidido = mostrado + timedelta(seconds=2 * PAUSE_CAP_SECONDS + 2)

        assert decision_seconds(mostrado, decidido, batch_size=2) is None

    def test_el_lote_reparte_su_tiempo_entre_sus_anuncios(self) -> None:
        mostrado = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        decidido = mostrado + timedelta(seconds=120)

        assert decision_seconds(mostrado, decidido, batch_size=4) == 30.0


class TestPanelPorCanalA4a:
    def test_dos_canales_homonimos_no_se_funden(self, session: Session) -> None:
        uno = _canal(session, "Ofertas")
        otro = _canal(session, "Ofertas")
        _ingestion_a4a(session, "a4-h-1", ProcessingStatus.CURATED, uno)
        _ingestion_a4a(session, "a4-h-2", ProcessingStatus.DISCARDED, otro)
        session.commit()

        intake = compute_channel_intake(session)

        assert len(intake) == 2
        assert {c.channel_id for c in intake} == {uno.id, otro.id}

    def test_desglosa_texto_e_imagen(self, session: Session) -> None:
        canal = _canal(session, "Mixto")
        _ingestion_a4a(session, "a4-t-1", ProcessingStatus.CURATED, canal)
        _ingestion_a4a(
            session, "a4-i-1", ProcessingStatus.PENDING, canal, content_type=ContentType.IMAGE
        )
        _ingestion_a4a(
            session, "a4-i-2", ProcessingStatus.PENDING, canal, content_type=ContentType.IMAGE
        )
        _ingestion_a4a(
            session, "a4-i-3", ProcessingStatus.DISCARDED, canal, content_type=ContentType.IMAGE
        )
        session.commit()

        (mixto,) = compute_channel_intake(session)

        assert (mixto.text.pending, mixto.text.curated, mixto.text.discarded) == (0, 1, 0)
        assert (mixto.image.pending, mixto.image.curated, mixto.image.discarded) == (2, 0, 1)
        assert (mixto.pending, mixto.curated, mixto.discarded) == (2, 1, 1)

    def test_desglosa_los_descartes_por_motivo(self, session: Session) -> None:
        canal = _canal(session, "Motivos")
        for i in range(2):
            _ingestion_a4a(
                session,
                f"a4-m-of-{i}",
                ProcessingStatus.DISCARDED,
                canal,
                discard_reason=DiscardReason.OFF_FOCUS.value,
            )
        _ingestion_a4a(
            session,
            "a4-m-nd",
            ProcessingStatus.DISCARDED,
            canal,
            discard_reason=DiscardReason.NO_DATA.value,
        )
        _ingestion_a4a(session, "a4-m-sin", ProcessingStatus.DISCARDED, canal)
        session.commit()

        (motivos,) = compute_channel_intake(session)

        assert motivos.discarded_off_focus == 2
        assert motivos.discarded_no_data == 1
        assert motivos.discarded_unrecorded == 1

    def test_la_tasa_no_cuenta_los_fuera_de_foco_como_el_spec(self, session: Session) -> None:
        """§12.5 mide «anuncios sin información económica extraíble»: un fuera
        de foco sí la tenía. Sale de numerador y de denominador, como en
        `IntakeHealth.discard_rate`."""
        canal = _canal(session, "Neta")
        for i in range(2):
            _ingestion_a4a(
                session,
                f"a4-n-of-{i}",
                ProcessingStatus.DISCARDED,
                canal,
                discard_reason=DiscardReason.OFF_FOCUS.value,
            )
        _ingestion_a4a(
            session,
            "a4-n-nd",
            ProcessingStatus.DISCARDED,
            canal,
            discard_reason=DiscardReason.NO_DATA.value,
        )
        _ingestion_a4a(session, "a4-n-cur", ProcessingStatus.CURATED, canal)
        session.commit()

        (neta,) = compute_channel_intake(session)

        assert neta.discard_rate == 0.5  # 1 de 2, sin los 2 fuera de foco
        assert neta.off_focus_rate == 0.5  # 2 de 4 decididos
        assert neta.above_objective is True

    def test_un_canal_solo_de_fuera_de_foco_no_tiene_tasa(self, session: Session) -> None:
        canal = _canal(session, "Otro rubro")
        _ingestion_a4a(
            session,
            "a4-f-1",
            ProcessingStatus.DISCARDED,
            canal,
            discard_reason=DiscardReason.OFF_FOCUS.value,
        )
        session.commit()

        (otro,) = compute_channel_intake(session)

        assert otro.discard_rate is None
        assert otro.above_objective is False

    def test_el_rendimiento_es_filas_sobre_anuncios_resueltos(self, session: Session) -> None:
        canal = _canal(session, "Rinde")
        producto = _producto(session, "Pollo")
        provincia = _provincia(session, "La Habana")
        cur_1 = _ingestion_a4a(session, "a4-r-c1", ProcessingStatus.CURATED, canal)
        cur_2 = _ingestion_a4a(session, "a4-r-c2", ProcessingStatus.CURATED, canal)
        _ingestion_a4a(session, "a4-r-d1", ProcessingStatus.DISCARDED, canal)
        _ingestion_a4a(session, "a4-r-p1", ProcessingStatus.PENDING, canal)
        for fuente in (cur_1, cur_1, cur_2):
            _precio(session, product=producto, province=provincia, source=fuente)
        session.commit()

        (rinde,) = compute_channel_intake(session)

        assert rinde.rows == 3
        assert rinde.decided == 3
        assert rinde.rows_per_decided == 1.0

    def test_sin_decididos_no_hay_rendimiento(self, session: Session) -> None:
        canal = _canal(session, "Nuevo")
        _ingestion_a4a(session, "a4-s-p1", ProcessingStatus.PENDING, canal)
        session.commit()

        (nuevo,) = compute_channel_intake(session)

        assert nuevo.rows_per_decided is None

    def test_suma_los_minutos_humanos_y_calcula_filas_por_hora(self, session: Session) -> None:
        canal = _canal(session, "Con tiempo")
        cur = _ingestion_a4a(session, "a4-e-c1", ProcessingStatus.CURATED, canal)
        des = _ingestion_a4a(session, "a4-e-d1", ProcessingStatus.DISCARDED, canal)
        _evento(session, cur, action="curated", segundos=300, detail={"row_count": 2})
        _evento(session, des, action="discarded", segundos=60)
        session.commit()

        (con_tiempo,) = compute_channel_intake(session)

        assert con_tiempo.human_minutes == 6.0
        # 2 filas en 6 minutos = 20 filas por hora.
        assert con_tiempo.rows_per_hour == 20.0

    def test_una_pausa_absurda_no_infla_los_minutos(self, session: Session) -> None:
        canal = _canal(session, "Pausa")
        cur = _ingestion_a4a(session, "a4-p-c1", ProcessingStatus.CURATED, canal)
        otro = _ingestion_a4a(session, "a4-p-c2", ProcessingStatus.CURATED, canal)
        _evento(session, cur, segundos=120, detail={"row_count": 1})
        _evento(session, otro, segundos=PAUSE_CAP_SECONDS + 600, detail={"row_count": 5})
        session.commit()

        (pausa,) = compute_channel_intake(session)

        assert pausa.human_minutes == 2.0
        # Las 5 filas de la decisión sin tiempo medible tampoco entran al
        # cociente: filas y tiempo se miden sobre las mismas decisiones.
        assert pausa.rows_per_hour == 30.0

    def test_el_lote_suma_su_tiempo_una_sola_vez(self, session: Session) -> None:
        canal = _canal(session, "Lote")
        anuncios = [
            _ingestion_a4a(session, f"a4-l-{i}", ProcessingStatus.DISCARDED, canal)
            for i in range(4)
        ]
        for anuncio in anuncios:
            _evento(
                session,
                anuncio,
                action="discarded",
                mode="batch_model",
                segundos=120,
                detail={"batch_size": 4},
            )
        session.commit()

        (lote,) = compute_channel_intake(session)

        assert lote.human_minutes == 2.0

    def test_editar_o_borrar_no_cuentan_como_tiempo_de_curar(self, session: Session) -> None:
        canal = _canal(session, "Retoques")
        cur = _ingestion_a4a(session, "a4-x-c1", ProcessingStatus.CURATED, canal)
        _evento(session, cur, action="edited", segundos=200)
        _evento(session, cur, action="deleted", segundos=None)
        session.commit()

        (retoques,) = compute_channel_intake(session)

        assert retoques.human_minutes is None

    def test_sin_eventos_no_hay_minutos_ni_filas_por_hora(self, session: Session) -> None:
        canal = _canal(session, "Sin bitácora")
        _ingestion_a4a(session, "a4-z-c1", ProcessingStatus.CURATED, canal)
        session.commit()

        (sin,) = compute_channel_intake(session)

        assert sin.human_minutes is None
        assert sin.rows_per_hour is None


# --- A2d: horas y automatización, semana a semana --------------------------------

# Viernes. Su semana ISO arranca el lunes 2026-09-28; la anterior, el 2026-09-21
# (y termina el domingo 2026-09-27); la anterior a esa, el 2026-09-14.
_HOY_A2D = date(2026, 10, 2)
_LUNES_ACTUAL = date(2026, 9, 28)
_LUNES_ANTERIOR = date(2026, 9, 21)
_LUNES_VACIO = date(2026, 9, 14)
_MARTES = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _evento_en(
    session: Session,
    ingestion: RawIngestion,
    decidido: datetime,
    *,
    action: str = "curated",
    mode: str = "individual",
    segundos: float | None = 60.0,
    detail: dict | None = None,
) -> None:
    """Como `_evento`, pero con la hora de la decisión a elección: acá lo que
    se prueba es en qué semana cae cada una."""
    session.add(
        CurationEvent(
            ingestion_id=ingestion.id,
            action=action,
            mode=mode,
            decided_at=decidido,
            shown_at=None if segundos is None else decidido - timedelta(seconds=segundos),
            detail=detail,
        )
    )


def _pendiente(session: Session, hash_: str, ingresado: datetime) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        created_at=ingresado,
    )
    session.add(row)
    session.flush()
    return row


def _propuesta(
    session: Session,
    ingestion: RawIngestion,
    *,
    model: str = "claude-haiku-4-5",
    input_tokens: int,
    output_tokens: int,
    creada: datetime = _MARTES,
) -> None:
    session.add(
        LlmProposal(
            ingestion_id=ingestion.id,
            model=model,
            prompt_version="test",
            status="ok",
            rows=[],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=creada,
        )
    )


def _semanas(session: Session, weeks: int = 3) -> dict:
    serie = compute_weekly_automation(session, weeks=weeks, hoy=_HOY_A2D)
    return {semana.week_start: semana for semana in serie}


class TestHorasYAutomatizacionA2d:
    """Las métricas del estudio §9, semana a semana (lunes a domingo).

    Los esperados están calculados a mano en cada test.
    """

    def test_devuelve_una_entrada_por_semana_de_la_mas_vieja_a_la_mas_nueva(
        self, session: Session
    ) -> None:
        serie = compute_weekly_automation(session, weeks=3, hoy=_HOY_A2D)

        assert [s.week_start for s in serie] == [_LUNES_VACIO, _LUNES_ANTERIOR, _LUNES_ACTUAL]

    def test_la_semana_vacia_es_explicita_y_no_afirma_nada(self, session: Session) -> None:
        anuncio = _ingestion(session, "a2d-v-1")
        _evento_en(session, anuncio, _MARTES)
        session.commit()

        vacia = _semanas(session)[_LUNES_VACIO]

        assert vacia.resolved == 0
        assert vacia.timed_resolved == 0
        assert vacia.human_hours == 0.0
        assert vacia.hours_per_thousand is None
        assert vacia.automation_rate is None
        assert vacia.freshness_days is None
        assert vacia.cost_per_ad is None

    def test_las_horas_por_mil_salen_del_promedio_por_decision(self, session: Session) -> None:
        uno = _ingestion(session, "a2d-h-1")
        dos = _ingestion(session, "a2d-h-2", ProcessingStatus.DISCARDED)
        _evento_en(session, uno, _MARTES, segundos=90)
        _evento_en(session, dos, _MARTES, action="discarded", segundos=30)
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.resolved == 2
        assert actual.timed_resolved == 2
        # 120 s en total = 1/30 de hora.
        assert actual.human_hours == pytest.approx(120 / 3600)
        # 60 s por anuncio → 60.000 s por mil → 16,67 h.
        assert actual.hours_per_thousand == pytest.approx(60_000 / 3600)

    def test_una_pausa_larga_no_infla_las_horas_pero_cuenta_como_resuelto(
        self, session: Session
    ) -> None:
        uno = _ingestion(session, "a2d-p-1")
        dos = _ingestion(session, "a2d-p-2", ProcessingStatus.DISCARDED)
        _evento_en(session, uno, _MARTES, segundos=90)
        _evento_en(
            session,
            dos,
            _MARTES,
            action="discarded",
            mode="batch_out_of_focus",
            segundos=PAUSE_CAP_SECONDS + 600,
        )
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.resolved == 2
        assert actual.timed_resolved == 1
        # Solo los 90 s medibles, sobre la única decisión medible: 25 h por mil.
        assert actual.human_hours == pytest.approx(90 / 3600)
        assert actual.hours_per_thousand == pytest.approx(25.0)
        # La pausa no tiene tiempo, pero la decisión existió y fue por lote.
        assert actual.automation_rate == 0.5

    def test_una_decision_sin_hora_de_vista_cuenta_sin_tiempo(self, session: Session) -> None:
        anuncio = _ingestion(session, "a2d-s-1")
        _evento_en(session, anuncio, _MARTES, segundos=None)
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.resolved == 1
        assert actual.timed_resolved == 0
        assert actual.hours_per_thousand is None
        assert actual.automation_rate == 0.0

    def test_el_lote_suma_su_tiempo_una_sola_vez(self, session: Session) -> None:
        lote = [
            _ingestion(session, f"a2d-l-{i}", ProcessingStatus.DISCARDED) for i in range(4)
        ]
        for anuncio in lote:
            _evento_en(
                session,
                anuncio,
                _MARTES,
                action="discarded",
                mode="batch_model",
                segundos=120,
                detail={"batch_size": 4},
            )
        suelto = _ingestion(session, "a2d-l-suelto")
        _evento_en(session, suelto, _MARTES, segundos=60)
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.resolved == 5
        assert actual.timed_resolved == 5
        # 120 s del lote (una vez) + 60 s del suelto = 180 s = 0,05 h.
        assert actual.human_hours == pytest.approx(0.05)
        # 36 s por anuncio → 10 h por mil.
        assert actual.hours_per_thousand == pytest.approx(10.0)
        # 4 de 5 no se tocaron uno por uno.
        assert actual.automation_rate == 0.8

    def test_editar_o_borrar_no_son_resueltos(self, session: Session) -> None:
        anuncio = _ingestion(session, "a2d-e-1")
        _evento_en(session, anuncio, _MARTES, segundos=40)
        _evento_en(session, anuncio, _MARTES, action="edited", segundos=200)
        _evento_en(session, anuncio, _MARTES, action="deleted", segundos=None)
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.resolved == 1
        assert actual.human_hours == pytest.approx(40 / 3600)

    def test_el_domingo_y_el_lunes_caen_en_semanas_distintas(self, session: Session) -> None:
        domingo = _ingestion(session, "a2d-d-1")
        lunes = _ingestion(session, "a2d-d-2")
        otro_lunes = _ingestion(session, "a2d-d-3", ProcessingStatus.DISCARDED)
        _evento_en(session, domingo, datetime(2026, 9, 27, 22, 0, tzinfo=UTC), segundos=30)
        _evento_en(session, lunes, datetime(2026, 9, 28, 1, 0, tzinfo=UTC), segundos=50)
        _evento_en(
            session,
            otro_lunes,
            datetime(2026, 9, 28, 1, 5, tzinfo=UTC),
            action="discarded",
            mode="batch_model",
            segundos=70,
        )
        session.commit()

        semanas = _semanas(session)

        assert semanas[_LUNES_ANTERIOR].resolved == 1
        assert semanas[_LUNES_ANTERIOR].human_hours == pytest.approx(30 / 3600)
        assert semanas[_LUNES_ANTERIOR].automation_rate == 0.0
        assert semanas[_LUNES_ACTUAL].resolved == 2
        assert semanas[_LUNES_ACTUAL].human_hours == pytest.approx(120 / 3600)
        assert semanas[_LUNES_ACTUAL].automation_rate == 0.5

    def test_lo_anterior_a_la_ventana_queda_fuera(self, session: Session) -> None:
        viejo = _ingestion(session, "a2d-w-1")
        _evento_en(session, viejo, datetime(2026, 9, 13, 12, 0, tzinfo=UTC))
        session.commit()

        serie = compute_weekly_automation(session, weeks=3, hoy=_HOY_A2D)

        assert sum(s.resolved for s in serie) == 0

    def test_la_frescura_es_la_mediana_de_dias_entre_publicacion_y_alta(
        self, session: Session
    ) -> None:
        arroz = _producto(session, "Arroz")
        habana = _provincia(session, "La Habana")
        origen = _ingestion(session, "a2d-f-1")
        # Altas del martes 29: publicadas el 28 (1 día), el 26 (3) y el 19 (10).
        for publicado in (date(2026, 9, 28), date(2026, 9, 26), date(2026, 9, 19)):
            _precio(
                session,
                product=arroz,
                province=habana,
                source=origen,
                created_at=_MARTES,
                published_date=publicado,
            )
        # Sin fecha de publicación: no hay qué medir, se ignora.
        _precio(session, product=arroz, province=habana, source=origen, created_at=_MARTES)
        # Semana anterior, dos filas: 1 y 4 días → mediana 2,5.
        for publicado in (date(2026, 9, 22), date(2026, 9, 19)):
            _precio(
                session,
                product=arroz,
                province=habana,
                source=origen,
                created_at=datetime(2026, 9, 23, 9, 0, tzinfo=UTC),
                published_date=publicado,
            )
        session.commit()

        semanas = _semanas(session)

        assert semanas[_LUNES_ACTUAL].freshness_days == 3
        assert semanas[_LUNES_ANTERIOR].freshness_days == 2.5
        assert semanas[_LUNES_VACIO].freshness_days is None

    def test_sin_fechas_de_publicacion_no_hay_frescura(self, session: Session) -> None:
        arroz = _producto(session, "Arroz")
        origen = _ingestion(session, "a2d-f-2")
        _precio(session, product=arroz, province=None, source=origen, created_at=_MARTES)
        session.commit()

        assert _semanas(session)[_LUNES_ACTUAL].freshness_days is None

    def test_el_costo_se_divide_por_anuncios_distintos_no_por_propuestas(
        self, session: Session
    ) -> None:
        anuncio = _ingestion(session, "a2d-c-1")
        # Haiku 4.5: 1 USD por millón de entrada, 5 por millón de salida.
        # 1.000.000 de entrada + 200.000 de salida = 1 + 1 = 2 USD.
        _propuesta(session, anuncio, input_tokens=1_000_000, output_tokens=200_000)
        # 500.000 de entrada = 0,5 USD. Mismo anuncio.
        _propuesta(session, anuncio, input_tokens=500_000, output_tokens=0)
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        assert actual.proposal_ads == 1
        assert actual.cost_usd == pytest.approx(2.5)
        assert actual.cost_per_ad == pytest.approx(2.5)

    def test_un_modelo_sin_tarifa_no_suma_costo(self, session: Session) -> None:
        con_tarifa = _ingestion(session, "a2d-c-2")
        sin_tarifa = _ingestion(session, "a2d-c-3")
        _propuesta(session, con_tarifa, input_tokens=1_000_000, output_tokens=0)
        _propuesta(
            session,
            sin_tarifa,
            model="modelo-desconocido",
            input_tokens=9_000_000,
            output_tokens=9_000_000,
        )
        session.commit()

        actual = _semanas(session)[_LUNES_ACTUAL]

        # 1 USD sobre dos anuncios con propuesta.
        assert actual.cost_usd == pytest.approx(1.0)
        assert actual.proposal_ads == 2
        assert actual.unpriced_proposals == 1
        assert actual.cost_per_ad == pytest.approx(0.5)

    def test_el_costo_va_a_la_semana_de_la_propuesta(self, session: Session) -> None:
        anuncio = _ingestion(session, "a2d-c-4")
        _propuesta(
            session,
            anuncio,
            input_tokens=2_000_000,
            output_tokens=0,
            creada=datetime(2026, 9, 27, 23, 0, tzinfo=UTC),
        )
        session.commit()

        semanas = _semanas(session)

        assert semanas[_LUNES_ANTERIOR].cost_per_ad == pytest.approx(2.0)
        assert semanas[_LUNES_ACTUAL].cost_per_ad is None


class TestPendienteMasViejoA2d:
    def test_cuenta_los_dias_desde_el_ingreso_del_pendiente_mas_antiguo(
        self, session: Session
    ) -> None:
        _pendiente(session, "a2d-o-1", datetime(2026, 9, 30, 8, 0, tzinfo=UTC))
        _pendiente(session, "a2d-o-2", datetime(2026, 9, 20, 23, 0, tzinfo=UTC))
        session.commit()

        # Del 20 de septiembre al 2 de octubre: 12 días.
        assert oldest_pending_age_days(session, hoy=_HOY_A2D) == 12

    def test_lo_ya_decidido_no_cuenta_aunque_sea_mas_viejo(self, session: Session) -> None:
        curado = _ingestion(session, "a2d-o-3")
        assert curado.processing_status == ProcessingStatus.CURATED
        _pendiente(session, "a2d-o-4", datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
        session.commit()

        assert oldest_pending_age_days(session, hoy=_HOY_A2D) == 1

    def test_sin_pendientes_no_hay_antiguedad(self, session: Session) -> None:
        _ingestion(session, "a2d-o-5")
        session.commit()

        assert oldest_pending_age_days(session, hoy=_HOY_A2D) is None
