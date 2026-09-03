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
    Currency,
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
    PHASE0_TARGET_ROWS,
    compute_channel_intake,
    compute_coverage,
    compute_daily_activity,
    compute_intake_health,
    compute_open_gaps,
    compute_pace,
    compute_phase0_progress,
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
) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            province_id=province.id if province else None,
            price=Decimal(price),
            currency=currency,
            source_id=source.id,
            unit=unit,
            sale_format=sale_format,
            price_cup=Decimal(price_cup) if price_cup is not None else None,
            created_at=created_at or datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
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
