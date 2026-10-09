"""Carril 1 encendido: el pre-cálculo guarda las filas (slice B2b-2).

Contrato: `docs/spec-slice-B2b.md`, casos 11 a 15. Los esperados están escritos
A MANO, antes que el código: el anuncio dice «Arroz a 320 la libra» en un canal
de La Habana, así que la fila correcta es arroz, 320 CUP, 1 lb, La Habana, con
el precio por unidad. Ningún esperado sale de correr el pre-cálculo.

El modelo y la tasa de cambio son dobles: ningún test toca la red.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.extraction import prefill
from saimc.models import (
    Channel,
    ContentType,
    CurationAction,
    CurationEvent,
    LlmProposal,
    LlmReview,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReviewOutcome,
    SourceType,
)
from saimc.validation import context, lane1
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher

# --- dobles ---------------------------------------------------------------------


def _fila(**cambios: Any) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "product": "Arroz",
        "price": 320,
        "currency": "CUP",
        "quantity": 1,
        "unit": "lb",
        "province": None,
        "sale_format": "retail",
        "price_basis": "unit",
        "packaging": None,
        "evidence": "Arroz a 320 la libra",
        "currency_explicit": False,
    }
    fila.update(cambios)
    return fila


def _propone(*filas: dict[str, Any]) -> dict[str, Any]:
    return {"discard": False, "discard_reason": None, "rows": list(filas)}


class ClienteFalso:
    """Doble de `anthropic.Anthropic`: contesta el cuerpo que le toque al texto
    del anuncio (`por_texto`), o `cuerpo` si no hay uno propio."""

    def __init__(self, cuerpo: object, por_texto: dict[str, object] | None = None) -> None:
        self._cuerpo = cuerpo
        self._por_texto = por_texto or {}
        self.llamadas = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw: Any) -> SimpleNamespace:
        self.llamadas += 1
        pedido = json.dumps(kw, default=str, ensure_ascii=False)
        cuerpo = next((c for t, c in self._por_texto.items() if t in pedido), self._cuerpo)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(cuerpo))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def _sin_tasa(key: str, day: date) -> Decimal | None:
    return None


def _tasa_400(key: str, day: date) -> Decimal | None:
    return Decimal("400")


# --- datos ----------------------------------------------------------------------


@pytest.fixture
def mundo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    session.add_all([arroz, lh])
    session.flush()
    canal = Channel(name="Mercado Habana", platform=Platform.WHATSAPP_GROUP, province_id=lh.id)
    otro = Channel(name="Otro Mercado", platform=Platform.WHATSAPP_GROUP, province_id=lh.id)
    session.add_all([canal, otro])
    session.commit()
    return SimpleNamespace(arroz=arroz, lh=lh, canal=canal, otro=otro)


_n = 0


def _anuncio(
    session: Session,
    canal: Channel,
    texto: str = "Arroz a 320 la libra",
    *,
    minutos: int = 0,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.WHATSAPP_CHANNEL,
        channel_id=canal.id,
        source_reference=canal.name,
        file_path=f"raw/l1-{_n}.txt",
        content_hash=f"l1-{_n}",
        content_type=ContentType.TEXT,
        original_text=texto,
        processing_status=ProcessingStatus.PENDING,
        # Mediodía UTC: el mismo día en La Habana, sin borde de medianoche.
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutos),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _ajustes(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "clave-de-prueba",
        "llm_assist_log_path": tmp_path / "assist.jsonl",
        "raw_store_dir": tmp_path / "raw",
        "prefill_lock_path": tmp_path / "prefill.lock",
        "prefill_status_path": tmp_path / "prefill-status.json",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _precalcular(
    session: Session,
    tmp_path: Path,
    cliente: ClienteFalso,
    *,
    tasa: Any = _sin_tasa,
    **ajustes: Any,
) -> str:
    salida: list[str] = []
    prefill.execute(
        prefill.parse_args(["--execute"]),
        session,
        _ajustes(tmp_path, **ajustes),
        client=cliente,
        transcribe=lambda path: "",
        rate_fetcher=tasa,
        out=salida.append,
    )
    return "\n".join(salida)


def _filas(session: Session, ingesta: RawIngestion) -> list[PriceHistory]:
    return list(
        session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingesta.id)
        ).scalars()
    )


def _eventos(session: Session, ingesta: RawIngestion) -> list[CurationEvent]:
    return list(
        session.execute(
            select(CurationEvent).where(CurationEvent.ingestion_id == ingesta.id)
        ).scalars()
    )


def _sigue_pendiente_sin_filas(session: Session, ingesta: RawIngestion) -> None:
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert _filas(session, ingesta) == []
    assert _eventos(session, ingesta) == []


# --- los valores que fijó G1b -----------------------------------------------------


def test_los_parametros_son_los_que_aprobo_el_operador() -> None:
    assert lane1.CONFIG.lane1_max_rows == 1
    assert lane1.CONFIG.channel_min_history == 3
    assert lane1.CONFIG.channel_max_edit_rate == 0.5
    assert lane1.CONFIG.min_cell_n == 5
    assert lane1.CONFIG.signal_high_probability is None


# --- caso 11: interruptor apagado ---------------------------------------------------


def test_caso_11_con_el_interruptor_apagado_el_precalculo_es_el_de_hoy(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _anuncio(session, mundo.canal)

    salida = _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())))

    assert session.execute(select(LlmProposal)).scalars().one().ingestion_id == ingesta.id
    _sigue_pendiente_sin_filas(session, ingesta)
    assert "Aceptados solos (carril 1): 0 (interruptor apagado)" in salida


# --- el carril 1 guarda la fila -----------------------------------------------------


def test_un_anuncio_del_carril_1_queda_curado_con_su_fila_automatica(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _anuncio(session, mundo.canal)

    salida = _precalcular(
        session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True
    )

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.CURATED
    (fila,) = _filas(session, ingesta)
    assert fila.product_id == mundo.arroz.id
    assert fila.price == Decimal("320")
    assert fila.currency == "CUP"
    assert fila.quantity == Decimal("1")
    assert fila.unit == "lb"
    assert fila.province_id == mundo.lh.id
    assert fila.price_basis == "unit"
    assert fila.sale_format == "retail"
    assert fila.published_date == date(2026, 9, 1)
    assert fila.provenance == "auto"
    # Canal sin historial y celda sin referencias: entra, pero sin aprobar.
    assert fila.validation_status == "unverified"

    propuesta = session.execute(select(LlmProposal)).scalars().one()
    (evento,) = _eventos(session, ingesta)
    assert evento.action == CurationAction.CURATED
    assert evento.mode == "auto"
    assert evento.proposal_id == propuesta.id
    assert evento.shown_at is None
    assert evento.detail == {"row_count": 1}
    # La revisión deja al anuncio fuera de H0, como todo lo asistido.
    revision = session.execute(select(LlmReview)).scalars().one()
    assert revision.outcome == ReviewOutcome.CURATED
    assert revision.proposal_id == propuesta.id
    assert "Aceptados solos (carril 1): 1" in salida


def test_dos_filas_superan_el_tope_y_el_anuncio_sigue_pendiente(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = "Arroz a 320 la libra\nArroz a 300 la libra por saco"
    ingesta = _anuncio(session, mundo.canal, texto)
    cuerpo = _propone(
        _fila(), _fila(price=300, evidence="Arroz a 300 la libra por saco")
    )

    _precalcular(session, tmp_path, ClienteFalso(cuerpo), lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, ingesta)


def test_un_anuncio_sin_canal_sigue_pendiente(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/l1-inbox.txt",
        content_hash="l1-inbox",
        content_type=ContentType.TEXT,
        original_text="Arroz a 320 la libra",
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    session.add(ingesta)
    session.commit()

    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, ingesta)


def test_el_carril_0_gana_y_el_carril_1_no_toca_un_descarte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _anuncio(session, mundo.canal, "Buenos días grupo, feliz domingo")
    cuerpo = {"discard": True, "discard_reason": "no_data", "rows": []}

    _precalcular(
        session,
        tmp_path,
        ClienteFalso(cuerpo),
        lane0_text_enabled=True,
        lane1_enabled=True,
    )

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert _filas(session, ingesta) == []


# --- caso 12: lo mismo que curar sin tocar nada --------------------------------------

_IGUALES = (
    "product_id",
    "province_id",
    "price",
    "currency",
    "quantity",
    "unit",
    "sale_format",
    "format_id",
    "variant_id",
    "min_purchase_quantity",
    "price_basis",
    "published_date",
    "price_cup",
    "exchange_rate",
    "exchange_rate_date",
    "exchange_rate_source",
    "validation_status",
    "confidence_score",
)


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    app.dependency_overrides[get_rate_fetcher] = lambda: _tasa_400
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("texto", "fila_modelo", "formulario"),
    [
        (
            "Arroz a 320 la libra",
            _fila(),
            {"price_0": "320", "currency_0": "CUP", "quantity_0": "1", "unit_0": "lb",
             "price_basis_0": "unit", "sale_format_0": "retail", "packaging_0": ""},
        ),
        (
            "Arroz 25 USD el saco de 50 lb",
            _fila(
                price=25, currency="USD", quantity=50, unit="lb", price_basis="total",
                packaging="saco", evidence="Arroz 25 USD el saco de 50 lb",
                currency_explicit=True,
            ),
            {"price_0": "25", "currency_0": "USD", "quantity_0": "50", "unit_0": "lb",
             "price_basis_0": "total", "sale_format_0": "retail", "packaging_0": "saco"},
        ),
    ],
    ids=["cup_por_libra", "usd_saco_con_formato"],
)
def test_caso_12_las_filas_son_las_mismas_que_si_el_operador_curara_sin_tocar(
    session: Session,
    mundo: SimpleNamespace,
    tmp_path: Path,
    client: TestClient,
    texto: str,
    fila_modelo: dict[str, Any],
    formulario: dict[str, str],
) -> None:
    # Dos canales distintos de la misma provincia: el mismo anuncio en cada
    # uno, para que ninguno sea «posible duplicado» del otro.
    a_mano = _anuncio(session, mundo.otro, texto)
    cliente = ClienteFalso(_propone(fila_modelo))
    _precalcular(session, tmp_path, cliente, tasa=_tasa_400)  # solo la propuesta
    propuesta = session.execute(select(LlmProposal)).scalars().one()
    respuesta = client.post(
        f"/curation/{a_mano.id}",
        data={
            "row_count": "1",
            "published_date": "2026-09-01",
            "proposal_id": str(propuesta.id),
            "proposal_row_0": "0",
            "product_id_0": str(mundo.arroz.id),
            "province_id_0": str(mundo.lh.id),
            **formulario,
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303, respuesta.text

    solo = _anuncio(session, mundo.canal, texto, minutos=1)
    _precalcular(session, tmp_path, cliente, tasa=_tasa_400, lane1_enabled=True)

    (humana,) = _filas(session, a_mano)
    (automatica,) = _filas(session, solo)
    for campo in _IGUALES:
        assert getattr(automatica, campo) == getattr(humana, campo), campo
    # Que la comparación no sea `None == None`: la fila a mano trae lo que el
    # formulario mandó, y el saco resolvió su formato.
    assert humana.price == Decimal(formulario["price_0"])
    assert humana.validation_status is not None
    assert (humana.format_id is not None) == bool(formulario["packaging_0"])
    assert (humana.price_cup is not None) == (formulario["currency_0"] != "CUP")
    assert humana.provenance == "human"
    assert automatica.provenance == "auto"


# --- caso 13: divisa sin tasa -------------------------------------------------------


def test_caso_13_fila_en_usd_sin_tasa_resoluble_deja_el_anuncio_pendiente(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = "Arroz 25 USD el saco de 50 lb"
    ingesta = _anuncio(session, mundo.canal, texto)
    fila = _fila(
        price=25, currency="USD", quantity=50, unit="lb", price_basis="total",
        packaging="saco", evidence=texto, currency_explicit=True,
    )

    salida = _precalcular(
        session, tmp_path, ClienteFalso(_propone(fila)), tasa=_sin_tasa, lane1_enabled=True
    )

    _sigue_pendiente_sin_filas(session, ingesta)
    assert session.execute(select(LlmReview)).scalars().all() == []
    assert "Aceptados solos (carril 1): 0" in salida


def test_fila_en_usd_con_tasa_se_guarda_convertida(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = "Arroz 25 USD el saco de 50 lb"
    ingesta = _anuncio(session, mundo.canal, texto)
    fila = _fila(
        price=25, currency="USD", quantity=50, unit="lb", price_basis="total",
        packaging="saco", evidence=texto, currency_explicit=True,
    )

    _precalcular(
        session, tmp_path, ClienteFalso(_propone(fila)), tasa=_tasa_400, lane1_enabled=True
    )

    (guardada,) = _filas(session, ingesta)
    assert guardada.price_cup == Decimal("10000.00")  # 25 USD a 400
    assert guardada.exchange_rate == Decimal("400")


def test_un_anuncio_cuya_fecha_por_defecto_el_formulario_rechazaria_sigue_pendiente(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    # 02:00 UTC del día 2 son las 22:00 del día 1 en La Habana: la fecha por
    # defecto (día 2, en UTC) es posterior al día de la captura, y el
    # formulario le habría pedido al operador que la corrigiera.
    ingesta = _anuncio(session, mundo.canal, minutos=14 * 60)

    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, ingesta)


# --- caso 14: lo aceptado solo no es una decisión del operador ------------------------


def test_caso_14_lo_aceptado_solo_no_suma_al_historial_del_canal(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _anuncio(session, mundo.canal)

    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

    historial = context.channel_history(session, mundo.canal)
    assert historial.decisions == 0
    assert historial.proposed_rows == 0


# --- caso 15: el comando para lo que ya está pendiente --------------------------------


def _con_propuesta(session: Session, tmp_path: Path, canal: Channel) -> RawIngestion:
    """Un anuncio pendiente que ya pasó por el pre-cálculo con el carril apagado."""
    ingesta = _anuncio(session, canal)
    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())))
    return ingesta


def _comando(session: Session, tmp_path: Path, *argumentos: str, **ajustes: Any) -> tuple[int, str]:
    salida: list[str] = []
    codigo = lane1.run(
        lane1.parse_args(list(argumentos)),
        session,
        _ajustes(tmp_path, **ajustes),
        rate_fetcher=_sin_tasa,
        out=salida.append,
    )
    return codigo, "\n".join(salida)


def test_caso_15_el_comando_en_seco_lista_y_no_escribe(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _con_propuesta(session, tmp_path, mundo.canal)

    codigo, salida = _comando(session, tmp_path)

    assert codigo == 0
    assert "EN SECO" in salida
    assert "Entrarían al carril 1: 1 de 1" in salida
    assert str(ingesta.id)[:8] in salida
    _sigue_pendiente_sin_filas(session, ingesta)


def test_caso_15_con_execute_acepta_lo_que_listo(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _con_propuesta(session, tmp_path, mundo.canal)

    codigo, salida = _comando(session, tmp_path, "--execute", lane1_enabled=True)

    assert codigo == 0
    assert "Aceptados solos (carril 1): 1 de 1" in salida
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.CURATED
    (fila,) = _filas(session, ingesta)
    assert fila.provenance == "auto"
    assert [e.mode for e in _eventos(session, ingesta)] == ["auto"]


def test_execute_con_el_interruptor_apagado_no_escribe(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _con_propuesta(session, tmp_path, mundo.canal)

    codigo, salida = _comando(session, tmp_path, "--execute")

    assert codigo == 2
    assert "SAIMC_LANE1_ENABLED" in salida
    _sigue_pendiente_sin_filas(session, ingesta)


def test_el_comando_no_toca_imagenes_ni_anuncios_sin_propuesta(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    sin_propuesta = _anuncio(session, mundo.canal)

    codigo, salida = _comando(session, tmp_path, "--execute", lane1_enabled=True)

    assert codigo == 0
    assert "Aceptados solos (carril 1): 0 de 0" in salida
    _sigue_pendiente_sin_filas(session, sin_propuesta)


def test_el_limite_corta_cuantos_se_miran(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _anuncio(session, mundo.canal)
    _anuncio(session, mundo.otro, minutos=1)
    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())))

    _, salida = _comando(session, tmp_path, "--limit", "1")

    assert "de 1" in salida

