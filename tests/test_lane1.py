"""Carril 1 encendido: el pre-cálculo guarda las filas (slice B2b-2).

Contrato: `docs/spec-slice-B2b.md`, casos 11 a 15. Los esperados están escritos
A MANO, antes que el código: el anuncio dice «Arroz a 320 la libra» en un canal
de La Habana, así que la fila correcta es arroz, 320 CUP, 1 lb, La Habana, con
el precio por unidad. Ningún esperado sale de correr el pre-cálculo.

El modelo y la tasa de cambio son dobles: ningún test toca la red.
"""

from __future__ import annotations

import dataclasses
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

from saimc import curation_rows
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
def tope_de_una_fila(monkeypatch: pytest.MonkeyPatch) -> None:
    """El tope de filas como lo fijó B2b (1), para probar el mecanismo del tope.

    El valor vigente es 4 (2026-10-13); estos tests ejercen que un tope puesto
    se respete, sea cual sea el número.
    """
    monkeypatch.setattr(lane1, "CONFIG", dataclasses.replace(lane1.CONFIG, lane1_max_rows=1))


@pytest.fixture
def tope_de_30_sin_auditar(monkeypatch: pytest.MonkeyPatch) -> None:
    """El tope de aceptados sin auditar como lo fijó G1b (30). Hoy no hay tope
    (`None`); el mecanismo sigue en el código y estos tests lo ejercen."""
    monkeypatch.setattr(lane1, "MAX_UNAUDITED", 30)


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
    return list(session.execute(select(PriceHistory).where(PriceHistory.source_id == ingesta.id)).scalars())


def _eventos(session: Session, ingesta: RawIngestion) -> list[CurationEvent]:
    return list(
        session.execute(select(CurationEvent).where(CurationEvent.ingestion_id == ingesta.id)).scalars()
    )


def _sigue_pendiente_sin_filas(session: Session, ingesta: RawIngestion) -> None:
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert _filas(session, ingesta) == []
    assert _eventos(session, ingesta) == []


# --- los valores que fijó G1b -----------------------------------------------------


def test_los_parametros_son_los_que_aprobo_el_operador() -> None:
    assert lane1.CONFIG.lane1_max_rows == 4
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

    salida = _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

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
    tope_de_una_fila: None, session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = "Arroz a 320 la libra\nArroz a 300 la libra por saco"
    ingesta = _anuncio(session, mundo.canal, texto)
    cuerpo = _propone(_fila(), _fila(price=300, evidence="Arroz a 300 la libra por saco"))

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
            {
                "price_0": "320",
                "currency_0": "CUP",
                "quantity_0": "1",
                "unit_0": "lb",
                "price_basis_0": "unit",
                "sale_format_0": "retail",
                "packaging_0": "",
            },
        ),
        (
            "Arroz 25 USD el saco de 50 lb",
            _fila(
                price=25,
                currency="USD",
                quantity=50,
                unit="lb",
                price_basis="total",
                packaging="saco",
                evidence="Arroz 25 USD el saco de 50 lb",
                currency_explicit=True,
            ),
            {
                "price_0": "25",
                "currency_0": "USD",
                "quantity_0": "50",
                "unit_0": "lb",
                "price_basis_0": "total",
                "sale_format_0": "retail",
                "packaging_0": "saco",
            },
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
        price=25,
        currency="USD",
        quantity=50,
        unit="lb",
        price_basis="total",
        packaging="saco",
        evidence=texto,
        currency_explicit=True,
    )

    salida = _precalcular(session, tmp_path, ClienteFalso(_propone(fila)), tasa=_sin_tasa, lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, ingesta)
    assert session.execute(select(LlmReview)).scalars().all() == []
    assert "Aceptados solos (carril 1): 0" in salida


def test_fila_en_usd_con_tasa_se_guarda_convertida(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = "Arroz 25 USD el saco de 50 lb"
    ingesta = _anuncio(session, mundo.canal, texto)
    fila = _fila(
        price=25,
        currency="USD",
        quantity=50,
        unit="lb",
        price_basis="total",
        packaging="saco",
        evidence=texto,
        currency_explicit=True,
    )

    _precalcular(session, tmp_path, ClienteFalso(_propone(fila)), tasa=_tasa_400, lane1_enabled=True)

    (guardada,) = _filas(session, ingesta)
    assert guardada.price_cup == Decimal("10000.00")  # 25 USD a 400
    assert guardada.exchange_rate == Decimal("400")


def test_un_anuncio_capturado_de_madrugada_en_utc_se_fecha_en_el_dia_de_la_habana(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    # 02:00 UTC del día 2 son las 22:00 del día 1 en La Habana (T2): la fecha
    # por defecto es el día 1, la que el formulario acepta. Antes salía el día
    # 2 (UTC), posterior a la captura, y el anuncio se quedaba pendiente.
    ingesta = _anuncio(session, mundo.canal, minutos=14 * 60)

    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

    (fila,) = _filas(session, ingesta)
    assert fila.published_date == date(2026, 9, 1)


def test_una_fecha_por_defecto_que_el_formulario_rechazaria_sigue_pendiente(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # La guarda sigue en pie aunque la fecha por defecto ya no pueda salir
    # posterior al día de La Habana: si algún día vuelve a pasar, el carril no
    # guarda una fecha que el formulario le haría corregir al operador.
    ingesta = _anuncio(session, mundo.canal)
    monkeypatch.setattr(curation_rows, "default_published_date", lambda ingestion, text: date(2026, 9, 2))

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


def test_el_limite_corta_cuantos_se_miran(session: Session, mundo: SimpleNamespace, tmp_path: Path) -> None:
    _anuncio(session, mundo.canal)
    _anuncio(session, mundo.otro, minutos=1)
    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())))

    _, salida = _comando(session, tmp_path, "--limit", "1")

    assert "de 1" in salida


# =============================================================================
# B2b-3a: auditoría y freno (docs/spec-slice-B2b.md, casos 16 a 23)
# =============================================================================
#
# Las cotas esperadas salen de la especificación (Clopper-Pearson unilateral al
# 95 %): 4 errores en 10 dan una cota inferior de 15,0 %; 2 en 10, de 3,7 %;
# 2 en 60 dan una cota superior de 10,1 %; 1 en 60, de 7,7 %. La meta es 10 %.


def _aceptar(session: Session, tmp_path: Path, canal: Channel, n: int = 1) -> list[RawIngestion]:
    """`n` anuncios del canal, pasados por el pre-cálculo con el carril encendido."""
    global _n
    base = _n
    ingestas = [_anuncio(session, canal, minutos=base + i) for i in range(n)]
    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)
    for ingesta in ingestas:
        session.refresh(ingesta)
    return ingestas


def _editar(client: TestClient, ingesta: RawIngestion, fila: PriceHistory, **cambios: str) -> None:
    datos = {
        "source_reference": ingesta.source_reference or "",
        "published_date": "2026-09-01",
        "row_count": "1",
        "price_id_0": str(fila.id),
        "product_id_0": str(fila.product_id),
        "province_id_0": str(fila.province_id),
        "price_0": str(fila.price),
        "currency_0": fila.currency,
        "quantity_0": str(fila.quantity),
        "unit_0": fila.unit or "",
        "sale_format_0": fila.sale_format or "",
        "packaging_0": "",
        "price_basis_0": fila.price_basis or "",
    }
    datos.update({f"{campo}_0": valor for campo, valor in cambios.items()})
    respuesta = client.post(f"/curation/{ingesta.id}/edit", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303, respuesta.text


def test_las_metas_de_la_auditoria_son_las_que_aprobo_el_operador() -> None:
    assert lane1.ERROR_TARGET == 0.10
    assert lane1.AUDIT_N0 == 60
    # Sin tope de aceptados sin auditar (2026-10-13): el carril no espera al
    # operador; el único freno es el error medido.
    assert lane1.MAX_UNAUDITED is None


def test_muchos_aceptados_sin_auditar_no_frenan_el_carril() -> None:
    assert not lane1.brake_for(audited=0, errors=0, unaudited=5000).on


def test_el_error_sigue_frenando_aunque_no_haya_tope_de_sin_auditar() -> None:
    # 8 errores en 10 auditados: la cota inferior supera la meta de 10 %.
    assert lane1.brake_for(audited=10, errors=8, unaudited=500).on


def test_caso_16_esta_bien_deja_las_filas_auditadas_y_no_suma_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    ajustes = _ajustes(tmp_path)
    antes = lane1.audit_state(session, ajustes)
    assert (antes.audited, antes.errors, antes.unaudited) == (0, 0, 1)

    assert lane1.mark_ok(session, ingesta.id) is True
    session.commit()

    (fila,) = _filas(session, ingesta)
    assert fila.provenance == "audited"
    despues = lane1.audit_state(session, ajustes)
    assert (despues.audited, despues.errors, despues.unaudited) == (1, 0, 0)
    # Una segunda respuesta no cambia nada.
    assert lane1.mark_ok(session, ingesta.id) is False


def test_caso_14_despues_de_auditado_si_suma_al_historial_del_canal(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)

    lane1.mark_ok(session, ingesta.id)
    session.commit()

    historial = context.channel_history(session, mundo.canal)
    assert (historial.decisions, historial.proposed_rows, historial.changed_rows) == (1, 1, 0)


def test_caso_17_corregir_un_precio_suma_un_error_y_ensucia_el_canal(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    (fila,) = _filas(session, ingesta)

    _editar(client, ingesta, fila, price="330")

    session.expire_all()
    (corregida,) = _filas(session, ingesta)
    assert corregida.price == Decimal("330")
    assert corregida.provenance == "audited"
    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors, estado.unaudited) == (1, 1, 0)
    historial = context.channel_history(session, mundo.canal)
    assert (historial.decisions, historial.proposed_rows, historial.changed_rows) == (1, 1, 1)


def test_abrir_la_edicion_y_guardar_sin_cambios_cuenta_como_esta_bien(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    (fila,) = _filas(session, ingesta)

    _editar(client, ingesta, fila)

    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors) == (1, 0)


def test_caso_18_no_era_valido_borra_las_filas_descarta_y_suma_un_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)

    assert lane1.mark_invalid(session, ingesta.id, "no_data") is True
    session.commit()

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == "no_data"
    assert _filas(session, ingesta) == []
    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors, estado.unaudited) == (1, 1, 0)


def test_editar_un_anuncio_curado_a_mano_no_es_una_auditoria(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _anuncio(session, mundo.canal)

    assert lane1.mark_ok(session, ingesta.id) is False
    assert lane1.mark_invalid(session, ingesta.id, "no_data") is False
    assert lane1.audit_state(session, _ajustes(tmp_path)).audited == 0


# --- el freno ----------------------------------------------------------------------


def test_caso_19_con_30_sin_auditar_el_siguiente_queda_pendiente(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingestas = _aceptar(session, tmp_path, mundo.canal, n=31)

    estados = [i.processing_status for i in ingestas]
    assert estados.count(ProcessingStatus.CURATED) == 30
    assert estados.count(ProcessingStatus.PENDING) == 1
    freno = lane1.brake(session, _ajustes(tmp_path))
    assert freno.on is True
    assert "30" in freno.reason and "sin auditar" in freno.reason


def test_auditar_levanta_el_freno_de_los_sin_auditar(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingestas = _aceptar(session, tmp_path, mundo.canal, n=30)
    assert lane1.brake(session, _ajustes(tmp_path)).on is True

    lane1.mark_ok(session, ingestas[0].id)
    session.commit()

    assert lane1.brake(session, _ajustes(tmp_path)).on is False


@pytest.mark.parametrize(
    ("auditados", "errores", "puesto"),
    [
        (10, 4, True),  # cota inferior 15,0 % > 10 %
        (10, 2, False),  # cota inferior 3,7 %
        (0, 0, False),
        (59, 5, False),  # antes de n0 solo frena la evidencia: cota inferior 3,4 %
        (60, 2, True),  # cota superior 10,1 % > 10 %
        (60, 1, False),  # cota superior 7,7 %
        (150, 3, False),  # cota superior 5,1 %
    ],
)
def test_casos_20_y_21_el_freno_por_error_auditado(auditados: int, errores: int, puesto: bool) -> None:
    freno = lane1.brake_for(audited=auditados, errors=errores, unaudited=0)

    assert freno.on is puesto
    assert bool(freno.reason) is puesto


def test_caso_22_audit_since_deja_fuera_los_errores_anteriores(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingestas = _aceptar(session, tmp_path, mundo.canal, n=2)
    for ingesta in ingestas:
        lane1.mark_invalid(session, ingesta.id, "no_data")
    session.commit()
    todo = lane1.audit_state(session, _ajustes(tmp_path))
    assert (todo.audited, todo.errors) == (2, 2)

    manana = date.today() + timedelta(days=1)
    desde = lane1.audit_state(session, _ajustes(tmp_path, lane1_audit_since=manana))

    assert (desde.audited, desde.errors) == (0, 0)


def test_caso_23_una_celda_divergente_deja_pendiente_solo_a_ese_anuncio(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True)
    session.add(aceite)
    session.commit()
    monkeypatch.setattr(
        lane1, "diverging_cells", lambda session, settings: frozenset({("Arroz", "La Habana")})
    )
    de_arroz = _anuncio(session, mundo.canal)
    de_aceite = _anuncio(session, mundo.canal, "Aceite a 900 el litro", minutos=1)
    cliente = ClienteFalso(
        _propone(_fila()),
        por_texto={
            "Aceite a 900 el litro": _propone(
                _fila(product="Aceite", price=900, unit="l", evidence="Aceite a 900 el litro")
            )
        },
    )

    _precalcular(session, tmp_path, cliente, lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, de_arroz)
    session.refresh(de_aceite)
    assert de_aceite.processing_status == ProcessingStatus.CURATED


def test_el_precalculo_dice_por_que_esta_puesto_el_freno(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=30)
    _anuncio(session, mundo.otro, minutos=999)

    salida = _precalcular(session, tmp_path, ClienteFalso(_propone(_fila())), lane1_enabled=True)

    assert "Aceptados solos (carril 1): 0" in salida
    assert "freno puesto" in salida and "sin auditar" in salida


def test_el_comando_con_execute_respeta_el_freno(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=30)
    pendiente = _con_propuesta(session, tmp_path, mundo.otro)

    codigo, salida = _comando(session, tmp_path, "--execute", lane1_enabled=True)

    assert codigo == 0
    assert "freno puesto" in salida
    _sigue_pendiente_sin_filas(session, pendiente)


# --- la pantalla ---------------------------------------------------------------------


def test_la_auditoria_lista_los_aceptados_solos_con_sus_tres_acciones(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)

    html = client.get("/curation/auditoria").text

    assert "Aceptados solos" in html
    assert "Arroz a 320 la libra" in html
    assert f"/curation/auditoria/{ingesta.id}/aceptado-bien" in html
    assert f"/curation/{ingesta.id}/edit" in html
    assert f"/curation/auditoria/{ingesta.id}/aceptado-invalido" in html
    assert "0 de 60" in html  # cuánto falta para dejar la auditoría total


def test_los_botones_de_la_auditoria_escriben_la_respuesta(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    bien, mal = _aceptar(session, tmp_path, mundo.canal, n=2)

    r1 = client.post(f"/curation/auditoria/{bien.id}/aceptado-bien", follow_redirects=False)
    r2 = client.post(
        f"/curation/auditoria/{mal.id}/aceptado-invalido",
        data={"discard_reason": "off_focus"},
        follow_redirects=False,
    )

    assert (r1.status_code, r2.status_code) == (303, 303)
    session.expire_all()
    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors, estado.unaudited) == (2, 1, 0)
    session.refresh(mal)
    assert mal.discard_reason == "off_focus"
    html = client.get("/curation/auditoria").text
    assert f"/curation/auditoria/{bien.id}/aceptado-bien" not in html


# --- anuncios curados sin filas (2026-10-17) ------------------------------------------


def _borrar_las_filas(session: Session, ingesta: RawIngestion) -> None:
    for fila in _filas(session, ingesta):
        session.delete(fila)
    session.commit()


def test_esta_bien_no_acepta_un_anuncio_sin_filas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    _borrar_las_filas(session, ingesta)

    assert lane1.mark_ok(session, ingesta.id) is False

    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors) == (0, 0)


def test_cuenta_los_curados_sin_filas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    con_filas, sin_filas = _aceptar(session, tmp_path, mundo.canal, n=2)
    assert lane1.curated_without_rows(session) == 0

    _borrar_las_filas(session, sin_filas)

    assert lane1.curated_without_rows(session) == 1
    assert con_filas.processing_status == "curated"


def test_la_pantalla_avisa_de_los_curados_sin_filas_y_quita_esta_bien(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    html = client.get("/curation/auditoria").text
    assert "sin filas" not in html

    _borrar_las_filas(session, ingesta)
    html = client.get("/curation/auditoria").text

    assert "1 anuncio curado sin filas" in html
    assert f"/curation/auditoria/{ingesta.id}/aceptado-bien" not in html
    assert f"/curation/auditoria/{ingesta.id}/aceptado-invalido" in html


# --- el total sin auditar y lo que espera (2026-10-17) ---------------------------------


def test_en_el_arranque_todo_lo_sin_auditar_toca_y_se_mide_desde_el_mas_viejo(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=3)

    pendiente = lane1.audit_pending(
        session, _ajustes(tmp_path), now=datetime.now(UTC) + timedelta(days=4)
    )

    assert (pendiente.total, pendiente.due, pendiente.starting) == (3, 3, True)
    assert pendiente.waiting_hours is not None and 95 <= pendiente.waiting_hours <= 97


def test_pasado_el_arranque_el_total_incluye_lo_que_no_toca_auditar(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lane1, "AUDIT_N0", 1)
    monkeypatch.setattr(lane1, "MAX_UNAUDITED", 100)
    monkeypatch.setattr(lane1, "WEEKLY_SAMPLE", 2)
    _aceptar(session, tmp_path, mundo.canal, n=6)
    _arranque_terminado(session, tmp_path, mundo)
    ajustes = _ajustes(tmp_path)

    abierta = lane1.audit_pending(session, ajustes, now=datetime.now(UTC))
    cerrada = lane1.audit_pending(session, ajustes, now=datetime.now(UTC) + timedelta(days=8))

    # Con la semana abierta no toca nada; el total sigue siendo 6 (el 7.º está auditado).
    assert (abierta.total, abierta.due, abierta.starting, abierta.waiting_hours) == (6, 0, False, None)
    # Cerrada, la muestra de 2 se reparte en ronda entre canales: un lugar es del
    # tercer canal (ya auditado), así que toca 1. Espera desde que cerró la semana.
    assert (cerrada.total, cerrada.due, cerrada.starting) == (6, 1, False)
    assert cerrada.waiting_hours is not None and 0 < cerrada.waiting_hours <= 8 * 24


def test_la_pantalla_de_auditoria_muestra_el_total_aparte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lane1, "AUDIT_N0", 1)
    monkeypatch.setattr(lane1, "MAX_UNAUDITED", 100)
    _aceptar(session, tmp_path, mundo.canal, n=3)
    _arranque_terminado(session, tmp_path, mundo)

    html = client.get("/curation/auditoria").text

    assert "Sin auditar en total: 3" in html
    assert "por auditar ahora: 0" in html


def test_control_muestra_la_tarjeta_de_auditoria_cuando_el_carril_tiene_filas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=2)

    html = client.get("/control").text

    assert "Auditoría del carril 1" in html
    assert "2 aceptados sin auditar" in html


def test_la_pantalla_avisa_cuando_el_freno_esta_puesto(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=30)

    html = client.get("/curation/auditoria").text

    assert "Freno puesto" in html


# =============================================================================
# B2b-3b: lo público, /control y la muestra semanal (docs/spec-slice-B2b.md)
# =============================================================================

_SIN_REVISION_PREVIA = "Ningún precio llega al sitio sin esa revisión"


def _visible(html: str) -> str:
    return " ".join(html.split())


def test_la_metodologia_no_cambia_mientras_el_carril_1_no_acepto_nada(
    session: Session, mundo: SimpleNamespace, client: TestClient
) -> None:
    texto = _visible(client.get("/metodologia").text)

    assert _SIN_REVISION_PREVIA in texto
    assert "sin que una persona" not in texto


def test_la_metodologia_dice_que_hay_precios_sin_revision_previa(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    bien, _otro = _aceptar(session, tmp_path, mundo.canal, n=2)
    lane1.mark_ok(session, bien.id)
    session.commit()

    texto = _visible(client.get("/metodologia").text)

    # Deja de afirmar lo que ya no es cierto, y dice lo que pasa.
    assert _SIN_REVISION_PREVIA not in texto
    assert "sin que una persona lo revise antes" in texto
    # Los conteos por procedencia: 0 a mano, 1 automática sin auditar, 1 auditada.
    assert "1 entró sola y todavía no se auditó" in texto
    assert "1 entró sola y ya se auditó" in texto
    # El error auditado va con su n y su cota (0 errores en 1: cota 95,0 %).
    assert "0 errores en 1" in texto
    assert "95,0 %" in texto


def test_control_muestra_el_carril_1_con_su_procedencia_y_su_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    bien, mal, _queda = _aceptar(session, tmp_path, mundo.canal, n=3)
    lane1.mark_ok(session, bien.id)
    lane1.mark_invalid(session, mal.id, "no_data")
    session.commit()

    texto = _visible(client.get("/control/calidad").text)

    assert "Carril 1" in texto
    assert "Automáticas sin auditar: 1" in texto
    assert "Auditadas: 1" in texto
    assert "1 errores en 2 auditados" in texto
    assert "Freno puesto" not in texto


def test_control_marca_el_freno_con_su_motivo(
    tope_de_30_sin_auditar: None, session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    _aceptar(session, tmp_path, mundo.canal, n=30)

    texto = _visible(client.get("/control/calidad").text)

    assert "Freno puesto" in texto
    assert "30 aceptados sin auditar" in texto


# --- la muestra semanal, desde que termina el arranque ------------------------------


def _semana_siguiente() -> date:
    return date.today() + timedelta(days=8)


def _arranque_terminado(session: Session, tmp_path: Path, mundo: SimpleNamespace) -> None:
    """Un anuncio de un tercer canal, aceptado y auditado: con `AUDIT_N0` en 1
    eso termina el arranque. Va después de aceptar los demás: con un solo
    auditado la cota superior ya pasa la meta y el freno no dejaría entrar nada."""
    tercero = Channel(name="Tercer Mercado", platform=Platform.WHATSAPP_GROUP, province_id=mundo.lh.id)
    session.add(tercero)
    session.commit()
    (primero,) = _aceptar(session, tmp_path, tercero)
    lane1.mark_ok(session, primero.id)
    session.commit()


def test_en_el_arranque_la_cola_de_auditoria_trae_todo(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingestas = _aceptar(session, tmp_path, mundo.canal, n=3)

    cola = lane1.audit_queue(session, _ajustes(tmp_path))

    # El orden de la cola es el de aceptación; el pre-cálculo acepta primero lo
    # más nuevo, así que acá solo importa que estén todos.
    assert {a.ingestion.id for a in cola} == {i.id for i in ingestas}


def test_en_el_arranque_la_cola_va_en_orden_al_azar_y_estable(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    ingestas = _aceptar(session, tmp_path, mundo.canal, n=12)
    ajustes = _ajustes(tmp_path)

    cola = [a.ingestion.id for a in lane1.audit_queue(session, ajustes)]
    otra = [a.ingestion.id for a in lane1.audit_queue(session, ajustes)]

    assert cola == otra  # estable entre cargas de la pantalla
    por_antiguedad = [
        a.ingestion.id
        for a in sorted(lane1.audit_queue(session, ajustes), key=lambda a: a.event.decided_at)
    ]
    assert cola != por_antiguedad  # 1 posibilidad en 12! de coincidir por azar
    # Al auditar uno, los demás conservan su lugar relativo.
    lane1.mark_ok(session, cola[0])
    session.commit()
    despues = [a.ingestion.id for a in lane1.audit_queue(session, ajustes)]
    assert despues == cola[1:]
    assert {i.id for i in ingestas} == set(cola)


def test_la_pantalla_dice_que_el_arranque_se_audita_al_azar(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    _aceptar(session, tmp_path, mundo.canal)

    html = client.get("/curation/auditoria").text

    assert "en orden al azar" in html
    assert "del más viejo al más nuevo" not in html


def test_despues_del_arranque_la_cola_es_una_muestra_por_canal_de_la_semana_cerrada(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lane1, "AUDIT_N0", 1)
    monkeypatch.setattr(lane1, "MAX_UNAUDITED", 100)
    # Tres canales: 6, 2 y 1 anuncios. En ronda, de a uno por canal, cinco
    # lugares son 2 + 2 + 1; el del tercer canal ya está auditado.
    monkeypatch.setattr(lane1, "WEEKLY_SAMPLE", 5)
    del_canal = _aceptar(session, tmp_path, mundo.canal, n=6)
    del_otro = _aceptar(session, tmp_path, mundo.otro, n=2)
    _arranque_terminado(session, tmp_path, mundo)
    ajustes = _ajustes(tmp_path)

    # La semana en curso nunca se muestrea.
    assert lane1.audit_queue(session, ajustes) == []

    cola = lane1.audit_queue(session, ajustes, today=_semana_siguiente())
    ids = {a.ingestion.id for a in cola}

    assert len(cola) == 4
    # Estratificada: el canal chico no queda afuera aunque el grande tenga más.
    assert len(ids & {i.id for i in del_otro}) == 2
    assert len(ids & {i.id for i in del_canal}) == 2
    # Reproducible: la misma semana da la misma muestra.
    otra = lane1.audit_queue(session, ajustes, today=_semana_siguiente())
    assert [a.ingestion.id for a in otra] == [a.ingestion.id for a in cola]


def test_lo_auditado_fuera_de_la_muestra_no_entra_en_la_cota(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lane1, "AUDIT_N0", 1)
    monkeypatch.setattr(lane1, "MAX_UNAUDITED", 100)
    # Dos canales, dos lugares: el auditado del tercer canal y uno de estos tres.
    monkeypatch.setattr(lane1, "WEEKLY_SAMPLE", 2)
    resto = _aceptar(session, tmp_path, mundo.canal, n=3)
    _arranque_terminado(session, tmp_path, mundo)
    ajustes = _ajustes(tmp_path)
    (en_muestra,) = lane1.audit_queue(session, ajustes, today=_semana_siguiente())
    fuera = next(i for i in resto if i.id != en_muestra.ingestion.id)

    lane1.mark_invalid(session, fuera.id, "no_data")
    session.commit()

    estado = lane1.audit_state(session, ajustes, today=_semana_siguiente())
    assert (estado.audited, estado.errors, estado.outside) == (1, 0, 1)


# --- T0: por qué se queda afuera el resto (modo en seco) -----------------------------
#
# Los esperados están escritos a mano. Cinco anuncios, y se sabe de antemano qué
# frena a cada uno:
#   entra   — «Arroz a 320 la libra», una fila con su cita.
#   tope    — dos filas: solo el tope de filas.
#   cita    — una fila con una cita que el texto no trae: solo la cita.
#   ambos   — dos filas y una cita que el texto no trae: tope y cita.
#   fecha   — entra por el motor, pero su fecha por defecto cae un día después de la captura.

_TOPE = "la propuesta trae más filas que el tope del carril 1 (1)"
_CITA = "la cita del modelo no está verificada en el texto"
_FECHA = "la fecha por defecto cae después del día de La Habana"
_CELDA = "la celda diverge de sus filas de control"


def _cinco_anuncios(session: Session, tmp_path: Path, canal: Channel) -> dict[str, RawIngestion]:
    textos = {
        "entra": "Arroz a 320 la libra",
        "tope": "Arroz a 330 la libra\nArroz a 300 la libra por saco",
        "cita": "Arroz a 340 la libra",
        "ambos": "Arroz a 350 la libra\nArroz a 360 la libra por saco",
        "fecha": "Arroz a 370 la libra",
    }
    anuncios = {nombre: _anuncio(session, canal, texto) for nombre, texto in textos.items()}
    por_texto = {
        textos["entra"]: _propone(_fila()),
        # El doble busca el texto serializado: la clave es la primera línea.
        textos["tope"].split("\n")[0]: _propone(
            _fila(price=330, evidence="Arroz a 330 la libra"),
            _fila(price=300, evidence="Arroz a 300 la libra por saco"),
        ),
        textos["cita"]: _propone(_fila(price=340, evidence="no está en el texto")),
        textos["ambos"].split("\n")[0]: _propone(
            _fila(price=350, evidence="Arroz a 350 la libra"),
            _fila(price=360, evidence="no está en el texto"),
        ),
        textos["fecha"]: _propone(_fila(price=370, evidence="Arroz a 370 la libra")),
    }
    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila()), por_texto))
    return anuncios


def _fecha_posterior_al_anuncio_fecha(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desde T2 la fecha por defecto no puede salir posterior a la captura. Al
    anuncio «fecha» se la ponemos un día después a propósito, para ejercer la
    guarda del carril."""
    fecha_normal = curation_rows.default_published_date
    monkeypatch.setattr(
        curation_rows,
        "default_published_date",
        lambda ingestion, text: (
            date(2026, 9, 2) if text == "Arroz a 370 la libra" else fecha_normal(ingestion, text)
        ),
    )


def test_contar_motivos_separa_cada_motivo_del_que_frena_solo() -> None:
    conjuntos = [
        frozenset(),
        frozenset({"a"}),
        frozenset({"a", "b"}),
        frozenset({"b"}),
        frozenset({"b"}),
    ]

    assert lane1.count_reasons(conjuntos) == [("b", 3, 2), ("a", 2, 1)]


def test_el_comando_en_seco_desglosa_por_que_se_queda_afuera_cada_anuncio(
    tope_de_una_fila: None,
    session: Session,
    mundo: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cinco_anuncios(session, tmp_path, mundo.canal)
    _fecha_posterior_al_anuncio_fecha(monkeypatch)

    codigo, salida = _comando(session, tmp_path)

    assert codigo == 0
    assert "Entrarían al carril 1: 1 de 5" in salida
    assert "Quedan afuera: 4 de 5" in salida
    # (motivo, anuncios con ese motivo, anuncios que frena solo ese motivo)
    esperados = [(_TOPE, 2, 1), (_CITA, 2, 1), (_FECHA, 1, 1)]
    for motivo, con_motivo, solo in esperados:
        linea = next(linea for linea in salida.splitlines() if motivo in linea)
        assert linea.split()[:2] == [str(con_motivo), str(solo)], linea


def test_el_desglose_cuenta_la_celda_divergente_como_motivo_propio(
    session: Session,
    mundo: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _con_propuesta(session, tmp_path, mundo.canal)
    monkeypatch.setattr(
        lane1, "diverging_cells", lambda session, settings: frozenset({("Arroz", "La Habana")})
    )

    codigo, salida = _comando(session, tmp_path)

    assert codigo == 0
    assert "Entrarían al carril 1: 0 de 1" in salida
    linea = next(linea for linea in salida.splitlines() if _CELDA in linea)
    assert linea.split()[:2] == ["1", "1"]


def test_con_todo_aceptado_el_desglose_no_inventa_motivos(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _con_propuesta(session, tmp_path, mundo.canal)

    _, salida = _comando(session, tmp_path)

    assert "Quedan afuera: 0 de 1" in salida
    assert "Motivos" not in salida


def test_execute_no_imprime_el_desglose(
    tope_de_una_fila: None,
    session: Session,
    mundo: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cinco_anuncios(session, tmp_path, mundo.canal)
    _fecha_posterior_al_anuncio_fecha(monkeypatch)

    _, salida = _comando(session, tmp_path, "--execute", lane1_enabled=True)

    assert "Quedan afuera" not in salida
    assert "Aceptados solos (carril 1): 1 de 5" in salida


def test_con_el_tope_vigente_dos_filas_entran_y_cinco_no(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    dos = _anuncio(session, mundo.canal, "Arroz a 320 la libra\nArroz a 300 la libra por saco")
    cinco = _anuncio(session, mundo.canal, "Pollo a 1 la libra\nPollo a 2\nPollo a 3\nPollo a 4\nPollo a 5")
    por_texto = {
        "Arroz a 320 la libra": _propone(_fila(), _fila(price=300, evidence="Arroz a 300 la libra por saco")),
        "Pollo a 1 la libra": _propone(*(_fila(price=100 + n, evidence=f"Pollo a {n}") for n in range(1, 6))),
    }

    _precalcular(session, tmp_path, ClienteFalso(_propone(_fila()), por_texto), lane1_enabled=True)

    session.refresh(dos)
    assert dos.processing_status == ProcessingStatus.CURATED
    _sigue_pendiente_sin_filas(session, cinco)


def test_muestra_imprime_texto_y_canal_de_los_anuncios_de_un_motivo(
    session: Session,
    mundo: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tope_de_una_fila: None,
) -> None:
    _cinco_anuncios(session, tmp_path, mundo.canal)
    _fecha_posterior_al_anuncio_fecha(monkeypatch)

    codigo, salida = _comando(session, tmp_path, "--muestra", "no está verificada", "--n", "5")

    assert codigo == 0
    assert "Muestra" in salida
    assert "Arroz a 340 la libra" in salida
    # Un anuncio que no tiene ese motivo no aparece.
    assert "Arroz a 320 la libra" not in salida


def test_muestra_no_cambia_nada(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, tope_de_una_fila: None
) -> None:
    ingesta = _con_propuesta(session, tmp_path, mundo.canal)

    _comando(session, tmp_path, "--muestra", "lo que sea", lane1_enabled=True)

    _sigue_pendiente_sin_filas(session, ingesta)


# --- la compra mínima no es un error de lectura (2026-10-13) -------------------------


def test_completar_solo_la_compra_minima_no_cuenta_como_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    (fila,) = _filas(session, ingesta)

    _editar(client, ingesta, fila, min_purchase="30")

    session.expire_all()
    (completada,) = _filas(session, ingesta)
    assert completada.min_purchase_quantity == Decimal("30")
    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors, estado.unaudited) == (1, 0, 0)


def test_compra_minima_y_precio_juntos_siguen_siendo_un_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    (fila,) = _filas(session, ingesta)

    _editar(client, ingesta, fila, min_purchase="30", price="330")

    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors) == (1, 1)


def test_cambiar_otro_campo_que_no_es_la_compra_minima_sigue_siendo_un_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    (fila,) = _filas(session, ingesta)

    _editar(client, ingesta, fila, currency="USD")

    estado = lane1.audit_state(session, _ajustes(tmp_path))
    assert (estado.audited, estado.errors) == (1, 1)


# --- completar un formato vacío tampoco es un error (2026-10-17, criterio A) ---------

_FORMATO = "6ea43467-c549-4e68-b4df-9efabed0bbbf"
_OTRO_FORMATO = "f7847fc2-638b-44d1-a89d-69e893f89f52"


def _fila_editada(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    return {"price_history_id": "x", "op": "updated", "before": before, "after": after}


def test_completar_un_formato_vacio_no_es_un_error() -> None:
    detail = {"rows": [_fila_editada({"format_id": None}, {"format_id": _FORMATO})]}

    assert lane1._only_completes_gaps(detail)


def test_completar_formato_y_compra_minima_juntos_no_es_un_error() -> None:
    detail = {
        "rows": [
            _fila_editada({"format_id": None}, {"format_id": _FORMATO}),
            _fila_editada({"min_purchase_quantity": None}, {"min_purchase_quantity": "50"}),
        ]
    }

    assert lane1._only_completes_gaps(detail)


def test_cambiar_un_formato_por_otro_sigue_siendo_un_error() -> None:
    detail = {"rows": [_fila_editada({"format_id": _OTRO_FORMATO}, {"format_id": _FORMATO})]}

    assert not lane1._only_completes_gaps(detail)


def test_quitar_un_formato_sigue_siendo_un_error() -> None:
    detail = {"rows": [_fila_editada({"format_id": _FORMATO}, {"format_id": None})]}

    assert not lane1._only_completes_gaps(detail)


def test_completar_formato_junto_con_otro_campo_sigue_siendo_un_error() -> None:
    detail = {
        "rows": [
            _fila_editada(
                {"format_id": None, "price": "10.70"}, {"format_id": _FORMATO, "price": "1.07"}
            )
        ]
    }

    assert not lane1._only_completes_gaps(detail)


def test_completar_formato_y_borrar_una_fila_sigue_siendo_un_error() -> None:
    detail = {
        "rows": [
            _fila_editada({"format_id": None}, {"format_id": _FORMATO}),
            {"price_history_id": "y", "op": "deleted", "before": {"price": "1"}},
        ]
    }

    assert not lane1._only_completes_gaps(detail)
