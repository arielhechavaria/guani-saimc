"""Slice B3-4: `/metodologia` con los dos números y el aviso en la edición.

Contrato: `docs/spec-slice-B3.md`, secciones 2 y 5. Los esperados están
escritos a mano: el casillero de cada anuncio sintético y, de ahí, N1 y los
extremos de N2 (no se derivan de correr `automation_summary`).

- Los dos números salen rotulados «Resuelto sin el operador» y «Publicado sin
  el operador», cada uno con su error auditado y su n. Sin auditar todavía, el
  bloque lo dice y el número sale como provisional.
- Ningún número compuesto, ninguna «tasa de automatización», ninguna meta
  nueva: las que se muestran son las ya decididas (10 % y 20 %; 10 %).
- La edición de un congelado avisa en una línea y no bloquea nada.
"""

# ruff: noqa: F811 — `client` y `catalogo` son fixtures importadas de test_reference_set
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.automation import AutomationSummary, Partition, Slice
from saimc.models import (
    CurationAction,
    CurationMode,
    ProcessingStatus,
)
from saimc.validation import lane0, lane1
from saimc.web.routers import public

# Fixtures y ayudantes de los módulos hermanos: importarlas las registra acá.
from tests.test_automation_summary import _anuncio
from tests.test_reference_set import (  # noqa: F401
    _congelar,
    _curado,
    _fila,
    catalogo,
    client,
)

AVISO = (
    "Este anuncio está en el conjunto de referencia (versión {n}): "
    "la corrección cambia lo publicado, no la referencia"
)


def _visible(respuesta_html: str) -> str:
    sin_estilo = re.sub(r"<(style|script).*?</\1>", " ", respuesta_html, flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", sin_estilo)
    return " ".join(html_lib.unescape(texto).split())


def _metodologia(client: TestClient) -> str:
    respuesta = client.get("/metodologia")
    assert respuesta.status_code == 200
    return _visible(respuesta.text)


# --- /metodologia -------------------------------------------------------------------


def test_sin_nada_auditado_los_dos_numeros_salen_provisionales(session: Session, client: TestClient) -> None:
    texto = _metodologia(client)

    assert "Resuelto sin el operador" in texto
    assert "Publicado sin el operador" in texto
    assert "sin auditar todavía" in texto
    assert "provisional" in texto.lower()
    assert "sin datos" in texto


def test_el_total_trae_los_dos_numeros_con_su_conteo(session: Session, client: TestClient) -> None:
    # A, H, D0 y P: cuatro anuncios, uno de cada casillero útil.
    _anuncio(session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO))
    _anuncio(session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.INDIVIDUAL))
    _anuncio(session, ProcessingStatus.DISCARDED, (CurationAction.DISCARDED, CurationMode.AUTO))
    _anuncio(session, ProcessingStatus.PENDING)

    texto = _metodologia(client)

    # N1 = (A + D0) / todo = 2 de 4. N2: piso 1/3 (pendiente válido), techo 1/2.
    assert "50,0 %" in texto
    assert "2 de 4 anuncios" in texto
    assert "33,3–50,0 %" in texto


def _resumen_falso(
    *, texto: lane0.Bound, imagen: lane0.Bound, carril1: lane1.AuditState
) -> AutomationSummary:
    parte = Partition(a=3, h=1, d0=4, dh=1, p=1)
    return AutomationSummary(
        cohorts=[],
        total=Slice(None, parte, Partition()),
        auto_since=None,
        lane0={"text": texto, "image": imagen},
        lane1=carril1,
    )


def test_cada_numero_lleva_su_error_auditado_con_su_n(
    session: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    texto_b = lane0.Bound("text", k=1, n=20)
    resumen = _resumen_falso(
        texto=texto_b,
        imagen=lane0.Bound("image", k=0, n=0),
        carril1=lane1.AuditState(audited=10, errors=1, unaudited=0),
    )
    monkeypatch.setattr(public, "automation_summary", lambda *a, **k: resumen)

    texto = _metodologia(client)

    cota0 = f"{texto_b.upper * 100:.1f}".replace(".", ",")
    cota1 = f"{resumen.lane1.upper * 100:.1f}".replace(".", ",")
    assert "1 rescatado de 20 auditados" in texto
    assert f"no pasa de {cota0} %" in texto
    assert "meta del 10 %" in texto
    # Imagen no tiene nada auditado: lo dice, y el número del carril 0 es provisional.
    assert "Imagen: sin auditar todavía" in texto
    assert "1 con error de 10 auditados" in texto
    assert f"no pasa de {cota1} %" in texto


def test_con_todo_auditado_no_hay_rotulo_provisional(
    session: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resumen = _resumen_falso(
        texto=lane0.Bound("text", k=0, n=30),
        imagen=lane0.Bound("image", k=0, n=15),
        carril1=lane1.AuditState(audited=30, errors=0, unaudited=0),
    )
    monkeypatch.setattr(public, "automation_summary", lambda *a, **k: resumen)

    assert "provisional" not in _metodologia(client).lower()


def test_no_inventa_nombres_ni_numeros_compuestos(session: Session, client: TestClient) -> None:
    texto = _metodologia(client).lower()

    assert "tasa de automatización" not in texto
    assert "publicado solo y correcto" not in texto


# --- aviso en la edición de un congelado --------------------------------------------


def _congelado(session: Session, catalogo: SimpleNamespace, tmp_path: Path):
    ingesta = _curado(session)
    _fila(session, ingesta, catalogo.pollo, catalogo.stgo)
    return ingesta, _congelar(session, tmp_path)


def test_editar_un_congelado_muestra_el_aviso_con_la_version(
    session: Session, catalogo: SimpleNamespace, client: TestClient, tmp_path: Path
) -> None:
    ingesta, version = _congelado(session, catalogo, tmp_path)

    respuesta = client.get(f"/curation/{ingesta.id}/edit")

    assert respuesta.status_code == 200
    assert AVISO.format(n=version) in _visible(respuesta.text)


def test_editar_un_no_congelado_no_muestra_el_aviso(
    session: Session, catalogo: SimpleNamespace, client: TestClient, tmp_path: Path
) -> None:
    _congelado(session, catalogo, tmp_path)
    otro = _curado(session)
    _fila(session, otro, catalogo.aceite, catalogo.stgo)

    respuesta = client.get(f"/curation/{otro.id}/edit")

    assert respuesta.status_code == 200
    assert "conjunto de referencia" not in _visible(respuesta.text)


def test_el_aviso_nombra_la_version_vigente_en_la_que_es_miembro(
    session: Session, catalogo: SimpleNamespace, client: TestClient, tmp_path: Path
) -> None:
    ingesta, _v1 = _congelado(session, catalogo, tmp_path)
    otro = _curado(session)
    _fila(session, otro, catalogo.aceite, catalogo.stgo)

    # La versión 2 deja afuera al primero: ya no es parte de la referencia vigente.
    v2 = _congelar(session, tmp_path, "--exclude", str(ingesta.id))

    fuera = _visible(client.get(f"/curation/{ingesta.id}/edit").text)
    dentro = _visible(client.get(f"/curation/{otro.id}/edit").text)

    assert "conjunto de referencia" not in fuera
    assert AVISO.format(n=v2) in dentro
