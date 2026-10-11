"""Persistir la validación (slice B1): guardado, revalidación y `/control`.

Casos 9 a 13 y 16 a 18 de «Casos esperados» de `docs/spec-slice-B1.md` (los 1 a
8 están en `test_validation_stored_row.py`; los 14 y 15, con las migraciones, en
`test_processing_layer.py`). Los valores esperados salen de la especificación y
de contar el fixture a ojo, nunca de correr el código.

El mundo de prueba: arroz (piloto, por libra) en La Habana, con referencias de
300 a 340 CUP en días anteriores al anuncio. Contra esa celda, 320 está dentro
de la banda y 3200 queda diez veces afuera.
"""

from __future__ import annotations

import logging
import re
import uuid
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

from saimc.config import get_settings
from saimc.models import (
    Channel,
    ContentType,
    JevSignal,
    LlmProposal,
    PriceHistory,
    ProcessingStatus,
    Product,
    Provenance,
    Province,
    RawIngestion,
    SourceType,
)
from saimc.validation import ENGINE_VERSION, jev_signal, persist, revalidate
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher

# El anuncio bajo prueba: 10 de septiembre de 2026, mediodía de Cuba.
AD_AT = datetime(2026, 9, 10, 16, 0, tzinfo=UTC)
# Su fecha de publicación en el formulario: el día anterior a la captura.
AD_DAY = date(2026, 9, 9)

SEIS_REGLAS = {"structure", "price", "location", "trust", "duplicate", "quantity_price"}

_n = 0


@pytest.fixture
def mundo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="la-habana", name="La Habana")
    session.add_all([arroz, habana])
    session.flush()
    canal = Channel(name="Mercado Habana", platform="whatsapp_channel", province_id=habana.id)
    otro_canal = Channel(name="Otro canal", platform="whatsapp_channel", province_id=habana.id)
    session.add_all([canal, otro_canal])
    session.commit()
    return SimpleNamespace(arroz=arroz, habana=habana, canal=canal, otro_canal=otro_canal)


def _ingesta(
    session: Session,
    canal: Channel | None,
    *,
    cuando: datetime = AD_AT,
    estado: str = ProcessingStatus.PENDING,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.WHATSAPP_CHANNEL if canal is not None else SourceType.INBOX_TEXT,
        source_reference=canal.name if canal is not None else None,
        channel_id=canal.id if canal is not None else None,
        file_path=f"raw/b1-{_n}.txt",
        content_hash=f"hash-b1-{_n}",
        content_type=ContentType.TEXT,
        original_text="Arroz 320 CUP la libra",
        processing_status=estado,
        captured_at=cuando,
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _fila(
    session: Session,
    mundo: SimpleNamespace,
    origen: RawIngestion,
    precio: str,
    dia: date,
    *,
    procedencia: str = "human",
) -> PriceHistory:
    """Una fila ya guardada, SIN validar: como las anteriores a B1."""
    fila = PriceHistory(
        product_id=mundo.arroz.id,
        province_id=mundo.habana.id,
        price=Decimal(precio),
        currency="CUP",
        quantity=Decimal(1),
        unit="lb",
        published_date=dia,
        source_id=origen.id,
        provenance=procedencia,
    )
    session.add(fila)
    session.commit()
    return fila


def _referencias(session: Session, mundo: SimpleNamespace, *precios: str) -> list[PriceHistory]:
    """Una fila de referencia por precio, cada una de su propio anuncio de otro
    canal, en días distintos y todos anteriores al anuncio bajo prueba."""
    filas = []
    for i, precio in enumerate(precios):
        dia = date(2026, 9, 3) + timedelta(days=i)
        cuando = datetime(dia.year, dia.month, dia.day, 16, 0, tzinfo=UTC)
        origen = _ingesta(session, mundo.otro_canal, cuando=cuando, estado=ProcessingStatus.CURATED)
        filas.append(_fila(session, mundo, origen, precio, dia))
    return filas


CINCO = ("300", "310", "320", "330", "340")
CUATRO = ("300", "310", "320", "330")


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _get_db_override
    # Ningún test paga red: sin tasas, una fila en divisa queda sin convertir.
    app.dependency_overrides[get_rate_fetcher] = lambda: lambda key, day: None
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _campos_fila(mundo: SimpleNamespace, indice: int, precio: str, **extra: str) -> dict[str, str]:
    campos = {
        "product_id": str(mundo.arroz.id),
        "price": precio,
        "currency": "CUP",
        "quantity": "1",
        "unit": "lb",
        "province_id": str(mundo.habana.id),
        **extra,
    }
    return {f"{nombre}_{indice}": valor for nombre, valor in campos.items()}


def _curar(
    client: TestClient, mundo: SimpleNamespace, anuncio: RawIngestion, *precios: str, **extra: str
) -> None:
    datos = {"published_date": AD_DAY.isoformat(), "row_count": str(len(precios))}
    for indice, precio in enumerate(precios):
        datos.update(_campos_fila(mundo, indice, precio, **extra))
    respuesta = client.post(f"/curation/{anuncio.id}", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303


def _editar(
    client: TestClient, mundo: SimpleNamespace, anuncio: RawIngestion, filas: dict[uuid.UUID, str]
) -> None:
    """Corrige el anuncio mandando todas sus filas, cada una con su precio."""
    datos = {"published_date": AD_DAY.isoformat(), "row_count": str(len(filas))}
    for indice, (fila_id, precio) in enumerate(filas.items()):
        datos.update(_campos_fila(mundo, indice, precio, price_id=str(fila_id)))
    respuesta = client.post(f"/curation/{anuncio.id}/edit", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303


def _detalle(fila: PriceHistory) -> dict[str, Any]:
    """El `validation_detail` de una fila que tiene que estar validada."""
    assert fila.validation_detail is not None, "la fila quedó sin validar"
    return fila.validation_detail


def _releer(session: Session, fila: PriceHistory) -> PriceHistory:
    session.expire_all()
    releida = session.get(PriceHistory, fila.id)
    assert releida is not None
    return releida


def _filas_de(session: Session, anuncio: RawIngestion) -> list[PriceHistory]:
    session.expire_all()
    return list(
        session.execute(
            select(PriceHistory)
            .where(PriceHistory.source_id == anuncio.id)
            .order_by(PriceHistory.price)
        ).scalars()
    )


# --- Guardado: al curar ---------------------------------------------------------


def test_caso_9_al_curar_cada_fila_queda_validada_y_con_procedencia_humana(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert fila.provenance == "human"
    # El caso 1 de la especificación, ahora contra la base.
    assert fila.validation_status == "unverified"
    assert fila.confidence_score == 83
    detalle = _detalle(fila)
    assert set(detalle) == {"rules", "reasons", "engine_version", "config", "validated_at"}
    assert detalle["rules"] == {
        "structure": "pass",
        "price": "pass",
        "location": "pass",
        "trust": "unknown",
        "duplicate": "pass",
        "quantity_price": "pass",
    }
    assert detalle["reasons"] == ["el canal no tiene historial suficiente"]
    assert detalle["engine_version"] == "2026-10-03.1" == ENGINE_VERSION
    assert detalle["config"] == {"min_cell_n": 5}
    validada = datetime.fromisoformat(detalle["validated_at"])
    assert validada.tzinfo is not None
    assert abs(datetime.now(UTC) - validada) < timedelta(minutes=5)


def test_caso_9_un_precio_fuera_de_la_banda_queda_como_anomalia_al_curar(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "3200")

    (fila,) = _filas_de(session, anuncio)
    assert fila.validation_status == "anomaly"
    assert _detalle(fila)["rules"]["price"] == "fail"
    assert "el precio cae fuera de la banda de su celda" in _detalle(fila)["reasons"]


def test_caso_10_un_anuncio_sin_propuesta_del_modelo_se_valida_igual(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    # De la bandeja: sin canal y sin ninguna propuesta guardada.
    anuncio = _ingesta(session, None)
    assert session.execute(select(LlmProposal)).first() is None

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert fila.validation_status == "unverified"
    # Sin canal no hay con qué contrastar la provincia: 4 reglas de 6 en pass.
    assert _detalle(fila)["rules"]["location"] == "unknown"
    assert _detalle(fila)["rules"]["price"] == "pass"
    assert fila.confidence_score == 67


def test_caso_11_dos_filas_del_mismo_anuncio_no_se_ven_entre_si(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    # Con 4 referencias la celda no opina. Si la fila hermana entrara en la
    # celda serían 5 y opinaría; si entrara en el duplicado, las dos filas
    # (mismo producto, provincia y precio) se marcarían entre sí.
    _referencias(session, mundo, *CUATRO)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "320", "320")

    filas = _filas_de(session, anuncio)
    assert len(filas) == 2
    for fila in filas:
        assert _detalle(fila)["rules"]["price"] == "unknown"
        assert _detalle(fila)["rules"]["quantity_price"] == "unknown"
        assert _detalle(fila)["rules"]["duplicate"] == "pass"
        assert fila.validation_status == "unverified"


def test_una_fila_igual_de_otro_anuncio_del_canal_en_24_h_es_posible_duplicado(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    """Caso 6 contra la base: el contexto trae las filas recientes del canal."""
    _referencias(session, mundo, *CINCO)
    previo = _ingesta(
        session, mundo.canal, cuando=AD_AT - timedelta(hours=3), estado=ProcessingStatus.CURATED
    )
    _fila(session, mundo, previo, "320", AD_DAY)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert fila.validation_status == "possible_duplicate"


def test_la_celda_solo_cuenta_dias_estrictamente_anteriores_a_la_fecha_del_anuncio(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    # Cuatro referencias anteriores y una quinta del MISMO día del anuncio, de
    # otro canal: la del mismo día no entra, así que la celda no opina.
    _referencias(session, mundo, *CUATRO)
    mismo_dia = _ingesta(
        session, mundo.otro_canal, cuando=AD_AT - timedelta(hours=30), estado=ProcessingStatus.CURATED
    )
    _fila(session, mundo, mismo_dia, "340", AD_DAY)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert _detalle(fila)["rules"]["price"] == "unknown"


def test_caso_7_una_fila_en_divisa_sin_tasa_queda_sin_comparar(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "1", currency="USD")

    (fila,) = _filas_de(session, anuncio)
    assert fila.price_cup is None
    assert _detalle(fila)["rules"]["price"] == "unknown"
    assert _detalle(fila)["rules"]["quantity_price"] == "unknown"


def test_caso_13_si_el_motor_falla_la_curaduria_se_guarda_sin_validacion(
    client: TestClient,
    session: Session,
    mundo: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _romper(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("motor roto")

    monkeypatch.setattr(persist, "validate_stored_row", _romper)
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)

    with caplog.at_level(logging.ERROR):
        _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert fila.price == Decimal("320")
    assert fila.provenance == "human"
    assert fila.validation_status is None
    assert fila.confidence_score is None
    assert fila.validation_detail is None
    session.refresh(anuncio)
    assert anuncio.processing_status == ProcessingStatus.CURATED
    # El error no se traga: va al log, con el id de la ingesta.
    assert str(anuncio.id) in caplog.text


# --- Guardado: al editar --------------------------------------------------------


def test_caso_12_al_editar_se_revalidan_todas_las_filas_del_anuncio(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    tocada = _fila(session, mundo, anuncio, "320", AD_DAY)
    otra = _fila(session, mundo, anuncio, "330", AD_DAY)

    # Se saca de la banda el precio de una sola fila; la otra viaja igual.
    _editar(client, mundo, anuncio, {tocada.id: "3200", otra.id: "330"})

    tocada, otra = _releer(session, tocada), _releer(session, otra)
    assert tocada.validation_status == "anomaly"
    assert tocada.provenance == "human"
    # La que no se tocó estaba sin validar y también quedó validada.
    assert otra.validation_status == "unverified"
    assert _detalle(otra)["rules"]["price"] == "pass"
    assert otra.provenance == "human"


def test_al_editar_no_cambia_la_procedencia(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    """`provenance` no es del formulario: una fila `auto` sigue `auto`."""
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    fila = _fila(session, mundo, anuncio, "320", AD_DAY, procedencia="auto")

    _editar(client, mundo, anuncio, {fila.id: "325"})

    assert _releer(session, fila).provenance == "auto"


def test_al_borrar_una_fila_las_que_quedan_se_revalidan(
    client: TestClient, session: Session, mundo: SimpleNamespace
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    queda = _fila(session, mundo, anuncio, "320", AD_DAY)
    sobra = _fila(session, mundo, anuncio, "330", AD_DAY)

    datos = {"published_date": AD_DAY.isoformat(), "row_count": "2", "delete_1": "1"}
    datos.update(_campos_fila(mundo, 0, "320", price_id=str(queda.id)))
    datos.update(_campos_fila(mundo, 1, "330", price_id=str(sobra.id)))
    respuesta = client.post(f"/curation/{anuncio.id}/edit", data=datos, follow_redirects=False)

    assert respuesta.status_code == 303
    (fila,) = _filas_de(session, anuncio)
    assert fila.id == queda.id
    assert fila.validation_status == "unverified"


def test_caso_13_si_el_motor_falla_al_editar_la_validacion_vieja_se_borra(
    client: TestClient, session: Session, mundo: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    _curar(client, mundo, anuncio, "320")
    (fila,) = _filas_de(session, anuncio)
    assert fila.validation_status == "unverified"

    def _romper(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("motor roto")

    monkeypatch.setattr(persist, "validate_stored_row", _romper)
    _editar(client, mundo, anuncio, {fila.id: "3200"})

    (fila,) = _filas_de(session, anuncio)
    # La corrección entró, y la validación que describía el precio viejo no
    # sobrevive: mejor nula que desactualizada.
    assert fila.price == Decimal("3200")
    assert fila.validation_status is None
    assert fila.confidence_score is None
    assert fila.validation_detail is None


# --- Revalidación ---------------------------------------------------------------


@pytest.fixture
def historico(session: Session, mundo: SimpleNamespace) -> SimpleNamespace:
    """Siete filas anteriores a B1, todas sin validar.

    Cinco referencias en días distintos (cada una tiene menos de cinco
    anteriores: su celda no opina) y, después, dos anuncios del mismo día: uno
    a 320 (dentro de la banda de las cinco) y otro a 3200 (diez veces afuera).
    """
    referencias = _referencias(session, mundo, *CINCO)
    normal = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    absurdo = _ingesta(session, mundo.otro_canal, estado=ProcessingStatus.CURATED)
    return SimpleNamespace(
        referencias=referencias,
        normal=_fila(session, mundo, normal, "320", AD_DAY),
        absurdo=_fila(session, mundo, absurdo, "3200", AD_DAY, procedencia="auto"),
    )


def _estados(session: Session) -> dict[uuid.UUID, tuple[str | None, int | None]]:
    session.expire_all()
    return {
        fila.id: (fila.validation_status, fila.confidence_score)
        for fila in session.execute(select(PriceHistory)).scalars()
    }


def test_caso_16_revalidate_en_seco_no_escribe(session: Session, historico: SimpleNamespace) -> None:
    lineas: list[str] = []

    codigo = revalidate.execute(revalidate.parse_args([]), session, out=lineas.append)
    session.commit()

    assert codigo == 0
    assert set(_estados(session).values()) == {(None, None)}
    assert all(
        fila.validation_detail is None for fila in session.execute(select(PriceHistory)).scalars()
    )
    # El resumen dice cuántas filas cambiarían de estado, por estado.
    salida = "\n".join(lineas)
    assert "EN SECO" in salida
    assert re.search(r"Cambian de estado: 7 de 7", salida)
    assert re.search(r"sin validar → unverified: 6", salida)
    assert re.search(r"sin validar → anomaly: 1", salida)


def test_caso_16_revalidate_con_execute_llena_las_filas_y_no_toca_la_procedencia(
    session: Session, historico: SimpleNamespace
) -> None:
    # Releídas de la base, para comparar lo guardado con lo guardado (SQLite
    # devuelve las fechas sin zona).
    session.expire_all()
    antes = {
        fila.id: (fila.price, fila.price_cup, fila.published_date, fila.created_at)
        for fila in session.execute(select(PriceHistory)).scalars()
    }

    codigo = revalidate.execute(revalidate.parse_args(["--execute"]), session, out=lambda _: None)
    session.commit()

    assert codigo == 0
    estados = _estados(session)
    assert estados[historico.normal.id] == ("unverified", 83)
    assert estados[historico.absurdo.id][0] == "anomaly"
    for referencia in historico.referencias:
        assert estados[referencia.id][0] == "unverified"
    filas = list(session.execute(select(PriceHistory)).scalars())
    assert all(_detalle(fila)["engine_version"] == ENGINE_VERSION for fila in filas)
    assert all(_detalle(fila)["config"] == {"min_cell_n": 5} for fila in filas)
    # Ni la procedencia ni ninguna otra columna.
    assert _releer(session, historico.absurdo).provenance == "auto"
    assert _releer(session, historico.normal).provenance == "human"
    assert antes == {
        fila.id: (fila.price, fila.price_cup, fila.published_date, fila.created_at)
        for fila in filas
    }


def test_revalidate_una_segunda_corrida_no_cambia_ningun_estado(
    session: Session, historico: SimpleNamespace
) -> None:
    ingestas = revalidate.select_ingestions(session)
    revalidate.revalidate(session, ingestas, execute=True)
    session.commit()

    resumen = revalidate.revalidate(session, ingestas, execute=True)

    assert resumen.rows == 7
    assert resumen.changed == 0


def test_caso_17_revalidate_da_lo_mismo_en_cualquier_orden(
    session: Session, historico: SimpleNamespace
) -> None:
    ingestas = revalidate.select_ingestions(session)
    assert len(ingestas) == 7

    def _reglas() -> dict[uuid.UUID, tuple[Any, ...]]:
        session.expire_all()
        return {
            fila.id: (fila.validation_status, fila.confidence_score, _detalle(fila)["rules"])
            for fila in session.execute(select(PriceHistory)).scalars()
        }

    revalidate.revalidate(session, ingestas, execute=True)
    session.commit()
    en_orden = _reglas()

    for fila in session.execute(select(PriceHistory)).scalars():
        fila.validation_status = fila.confidence_score = fila.validation_detail = None
    session.commit()
    revalidate.revalidate(session, list(reversed(ingestas)), execute=True)
    session.commit()
    al_reves = _reglas()

    assert en_orden == al_reves
    # Y es el esperado, no solo el mismo: cada fila contra lo anterior a SU anuncio.
    assert en_orden[historico.normal.id][0] == "unverified"
    assert en_orden[historico.normal.id][2]["price"] == "pass"
    assert en_orden[historico.absurdo.id][0] == "anomaly"
    for referencia in historico.referencias:
        assert en_orden[referencia.id][2]["price"] == "unknown"


def test_revalidate_sigue_si_un_anuncio_falla_y_no_toca_sus_filas(
    session: Session, historico: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = persist.validate_stored_row

    def _romper_el_absurdo(row: Any, *args: Any, **kwargs: Any) -> Any:
        if row.price == Decimal("3200"):
            raise RuntimeError("motor roto")
        return real(row, *args, **kwargs)

    monkeypatch.setattr(persist, "validate_stored_row", _romper_el_absurdo)

    resumen = revalidate.revalidate(session, revalidate.select_ingestions(session), execute=True)
    session.commit()

    assert resumen.failed_rows == 1
    estados = _estados(session)
    assert estados[historico.absurdo.id] == (None, None)
    assert estados[historico.normal.id] == ("unverified", 83)


# --- /control -------------------------------------------------------------------


def _seccion_validacion(html: str) -> str:
    """El HTML de la sección «Validación», y nada de las secciones vecinas."""
    secciones = [parte for parte in html.split("<section") if 'id="validacion"' in parte]
    assert len(secciones) == 1, "no está la sección «Validación»"
    return secciones[0].split("</section>")[0]


def _conteo(seccion: str, estado: str) -> int:
    encontrado = re.search(
        rf'<tr data-status="{estado}">.*?<td class="is-num">\s*(?:<span[^>]*>)?(\d+)', seccion, re.DOTALL
    )
    assert encontrado is not None, estado
    return int(encontrado.group(1))


def _sin_validar(seccion: str) -> int:
    encontrado = re.search(
        r'Sin validar</div>\s*<div class="m3-metric-value-sm">\s*(\d+)\s*</div>', seccion
    )
    assert encontrado is not None, "no está el contador de filas sin validar"
    return int(encontrado.group(1))


@pytest.fixture
def validadas(session: Session, mundo: SimpleNamespace) -> None:
    """Dos filas sin verificar, una anomalía y una sin validar."""
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    for precio, estado in (("320", "unverified"), ("330", "unverified"), ("3200", "anomaly"), ("310", None)):
        fila = _fila(session, mundo, anuncio, precio, AD_DAY)
        if estado is not None:
            fila.validation_status = estado
            fila.confidence_score = 83
            fila.validation_detail = {"reasons": ["motivo-que-no-se-publica"]}
    session.commit()


def test_caso_18_la_consulta_de_control_solo_trae_conteos(session: Session, validadas: None) -> None:
    conteos = persist.status_counts(session)

    assert conteos.by_status == {"unverified": 2, "anomaly": 1}
    assert conteos.unvalidated == 1
    assert conteos.total == 4


def test_caso_18_control_muestra_el_conteo_por_estado_y_las_filas_sin_validar(
    client: TestClient, session: Session, validadas: None
) -> None:
    respuesta = client.get("/control/calidad")

    assert respuesta.status_code == 200
    seccion = _seccion_validacion(respuesta.text)
    assert _conteo(seccion, "unverified") == 2
    assert _conteo(seccion, "anomaly") == 1
    # Los siete estados están, con su nombre en español, aunque valgan cero.
    assert _conteo(seccion, "approved") == 0
    for nombre in (
        "Aprobada",
        "Incompleta",
        "Anomalía",
        "Inconsistente",
        "Posible duplicado",
        "Fuente no confiable",
        "Sin verificar",
    ):
        assert nombre in seccion
    assert _sin_validar(seccion) == 1
    assert (
        "Ninguna fila figura como aprobada: la reputación de los canales todavía no se mide."
        in seccion
    )


def test_caso_18_control_no_muestra_canales_emisores_ni_detalle_por_fila(
    client: TestClient, session: Session, mundo: SimpleNamespace, validadas: None
) -> None:
    seccion = _seccion_validacion(client.get("/control/calidad").text)

    assert mundo.canal.name not in seccion
    assert "motivo-que-no-se-publica" not in seccion
    for fila in session.execute(select(PriceHistory)).scalars():
        assert str(fila.id) not in seccion
        assert str(fila.source_id) not in seccion


def test_control_sin_filas_muestra_la_seccion_en_cero(client: TestClient) -> None:
    seccion = _seccion_validacion(client.get("/control/calidad").text)

    assert _conteo(seccion, "unverified") == 0
    assert _sin_validar(seccion) == 0


def test_la_procedencia_tiene_los_tres_valores_de_la_especificacion() -> None:
    assert {p.value for p in Provenance} == {"human", "auto", "audited"}


# --- Jev en sombra (A6b-1) ------------------------------------------------------
#
# Casos 1, 2, 3, 15, 16 y 17 de «Casos esperados» de `docs/spec-slice-A6b.md`,
# escritos antes que el código. La fila base es la F: el caso 9 de arriba
# (`unverified`, 83, `human`). Jev solo agrega la clave `jev` al detalle: el
# estado, el puntaje y la procedencia tienen que salir iguales con y sin él.

CINCO_CLAVES = {"rules", "reasons", "engine_version", "config", "validated_at"}
CLAVE_JEV = "clave-falsa-de-jev-123"


@pytest.fixture
def pilotos(session: Session, mundo: SimpleNamespace) -> None:
    """El fixture común de A6b: arroz, pollo y aceite pilotos; café, no."""
    session.add_all(
        [
            Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True),
            Product(name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True),
            Product(name="Café", category="otros", unit="lb", aliases=[], is_pilot=False),
        ]
    )
    session.commit()


def _poner_jev(monkeypatch: pytest.MonkeyPatch, *, encendido: bool, clave: str) -> None:
    # Las variables de entorno le ganan al `.env`: el test no depende de lo que
    # el operador tenga configurado en su checkout.
    monkeypatch.setenv("SAIMC_LLM_JEV_ENABLED", "true" if encendido else "false")
    # La clave del gateway tiene dos nombres: se fija la nueva y se vacía la
    # anterior, para que «sin clave» sea sin ninguna de las dos.
    monkeypatch.setenv("SAIMC_VERCEL_API_KEY", clave)
    monkeypatch.setenv("SAIMC_LLM_JEV_API_KEY", "")
    get_settings.cache_clear()


@pytest.fixture
def jev_activo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _poner_jev(monkeypatch, encendido=True, clave=CLAVE_JEV)
    yield
    get_settings.cache_clear()


def _respuestas(producto: tuple[str, float, float] = ("arroz", 0.97, 0.96)) -> dict[str, Any]:
    clave, probabilidad, confianza = producto
    return {
        "triage": {"type": "noul", "noul": 0.98},
        "product": {
            "type": "choice",
            "choice": clave,
            "probabilities": {clave: probabilidad},
            "confidence": confianza,
        },
        "currency": {
            "type": "choice",
            "choice": "CUP",
            "probabilities": {"CUP": 0.99},
            "confidence": 0.98,
        },
    }


def _senal(session: Session, anuncio: RawIngestion, respuestas: dict[str, Any]) -> JevSignal:
    """Una señal ya guardada para el anuncio, con la huella vigente."""
    opciones = jev_signal.load_options(session)
    senal = JevSignal(
        ingestion_id=anuncio.id,
        model="typesafe-ai/jev",
        questions_hash=opciones.questions_hash,
        answers=respuestas,
        product_keys=opciones.product_keys,
        input_tokens=500,
        output_tokens=0,
        cost_usd=Decimal("0.00003"),
        source="prefill",
    )
    session.add(senal)
    session.commit()
    return senal


def _es_la_fila_f(fila: PriceHistory) -> None:
    assert fila.validation_status == "unverified"
    assert fila.confidence_score == 83
    assert fila.provenance == "human"
    detalle = _detalle(fila)
    assert detalle["rules"] == {
        "structure": "pass",
        "price": "pass",
        "location": "pass",
        "trust": "unknown",
        "duplicate": "pass",
        "quantity_price": "pass",
    }
    assert detalle["reasons"] == ["el canal no tiene historial suficiente"]
    assert detalle["engine_version"] == ENGINE_VERSION
    assert detalle["config"] == {"min_cell_n": 5}


@pytest.mark.parametrize(
    ("encendido", "clave"),
    [(False, CLAVE_JEV), (True, "")],
    ids=["interruptor-apagado", "encendido-sin-clave"],
)
def test_a6b_caso_1_inactivo_la_fila_queda_identica_aunque_haya_senal(
    client: TestClient,
    session: Session,
    mundo: SimpleNamespace,
    pilotos: None,
    monkeypatch: pytest.MonkeyPatch,
    encendido: bool,
    clave: str,
) -> None:
    _poner_jev(monkeypatch, encendido=encendido, clave=clave)
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    _senal(session, anuncio, _respuestas())

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    _es_la_fila_f(fila)
    assert set(_detalle(fila)) == CINCO_CLAVES


def test_a6b_caso_2_coincide_se_anota_y_la_validacion_no_cambia(
    client: TestClient, session: Session, mundo: SimpleNamespace, pilotos: None, jev_activo: None
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    senal = _senal(session, anuncio, _respuestas())

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    _es_la_fila_f(fila)
    detalle = _detalle(fila)
    assert set(detalle) == CINCO_CLAVES | {"jev"}
    jev = detalle["jev"]
    assert jev["signal_id"] == str(senal.id)
    assert jev["model"] == "typesafe-ai/jev"
    assert jev["questions_hash"] == senal.questions_hash
    assert jev["source"] == "prefill"
    assert datetime.fromisoformat(jev["asked_at"]).tzinfo is not None
    assert jev["triage"] == {"status": "ok", "probability": 0.98}
    assert jev["product"]["agrees"] is True
    assert jev["currency"]["agrees"] is True


def test_a6b_caso_3_discrepa_se_anota_y_la_validacion_no_cambia(
    client: TestClient, session: Session, mundo: SimpleNamespace, pilotos: None, jev_activo: None
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    _senal(session, anuncio, _respuestas(("pollo", 0.93, 0.92)))

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    _es_la_fila_f(fila)
    producto = _detalle(fila)["jev"]["product"]
    assert producto["agrees"] is False
    assert producto["row_value"] == "arroz"
    assert producto["answer"] == "pollo"
    assert (producto["probability"], producto["confidence"]) == (0.93, 0.92)


def test_a6b_activo_y_sin_senal_la_clave_jev_no_existe(
    client: TestClient, session: Session, mundo: SimpleNamespace, pilotos: None, jev_activo: None
) -> None:
    """El anuncio que se cura antes de que pase el temporizador: caso 5, al final."""
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    _es_la_fila_f(fila)
    assert set(_detalle(fila)) == CINCO_CLAVES


def test_a6b_una_senal_de_otra_huella_no_se_anota(
    client: TestClient, session: Session, mundo: SimpleNamespace, pilotos: None, jev_activo: None
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    senal = _senal(session, anuncio, _respuestas())
    senal.questions_hash = "huella-vieja-000"
    session.commit()

    _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    assert set(_detalle(fila)) == CINCO_CLAVES


def test_a6b_caso_15_si_leer_la_senal_falla_la_fila_queda_validada_y_sin_jev(
    client: TestClient,
    session: Session,
    mundo: SimpleNamespace,
    pilotos: None,
    jev_activo: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _romper(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("jev_signals no contesta")

    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal)
    _senal(session, anuncio, _respuestas())
    monkeypatch.setattr(jev_signal, "current_signal", _romper)

    with caplog.at_level(logging.ERROR):
        _curar(client, mundo, anuncio, "320")

    (fila,) = _filas_de(session, anuncio)
    _es_la_fila_f(fila)
    assert set(_detalle(fila)) == CINCO_CLAVES
    session.refresh(anuncio)
    assert anuncio.processing_status == ProcessingStatus.CURATED
    assert str(anuncio.id) in caplog.text


def test_a6b_caso_16_al_editar_una_fila_quedan_anotadas_las_dos(
    client: TestClient, session: Session, mundo: SimpleNamespace, pilotos: None, jev_activo: None
) -> None:
    _referencias(session, mundo, *CINCO)
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.CURATED)
    tocada = _fila(session, mundo, anuncio, "320", AD_DAY)
    otra = _fila(session, mundo, anuncio, "330", AD_DAY)
    _senal(session, anuncio, _respuestas())

    _editar(client, mundo, anuncio, {tocada.id: "325", otra.id: "330"})

    for fila in (_releer(session, tocada), _releer(session, otra)):
        assert fila.provenance == "human"
        assert _detalle(fila)["jev"]["product"]["agrees"] is True
        assert _detalle(fila)["jev"]["product"]["row_value"] == "arroz"


def test_a6b_caso_17_revalidate_anota_con_jev_activo_y_quita_la_clave_inactivo(
    session: Session,
    mundo: SimpleNamespace,
    pilotos: None,
    historico: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anuncio = session.get(RawIngestion, historico.normal.source_id)
    assert anuncio is not None
    _senal(session, anuncio, _respuestas())
    argumentos = revalidate.parse_args(["--execute"])

    # Sin Jev: la referencia contra la que se comparan las otras dos corridas.
    _poner_jev(monkeypatch, encendido=False, clave=CLAVE_JEV)
    revalidate.execute(argumentos, session, out=lambda _: None)
    session.commit()
    sin_jev = _estados(session)
    procedencias = {f.id: f.provenance for f in session.execute(select(PriceHistory)).scalars()}
    assert set(_detalle(_releer(session, historico.normal))) == CINCO_CLAVES

    _poner_jev(monkeypatch, encendido=True, clave=CLAVE_JEV)
    revalidate.execute(argumentos, session, out=lambda _: None)
    session.commit()
    assert _detalle(_releer(session, historico.normal))["jev"]["product"]["agrees"] is True
    # Las filas de anuncios sin señal no llevan la clave.
    assert "jev" not in _detalle(_releer(session, historico.absurdo))
    assert _estados(session) == sin_jev

    _poner_jev(monkeypatch, encendido=False, clave=CLAVE_JEV)
    revalidate.execute(argumentos, session, out=lambda _: None)
    session.commit()
    assert set(_detalle(_releer(session, historico.normal))) == CINCO_CLAVES
    assert _estados(session) == sin_jev
    assert procedencias == {
        f.id: f.provenance for f in session.execute(select(PriceHistory)).scalars()
    }
    get_settings.cache_clear()


def test_a6b_revalidate_en_seco_no_anota(
    session: Session,
    mundo: SimpleNamespace,
    pilotos: None,
    historico: SimpleNamespace,
    jev_activo: None,
) -> None:
    anuncio = session.get(RawIngestion, historico.normal.source_id)
    assert anuncio is not None
    _senal(session, anuncio, _respuestas())

    revalidate.execute(revalidate.parse_args([]), session, out=lambda _: None)
    session.commit()

    assert _releer(session, historico.normal).validation_detail is None
