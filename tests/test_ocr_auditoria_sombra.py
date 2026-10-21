# Los fixtures `mundo` y `web` se importan de `test_ocr_como_texto`: pytest los
# inyecta por nombre, y ruff lo lee como una redefinición.
# ruff: noqa: F811
"""Auditoría en sombra de las imágenes del carril 1 y su tope de arranque.

Contrato: `docs/spec-slice-ocr-auditoria-sombra.md`, sección «Tests primero».
Los esperados están escritos A MANO desde la especificación, antes que el código:

- la imagen trae, leído por el OCR, «Arroz a 320 la libra» en un canal de La
  Habana, y el modelo propone eso mismo (arroz, 320 CUP, 1 lb). Es el caso que
  el carril 1 acepta (`test_ocr_como_texto`);
- la persona la cura en la cola, con el formulario de siempre: igual a la
  propuesta es un acierto; con el precio cambiado (300) es un error;
- la cota es la del carril 1 (Clopper-Pearson unilateral al 95 %): 0 errores
  en 1 dan 95,0 %; 1 en 1, 100,0 %;
- una propuesta cuya cita no está en el texto del OCR no la acepta el carril 1:
  queda afuera con ese motivo;
- tope de arranque: con tope 2, de 5 imágenes aceptables en la misma semana
  entran 2; con 60 imágenes ya auditadas el tope deja de regir; con tope 0 no
  hay tope; los textos no se limitan nunca.

Ningún esperado sale de correr el código. El modelo, el OCR y la tasa son
dobles: nada toca la red ni corre tesseract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.extraction import llm_store
from saimc.models import (
    CurationEvent,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    RawIngestion,
)
from saimc.validation import lane1
from tests.test_ocr_como_texto import (  # noqa: F401 — `mundo` y `web` son fixtures
    _ACEPTA,
    IMAGE,
    OCR_ARROZ,
    TEXT,
    _aceptados_y_auditados,
    _ajustes,
    _anuncio,
    _estado,
    _fila,
    _precalcular,
    _sin_tasa,
    mundo,
    web,
)

# --- ayudas -------------------------------------------------------------------------


def _curar(
    web: TestClient, session: Session, ingesta: RawIngestion, mundo: SimpleNamespace, **cambios: str
) -> None:
    """La persona cura el anuncio en la cola con la propuesta a la vista: deja
    la fila tal como la propuso el modelo, salvo `cambios`."""
    propuesta = llm_store.latest_ok_proposal(session, ingesta.id)
    assert propuesta is not None
    datos = {
        "original_text": OCR_ARROZ if ingesta.content_type == IMAGE else "",
        "source_reference": ingesta.source_reference or "",
        "published_date": "2026-09-01",
        "proposal_id": str(propuesta.id),
        "row_count": "1",
        "proposal_row_0": "0",
        "product_id_0": str(mundo.arroz.id),
        "province_id_0": str(mundo.lh.id),
        "price_0": "320",
        "currency_0": "CUP",
        "quantity_0": "1",
        "unit_0": "lb",
        "sale_format_0": "retail",
        "packaging_0": "",
        "price_basis_0": "unit",
    }
    datos.update({f"{campo}_0": valor for campo, valor in cambios.items()})
    respuesta = web.post(f"/curation/{ingesta.id}", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303, respuesta.text
    session.expire_all()
    assert _estado(session, ingesta) == ProcessingStatus.CURATED


def _imagen_curada(
    session: Session,
    tmp_path: Path,
    web: TestClient,
    mundo: SimpleNamespace,
    cuerpo: object = _ACEPTA,
    **cambios: str,
) -> RawIngestion:
    """Una imagen con OCR guardado y propuesta `ok` (carril 1 apagado), curada
    después por una persona."""
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path, cuerpo)
    assert _estado(session, imagen) == ProcessingStatus.PENDING
    _curar(web, session, imagen, mundo, **cambios)
    return imagen


def _sombra(session: Session, tmp_path: Path, *argv: str, **ajustes: Any) -> str:
    salida: list[str] = []
    codigo = lane1.run(
        lane1.parse_args(["--sombra-imagenes", *argv]),
        session,
        _ajustes(tmp_path, **ajustes),
        rate_fetcher=_sin_tasa,
        out=salida.append,
    )
    assert codigo == 0
    return "\n".join(salida)


def _conteos(session: Session) -> tuple[int, ...]:
    return tuple(
        session.execute(select(func.count()).select_from(modelo)).scalar_one()
        for modelo in (PriceHistory, CurationEvent, LlmReview, RawIngestion)
    )


# --- A. hacia atrás -----------------------------------------------------------------


def test_imagen_curada_igual_a_la_propuesta_es_un_acierto(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    _imagen_curada(session, tmp_path, web, mundo)

    salida = _sombra(session, tmp_path)

    assert "habría aceptado n = 1: aciertos 1, errores 0" in salida
    assert "cota superior 95,0 %" in salida
    # Anclaje: la persona vio la propuesta antes de curar.
    assert "piso" in salida


def test_imagen_curada_con_el_precio_corregido_es_un_error(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    _imagen_curada(session, tmp_path, web, mundo, price="300")

    salida = _sombra(session, tmp_path)

    assert "habría aceptado n = 1: aciertos 0, errores 1" in salida
    assert "cota superior 100,0 %" in salida


def test_imagen_que_la_persona_descarto_cuenta_como_no_valida(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    # El carril 1 la habría aceptado; la persona dijo que no era un anuncio
    # válido. En la auditoría eso es `invalid`: error.
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path)
    propuesta = llm_store.latest_ok_proposal(session, imagen.id)
    assert propuesta is not None
    respuesta = web.post(
        f"/curation/{imagen.id}/discard",
        data={"reason": "no_data", "proposal_id": str(propuesta.id)},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303

    salida = _sombra(session, tmp_path)

    assert "Universo: 1 imágenes (0 curadas, 1 descartadas)" in salida
    assert "habría aceptado n = 1: aciertos 0, errores 1" in salida


def test_imagen_que_el_carril_1_rechazaria_queda_afuera_con_su_motivo(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    sin_cita = {
        "discard": False,
        "discard_reason": None,
        "rows": [_fila(evidence="Arroz importado a 320 la libra en oferta")],
    }
    _imagen_curada(session, tmp_path, web, mundo, sin_cita)

    salida = _sombra(session, tmp_path)

    assert "Universo: 1 imágenes" in salida
    assert "habría aceptado n = 0" in salida
    assert "la cita del modelo no está verificada en el texto" in salida


def test_un_texto_nativo_curado_no_entra_a_la_medicion_de_imagenes(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    texto = _anuncio(session, mundo.canal, tipo=TEXT)
    _precalcular(session, tmp_path)
    _curar(web, session, texto, mundo)

    salida = _sombra(session, tmp_path)

    assert "Universo: 0 imágenes" in salida
    assert "habría aceptado n = 0" in salida


# --- solo lectura ---------------------------------------------------------------------


def test_el_comando_no_escribe_nada_y_anda_con_el_interruptor_apagado(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    curada = _imagen_curada(session, tmp_path, web, mundo)
    pendiente = _anuncio(session, mundo.otro, tipo=IMAGE, minutos=5)
    _precalcular(session, tmp_path)
    antes = _conteos(session)

    salida = _sombra(session, tmp_path, lane1_enabled=False, lane1_image_enabled=False)

    session.expire_all()
    assert _conteos(session) == antes
    assert _estado(session, pendiente) == ProcessingStatus.PENDING
    assert _estado(session, curada) == ProcessingStatus.CURATED
    # B: la pendiente que el carril 1 aceptaría hoy, para curarla primero.
    assert str(pendiente.id)[:8] in salida
    assert mundo.otro.name in salida
    assert "Arroz" in salida
    # La curada ya no es pendiente: no aparece en B.
    assert str(curada.id)[:8] not in salida


def test_hacia_adelante_lista_hasta_n(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagenes = [_anuncio(session, mundo.canal, tipo=IMAGE, minutos=i) for i in range(3)]
    _precalcular(session, tmp_path)

    salida = _sombra(session, tmp_path, "--n", "2")

    listadas = [i for i in imagenes if str(i.id)[:8] in salida]
    assert len(listadas) == 2
    assert all(_estado(session, i) == ProcessingStatus.PENDING for i in imagenes)


def test_sombra_y_execute_no_se_combinan(session: Session, tmp_path: Path) -> None:
    salida: list[str] = []
    codigo = lane1.run(
        lane1.parse_args(["--sombra-imagenes", "--execute"]),
        session,
        _ajustes(tmp_path, lane1_enabled=True),
        rate_fetcher=_sin_tasa,
        out=salida.append,
    )

    assert codigo == 2


# --- tope de arranque de las imágenes -------------------------------------------------


def test_el_tope_semanal_de_imagenes_vale_20_por_defecto() -> None:
    assert Settings(_env_file=None).lane1_image_weekly_cap == 20  # type: ignore[call-arg]


def test_un_tope_negativo_falla_al_construir_la_configuracion() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, lane1_image_weekly_cap=-1)  # type: ignore[call-arg]


_IMAGENES: dict[str, Any] = {"lane1_enabled": True, "lane1_image_enabled": True}


def test_con_tope_2_de_5_imagenes_de_la_misma_semana_entran_2(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagenes = [_anuncio(session, mundo.canal, tipo=IMAGE, minutos=i) for i in range(5)]

    _precalcular(session, tmp_path, lane1_image_weekly_cap=2, **_IMAGENES)

    estados = [_estado(session, i) for i in imagenes]
    assert estados.count(ProcessingStatus.CURATED) == 2
    assert estados.count(ProcessingStatus.PENDING) == 3

    # Es por semana: otra corrida de la misma semana no suma ninguna.
    _precalcular(session, tmp_path, lane1_image_weekly_cap=2, **_IMAGENES)

    estados = [_estado(session, i) for i in imagenes]
    assert estados.count(ProcessingStatus.CURATED) == 2


def test_con_60_imagenes_auditadas_el_tope_deja_de_regir(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=0)
    imagenes = [_anuncio(session, mundo.otro, tipo=IMAGE, minutos=500 + i) for i in range(5)]

    _precalcular(session, tmp_path, lane1_image_weekly_cap=2, **_IMAGENES)

    assert all(_estado(session, i) == ProcessingStatus.CURATED for i in imagenes)


def test_con_tope_0_entran_las_5(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagenes = [_anuncio(session, mundo.canal, tipo=IMAGE, minutos=i) for i in range(5)]

    _precalcular(session, tmp_path, lane1_image_weekly_cap=0, **_IMAGENES)

    assert all(_estado(session, i) == ProcessingStatus.CURATED for i in imagenes)


def test_el_tope_no_limita_los_textos(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    textos = [_anuncio(session, mundo.canal, minutos=i) for i in range(5)]

    _precalcular(session, tmp_path, lane1_image_weekly_cap=2, **_IMAGENES)

    assert all(_estado(session, t) == ProcessingStatus.CURATED for t in textos)
