"""Acceso al sitio público con usuario y contraseña (2026-10-13).

El operador pasó el enlace a algunas personas para recibir comentarios y no
quiere que nadie más entre. Con `SAIMC_SITE_GATE_ENABLED` encendido, la capa
pública pide acceso; las rutas del operador siguen con su propio HTTP Basic y
el webhook de WAHA con su HMAC. Los casos se escribieron antes del código.
"""

# ruff: noqa: S106
from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.web import access
from saimc.web.app import app
from saimc.web.deps import get_db

USUARIO = "operador"
CLAVE = "clave-del-operador"


def _settings(**cambios: Any) -> Settings:
    valores: dict[str, Any] = {
        "curation_username": USUARIO,
        "curation_password": CLAVE,
        "site_gate_enabled": True,
    }
    valores.update(cambios)
    return Settings(_env_file=None, **valores)  # type: ignore[arg-type, call-arg]


@pytest.fixture
def usar(monkeypatch: pytest.MonkeyPatch):
    """Fija la configuración con la que corre la app en este test."""

    def _usar(**cambios: Any) -> None:
        monkeypatch.setattr(access, "get_settings", lambda: _settings(**cambios))

    _usar()
    return _usar


@pytest.fixture(autouse=True)
def _sin_intentos_previos() -> None:
    access.reset_attempts()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _entrar(client: TestClient, clave: str = CLAVE, usuario: str = USUARIO, **extra: str):
    return client.post("/acceso", data={"username": usuario, "password": clave, **extra})


# --- apagado: nada cambia ---------------------------------------------------------


def test_con_el_acceso_apagado_el_sitio_se_comporta_como_siempre(client: TestClient, usar) -> None:
    usar(site_gate_enabled=False)

    assert client.get("/quienes-somos").status_code == 200


# --- encendido: lo público se cierra ----------------------------------------------


@pytest.mark.parametrize(
    "ruta",
    [
        "/",
        "/precios",
        "/producto/arroz",
        "/anuncio/abc",
        "/datos.csv",
        "/quienes-somos",
        "/metodologia",
        "/preguntas",
        "/terminos",
        "/privacidad",
        "/uso-de-datos",
        "/contacto",
    ],
)
def test_una_ruta_publica_sin_acceso_manda_al_formulario(client: TestClient, usar, ruta: str) -> None:
    respuesta = client.get(ruta)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/acceso?next=")
    assert "no-store" in respuesta.headers["cache-control"]


def test_el_formulario_recuerda_a_donde_iba_el_visitante(client: TestClient, usar) -> None:
    respuesta = client.get("/precios?vista=mapa")

    assert respuesta.headers["location"] == "/acceso?next=%2Fprecios%3Fvista%3Dmapa"


def test_un_pedido_de_htmx_recibe_401_con_la_redireccion(client: TestClient, usar) -> None:
    respuesta = client.get("/precios", headers={"hx-request": "true"})

    assert respuesta.status_code == 401
    assert respuesta.headers["hx-redirect"] == "/acceso"


def test_lo_que_no_es_la_capa_publica_no_se_toca(client: TestClient, usar) -> None:
    # El operador tiene su HTTP Basic; el estático y el formulario siguen abiertos.
    assert client.get("/control").status_code == 401
    assert client.get("/curation").status_code == 401
    assert client.get("/acceso").status_code == 200
    assert client.get("/static/saimc-public.css").status_code == 200


def test_el_webhook_de_whatsapp_no_pasa_por_el_formulario(client: TestClient, usar) -> None:
    respuesta = client.post("/webhooks/whatsapp", content=b"{}")

    assert respuesta.status_code != 303


def test_sin_contrasena_configurada_el_sitio_se_cierra_del_todo(client: TestClient, usar) -> None:
    usar(curation_password=None)

    assert client.get("/quienes-somos").status_code == 503


# --- el formulario -----------------------------------------------------------------


def test_el_formulario_pide_usuario_y_contrasena_sin_enlaces_al_operador(client: TestClient, usar) -> None:
    html = client.get("/acceso").text

    assert re.search(r'<input[^>]*name="username"', html)
    assert re.search(r'<input[^>]*name="password"[^>]*type="password"', html)
    assert 'autocomplete="current-password"' in html
    assert 'name="robots" content="noindex' in html
    for prohibido in ("/curation", "/control", "/catalog", "/marketing", "/docs"):
        assert prohibido not in html


def test_el_formulario_no_usa_voseo(client: TestClient, usar) -> None:
    texto = re.sub(r"<(style|script)\b.*?</\1>", " ", client.get("/acceso").text, flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)

    assert not re.search(r"\b(entrá|escribí|ingresá|vos|tenés|podés)\b", texto, flags=re.I)


def test_el_formulario_no_se_guarda_en_cache(client: TestClient, usar) -> None:
    assert "no-store" in client.get("/acceso").headers["cache-control"]


# --- entrar ------------------------------------------------------------------------


def test_con_las_credenciales_del_operador_se_entra_y_se_vuelve_a_donde_iba(client: TestClient, usar) -> None:
    respuesta = _entrar(client, next="/precios?vista=mapa")

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/precios?vista=mapa"
    cookie = respuesta.headers["set-cookie"]
    assert access.COOKIE in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/quienes-somos").status_code == 200


def test_con_contrasena_equivocada_no_se_entra(client: TestClient, usar) -> None:
    respuesta = _entrar(client, clave="otra")

    assert respuesta.status_code == 401
    assert "set-cookie" not in respuesta.headers
    assert "incorrect" in respuesta.text.lower()
    assert client.get("/quienes-somos").status_code == 303


def test_con_usuario_equivocado_no_se_entra(client: TestClient, usar) -> None:
    assert _entrar(client, usuario="otro").status_code == 401


def test_no_se_puede_usar_el_formulario_para_salir_del_sitio(client: TestClient, usar) -> None:
    for destino in ("//malo.example", "https://malo.example", "/\\malo.example", "malo"):
        respuesta = _entrar(client, next=destino)
        assert respuesta.headers["location"] == "/", destino


def test_una_contrasena_propia_del_sitio_evita_dar_la_del_operador(client: TestClient, usar) -> None:
    usar(site_password="para-visitas")

    assert _entrar(client, clave="para-visitas").status_code == 303
    # La del operador sigue valiendo en el formulario.
    assert _entrar(client).status_code == 303


def test_la_contrasena_de_visitas_no_abre_la_herramienta_del_operador(client: TestClient, usar) -> None:
    usar(site_password="para-visitas")
    _entrar(client, clave="para-visitas")

    assert client.get("/control").status_code == 401


# --- abuso -------------------------------------------------------------------------


def test_demasiados_intentos_fallidos_se_frenan(client: TestClient, usar) -> None:
    for _ in range(access.MAX_FAILURES):
        assert _entrar(client, clave="mal").status_code == 401

    bloqueado = _entrar(client, clave="mal")

    assert bloqueado.status_code == 429
    # Ni siquiera la contraseña buena entra mientras dure el bloqueo.
    assert _entrar(client).status_code == 429


def test_una_cookie_manipulada_no_vale(client: TestClient, usar) -> None:
    futuro = int(time.time()) + 3600
    client.cookies.set(access.COOKIE, f"{futuro}.{'0' * 64}")

    assert client.get("/quienes-somos").status_code == 303


def test_una_cookie_vencida_no_vale(client: TestClient, usar) -> None:
    ajustes = _settings()
    vencida = access.make_token(ajustes, now=time.time() - 40 * 86400)  # válida 30 días
    client.cookies.set(access.COOKIE, vencida)

    assert client.get("/quienes-somos").status_code == 303


def test_cambiar_la_contrasena_cierra_todas_las_sesiones(client: TestClient, usar) -> None:
    _entrar(client)
    assert client.get("/quienes-somos").status_code == 200

    usar(curation_password="nueva-clave")

    assert client.get("/quienes-somos").status_code == 303
