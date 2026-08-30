"""Los assets del rediseño se sirven desde el repo, sin salir a la red.

`CLAUDE.md` fija que todo va vendorizado: la conexión del operador es lenta
e intermitente, así que una fuente que se cargue desde `fonts.googleapis.com`
en runtime deja la interfaz sin iconos justo cuando la red falla. Estos casos
fallan si alguien borra un archivo o vuelve a meter un CDN.

La misma restricción manda sobre cuántos bytes viajan, así que acá vive también
la cobertura de `GZipMiddleware` (`docs/decisions.md`, 2026-08-22).
"""

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.web.app import HTMX_VERSION, app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

# Firma de un archivo WOFF2 (los cuatro primeros bytes).
WOFF2_MAGIC = b"wOF2"

# htmx 2.0.10, extraído de `package/dist/htmx.min.js` del tarball oficial de npm
# después de verificar su `integrity` sha512 publicado, y byte a byte idéntico al
# que sirve unpkg (comprobado el 2026-08-22). Fijar el hash acá convierte
# cualquier cambio del archivo vendorizado en un test rojo: un blob de 51 KB que
# nadie puede volver a verificar es una deuda, no un asset.
HTMX_SHA256 = "71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de"
HTMX_BYTES = 51_238

# marked 18.0.11 (MIT), extraído de `package/lib/marked.umd.js` del tarball
# oficial de npm después de verificar su `integrity` sha512 publicado. Lo carga
# el gestor de documentos para renderizar markdown en el navegador. Mismo
# criterio que htmx: un blob que nadie puede volver a verificar es una deuda,
# no un asset.
MARKED_SHA256 = "69451c8541c9c1e7a4bf3ffc6f73c4d89633de92bfbe3e484dfe182ef8091f88"
MARKED_BYTES = 44_679

FUENTES = [
    "/static/fonts/roboto.woff2",
    "/static/fonts/roboto-mono.woff2",
    "/static/fonts/material-symbols-rounded.woff2",
    "/static/fonts/alice.woff2",
]

# "/" y no solo las vistas del operador: el rediseño de la home pública
# (docs/decisions.md, 2026-08-23) le agrega Alice, vendorizada acá mismo, y es
# la vista con más riesgo de que alguien deje colada una línea a Google Fonts
# copiada del prototipo `redesing/`.
VISTAS = ["/", "/catalog", "/curation", "/control", "/curation/history"]


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    # `/catalog`, `/curation` y `/curation/history` van detrás de
    # `require_operator`: sin este override devuelven 401 y el texto de esa
    # vista nunca llega a ejecutarse, así que
    # `test_ninguna_vista_pide_assets_a_la_red` pasaba en vacío para esas 3
    # rutas. `GET /curation` además ingesta la bandeja antes de mostrar la
    # cola (ver `TestIngestaAutomatica` en `test_curation.py`): sin aislar
    # estos directorios, correrla acá escanearía y movería archivos reales de
    # `inbox/` hacia `data/raw/`, la bandeja real del operador.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


class TestAssetsVendorizados:
    def test_la_hoja_de_estilo_se_sirve(self, client: TestClient) -> None:
        response = client.get("/static/saimc-material.css")

        assert response.status_code == 200
        assert "--m3-primary" in response.text

    def test_la_hoja_publica_se_sirve(self, client: TestClient) -> None:
        """`saimc-public.css` es el subconjunto que carga `_public_shell.html`
        (docs/decisions.md, split de la hoja del operador)."""
        response = client.get("/static/saimc-public.css")

        assert response.status_code == 200
        assert "--m3-primary" in response.text

    def test_la_home_publica_no_carga_la_hoja_del_operador(
        self, client: TestClient
    ) -> None:
        """`/` es la única vista servida por `_public_shell.html`. Si vuelve a
        enlazar `saimc-material.css` (la hoja completa del operador), el split
        que evita arrastrar topnav/curaduría/botones se rompió en silencio."""
        texto = client.get("/").text

        assert '/static/saimc-public.css?v=' in texto
        assert '/static/saimc-material.css?v=' not in texto

    @pytest.mark.parametrize("ruta", ["/catalog", "/curation", "/control"])
    def test_las_vistas_del_operador_siguen_con_la_hoja_completa(
        self, client: TestClient, ruta: str
    ) -> None:
        texto = client.get(ruta).text

        assert '/static/saimc-material.css?v=' in texto
        assert '/static/saimc-public.css?v=' not in texto

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
        las ligaduras que usan las plantillas, ~10 KB. El margen es amplio a
        propósito: la cota corta un archivo sin subsetear, no una ligadura
        nueva."""
        response = client.get("/static/fonts/material-symbols-rounded.woff2")

        assert len(response.content) < 60_000

    def test_el_subconjunto_cubre_todos_los_iconos_de_las_plantillas(self) -> None:
        """Tripwire: la fuente se subsetea con `tools/build_icon_font.py`, que
        deja un manifiesto al lado con los iconos que metió. Si una plantilla
        suma un icono y nadie corre la herramienta, el manifiesto queda corto y
        esto se pone rojo — en vez de que el navegador dibuje el texto crudo
        `menu_book`. Pasó con `flag`, `history` y `rule` (2026-08-30)."""
        templates = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "templates"
        usados: set[str] = set()
        for tpl in templates.glob("*.html"):
            usados.update(
                re.findall(r'class="m3-icon[^"]*">([a-z0-9_]+)<', tpl.read_text(encoding="utf-8"))
            )

        manifiesto = (
            templates.parent / "static" / "fonts" / "material-symbols-rounded.icons.txt"
        ).read_text(encoding="utf-8").split()

        faltan = usados - set(manifiesto)
        assert not faltan, (
            f"iconos usados sin subsetear: {sorted(faltan)} — "
            "corré `python tools/build_icon_font.py`"
        )

    def test_htmx_se_sirve_desde_el_repo(self, client: TestClient) -> None:
        response = client.get("/static/htmx.min.js")

        assert response.status_code == 200
        # Que sea htmx y no cualquier otro JS: la cadena de versión es del
        # propio bundle, no un nombre de archivo que cualquiera puede poner.
        assert f'version:"{HTMX_VERSION}"' in response.text

    def test_htmx_es_el_archivo_que_se_verificó(self, client: TestClient) -> None:
        """Guarda de cadena de suministro: si alguien reemplaza el bundle —por
        una versión nueva sin verificar, o por cualquier otra cosa— esto se
        pone rojo y obliga a repetir la verificación de procedencia."""
        response = client.get("/static/htmx.min.js")

        assert len(response.content) == HTMX_BYTES
        assert hashlib.sha256(response.content).hexdigest() == HTMX_SHA256

    def test_la_version_del_cache_busting_es_la_del_archivo_servido(
        self, client: TestClient
    ) -> None:
        """`htmx_version()` alimenta el `?v=` de la plantilla que opte por htmx.
        Si se bumpea el constante sin reemplazar el bundle —o al revés— el
        navegador cachearía una versión creyendo que tiene otra."""
        expuesta = app.state.templates.env.globals["htmx_version"]()

        assert expuesta == HTMX_VERSION
        assert f'version:"{expuesta}"' in client.get("/static/htmx.min.js").text

    def test_marked_es_el_archivo_que_se_verificó(self, client: TestClient) -> None:
        """Misma guarda de cadena de suministro que htmx: si alguien reemplaza
        el bundle, esto se pone rojo y obliga a repetir la verificación de
        procedencia contra el tarball de npm."""
        response = client.get("/static/marked.umd.js")

        assert len(response.content) == MARKED_BYTES
        assert hashlib.sha256(response.content).hexdigest() == MARKED_SHA256

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
        # Los dos CDN por los que htmx se sirve habitualmente. Va acá desde que
        # existe `/static/htmx.min.js`: la forma más fácil de romper el ADR 001
        # es cargarlo con una línea copiada de la documentación de htmx.
        assert "unpkg.com" not in texto


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

    def test_el_css_publico_viaja_comprimido(self, client: TestClient) -> None:
        response = client.get(
            "/static/saimc-public.css", headers={"accept-encoding": "gzip"}
        )

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
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

    def test_htmx_viaja_comprimido(self, client: TestClient) -> None:
        """51.238 B en disco. Comprimido son ~16,5 KB, que es el número con el
        que el ADR 001 justificó adoptarlo."""
        response = client.get(
            "/static/htmx.min.js", headers={"accept-encoding": "gzip"}
        )

        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert len(response.content) == HTMX_BYTES

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
