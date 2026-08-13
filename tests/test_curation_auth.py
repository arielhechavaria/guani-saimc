"""HTTP Basic de `/curation` (decisions.md, 2026-08-13).

Sin este dependency, `/evidence/{id}` serviría capturas con datos personales
a cualquiera que llegue a la IP pública — el despliegue real no tiene proxy
ni firewall delante de uvicorn. Estos tests corren sin el override que usa
`test_curation.py`, para probar el dependency mismo, no la lógica de negocio
que protege.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.web.app import app
from saimc.web.deps import get_db


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    # `/control` y el `/curation` autenticado llegan al cuerpo de la ruta, que
    # pide `get_db` — sin este override golpearían la Postgres real de `.env`
    # en vez de la SQLite en memoria que usa el resto de la suite.
    def _get_db_override() -> Iterator[Session]:
        yield session

    # `GET /curation` ingesta la bandeja antes de mostrar la cola (ver
    # test_curation.py): sin aislar estos directorios movería archivos reales
    # de `inbox/` del repo hacia `data/raw/`.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestRequireOperator:
    def test_sin_password_configurado_niega_todo(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cierra en falso: sin password configurado, nadie entra —
        con credenciales enviadas para llegar más allá del 401 de
        `HTTPBasic` y probar puntualmente esta rama.

        El vacío se fuerza con `setenv("")` en vez de confiar en que la
        variable no exista: `Settings` lee el `.env` real, que en la máquina
        del operador ya define la password de producción.
        """
        monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "")
        get_settings.cache_clear()

        response = client.get("/curation", auth=("operador", "cualquiera"))

        assert response.status_code == 503

    def test_sin_encabezado_devuelve_401(self, client: TestClient) -> None:
        """Sin `Authorization` en absoluto también queda afuera."""
        response = client.get("/curation")

        assert response.status_code == 401

    def test_sin_credenciales_devuelve_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
        get_settings.cache_clear()

        response = client.get("/curation")

        assert response.status_code == 401

    def test_credenciales_incorrectas_devuelven_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
        get_settings.cache_clear()

        response = client.get("/curation", auth=("operador", "incorrecta"))

        assert response.status_code == 401

    def test_credenciales_correctas_entran(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
        get_settings.cache_clear()

        response = client.get("/curation", auth=("operador", "correcta"))

        assert response.status_code == 200

    def test_evidence_tambien_queda_protegida(self, client: TestClient) -> None:
        """`/evidence/{id}` es la ruta que motivó esto — sin auth, servía
        capturas con datos personales a cualquiera con la IP pública."""
        response = client.get("/evidence/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 401

    def test_control_sigue_publico_sin_auth(self, client: TestClient) -> None:
        """`/control` es el dashboard público — esta protección no lo toca."""
        response = client.get("/control")

        assert response.status_code == 200
