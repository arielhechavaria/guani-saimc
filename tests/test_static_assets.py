"""Los assets del rediseño se sirven desde el repo, sin salir a la red.

`CLAUDE.md` fija que todo va vendorizado: la conexión del operador es lenta
e intermitente, así que una fuente que se cargue desde `fonts.googleapis.com`
en runtime deja la interfaz sin iconos justo cuando la red falla. Estos casos
fallan si alguien borra un archivo o vuelve a meter un CDN.

La misma restricción manda sobre cuántos bytes viajan, así que acá vive también
la cobertura de `GZipMiddleware` (`docs/decisions.md`, 2026-08-22).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.web.app import app
from saimc.web.deps import get_db

# Firma de un archivo WOFF2 (los cuatro primeros bytes).
WOFF2_MAGIC = b"wOF2"

FUENTES = [
    "/static/fonts/roboto.woff2",
    "/static/fonts/roboto-mono.woff2",
    "/static/fonts/material-symbols-rounded.woff2",
]

VISTAS = ["/catalog", "/curation", "/control", "/curation/history"]


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestAssetsVendorizados:
    def test_la_hoja_de_estilo_se_sirve(self, client: TestClient) -> None:
        response = client.get("/static/saimc-material.css")

        assert response.status_code == 200
        assert "--m3-primary" in response.text

    @pytest.mark.parametrize("ruta", FUENTES)
    def test_cada_fuente_se_sirve_y_es_un_woff2(
        self, client: TestClient, ruta: str
    ) -> None:
        response = client.get(ruta)

        assert response.status_code == 200
        # Sin esto, un HTML de error de 404 guardado por accidente pasaría
        # como si fuera la fuente.
        assert response.content[:4] == WOFF2_MAGIC

    def test_los_iconos_van_subseteados(self, client: TestClient) -> None:
        """La fuente completa de Material Symbols pesa ~370 KB; subseteada a
        las ligaduras que usan las plantillas, ~6 KB. El margen es amplio a
        propósito: la cota corta un archivo sin subsetear, no una ligadura
        nueva."""
        response = client.get("/static/fonts/material-symbols-rounded.woff2")

        assert len(response.content) < 60_000

    def test_bootstrap_ya_no_existe(self, client: TestClient) -> None:
        assert client.get("/static/bootstrap.min.css").status_code == 404
        assert client.get("/static/curation.css").status_code == 404

    @pytest.mark.parametrize("ruta", VISTAS)
    def test_ninguna_vista_pide_assets_a_la_red(
        self, client: TestClient, ruta: str
    ) -> None:
        texto = client.get(ruta).text

        assert "fonts.googleapis.com" not in texto
        assert "fonts.gstatic.com" not in texto
        assert "cdn.jsdelivr.net" not in texto


class TestCompresion:
    """`GZipMiddleware`: qué se comprime, qué no, y qué pasa sin soporte.

    El `accept-encoding` va explícito en cada caso en vez de confiar en el
    default de httpx: lo que se afirma es el comportamiento del servidor ante un
    encabezado dado, y un cambio de default de la biblioteca no debería mover
    estos casos de verde a rojo ni al revés.
    """

    def test_el_css_viaja_comprimido(self, client: TestClient) -> None:
        response = client.get(
            "/static/saimc-material.css", headers={"accept-encoding": "gzip"}
        )

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        # El cliente lo descomprime y sigue siendo el CSS: comprimir no puede
        # cambiar lo que llega.
        assert "--m3-primary" in response.text

    @pytest.mark.parametrize("ruta", FUENTES)
    def test_las_fuentes_no_se_comprimen_dos_veces(
        self, client: TestClient, ruta: str
    ) -> None:
        """Un woff2 ya viene comprimido: gzipearlo quema CPU para no ganar nada.

        Lo cubre `DEFAULT_EXCLUDED_CONTENT_TYPES` de Starlette, que trae
        `font/woff2`, pero solo funciona si StaticFiles etiqueta bien el
        archivo. Este caso falla si el tipo MIME deja de resolverse.
        """
        response = client.get(ruta, headers={"accept-encoding": "gzip"})

        assert response.status_code == 200
        assert response.headers.get("content-encoding") != "gzip"
        assert response.content[:4] == WOFF2_MAGIC

    def test_el_html_de_una_vista_viaja_comprimido(self, client: TestClient) -> None:
        """`/control` y no toda `VISTAS`: `/catalog` y `/curation` van detrás de
        `require_operator` y devuelven 401 sin credenciales, así que probar la
        compresión ahí mediría el tamaño de una página de error. Lo que se
        afirma es que una respuesta HTML real se comprime, y `/control` —el
        único público— alcanza para eso."""
        response = client.get("/control", headers={"accept-encoding": "gzip"})

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert "<html" in response.text

    def test_un_cliente_sin_gzip_recibe_la_respuesta_entera(
        self, client: TestClient
    ) -> None:
        """La compresión es negociada, nunca impuesta: sin `accept-encoding`
        que la anuncie, la respuesta sale sin comprimir y sigue sirviendo."""
        response = client.get(
            "/static/saimc-material.css", headers={"accept-encoding": "identity"}
        )

        assert response.status_code == 200
        assert response.headers.get("content-encoding") != "gzip"
        assert "--m3-primary" in response.text
