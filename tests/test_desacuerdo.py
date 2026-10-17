"""Vista de desacuerdo (`/curation/desacuerdo`).

Un anuncio está en desacuerdo cuando el modelo lo descarta (`off_focus` o
`no_data`) pero las reglas ven un producto piloto con algún dígito: el carril 0
lo manda a revisión por eso y nada más. Los casos están escritos a mano, antes
que el código: la respuesta correcta sale de leer el texto del fixture, no de
correr la vista.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    LlmProposal,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Provenance,
    Province,
    RawIngestion,
    ReviewOutcome,
    SourceType,
)
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    monkeypatch.setenv("SAIMC_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def catalogo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    session.add_all([arroz, lh])
    session.commit()
    return SimpleNamespace(arroz=arroz, lh=lh)


_n = 0

# Un renglón con producto piloto y precio claro.
TEXTO_CON_ARROZ = "Arroz 250 cup la lb"


def _anuncio(
    session: Session,
    *,
    texto: str | None = TEXTO_CON_ARROZ,
    fuente: str = SourceType.WHATSAPP_CHANNEL,
    estado: str = ProcessingStatus.PENDING,
    tipo: str = ContentType.TEXT,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=fuente,
        file_path=f"raw/des-{_n}.txt",
        content_hash=f"des-{_n}",
        content_type=tipo,
        original_text=texto,
        processing_status=estado,
        captured_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=_n),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _propuesta(
    session: Session,
    ingesta: RawIngestion,
    *,
    descarta: bool | None = True,
    motivo: str | None = "off_focus",
    estado: str = "ok",
) -> LlmProposal:
    propuesta = LlmProposal(
        ingestion_id=ingesta.id,
        model="modelo-de-prueba",
        prompt_version="prueba",
        status=estado,
        discard=descarta,
        discard_reason=motivo,
        rows=[],
        input_tokens=0,
        output_tokens=0,
    )
    session.add(propuesta)
    session.commit()
    return propuesta


def _en_desacuerdo(session: Session, **kw: object) -> tuple[RawIngestion, LlmProposal]:
    ingesta = _anuncio(session, **kw)  # type: ignore[arg-type]
    return ingesta, _propuesta(session, ingesta)


# --- qué entra a la vista ---------------------------------------------------------


def test_lista_el_anuncio_que_el_modelo_descarta_y_las_reglas_ven(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta, _ = _en_desacuerdo(session)

    html = client.get("/curation/desacuerdo").text

    assert str(ingesta.id) in html
    assert "Arroz 250 cup la lb" in html  # el texto del anuncio
    assert "fuera de foco" in html  # lectura del modelo: su motivo
    assert "Arroz" in html and "250" in html  # lectura de las reglas: producto y precio


@pytest.mark.parametrize(
    "armar",
    [
        # Las reglas no ven ningún piloto: el carril 0 lo descarta solo.
        lambda s: _propuesta(s, _anuncio(s, texto="Buenos días 12345 a todos")),
        # El texto nombra el piloto pero sin un solo dígito: no hay precio posible.
        lambda s: _propuesta(s, _anuncio(s, texto="Arroz agotado")),
        # El modelo no descarta: no hay desacuerdo, hay filas.
        lambda s: _propuesta(s, _anuncio(s), descarta=False, motivo=None),
        # La propuesta degradó: el modelo no habló.
        lambda s: _propuesta(s, _anuncio(s), descarta=None, motivo=None, estado="degraded"),
        # Sin propuesta del modelo.
        lambda s: _anuncio(s),
        # Ya no está pendiente.
        lambda s: _propuesta(s, _anuncio(s, estado=ProcessingStatus.DISCARDED)),
    ],
    ids=[
        "sin-piloto",
        "sin-digito",
        "modelo-no-descarta",
        "propuesta-degradada",
        "sin-propuesta",
        "ya-resuelto",
    ],
)
def test_lo_que_no_es_desacuerdo_no_aparece(
    client: TestClient, session: Session, catalogo: SimpleNamespace, armar: object
) -> None:
    armar(session)  # type: ignore[operator]

    html = client.get("/curation/desacuerdo").text

    assert "No hay anuncios en desacuerdo" in html


def test_un_anuncio_con_otra_ultima_propuesta_ok_usa_esa(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta, _ = _en_desacuerdo(session)
    # La última propuesta ok ya no descarta: deja de haber desacuerdo.
    _propuesta(session, ingesta, descarta=False, motivo=None)

    html = client.get("/curation/desacuerdo").text

    assert str(ingesta.id) not in html


def test_la_pestana_desacuerdo_esta_en_la_subnavegacion(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    html = client.get("/curation/desacuerdo").text

    assert 'href="/curation/desacuerdo"' in html
    assert 'aria-current="page"' in html


# --- aceptar modelo ---------------------------------------------------------------


def test_aceptar_modelo_descarta_con_el_motivo_del_modelo(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta, propuesta = _en_desacuerdo(session)

    resp = client.post(
        f"/curation/desacuerdo/{ingesta.id}/modelo", follow_redirects=False
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/curation/desacuerdo"
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == "off_focus"
    (revision,) = session.execute(select(LlmReview)).scalars()
    assert revision.outcome == ReviewOutcome.DISCARDED
    assert revision.proposal_id == propuesta.id
    (evento,) = session.execute(select(CurationEvent)).scalars()
    assert evento.action == CurationAction.DISCARDED
    assert evento.mode == CurationMode.INDIVIDUAL
    assert evento.discard_reason == "off_focus"
    assert evento.detail == {"desacuerdo": "modelo"}
    assert session.execute(select(PriceHistory)).first() is None


# --- aceptar reglas ---------------------------------------------------------------


def test_aceptar_reglas_guarda_la_fila_de_las_reglas_como_humana(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta, propuesta = _en_desacuerdo(session)

    resp = client.post(
        f"/curation/desacuerdo/{ingesta.id}/reglas", follow_redirects=False
    )

    assert resp.status_code == 303
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.CURATED
    (fila,) = session.execute(select(PriceHistory)).scalars()
    assert fila.product_id == catalogo.arroz.id
    assert fila.price == 250
    assert fila.currency == "CUP"
    assert fila.source_id == ingesta.id
    # La decidió el operador con las dos lecturas delante: no es automática.
    assert fila.provenance == Provenance.HUMAN.value
    # Un WhatsApp automatizado publica el día de la captura.
    assert fila.published_date is not None
    (revision,) = session.execute(select(LlmReview)).scalars()
    assert revision.outcome == ReviewOutcome.CURATED
    assert revision.proposal_id == propuesta.id
    (evento,) = session.execute(select(CurationEvent)).scalars()
    assert evento.action == CurationAction.CURATED
    assert evento.mode == CurationMode.INDIVIDUAL
    assert evento.detail == {"row_count": 1, "desacuerdo": "reglas"}


def test_aceptar_reglas_sin_fecha_de_publicacion_no_guarda_nada(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    # Un texto pegado a mano no tiene fecha: `captured_at` es el mtime del
    # archivo y el formulario la exige. Sin fecha confiable no se inventa una.
    ingesta, _ = _en_desacuerdo(session, fuente=SourceType.INBOX_TEXT)

    html = client.get("/curation/desacuerdo").text
    client.post(f"/curation/desacuerdo/{ingesta.id}/reglas")

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert session.execute(select(PriceHistory)).first() is None
    assert f"/curation?id={ingesta.id}" in html  # el camino completo sigue ahí


def test_aceptar_reglas_con_cantidad_sin_base_de_precio_no_guarda_nada(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    # «5 lb» a 250: ¿250 por libra o por las cinco? Las reglas no lo declaran
    # y el formulario lo exige. Esa pregunta es del operador, no de este botón.
    ingesta, _ = _en_desacuerdo(session, texto="Arroz 5 lb 250 cup")

    html = client.get("/curation/desacuerdo").text
    client.post(f"/curation/desacuerdo/{ingesta.id}/reglas")

    # Guarda: que no pase solo porque el anuncio no estaba en la lista.
    assert str(ingesta.id) in html and "disabled" in html
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert session.execute(select(PriceHistory)).first() is None


# --- descartar --------------------------------------------------------------------


@pytest.mark.parametrize("motivo", ["no_data", "off_focus"])
def test_descartar_usa_el_motivo_que_elige_el_operador(
    client: TestClient, session: Session, catalogo: SimpleNamespace, motivo: str
) -> None:
    ingesta, _ = _en_desacuerdo(session)

    client.post(f"/curation/desacuerdo/{ingesta.id}/descartar", data={"reason": motivo})

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == motivo
    (evento,) = session.execute(select(CurationEvent)).scalars()
    assert evento.detail == {"desacuerdo": "descartar"}


def test_descartar_sin_motivo_valido_no_toca_nada(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta, _ = _en_desacuerdo(session)

    resp = client.post(
        f"/curation/desacuerdo/{ingesta.id}/descartar", data={"reason": "otro"}
    )

    assert resp.status_code == 422
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING


# --- revalidación -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("accion", "datos"),
    [("modelo", {}), ("reglas", {}), ("descartar", {"reason": "no_data"})],
)
def test_un_anuncio_que_ya_no_esta_en_desacuerdo_se_ignora(
    client: TestClient,
    session: Session,
    catalogo: SimpleNamespace,
    accion: str,
    datos: dict[str, str],
) -> None:
    # Pasó a curado en otra pestaña entre el GET y el POST.
    ingesta, _ = _en_desacuerdo(session)
    ingesta.processing_status = ProcessingStatus.CURATED
    session.commit()

    resp = client.post(
        f"/curation/desacuerdo/{ingesta.id}/{accion}", data=datos, follow_redirects=False
    )

    assert resp.status_code == 303
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.CURATED
    assert session.execute(select(CurationEvent)).first() is None
    assert session.execute(select(PriceHistory)).first() is None


def test_un_anuncio_que_nunca_estuvo_en_desacuerdo_no_se_puede_forzar(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    # El POST no confía en el id que llega: revalida contra la misma selección.
    sin_piloto = _anuncio(session, texto="Buenos días 12345 a todos")
    _propuesta(session, sin_piloto)

    client.post(f"/curation/desacuerdo/{sin_piloto.id}/modelo")

    session.refresh(sin_piloto)
    assert sin_piloto.processing_status == ProcessingStatus.PENDING
