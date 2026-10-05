"""HTTP Basic de `/control` y `/marketing` (decisions.md, 2026-10-05).

Las dos estuvieron abiertas hasta ese día. `/control` nombra los canales y
grupos de origen en sus desgloses, y su `POST /control/lint-drift` corre un
script sobre el código del servidor; `/marketing` es una herramienta del
operador. Estos tests corren sin el override de `require_operator` que usan
`test_control.py` y `test_marketing_router.py`: prueban la puerta, no lo que
hay detrás.
"""

import subprocess
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.routers import control, marketing

_BUENAS = ("operador", "correcta")
_MALAS = ("operador", "incorrecta")

#: Todas las rutas de los dos routers. Las de `/marketing` usan una tarjeta
#: estática, que se arma sin datos en la base.
_RUTAS = [
    ("GET", "/control"),
    ("POST", "/control/lint-drift"),
    ("GET", "/marketing"),
    ("GET", "/marketing/preview/carrusel-cierre"),
    ("GET", "/marketing/card/carrusel-cierre.png"),
]


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    def _fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="sin cambios", stderr="")

    # Lo que hay detrás de la puerta no se ejercita acá: ni el script de lint
    # (que toca el árbol de trabajo) ni el Chromium que rasteriza la tarjeta.
    monkeypatch.setattr(control.subprocess, "run", _fake_run)
    monkeypatch.setattr(marketing, "render_png", lambda html, *, width, height: b"\x89PNG")
    monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.parametrize(("method", "path"), _RUTAS)
class TestPuertaDelOperador:
    def test_sin_credenciales_devuelve_401(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path)

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Basic"

    def test_credenciales_incorrectas_devuelven_401(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, auth=_MALAS)

        assert response.status_code == 401

    def test_credenciales_correctas_entran(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, auth=_BUENAS)

        assert response.status_code == 200

    def test_sin_password_configurado_niega_todo(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, path: str
    ) -> None:
        """Cierra en falso, igual que `/curation`: sin contraseña no entra nadie."""
        monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "")
        get_settings.cache_clear()

        response = client.request(method, path, auth=_BUENAS)

        assert response.status_code == 503


def test_sin_credenciales_control_no_nombra_ningun_canal(client: TestClient) -> None:
    """Lo que motivó el cierre: la respuesta sin clave no trae el tablero."""
    response = client.get("/control")

    assert "Descarte por canal" not in response.text
