"""Controles de §10.7 (slice C3): `cell_controls`, pura, y su lectura de la base.

Los casos 1 a 9 son los de `docs/spec-slice-C3.md`, «Tests primero». Lo que se
espera sale de la especificación y de la cuenta hecha a mano en cada test,
nunca de correr el código.

El cerco (`validation/fence.py`) con `FENCE_IQR = 3` y `FENCE_MIN_FACTOR = 2`:
sobre los logaritmos, `margen = max(3 · (q3 − q1), ln 2)` y el cerco va de
`exp(q1 − margen)` a `exp(q3 + margen)`. Las filas humanas de estos tests son
96, 98, 100, 100, 102 y 104. Los cuartiles caen entre el segundo y el tercer
valor y entre el cuarto y el quinto, así que 98 ≤ q1 ≤ 100 ≤ q3 ≤ 102. El ancho
de caja es a lo sumo ln(102/98) = 0,040, y tres veces eso (0,12) no llega a
ln 2 = 0,693: manda el piso, margen = ln 2. El cerco queda entre q1/2 (de 49 a
50) y 2·q3 (de 200 a 204).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.ingestion.origin import origin_resolver, resolve_origin
from saimc.models import (
    Channel,
    Currency,
    Platform,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.validation.control_cells import compute_cell_controls
from saimc.validation.controls import ControlRow, cell_controls, normalize_text

MIN_N = 5
BURST = timedelta(minutes=10)

HUMANS = ("96", "98", "100", "100", "102", "104")
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

CANAL_A = uuid.UUID(int=1)
CANAL_B = uuid.UUID(int=2)


def _rows(prices: tuple[str, ...], provenance: str) -> list[ControlRow]:
    return [ControlRow(price=Decimal(p), provenance=provenance) for p in prices]


def _controls(rows: list[ControlRow]):
    return cell_controls(rows, min_control_n=MIN_N, burst_window=BURST)


class TestIndiceDeControl:
    def test_caso_1_automaticas_alrededor_de_300_diverge(self) -> None:
        # 13 filas; la mediana es la séptima en orden. Las seis humanas son
        # menores que cualquier automática, así que la séptima es la automática
        # más chica: 290. El cerco de las humanas termina a lo sumo en 204.
        # 290 > 204: diverge.
        autos = ("290", "295", "300", "300", "305", "310", "320")
        result = _controls(_rows(HUMANS, "human") + _rows(autos, "auto"))

        assert result.count == 13
        assert result.control_count == 6
        assert result.median == Decimal("290")
        assert result.control_median == Decimal("100")
        assert result.anchored is True
        assert result.diverges is True

    def test_caso_2_automaticas_alrededor_de_105_no_diverge(self) -> None:
        # En orden: 96, 98, 100, 100, 102, 103, 104, 104, 105, 105, 106, 107,
        # 108. La séptima es 104, dentro del cerco (de ~49 a ~200).
        autos = ("103", "104", "105", "105", "106", "107", "108")
        result = _controls(_rows(HUMANS, "human") + _rows(autos, "auto"))

        assert result.median == Decimal("104")
        assert result.control_median == Decimal("100")
        assert result.anchored is True
        assert result.diverges is False

    def test_caso_3_con_4_humanas_queda_sin_ancla_y_no_diverge(self) -> None:
        # 4 filas de control < MIN_CELL_N (5). Aunque las automáticas estén en
        # 300, sin ancla no hay contra qué decir que diverge.
        humans = ("98", "100", "100", "102")
        autos = ("290", "295", "300", "300", "300", "305", "305", "310", "315", "320")
        result = _controls(_rows(humans, "human") + _rows(autos, "auto"))

        assert result.control_count == 4
        assert result.anchored is False
        assert result.diverges is False
        # El índice de control igual se informa: es la mediana de las 4.
        assert result.control_median == Decimal("100")

    def test_caso_4_solo_humanas_no_diverge_y_los_indices_coinciden(self) -> None:
        result = _controls(_rows(HUMANS, "human"))

        assert result.control_count == result.count == 6
        assert result.control_median == result.median == Decimal("100")
        assert result.diverges is False

    def test_caso_5_una_fila_audited_cuenta_como_control(self) -> None:
        # 4 humanas no alcanzan; con la auditada son 5 y la celda tiene ancla.
        rows = _rows(("98", "100", "100", "102"), "human") + _rows(("101",), "audited")
        result = _controls(rows)

        assert result.control_count == 5
        assert result.anchored is True

    def test_una_celda_sin_filas_de_control_no_tiene_indice(self) -> None:
        result = _controls(_rows(("300", "310"), "auto"))

        assert result.control_count == 0
        assert result.control_median is None
        assert result.anchored is False
        assert result.diverges is False


class TestConcentracion:
    def test_caso_6_ocho_filas_de_un_canal_y_dos_de_otro_dan_80(self) -> None:
        rows = [
            ControlRow(price=Decimal("100"), provenance="human", channel=canal)
            for canal in [CANAL_A] * 8 + [CANAL_B] * 2
        ]
        result = _controls(rows)

        assert result.top_channel_rows == 8
        assert result.rows_without_channel == 0
        assert result.channel_share == 0.8

    def test_las_filas_sin_canal_se_cuentan_aparte(self) -> None:
        # 3 de un canal, 1 de otro y 2 sin canal: 3 sobre las 4 identificadas.
        rows = [
            ControlRow(price=Decimal("100"), provenance="human", channel=canal)
            for canal in [CANAL_A] * 3 + [CANAL_B] + [None] * 2
        ]
        result = _controls(rows)

        assert result.rows_without_channel == 2
        assert result.channel_share == 3 / 4

    def test_caso_7_tres_filas_sin_emisor_de_diez(self) -> None:
        # 4 de un emisor, 3 de otro, 3 sin emisor: informa 3 sin emisor y la
        # parte es 4 sobre las 7 que sí lo tienen.
        rows = [
            ControlRow(price=Decimal("100"), provenance="human", sender=sender)
            for sender in ["a"] * 4 + ["b"] * 3 + [None] * 3
        ]
        result = _controls(rows)

        assert result.rows_without_sender == 3
        assert result.top_sender_rows == 4
        assert result.sender_share == 4 / 7

    def test_sin_ningun_emisor_no_hay_parte_que_informar(self) -> None:
        result = _controls(_rows(HUMANS, "human"))

        assert result.rows_without_sender == 6
        assert result.sender_share is None
        assert result.rows_without_channel == 6
        assert result.channel_share is None


class TestSincronia:
    def _pair(self, canal_2: uuid.UUID, minutes: int) -> list[ControlRow]:
        return [
            ControlRow(price=Decimal("100"), provenance="human", channel=CANAL_A, at=T0),
            ControlRow(
                price=Decimal("100"),
                provenance="human",
                channel=canal_2,
                at=T0 + timedelta(minutes=minutes),
            ),
        ]

    def test_caso_8_canales_distintos_a_5_minutos_cuentan(self) -> None:
        assert _controls(self._pair(CANAL_B, 5)).synchronous_rows == 2

    def test_caso_8_canales_distintos_a_15_minutos_no_cuentan(self) -> None:
        assert _controls(self._pair(CANAL_B, 15)).synchronous_rows == 0

    def test_caso_8_mismo_canal_a_5_minutos_no_cuenta(self) -> None:
        assert _controls(self._pair(CANAL_A, 5)).synchronous_rows == 0

    def test_a_10_minutos_justos_no_cuenta(self) -> None:
        # «A menos de» la ventana: el borde queda afuera.
        assert _controls(self._pair(CANAL_B, 10)).synchronous_rows == 0

    def test_una_fila_sin_canal_no_se_da_por_distinta(self) -> None:
        rows = self._pair(CANAL_B, 5)
        rows[1] = ControlRow(
            price=Decimal("100"), provenance="human", channel=None, at=rows[1].at
        )
        assert _controls(rows).synchronous_rows == 0


class TestTextoCasiIdentico:
    def test_forma_normal(self) -> None:
        # Minúsculas, sin tildes, sin dígitos ni espacios.
        assert normalize_text("Pollo 4x10 a 11500") == "polloxa"
        assert normalize_text("pollo 4x10  a 12000") == "polloxa"
        assert normalize_text("AZÚCAR  blanca\n3 lb") == "azucarblancalb"

    def test_caso_9_canales_distintos_cuentan(self) -> None:
        rows = [
            ControlRow(
                price=Decimal("11500"),
                provenance="human",
                channel=CANAL_A,
                text="Pollo 4x10 a 11500",
            ),
            ControlRow(
                price=Decimal("12000"),
                provenance="human",
                channel=CANAL_B,
                text="pollo 4x10  a 12000",
            ),
        ]
        assert _controls(rows).identical_text_rows == 2

    def test_caso_9_mismo_canal_y_mismo_emisor_no_cuentan(self) -> None:
        rows = [
            ControlRow(
                price=Decimal("11500"),
                provenance="human",
                channel=CANAL_A,
                sender="a",
                text="Pollo 4x10 a 11500",
            ),
            ControlRow(
                price=Decimal("12000"),
                provenance="human",
                channel=CANAL_A,
                sender="a",
                text="pollo 4x10  a 12000",
            ),
        ]
        assert _controls(rows).identical_text_rows == 0

    def test_mismo_canal_y_emisores_distintos_cuentan(self) -> None:
        rows = [
            ControlRow(
                price=Decimal("100"), provenance="human", channel=CANAL_A, sender=s, text="Arroz"
            )
            for s in ("a", "b")
        ]
        assert _controls(rows).identical_text_rows == 2

    def test_las_filas_sin_texto_no_participan_y_se_cuentan(self) -> None:
        rows = [
            ControlRow(price=Decimal("100"), provenance="human", channel=CANAL_A, text=None),
            ControlRow(price=Decimal("100"), provenance="human", channel=CANAL_B, text="  "),
            ControlRow(price=Decimal("100"), provenance="human", channel=CANAL_B, text="Arroz"),
        ]
        result = _controls(rows)

        assert result.rows_without_text == 2
        assert result.identical_text_rows == 0


# --- lectura de la base -------------------------------------------------------

HOY = date(2026, 10, 3)


class _Seed:
    """Una celda de Arroz en Santiago, minorista y por libra."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        self.scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([self.arroz, self.scu])
        session.flush()
        self._n = 0

    def channel(self, name: str) -> Channel:
        canal = Channel(name=name, platform=Platform.WHATSAPP_GROUP)
        self.session.add(canal)
        self.session.flush()
        return canal

    def row(
        self,
        price: str,
        *,
        provenance: str = "human",
        channel: Channel | None = None,
        source_reference: str | None = None,
        sender_hash: str | None = None,
        day: date = HOY,
        captured_at: datetime = T0,
        text: str | None = "Arroz",
    ) -> None:
        self._n += 1
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            source_reference=source_reference,
            file_path=f"raw/c3-{self._n}.txt",
            content_hash=f"hash-c3-{self._n}",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=captured_at,
            channel_id=channel.id if channel else None,
            sender_hash=sender_hash,
            original_text=text,
        )
        self.session.add(origen)
        self.session.flush()
        self.session.add(
            PriceHistory(
                provenance=provenance,
                product_id=self.arroz.id,
                province_id=self.scu.id,
                price=Decimal(price),
                currency=Currency.CUP.value,
                unit="lb",
                quantity=Decimal("1"),
                sale_format=SaleFormat.RETAIL.value,
                published_date=day,
                source_id=origen.id,
            )
        )
        self.session.flush()


class TestLecturaDeLaBase:
    def test_arma_la_celda_publica_con_su_indice_de_control(self, session: Session) -> None:
        seed = _Seed(session)
        for price in HUMANS:
            seed.row(price)
        for price in ("290", "295", "300", "300", "305", "310", "320"):
            seed.row(price, provenance="auto")

        cells = compute_cell_controls(session, burst_window=BURST, hoy=HOY)

        assert len(cells) == 1
        cell = cells[0]
        assert (cell.product_name, cell.province_name, cell.unit) == (
            "Arroz",
            "Santiago de Cuba",
            "lb",
        )
        assert cell.controls.count == 13
        assert cell.controls.control_count == 6
        assert cell.controls.diverges is True

    def test_una_fila_de_hace_mas_de_30_dias_queda_fuera(self, session: Session) -> None:
        seed = _Seed(session)
        seed.row("100")
        seed.row("100", day=HOY - timedelta(days=30))  # el borde entra
        seed.row("100", day=HOY - timedelta(days=31))

        (cell,) = compute_cell_controls(session, burst_window=BURST, hoy=HOY)

        assert cell.controls.count == 2

    def test_un_producto_que_no_es_piloto_no_aparece(self, session: Session) -> None:
        seed = _Seed(session)
        seed.arroz.is_pilot = False
        seed.row("100")

        assert compute_cell_controls(session, burst_window=BURST, hoy=HOY) == []

    def test_el_canal_de_una_ingesta_vieja_se_resuelve_por_nombre(
        self, session: Session
    ) -> None:
        # 3 filas con `channel_id` y 1 anterior a esa columna, que solo trae el
        # nombre del mismo canal: son 4 del mismo canal, ninguna sin canal.
        seed = _Seed(session)
        canal = seed.channel("Ventas Santiago")
        for _ in range(3):
            seed.row("100", channel=canal)
        seed.row("100", source_reference="Ventas Santiago")
        seed.row("100")  # sin ningún rastro de canal

        (cell,) = compute_cell_controls(session, burst_window=BURST, hoy=HOY)

        assert cell.controls.top_channel_rows == 4
        assert cell.controls.rows_without_channel == 1

    def test_primero_las_que_divergen_despues_las_sin_ancla(self, session: Session) -> None:
        seed = _Seed(session)
        hlg = Province(code="hlg", name="Holguín")
        lha = Province(code="lha", name="La Habana")
        session.add_all([hlg, lha])
        session.flush()
        # Santiago: 5 humanas, con ancla y sin divergir. Es la de mayor n.
        for _ in range(5):
            seed.row("100")
        seed.row("100", provenance="auto")
        # Holguín: una sola fila, sin ancla.
        seed.scu = hlg
        seed.row("100")
        # La Habana: 5 humanas en 100 y 6 automáticas en 300, diverge.
        seed.scu = lha
        for _ in range(5):
            seed.row("100")
        for _ in range(6):
            seed.row("300", provenance="auto")

        cells = compute_cell_controls(session, burst_window=BURST, hoy=HOY)

        assert [c.province_name for c in cells] == ["La Habana", "Holguín", "Santiago de Cuba"]
        assert [c.status for c in cells] == ["diverges", "unanchored", "anchored"]


class TestResolverDeOrigenEnBloque:
    """`origin_resolver` tiene que decir lo mismo que `resolve_origin`."""

    def _ingestion(self, session: Session, n: int, **kwargs: object) -> RawIngestion:
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/o-{n}.txt",
            content_hash=f"hash-o-{n}",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=T0,
            **kwargs,
        )
        session.add(ingestion)
        session.flush()
        return ingestion

    def test_coincide_con_resolve_origin_en_los_cuatro_casos(self, session: Session) -> None:
        unico = Channel(name="Único", platform=Platform.WHATSAPP_GROUP)
        doble_1 = Channel(name="Ventas Mayoristas", platform=Platform.WHATSAPP_GROUP)
        doble_2 = Channel(name="Ventas Mayoristas", platform=Platform.WHATSAPP_CHANNEL)
        session.add_all([unico, doble_1, doble_2])
        session.flush()
        ingestions = [
            self._ingestion(session, 1, channel_id=doble_2.id, source_reference="Único"),
            self._ingestion(session, 2, source_reference="Único"),
            self._ingestion(session, 3, source_reference="Ventas Mayoristas"),
            self._ingestion(session, 4, source_reference="No existe"),
            self._ingestion(session, 5),
        ]
        # Lo esperado, escrito a mano: el id manda; un nombre único resuelve;
        # un nombre ambiguo, uno desconocido y ninguno, no.
        expected = [doble_2.id, unico.id, None, None, None]

        resolve = origin_resolver(session)

        assert [resolve(i.channel_id, i.source_reference) for i in ingestions] == expected
        for ingestion, channel_id in zip(ingestions, expected, strict=True):
            resolved = resolve_origin(session, ingestion)
            assert (resolved.id if resolved else None) == channel_id
