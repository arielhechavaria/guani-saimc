"""Inventario de rutas: toda ruta nace cerrada salvo que se liste como pública.

`/control` y `/marketing` estuvieron meses abiertos porque cada router decide
por su cuenta si lleva `require_operator` (`docs/decisions.md`, 2026-10-05). Este
test recorre lo que la app tiene registrado y pide cada ruta sin credenciales:
si responde algo distinto de 401 y no está en `PUBLIC_ROUTES` ni en
`WEBHOOK_ROUTES`, falla. Una ruta nueva obliga a decidir de qué lado cae.

Se prueba el comportamiento (la respuesta HTTP), no la presencia de la
dependencia en el router: lo que importa es qué recibe quien llega sin clave.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.web.app import app
from saimc.web.deps import get_db

#: La capa pública, a propósito sin autenticación. Agregar una línea acá es una
#: decisión: la ruta no puede mostrar canal, emisor ni evidencia cruda.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/"),
        ("GET", "/precios"),
        ("GET", "/producto/{slug}"),
        ("GET", "/anuncio/{ingestion_id}"),
        ("GET", "/datos.csv"),
        ("GET", "/metodologia"),
        ("GET", "/preguntas"),
        ("GET", "/quienes-somos"),
        ("GET", "/contacto"),
        ("GET", "/terminos"),
        ("GET", "/privacidad"),
        ("GET", "/uso-de-datos"),
    }
)

#: Sin HTTP Basic porque WAHA no puede resolver el diálogo: se autentica por
#: HMAC (`routers/whatsapp_webhook.py`) y tiene sus propios tests.
WEBHOOK_ROUTES: frozenset[tuple[str, str]] = frozenset({("POST", "/webhooks/whatsapp")})

#: Montajes de archivos estáticos, públicos a propósito.
PUBLIC_MOUNTS: frozenset[str] = frozenset({"/static"})

_IGNORED_METHODS = {"HEAD", "OPTIONS"}

#: Valor de relleno para los parámetros de ruta. Una ruta cerrada responde 401
#: antes de mirar el parámetro, así que no hace falta que exista.
_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def _walk(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    """(método, ruta) de todo lo registrado; un montaje sale como `MOUNT`.

    FastAPI guarda un `include_router` como un nodo con `original_router` en
    vez de aplanar sus rutas en `app.routes`, así que hay que bajar.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            inner_prefix = getattr(route.include_context, "prefix", "") or ""
            yield from _walk(inner.routes, prefix + inner_prefix)
            continue
        path = prefix + route.path
        methods = getattr(route, "methods", None)
        if methods is None:
            yield "MOUNT", path
            continue
        for method in sorted(set(methods) - _IGNORED_METHODS):
            yield method, path


REGISTERED = sorted(set(_walk(app.routes)))
OPERATOR_ROUTES = [
    entry
    for entry in REGISTERED
    if entry[0] != "MOUNT" and entry not in PUBLIC_ROUTES and entry not in WEBHOOK_ROUTES
]


def _fill(path: str) -> str:
    """Reemplaza `{param}` y `{param:conv}` por el valor de relleno."""
    out = []
    for segment in path.split("/"):
        start = segment.find("{")
        if start == -1:
            out.append(segment)
            continue
        end = segment.rfind("}")
        out.append(segment[:start] + _PLACEHOLDER + segment[end + 1 :])
    return "/".join(out)


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    # Una ruta que hoy estuviera abierta llegaría a su cuerpo: que no toque la
    # Postgres real ni la bandeja del repo mientras el test la delata.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


class TestInventario:
    def test_el_recorrido_encuentra_las_rutas_conocidas(self) -> None:
        """Si el recorrido dejara de ver rutas, el resto pasaría sin probar nada."""
        for conocida in (
            ("GET", "/"),
            ("GET", "/curation"),
            ("GET", "/control"),
            ("GET", "/marketing"),
            ("GET", "/catalog"),
            ("GET", "/docs"),
            ("POST", "/webhooks/whatsapp"),
            ("MOUNT", "/static"),
        ):
            assert conocida in REGISTERED

    def test_las_listas_no_nombran_rutas_que_ya_no_existen(self) -> None:
        assert PUBLIC_ROUTES.issubset(REGISTERED)
        assert WEBHOOK_ROUTES.issubset(REGISTERED)

    def test_los_montajes_son_los_declarados(self) -> None:
        montajes = {path for method, path in REGISTERED if method == "MOUNT"}

        assert montajes == PUBLIC_MOUNTS

    def test_lo_publico_es_solo_lectura(self) -> None:
        """Nada de la capa pública muta: toda escritura es del operador."""
        assert {method for method, _ in PUBLIC_ROUTES} <= {"GET"}

    @pytest.mark.parametrize(("method", "path"), OPERATOR_ROUTES)
    def test_ruta_no_publica_responde_401_sin_credenciales(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, _fill(path))

        assert response.status_code == 401, (
            f"{method} {path} respondió {response.status_code} sin credenciales: "
            "ponele `require_operator` o listala en PUBLIC_ROUTES"
        )

    @pytest.mark.parametrize(("method", "path"), OPERATOR_ROUTES)
    def test_ruta_no_publica_responde_401_con_clave_equivocada(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, _fill(path), auth=("operador", "incorrecta"))

        assert response.status_code == 401
