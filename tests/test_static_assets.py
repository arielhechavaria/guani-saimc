"""Los assets del rediseño se sirven desde el repo, sin salir a la red.

`CLAUDE.md` fija que todo va vendorizado: la conexión del operador es lenta
e intermitente, así que una fuente que se cargue desde `fonts.googleapis.com`
en runtime deja la interfaz sin iconos justo cuando la red falla. Estos casos
fallan si alguien borra un archivo o vuelve a meter un CDN.

**Excepción única y a propósito, desde el 2026-09-12**: los tiles del mapa
interactivo (`tile.openstreetmap.org`). Es un apartamiento consciente de la
misma decisión cerrada 1 que este archivo viene protegiendo —el operador lo
pidió sabiendo el costo, ver `docs/decisions.md`—, no un descuido. Leaflet
en sí (el motor) sigue vendorizado como todo lo demás; lo que sale a la red
en cada pan/zoom son los tiles, que no se pueden vendorizar sin dejar de
ser un mapa navegable.

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
from saimc.web.app import HTMX_VERSION, LEAFLET_VERSION, app
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

# Leaflet 1.9.4 (BSD-2-Clause), extraído de `package/dist/leaflet.js` y
# `package/dist/leaflet.css` del tarball oficial de npm después de verificar
# su `integrity` sha512 publicado (comprobado el 2026-09-12). Mismo criterio
# que htmx y marked.
LEAFLET_JS_SHA256 = "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"
LEAFLET_JS_BYTES = 147_552
LEAFLET_CSS_SHA256 = "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"
LEAFLET_CSS_BYTES = 14_806

FUENTES = [
    "/static/fonts/roboto.woff2",
    "/static/fonts/roboto-mono.woff2",
    "/static/fonts/material-symbols-rounded.woff2",
    "/static/fonts/alice.woff2",
    "/static/fonts/noto-color-emoji.woff2",
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
        `menu_book`. Pasó con `flag`, `history` y `rule` (2026-08-30).

        `[^>]*` antes del `>` final (2026-09-14, hallado a mano): sin eso, un
        `<span class="m3-icon ..." aria-hidden="true">nombre</span>` —el
        patrón que ya usan varios iconos decorativos de esta misma
        plantilla— no matcheaba nunca, porque el regex exigía que `>` viniera
        pegado a la comilla de `class`. El tripwire no había tripeado nunca
        con ese patrón: `more_horiz`/`calendar_month` quedaron sin
        subsetear y el test seguía en verde."""
        templates = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "templates"
        usados: set[str] = set()
        for tpl in templates.glob("*.html"):
            usados.update(
                re.findall(
                    r'class="m3-icon[^"]*"[^>]*>([a-z0-9_]+)<', tpl.read_text(encoding="utf-8")
                )
            )

        manifiesto = (
            templates.parent / "static" / "fonts" / "material-symbols-rounded.icons.txt"
        ).read_text(encoding="utf-8").split()

        faltan = usados - set(manifiesto)
        assert not faltan, (
            f"iconos usados sin subsetear: {sorted(faltan)} — "
            "corré `python tools/build_icon_font.py`"
        )

    def test_los_emoji_van_subseteados(self, client: TestClient) -> None:
        """Noto Color Emoji completa pesa ~10 MB. Subseteada a las marcas de la
        interfaz más los ~230 emoji que traen los anuncios reales, ~520 KB. La
        cota corta la fuente entera, con margen para que el corpus crezca."""
        response = client.get("/static/fonts/noto-color-emoji.woff2")

        assert len(response.content) < 1_500_000

    def test_el_subconjunto_de_emoji_cubre_el_vocabulario_del_router(self) -> None:
        """Tripwire, gemelo del de iconos: la fuente se subsetea con
        `tools/build_emoji_font.py`, y el vocabulario de interfaz (`QUEUE_EMOJI`)
        SIEMPRE tiene que estar. Si alguien suma una marca a esa tupla y no corre
        la herramienta, esto se pone rojo antes de que un navegador sin fuente de
        emoji del sistema dibuje un cuadrado. La comparación es por code point:
        el subsetting trabaja sobre el cmap, y 🖼️ son dos (U+1F5BC U+FE0F)."""
        from saimc.web.routers.curation import QUEUE_EMOJI

        manifiesto = set(
            (
                Path(__file__).resolve().parents[1]
                / "src" / "saimc" / "web" / "static" / "fonts" / "noto-color-emoji.chars.txt"
            ).read_text(encoding="utf-8").strip()
        )

        faltan = sorted(
            {cp for glifo in QUEUE_EMOJI for cp in glifo} - manifiesto
        )
        assert not faltan, (
            f"code points de QUEUE_EMOJI sin subsetear: {faltan} — "
            "corré `python tools/build_emoji_font.py`"
        )

    def test_el_subconjunto_de_iconos_cubre_los_de_producto(self) -> None:
        """Los íconos de los chips de la home (`product_nav.PRODUCT_ICONS`) son
        ligaduras de Material Symbols que salen de Python, no de una plantilla,
        así que el tripwire de plantillas no los ve. Si alguien suma uno y no
        corre `build_icon_font.py`, el chip mostraría el nombre de la ligadura
        como texto en vez del ícono."""
        from saimc.web.product_nav import PRODUCT_ICONS

        manifiesto = set(
            (
                Path(__file__).resolve().parents[1]
                / "src" / "saimc" / "web" / "static" / "fonts"
                / "material-symbols-rounded.icons.txt"
            ).read_text(encoding="utf-8").split()
        )

        faltan = sorted(set(PRODUCT_ICONS.values()) - manifiesto)
        assert not faltan, (
            f"íconos de producto sin subsetear: {faltan} — "
            "corré `python tools/build_icon_font.py`"
        )

    def test_ningun_emoji_de_plantilla_queda_fuera_de_la_fuente(self) -> None:
        """Cualquier emoji que una plantilla escriba a mano tiene que estar en
        el subconjunto vendorizado, o se vería como cuadrado en un navegador sin
        fuente de emoji del sistema. Los del contenido de anuncios los cubre
        `scan_ad_emoji.py`; éstos son los pocos que viven en el HTML."""
        # Pictogramas + dingbats + flechas decorativas. No incluye el `→` de los
        # enlaces (U+2192), que es tipografía y la cubre Roboto.
        rango = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F]")
        manifiesto = set(
            (
                Path(__file__).resolve().parents[1]
                / "src" / "saimc" / "web" / "static" / "fonts" / "noto-color-emoji.chars.txt"
            ).read_text(encoding="utf-8").strip()
        )
        templates = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "templates"

        sueltos: dict[str, set[str]] = {}
        for tpl in templates.glob("*.html"):
            faltan = set(rango.findall(tpl.read_text(encoding="utf-8"))) - manifiesto
            if faltan:
                sueltos[tpl.name] = faltan

        assert not sueltos, (
            f"emoji en plantillas fuera de la fuente: {sueltos} — "
            "corré `python tools/build_emoji_font.py`"
        )

    @pytest.mark.parametrize("hoja", ["saimc-material.css", "saimc-public.css"])
    def test_la_fuente_de_emoji_esta_declarada_y_en_el_stack(self, hoja: str) -> None:
        """En LAS DOS hojas: sin `@font-face` la fuente vendorizada no se usa, y
        sin estar en `--m3-font` el navegador no la elige para los code points
        de emoji. En el Linux del operador, que no tiene fuente de emoji del
        sistema, cualquier emoji quedaría como cuadrado — también un nombre de
        producto con ✅ en la home pública."""
        css = (
            Path(__file__).resolve().parents[1]
            / "src" / "saimc" / "web" / "static" / hoja
        ).read_text(encoding="utf-8")

        assert '@font-face' in css and 'noto-color-emoji.woff2' in css
        assert '"Noto Color Emoji"' in re.search(r"--m3-font:[^;]+;", css).group(0)

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

    def test_leaflet_js_es_el_archivo_que_se_verificó(self, client: TestClient) -> None:
        """Misma guarda de cadena de suministro que htmx y marked, para el
        motor del mapa interactivo (2026-09-12)."""
        response = client.get("/static/leaflet/leaflet.js")

        assert len(response.content) == LEAFLET_JS_BYTES
        assert hashlib.sha256(response.content).hexdigest() == LEAFLET_JS_SHA256

    def test_leaflet_css_es_el_archivo_que_se_verificó(self, client: TestClient) -> None:
        response = client.get("/static/leaflet/leaflet.css")

        assert len(response.content) == LEAFLET_CSS_BYTES
        assert hashlib.sha256(response.content).hexdigest() == LEAFLET_CSS_SHA256

    def test_el_geojson_de_provincias_se_sirve(self, client: TestClient) -> None:
        """Geometría de las 16 provincias para el mapa Leaflet
        (`tools/build_cuba_geojson.py`, 2026-09-12) — reemplaza a las rutas
        SVG que antes vivían en `map_cuba.py` (borrado)."""
        response = client.get("/static/cuba_provincias.geojson")

        assert response.status_code == 200
        cuerpo = response.json()
        assert len(cuerpo["features"]) == 16

    def test_la_home_publica_referencia_el_geojson_versionado(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        assert "/static/cuba_provincias.geojson?v=" in texto

    def test_los_iconos_de_leaflet_se_sirven(self, client: TestClient) -> None:
        """`leaflet.css` los pide por ruta relativa (`images/marker-icon.png`,
        etc.) aunque el mapa de provincias no dibuje ningún marcador ni
        control de capas: sin vendorizarlos, esas dos cosas pedirían un
        archivo que 404 en silencio si alguna vez se prenden."""
        for nombre in ("layers.png", "layers-2x.png", "marker-icon.png"):
            assert client.get(f"/static/leaflet/images/{nombre}").status_code == 200

    def test_la_home_publica_referencia_leaflet_versionado(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        assert f"/static/leaflet/leaflet.js?v={LEAFLET_VERSION}" in texto
        assert f"/static/leaflet/leaflet.css?v={LEAFLET_VERSION}" in texto

    def test_el_mapa_pide_tiles_a_openstreetmap_a_propósito(
        self, client: TestClient
    ) -> None:
        """La única referencia a un servicio externo que este archivo no
        prohíbe (ver el docstring del módulo): confirma que sigue ahí y con
        atribución, en vez de asumirlo."""
        texto = client.get("/").text

        assert "tile.openstreetmap.org" in texto
        assert "openstreetmap.org/copyright" in texto

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


class TestTokensMaterial:
    """Los tokens `--m3-*` salen de una fuente única y nadie los cita al aire.

    `src/saimc/web/design_tokens.css` es la fuente; `tools/build_css_tokens.py`
    la copia dentro de las dos hojas servidas. Los tres casos de acá cubren las
    tres formas en que eso se rompe en silencio, todas medidas el 2026-09-04:

    1. Se edita un token y no se corre la herramienta → una hoja queda vieja.
    2. Se edita el bloque generado a mano → se pierde en la próxima corrida.
    3. Se cita `var(--m3-loquesea)` con un token que no existe → gana el
       fallback, que fue el caso de `--m3-tertiary` y `--m3-surface-variant`.
    """

    def _root(self, texto: str) -> dict[str, str]:
        """Los pares nombre→valor del primer bloque `:root { … }` del archivo."""
        bloque = re.search(r"^:root \{.*?^\}$", texto, re.DOTALL | re.MULTILINE)
        assert bloque is not None, "el archivo no tiene un bloque `:root { … }`"
        # Sin los comentarios: el bloque los lleva adentro y traen `#hex`.
        limpio = re.sub(r"/\*.*?\*/", "", bloque.group(0), flags=re.DOTALL)
        return dict(re.findall(r"(--(?:m3|ph)-[a-z0-9-]+):\s*([^;]+);", limpio))

    def _leer(self, *partes: str) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return raiz.joinpath(*partes).read_text(encoding="utf-8")

    @pytest.mark.parametrize("hoja", ["saimc-material.css", "saimc-public.css"])
    def test_la_hoja_lleva_los_tokens_de_la_fuente(self, hoja: str) -> None:
        """Tripwire de deriva. Rojo si se tocó un token sin correr la herramienta."""
        fuente = self._root(self._leer("src", "saimc", "web", "design_tokens.css"))
        servida = self._root(self._leer("src", "saimc", "web", "static", hoja))

        distintos = {
            nombre: (valor, servida.get(nombre))
            for nombre, valor in fuente.items()
            if servida.get(nombre) != valor
        }
        sobrantes = set(servida) - set(fuente)

        assert not distintos and not sobrantes, (
            f"{hoja} no coincide con design_tokens.css — "
            f"desincronizados: {sorted(distintos)}, sobrantes: {sorted(sobrantes)} — "
            "corré `python tools/build_css_tokens.py`"
        )

    def test_las_dos_hojas_declaran_el_mismo_bloque(self) -> None:
        """La hoja pública recibe el bloque completo, no un subconjunto.

        El recorte de `saimc-public.css` es de componentes. Cuando también
        recortaba tokens, mantener las dos listas a mano las dejó con valores
        distintos para el mismo nombre sin que nada lo dijera.
        """
        interna = self._leer("src", "saimc", "web", "static", "saimc-material.css")
        publica = self._leer("src", "saimc", "web", "static", "saimc-public.css")

        assert self._root(interna) == self._root(publica)

    def test_ningun_token_citado_queda_sin_definir(self) -> None:
        """Un `var(--m3-x)` con `x` inexistente es un color fuera del sistema.

        Pasó con `--m3-tertiary` (marca de campo asistido) y
        `--m3-surface-variant` (`code`/`pre`/`th` de `/docs`): las plantillas
        los citaban, ninguna hoja los definía, y el color que se veía era el
        del fallback — del esquema violáceo base de Material, no de este.
        """
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web"
        definidos = set(self._root(self._leer("src", "saimc", "web", "design_tokens.css")))

        archivos = [*(raiz / "templates").glob("*.html"), *(raiz / "static").glob("*.css")]
        citados: set[str] = set()
        for archivo in archivos:
            citados.update(
                re.findall(
                    r"var\((--(?:m3|ph)-[a-z0-9-]+)", archivo.read_text(encoding="utf-8")
                )
            )

        assert not citados - definidos, (
            f"tokens citados y nunca definidos: {sorted(citados - definidos)} — "
            "agregalos a design_tokens.css y corré `python tools/build_css_tokens.py`"
        )

    def test_ninguna_plantilla_cita_un_token_con_color_de_respaldo(self) -> None:
        """`var(--m3-primary, #006a60)` es una segunda paleta escondida.

        Los shells garantizan la hoja, así que el respaldo nunca se usa: lo
        único que hace es envejecer sin que nadie lo note. Los 20 que había
        tenían cuatro colores que ya no eran los del proyecto, entre ellos
        `#cac4d0`, que es del esquema violáceo base de Material.
        """
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "templates"
        con_respaldo: list[str] = []
        for tpl in sorted(raiz.glob("*.html")):
            for token, color in re.findall(
                r"var\((--(?:m3|ph)-[a-z0-9-]+),\s*(#[0-9a-fA-F]{3,8})\)",
                tpl.read_text(encoding="utf-8"),
            ):
                con_respaldo.append(f"{tpl.name}: var({token}, {color})")

        assert not con_respaldo, "quitá el color de respaldo:\n" + "\n".join(con_respaldo)


class TestEscalaResponsive:
    """Queda un solo corte responsive, 720px, y nadie inventa otro.

    `@media (max-width: var(--x))` no existe en CSS: las media queries se
    evalúan antes de que las custom properties tengan valor, así que la escala
    no puede vivir en `design_tokens.css` como token. Vive documentada ahí y se
    hace cumplir desde acá.

    El número está atado al cromo del operador, no a las window size classes
    de Material (600/840). El porqué —y qué habría que arreglar para sacarlo
    también— está en el bloque «Breakpoints» de `design_tokens.css`.
    """

    CORTES = {720}

    def test_ninguna_media_query_usa_un_corte_de_fantasia(self) -> None:
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web"
        archivos = [*(raiz / "static").glob("*.css"), *(raiz / "templates").glob("*.html")]

        intrusos: list[str] = []
        for archivo in archivos:
            for ancho in re.findall(
                r"@media[^{]*?(?:max|min)-width:\s*(\d+)px", archivo.read_text(encoding="utf-8")
            ):
                if int(ancho) not in self.CORTES:
                    intrusos.append(f"{archivo.name}: {ancho}px")

        assert not intrusos, (
            f"cortes fuera de la escala {sorted(self.CORTES)}: {intrusos} — "
            "usá el que hay o agregá el nuevo al bloque «Breakpoints» de "
            "design_tokens.css explicando a qué layout responde"
        )



class TestCuraduriaFluida:
    """`/curation` decide sus columnas sola; la evidencia sticky solo la refleja.

    `.m3-curation` es flex con wrap: la evidencia (mínimo 300px) y el
    formulario (mínimo 360px) comparten fila mientras entren con el gap entre
    medio, y se apilan cuando no. Ese es el único lugar donde se decide.

    La evidencia sticky necesita saber qué decidió el layout —apilada y
    sticky, se pegaría arriba y taparía el formulario—, y eso lo pregunta una
    container query sobre el propio `.m3-curation`. El umbral de esa query NO
    es un corte: es la condición de wrap de flexbox escrita en píxeles
    (`mínimo evidencia + gap + mínimo formulario`). Este test la recalcula
    desde las reglas, así que tocar un mínimo o el gap sin tocar la query
    rompe acá y no en la pantalla del operador.
    """

    CONTENEDORES = frozenset({"curation"})

    @staticmethod
    def _web() -> Path:
        return Path(__file__).resolve().parents[1] / "src" / "saimc" / "web"

    def _hoja(self) -> str:
        return (self._web() / "static" / "saimc-material.css").read_text(encoding="utf-8")

    def _regla(self, css: str, selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match, f"falta la regla `{selector}` en saimc-material.css"
        return match.group(1)

    def _minimo(self, css: str, selector: str) -> int:
        match = re.search(r"min-width:\s*min\((\d+)px,\s*100%\)", self._regla(css, selector))
        assert match, f"`{selector}` tiene que declarar `min-width: min(<px>, 100%)`"
        return int(match.group(1))

    def test_el_umbral_sticky_es_la_condicion_de_wrap(self) -> None:
        css = self._hoja()
        evidencia = self._minimo(css, ".m3-curation > .m3-evidence")
        formulario = self._minimo(css, ".m3-curation > :not(.m3-evidence)")

        paso = re.search(r"gap:\s*var\(--m3-space-(\d+)\)", self._regla(css, ".m3-curation"))
        assert paso, "el gap de `.m3-curation` tiene que ser un token de espaciado"
        tokens = (self._web() / "design_tokens.css").read_text(encoding="utf-8")
        gap = re.search(rf"--m3-space-{paso.group(1)}:\s*(\d+)px;", tokens)
        assert gap

        umbral = re.search(r"@container curation \(width >= (\d+)px\)", css)
        assert umbral, "falta `@container curation (width >= <px>)` para la evidencia sticky"
        assert int(umbral.group(1)) == evidencia + int(gap.group(1)) + formulario

    def test_la_evidencia_solo_es_sticky_lado_a_lado(self) -> None:
        css = self._hoja()
        consulta = re.search(r"@container curation \([^)]*\)\s*\{(.*?)\n\}", css, re.S)
        assert consulta and "position: sticky" in consulta.group(1)

        fuera = css.replace(consulta.group(0), "")
        pegadas = re.findall(r"([^{}]*\.m3-evidence)\s*\{[^}]*position:\s*sticky", fuera)
        assert not pegadas, f"evidencia sticky fuera de la container query: {pegadas}"

    def test_ninguna_container_query_sin_derivar(self) -> None:
        archivos = [*(self._web() / "static").glob("*.css"), *(self._web() / "templates").glob("*.html")]
        nombres = {
            nombre
            for archivo in archivos
            for nombre in re.findall(r"@container\s+([\w-]+)", archivo.read_text(encoding="utf-8"))
        }
        assert nombres <= self.CONTENEDORES, (
            f"container queries sin test que derive su umbral: {sorted(nombres - self.CONTENEDORES)}"
        )


class TestEscalaEspaciado:
    """Un espaciado que cae en la grilla de 4dp se escribe con su token.

    Solo `padding`, `margin` y `gap`, y solo en las dos hojas. Queda afuera a
    propósito:

    · `top`/`left`/`right`/`bottom`, que son posicionamiento — `top: 56px` es
      la altura de la topnav, no un espacio, y tokenizarlo mentiría.
    · Los valores fuera de grilla (6, 10, 13, 14, 18, 22, 26px). Son ajustes
      ópticos y la densidad de `/curation`, que el operador usa una hora por
      día; snapearlos cambiaría cuánta información entra en pantalla sin que
      nadie lo pidiera. La grilla es el default para lo nuevo.
    · El CSS que todavía vive dentro de las plantillas (~337 líneas). Sale de
      ahí cuando se repatrien los componentes, no antes.
    """

    ESCALA = {4: 1, 8: 2, 12: 3, 16: 4, 20: 5, 24: 6, 28: 7, 32: 8, 56: 14, 96: 24}

    def test_la_escala_del_test_es_la_de_la_fuente(self) -> None:
        """Que este test y `design_tokens.css` no se separen en silencio."""
        fuente = (
            Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "design_tokens.css"
        ).read_text(encoding="utf-8")
        declarados = {
            int(px): int(n) for n, px in re.findall(r"--m3-space-(\d+):\s*(\d+)px;", fuente)
        }
        assert declarados == self.ESCALA

    def test_los_valores_en_grilla_usan_el_token(self) -> None:
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "static"
        props = re.compile(
            r"\b((?:padding|margin)(?:-(?:top|right|bottom|left))?|(?:row-|column-)?gap):"
            r"\s*([^;{}]+);"
        )

        literales: list[str] = []
        for hoja in sorted(raiz.glob("*.css")):
            for prop, valor in props.findall(hoja.read_text(encoding="utf-8")):
                for px in re.findall(r"(?<![-\w.])(\d+)px", valor):
                    if int(px) in self.ESCALA:
                        literales.append(
                            f"{hoja.name}: {prop}: …{px}px… → var(--m3-space-{self.ESCALA[int(px)]})"
                        )

        assert not literales, "espaciados en grilla escritos como literal:\n" + "\n".join(
            literales
        )


class TestNingunColorFijoEnLasPlantillas:
    """Ningún `<style>` de plantilla declara un color a mano.

    Es el resultado de la repatriación (docs/decisions.md, 2026-09-04): la
    auditoría del estado visual contó **60** colores fijos repartidos en
    cuatro plantillas, y varios no salían de ninguna paleta del proyecto —
    `#cac4d0`, que es del esquema violáceo base de Material; los tres badges
    de vigencia de `/docs`; los ocho de la tira de salud de `/control`.
    El síntoma era que un mismo estado se veía de un color en una vista y de
    otro en la de al lado.

    Un color fijo nuevo en una plantilla es una segunda paleta empezando: el
    lugar de un color es `design_tokens.css`, que es lo único que las dos
    hojas leen.
    """

    def test_ninguna_plantilla_declara_un_color_a_mano(self) -> None:
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "templates"

        encontrados: list[str] = []
        for tpl in sorted(raiz.glob("*.html")):
            texto = tpl.read_text(encoding="utf-8")
            if "<style>" not in texto:
                continue
            bloque = texto[texto.index("<style>") : texto.index("</style>")]
            # Sin comentarios: nombrar un color al explicar de dónde salió es
            # justamente lo que queremos que la gente escriba.
            css = re.sub(r"/\*.*?\*/", "", bloque, flags=re.DOTALL)
            for color in re.findall(r"#[0-9a-fA-F]{3,6}\b", css) + re.findall(
                r"rgba?\([^)]+\)", css
            ):
                encontrados.append(f"{tpl.name}: {color}")

        assert not encontrados, (
            f"colores fijos en el CSS de una plantilla: {encontrados} — "
            "el lugar de un color es design_tokens.css"
        )


class TestClasesDelJavascriptExisten:
    """Toda clase que el JS asigna tiene que estar definida en algún lado.

    Es la trampa de repatriar: al renombrar una clase en el CSS y en el HTML,
    la que queda vieja es la del `className = "..."` del script — y eso **no
    falla**. No hay error en consola, no hay test rojo: el elemento aparece
    sin estilo y hay que verlo con el ojo. Estuvo por pasar dos veces seguidas
    al mover el filtro del índice de `/docs` y el envoltorio de tablas del
    lector.

    Va sobre TODAS las plantillas, no sobre las que se repatriaron: el riesgo
    es de cualquiera que tenga JS, y hoy el proyecto pasa limpio.
    """

    def test_ninguna_clase_asignada_por_js_quedo_huerfana(self) -> None:
        raiz = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web"
        hoja = (raiz / "static" / "saimc-material.css").read_text(encoding="utf-8")

        huerfanas: list[str] = []
        for tpl in sorted((raiz / "templates").glob("*.html")):
            texto = tpl.read_text(encoding="utf-8")
            local = (
                texto[texto.index("<style>") : texto.index("</style>")]
                if "<style>" in texto
                else ""
            )
            definido = hoja + local

            asignadas = re.findall(r'className\s*=\s*"([^"]+)"', texto)
            for lista in re.findall(r"classList\.(?:add|remove|toggle)\(([^)]*)\)", texto):
                asignadas.extend(re.findall(r'"([^"]+)"', lista))

            for asignacion in asignadas:
                for clase in asignacion.split():
                    if not re.search(rf"\.{re.escape(clase)}[^\w-]", definido):
                        huerfanas.append(f"{tpl.name}: {clase}")

        assert not huerfanas, (
            f"el JS asigna clases que no define ninguna hoja: {huerfanas} — "
            "se renombró el CSS y quedó el script viejo"
        )


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


class TestVistaMovil:
    """La barra superior dejó de ser rail lateral y pasó a barra sticky
    (e25aaec). En el teléfono eso apiló dos barras fijas —.m3-topnav y
    .m3-topbar— que se comían casi medio viewport, y la barra de acciones de
    curaduría no envolvía, así que "Descartar" + "Curar anuncio" se salían del
    ancho. Como el bug del atributo `hidden`, no se ve en el HTML: se afirma
    sobre el asset.
    """

    def _css(self) -> str:
        crudo = Path("src/saimc/web/static/saimc-material.css").read_text(
            encoding="utf-8"
        )
        return re.sub(r"/\*.*?\*/", "", crudo, flags=re.DOTALL)

    def _bloque_movil(self, css: str) -> str:
        inicio = css.index("@media (max-width: 720px)")
        resto = css[inicio + 1 :]
        siguiente = resto.find("@media")
        return resto if siguiente == -1 else resto[:siguiente]

    def test_la_barra_de_acciones_envuelve(self) -> None:
        css = self._css()
        bloque = css[
            css.index(".m3-actionbar {") : css.index(".m3-actionbar {") + 300
        ]

        assert "flex-wrap: wrap" in bloque, (
            "sin flex-wrap, los botones Descartar y Curar anuncio desbordan el "
            "ancho del teléfono"
        )

    def test_la_cabecera_de_pagina_no_queda_sticky_en_movil(self) -> None:
        bloque = self._bloque_movil(self._css())

        assert ".m3-topbar" in bloque and "position: static" in bloque, (
            ".m3-topnav ya es sticky arriba; si .m3-topbar sigue sticky debajo, "
            "las dos juntas tapan medio viewport en el teléfono"
        )

    def test_los_botones_de_accion_se_reparten_la_fila_en_movil(self) -> None:
        bloque = self._bloque_movil(self._css())

        assert ".m3-actionbar .m3-btn" in bloque and "flex: 1" in bloque, (
            "en el teléfono los dos botones comparten una fila propia a mitad "
            "y mitad para no desbordar aun en pantallas de 320px"
        )
