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
from typing import ClassVar

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
        texto = client.get("/?vista=mapa").text

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
        texto = client.get("/?vista=mapa").text

        assert f"/static/leaflet/leaflet.js?v={LEAFLET_VERSION}" in texto
        assert f"/static/leaflet/leaflet.css?v={LEAFLET_VERSION}" in texto

    def test_el_mapa_pide_tiles_a_openstreetmap_a_propósito(
        self, client: TestClient
    ) -> None:
        """La única referencia a un servicio externo que este archivo no
        prohíbe (ver el docstring del módulo): confirma que sigue ahí y con
        atribución, en vez de asumirlo."""
        texto = client.get("/?vista=mapa").text

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


class TestModoOscuro:
    """El bloque oscuro sigue al claro: mismo mecanismo, misma cobertura.

    `design_tokens.css` tiene dos `:root`: el claro (arriba) y
    `:root[data-theme="dark"]` (§Modo oscuro). `tools/build_css_tokens.py`
    proyecta los dos a las dos hojas servidas — estos casos son la versión
    para el segundo bloque de lo que `TestTokensMaterial` ya hace para el
    primero, más un chequeo sin equivalente ahí: que ningún token de color
    se quede sin versión oscura. Una tarjeta clara en una página por lo
    demás oscura no es un detalle menor, es un bug visible a simple vista.
    """

    #: Tokens que NO son color (tipografía, espaciado, radios) o que
    #: Material 3 define como estables entre temas («fixed») — el porqué de
    #: cada uno está en design_tokens.css §Modo oscuro. Lista cerrada a
    #: mano: agregar acá es la forma correcta de declarar «este no
    #: necesita oscuro», no dejarlo afuera en silencio.
    SIN_VERSION_OSCURA: ClassVar[set[str]] = {
        "--m3-primary-fixed-dim",
        "--m3-elev-1",
        "--m3-elev-2",
        "--m3-elev-3",
        "--m3-font",
        "--m3-mono",
        "--m3-space-1",
        "--m3-space-2",
        "--m3-space-3",
        "--m3-space-4",
        "--m3-space-5",
        "--m3-space-6",
        "--m3-space-7",
        "--m3-space-8",
        "--m3-space-14",
        "--m3-space-24",
        "--m3-radius-xs",
        "--m3-radius-sm",
        "--m3-radius-md",
        "--m3-radius-lg",
        "--m3-radius-full",
        "--ph-font-heading",
        "--ph-radius-lg",
        "--ph-radius-sm",
        "--ph-elev",
        "--ph-scrim",
        "--ph-sheet-shadow",
        # Medidas del panel lateral de `/`, no colores: el ancho de la columna
        # y el alto de la barra de filtros en angosto.
        "--ph-side-w",
        "--ph-bar-h",
    }

    def _bloques(self, texto: str) -> tuple[dict[str, str], dict[str, str]]:
        """(claro, oscuro): pares nombre→valor de los dos `:root` del archivo."""
        claro = re.search(r"^:root \{.*?^\}$", texto, re.DOTALL | re.MULTILINE)
        oscuro = re.search(r'^:root\[data-theme="dark"\] \{.*?^\}$', texto, re.DOTALL | re.MULTILINE)
        assert claro is not None, "falta el bloque `:root { … }`"
        assert oscuro is not None, 'falta el bloque `:root[data-theme="dark"] { … }`'

        def _pares(bloque: str) -> dict[str, str]:
            limpio = re.sub(r"/\*.*?\*/", "", bloque, flags=re.DOTALL)
            return dict(re.findall(r"(--(?:m3|ph)-[a-z0-9-]+):\s*([^;]+);", limpio))

        return _pares(claro.group(0)), _pares(oscuro.group(0))

    def _leer(self, *partes: str) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return raiz.joinpath(*partes).read_text(encoding="utf-8")

    def test_ningun_token_de_color_se_queda_sin_version_oscura(self) -> None:
        claro, oscuro = self._bloques(self._leer("src", "saimc", "web", "design_tokens.css"))
        faltantes = set(claro) - set(oscuro) - self.SIN_VERSION_OSCURA

        assert not faltantes, (
            f"sin versión oscura: {sorted(faltantes)} — agregalos al bloque "
            '`:root[data-theme="dark"]` o, si de verdad no aplica, a '
            "SIN_VERSION_OSCURA con el motivo"
        )

    def test_la_lista_de_excepciones_no_tiene_nombres_que_ya_no_existen(self) -> None:
        """Que la lista de arriba no se pudra: si un token se borra, se borra
        también acá — no puede quedar mintiendo que un token existe."""
        claro, _ = self._bloques(self._leer("src", "saimc", "web", "design_tokens.css"))
        raros = self.SIN_VERSION_OSCURA - set(claro)

        assert not raros, f"ya no existen en el bloque claro: {sorted(raros)}"

    @pytest.mark.parametrize("hoja", ["saimc-material.css", "saimc-public.css"])
    def test_la_hoja_lleva_el_bloque_oscuro_de_la_fuente(self, hoja: str) -> None:
        """Tripwire de deriva, versión oscura de
        `TestTokensMaterial.test_la_hoja_lleva_los_tokens_de_la_fuente`."""
        _, fuente_oscuro = self._bloques(self._leer("src", "saimc", "web", "design_tokens.css"))
        _, servido_oscuro = self._bloques(self._leer("src", "saimc", "web", "static", hoja))

        assert fuente_oscuro == servido_oscuro, (
            f"{hoja}: bloque oscuro desincronizado — corré "
            "`python tools/build_css_tokens.py`"
        )

    @pytest.mark.parametrize("hoja", ["saimc-material.css", "saimc-public.css"])
    def test_la_hoja_alterna_los_dos_iconos_del_toggle(self, hoja: str) -> None:
        """El subsetter de íconos (`tools/build_icon_font.py`) solo ve
        nombres que aparecen literales en las plantillas — sin las dos
        reglas de acá, uno de los dos estados del botón dibuja el nombre
        crudo del ícono en vez del glifo."""
        texto = self._leer("src", "saimc", "web", "static", hoja)

        assert ".theme-toggle-icon-light { display: none; }" in texto
        assert ':root[data-theme="dark"] .theme-toggle-icon-dark { display: none; }' in texto
        assert ':root[data-theme="dark"] .theme-toggle-icon-light { display: inline; }' in texto


class TestToggleDeTema:
    """El botón existe en los tres lugares que hoy sirven vistas.

    `/`, la home pública, es un caso aparte: esconde `.m3-public-top` entero
    (su titular vive en la barra del lienzo), así que necesita su propio
    botón dentro del dock del compás — no basta con el del shell.
    """

    def _leer(self, *partes: str) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return raiz.joinpath(*partes).read_text(encoding="utf-8")

    def test_shell_del_operador_trae_el_toggle(self) -> None:
        texto = self._leer("src", "saimc", "web", "templates", "_shell.html")
        assert 'class="m3-icon-btn theme-toggle"' in texto

    def test_shell_publico_trae_el_toggle(self) -> None:
        texto = self._leer("src", "saimc", "web", "templates", "_public_shell.html")
        assert "theme-toggle" in texto

    def test_home_publica_trae_su_propio_toggle(self) -> None:
        texto = self._leer("src", "saimc", "web", "templates", "public_home.html")
        assert 'class="ph-menu-item theme-toggle"' in texto

    def test_los_dos_shells_deciden_el_tema_antes_de_la_hoja(self) -> None:
        """Antiparpadeo: si el include corriera después del `<link
        rel="stylesheet">` —o peor, en `{% block scripts %}`, al pie—, cada
        carga mostraría un instante en el tema equivocado."""
        for nombre in ("_shell.html", "_public_shell.html"):
            texto = self._leer("src", "saimc", "web", "templates", nombre)
            pos_toggle = texto.index("_theme_toggle_head.html")
            pos_hoja = texto.index('rel="stylesheet"')

            assert pos_toggle < pos_hoja, f"{nombre}: decide el tema después de cargar la hoja"


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


class TestBlancoTactilDeLaIslaDeFiltros:
    """44px de área sensible en los controles del mapa, sin agrandarlos.

    Medido con Chromium a 390x844 contra el sitio en vivo el 2026-09-17: el
    disparador del calendario daba 30x30, los atajos de días 32 de alto, el
    botón Ver 35 y el selector de provincia 35. Todos por debajo del mínimo
    de 44px, y son los controles principales de la página pública.

    La isla flota sobre el mapa, así que agrandarlos de verdad es mapa que se
    tapa —en un teléfono acostado el alto disponible es ~390px—. Por eso el
    área crece con un `::after` y el dibujo no se mueve. `<select>` e
    `<input>` no admiten pseudo-elementos: esos dos sí crecen.
    """

    def _home(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return (
            raiz / "src" / "saimc" / "web" / "templates" / "public_home.html"
        ).read_text(encoding="utf-8")

    def _hoja(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return (
            raiz / "src" / "saimc" / "web" / "static" / "saimc-public.css"
        ).read_text(encoding="utf-8")

    def test_la_regla_declara_44px(self) -> None:
        css = self._hoja()
        inicio = css.index(".ph-tap-44::after {")
        bloque = css[inicio : inicio + 260]

        assert "height: 44px" in bloque, (
            "sin los 44px el área sensible vuelve al tamaño dibujado, que es "
            "lo que la regla existe para corregir"
        )

    def test_la_regla_vive_en_la_hoja_compartida(self) -> None:
        """Dejó de ser exclusiva del mapa: el tablero y el detalle de producto
        también la usan, y una copia por plantilla se desincroniza."""
        assert ".ph-tap-44 {" not in self._home()

    def test_hay_un_modificador_para_crecer_a_lo_ancho(self) -> None:
        """Para un ícono suelto, que tampoco llega a 44 de ancho."""
        css = self._hoja()
        inicio = css.index(".ph-tap-44.ph-tap-ancho::after {")
        bloque = css[inicio : inicio + 200]

        assert "width: 44px" in bloque

    @pytest.mark.parametrize(
        "control",
        [
            "ph-window-shortcut ph-tap-44",
            "ph-window-custom-trigger ph-tap-44 ph-tap-ancho",
            "ph-window-submit ph-tap-44",
        ],
    )
    def test_los_controles_llevan_la_clase(self, control: str) -> None:
        assert control in self._home(), (
            f"«{control}» perdió la clase: la regla existe pero no la aplica "
            "nadie, que es peor que no tenerla (parece resuelto y no lo está)"
        )

    def test_select_e_input_crecen_de_verdad(self) -> None:
        """Son elementos reemplazados: `::after` no los alcanza."""
        css = self._home()

        for selector in (".ph-province-select {", ".ph-window-label {"):
            inicio = css.index(selector)
            assert "min-height: 44px" in css[inicio : inicio + 400], selector

    def test_los_atajos_se_separan_lo_suficiente(self) -> None:
        """Dos áreas extendidas vecinas no se pisan: el gap pasó a .5rem."""
        css = self._home()
        inicio = css.index(".ph-window-shortcuts {")
        bloque = css[inicio : inicio + 120]

        assert "gap: .5rem" in bloque, (
            "con .3rem las áreas de 44px de dos atajos contiguos se solapan y "
            "el toque cae en el vecino"
        )


class TestBlancoTactilDelTablero:
    """44px en los controles principales del tablero (2026-09-17).

    Medido con Chromium a 390px: el toggle Mapa/Tablero daba 31px de alto,
    los chips de filtro 33px, y el producto dentro de la fila de tabla 16px.
    En total el tablero tenía 378 elementos por debajo de 44px, la mayoría
    filas de tabla y enlaces «ver anuncio» — este caso cubre los controles de
    navegación principales; el resto son evidencia dentro de un `<details>`
    que ya exige un clic para verse, no la primera interacción de la página.

    El toggle Tarjetas/Tabla y el índice de salto por provincia se retiraron
    el 2026-09-18 junto con las tarjetas del tablero (pedido del operador):
    ya no hay fragmentos que protegerles la clase.
    """

    def _dashboard(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return (
            raiz / "src" / "saimc" / "web" / "templates" / "public_dashboard.html"
        ).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "fragmento",
        [
            'class="js-vista-link ph-tap-44{% if vista == "mapa"',
            'class="js-vista-link ph-tap-44{% if vista == "tablero"',
            'class="ph-dash-chip ph-tap-44{% if choice.is_selected',
            # Dirección C (2026-09-18): el menú `<details>` del avatar pasó a
            # ser acciones a la vista en la cabecera, y los chips de producto
            # una lista cuyas filas ya miden 52px de alto (no necesitan la
            # clase: `min-height` en `.ph-watch-row`).
            'class="ph-dash-action ph-dash-action-wide ph-tap-44" href="{{ csv_href }}"',
            'class="ph-dash-action ph-tap-44 theme-toggle"',
        ],
    )
    def test_el_control_lleva_la_clase(self, fragmento: str) -> None:
        assert fragmento in self._dashboard(), (
            f"«{fragmento}» perdió `ph-tap-44`: el control vuelve a medir "
            "menos de 44px sin que ningún test lo note"
        )

    def test_la_navegacion_de_productos_aparece_desde_la_tableta(self) -> None:
        """Se escondía hasta 64rem: en una tableta el detalle se quedaba sin
        ningún modo de saltar de un producto a otro."""
        assert (
            "@media (min-width: 48rem) { .ph-nav.ph-panel { display: flex; } }"
            in self._dashboard()
        )

    def test_la_grilla_de_una_columna_no_nombra_filas(self) -> None:
        """Una fila nombrada que queda vacía —la navegación, escondida en el
        teléfono y ausente en «Todos»— mide cero pero conserva su `gap` a los
        dos lados: medido, 32px de aire donde tocaban 16. En una columna el
        orden del documento alcanza, así que las áreas viven solo en el bloque
        de 64rem, con un juego aparte para cuando hay navegación."""
        css = self._dashboard()
        hasta_64 = css[: css.index("@media (min-width: 64rem) {")]

        assert "grid-template-areas" not in hasta_64
        assert ".ph-layout.has-nav { grid-template-rows:" in css

    def test_el_toggle_va_en_su_propia_fila_en_el_movil(self) -> None:
        """Dos `.ph-tap-44` apilados sin separación se pisan las áreas
        extendidas (medido: la de arriba queda cortada a la mitad). En el
        móvil el toggle Mapa/Tablero va en su propia fila, debajo de la marca
        y las acciones, con aire arriba (`.ph-vista-wrap`)."""
        css = self._dashboard()
        inicio = css.index(".ph-vista-wrap {")
        bloque = css[inicio : css.index("}", inicio)]

        assert "flex: 0 0 100%" in bloque
        assert "padding-top" in bloque


class TestListaCompactaDelTablero:
    """El CSS de la lista compacta y del buscador (2026-09-21, prototipo D).

    Cada prueba fija una trampa concreta que apareció al construirla y que un
    test de HTML no ve, porque el HTML sale bien y lo que falla es cómo se ve:
    un atributo `hidden` que pierde contra un `display`, un campo que hace
    zoom en iOS, una cabecera pegada que deja ver las filas por detrás."""

    def _css(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        fuente = (
            raiz / "src" / "saimc" / "web" / "templates" / "public_dashboard.html"
        ).read_text(encoding="utf-8")
        return re.sub(r"/\*.*?\*/", "", fuente, flags=re.S)

    def _reglas(self) -> list[tuple[list[str], str]]:
        """(selectores, cuerpo) de cada regla, también las de dentro de un
        `@media`: la expresión solo toma llaves sin llaves adentro."""
        return [
            ([x.strip() for x in m.group(1).split(",")], m.group(2))
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", self._css())
        ]

    def _valor(self, selector: str, propiedad: str) -> str | None:
        """El valor de `propiedad` en la primera regla que nombra `selector`
        y la define."""
        for selectores, cuerpo in self._reglas():
            if selector in selectores:
                m = re.search(rf"(?:^|;)\s*{re.escape(propiedad)}\s*:\s*([^;]+)", cuerpo)
                if m:
                    return m.group(1).strip()
        return None

    def test_esconder_una_fila_le_gana_al_display_de_autor(self) -> None:
        """`.ph-fila` es `display: grid`, y un `display` de autor le gana al
        `hidden` del navegador: sin esta regla el buscador marcaría las filas
        como escondidas y seguirían a la vista."""
        assert self._valor(".ph-fila[hidden]", "display") == "none"

    def _con_display(self, clase: str) -> list[str]:
        """Los selectores de toda regla que nombra `clase` como clase entera y
        define `display`. Mira también los compuestos (`.ph-lista .ph-buscar`,
        `div.ph-buscar`), que `_valor` no ve: solo compara el selector exacto."""
        nombra = re.compile(rf"(?<![\w-])\.{re.escape(clase)}(?![\w-])")
        return [
            ", ".join(selectores)
            for selectores, cuerpo in self._reglas()
            if any(nombra.search(s) for s in selectores)
            and re.search(r"(?:^|;)\s*display\s*:", cuerpo)
        ]

    def test_lo_que_solo_esconde_el_atributo_no_tiene_display_propio(self) -> None:
        """El envoltorio del buscador y el «no hay resultados» se muestran y
        se esconden con `hidden`. Si alguno tuviera `display`, quedaría
        siempre a la vista, o el buscador sin script. Se miran todas las reglas
        que los nombran, no solo la del selector exacto."""
        assert self._con_display("ph-vacio") == []
        assert self._con_display("ph-buscar") == []

    def test_el_guardia_de_display_distingue_clases_y_ve_compuestos(self) -> None:
        """Prueba del propio guardia, contra el CSS real: sin esto podría estar
        mirando una lista vacía para siempre y dar verde sin mirar nada."""
        # Ve un compuesto: `.ph-lista-cab > span` define `display`.
        assert ".ph-lista-cab > span" in self._con_display("ph-lista-cab")
        # Respeta el borde de la clase: `.ph-buscar-caja` define `display` y no
        # por eso `.ph-buscar` lo tiene.
        assert ".ph-buscar-caja" in self._con_display("ph-buscar-caja")
        assert self._con_display("ph-buscar") == []

    @pytest.mark.parametrize(
        "selector",
        [".ph-fila", ".ph-buscar-caja", ".ph-buscar-input", ".ph-vacio button"],
    )
    def test_los_controles_de_la_lista_miden_44px_o_mas(self, selector: str) -> None:
        alto = self._valor(selector, "min-height")

        assert alto is not None, f"{selector} perdió su `min-height`"
        assert int(alto.removesuffix("px")) >= 44, f"{selector}: {alto}"

    def test_el_campo_no_provoca_zoom_en_ios(self) -> None:
        """Safari en iOS hace zoom al enfocar un campo de menos de 16px, y en el
        teléfono es la pantalla en la que más se va a usar."""
        letra = self._valor(".ph-buscar-input", "font")

        assert letra is not None
        tamano = re.search(r"(\d+)px", letra)

        assert tamano is not None, letra
        assert int(tamano.group(1)) >= 16, letra

    def test_la_cabecera_queda_pegada_y_tapa_lo_que_pasa_por_detras(self) -> None:
        """Sin fondo propio, las filas se verían a través de la cabecera fija."""
        assert self._valor(".ph-lista-cab", "position") == "sticky"
        assert self._valor(".ph-lista-cab", "top") == "0"
        assert self._valor(".ph-lista-cab", "background") == "var(--m3-surface)"

    def test_el_chevron_solo_desde_la_tableta(self) -> None:
        """En el teléfono cada píxel de ancho es de una cifra: sin chevrón ni su
        columna. Desde 48rem vuelven, y con ellos la última columna, que lo
        empuja al borde derecho."""
        corte = "@media (min-width: 48rem) {\n    .ph-lista"
        # Prototipo aprobado el 2026-09-26: la última columna es la del
        # chevrón, de ancho fijo pegado a la derecha de la fila.
        columnas = "repeat(var(--n, 2), minmax(0, 1.2fr)) 28px"
        css = self._css()
        hasta_48, desde_48 = css[: css.index(corte)], css[css.index(corte) :]

        assert re.search(r"\.ph-fila-chev, \.ph-lista-cab-chev \{ display: none; \}", hasta_48)
        assert columnas in desde_48
        assert columnas not in hasta_48

    def test_la_cifra_queda_pegada_al_borde_derecho(self) -> None:
        """Alinear las cifras a la derecha sirve para comparar hacia abajo, y
        solo si la cifra está SIEMPRE en el borde: la pastilla «más barata» va
        a su izquierda (`row-reverse`, con la cifra primera en el HTML) y no le
        quita el lugar. Sin esto, 298 con pastilla queda corrida contra 685."""
        assert self._valor(".ph-cel", "align-items") == "flex-end"
        assert self._valor(".ph-cel-cifra", "flex-direction") == "row-reverse"

    def _desde_la_tableta(self) -> str:
        """El CSS a partir del corte de la lista a 48rem: lo que solo vale
        desde la tableta."""
        css = self._css()
        return css[css.index("@media (min-width: 48rem) {\n    .ph-lista") :]

    def test_desde_la_tableta_la_celda_se_lee_de_izquierda_a_derecha(self) -> None:
        """Prototipo del tablero (2026-09-26): con espacio de sobra, la cifra
        va primero y a la izquierda, con la pastilla a su derecha y debajo el
        trazo junto al cambio. El teléfono sigue con la cifra pegada a la
        derecha (arriba): ahí cada píxel es de una cifra."""
        tableta = self._desde_la_tableta()

        assert re.search(r"\.ph-cel \{[^}]*align-items: flex-start", tableta)
        assert re.search(r"\.ph-cel-cifra \{[^}]*flex-direction: row;", tableta)

    def test_sin_producto_elegido_la_pagina_es_de_una_columna(self) -> None:
        """El prototipo aprobado pone la frescura arriba y «Sobre los datos»
        abajo, en una sola columna. La barra lateral de dos columnas queda
        solo para el detalle (`.has-nav`)."""
        css = self._css()

        assert 'grid-template-areas: "main fresh" "main datos" "main ."' not in css
        assert re.search(r"\.ph-layout\.has-nav \{[^}]*grid-template-columns: minmax\(0, 1fr\) 340px", css)

    def test_la_frescura_atrasada_usa_el_tono_de_aviso(self) -> None:
        """Informa, no asusta: un dato con días de atraso pasa al fondo de
        aviso del sistema (`--m3-warn-wash`), sin rojo."""
        assert self._valor('.ph-fresh[data-nivel="atrasado"]', "background") == "var(--m3-warn-wash)"
        assert self._valor('.ph-fresh[data-nivel="atrasado"]', "color") == "var(--m3-warn-strong)"

    def _keyframes(self) -> dict[str, str]:
        """Cuerpo de cada `@keyframes ph-*`, contando llaves: los cuerpos
        anidan `from { … } to { … }` y una regex simple los parte mal."""
        css = self._css()
        salida: dict[str, str] = {}
        for m in re.finditer(r"@keyframes (ph-[\w-]+)\s*\{", css):
            nivel, i = 1, m.end()
            while nivel and i < len(css):
                nivel += {"{": 1, "}": -1}.get(css[i], 0)
                i += 1
            salida[m.group(1)] = css[m.end() : i - 1]
        return salida

    def test_el_movimiento_existe_y_solo_anima_propiedades_baratas(self) -> None:
        """Prototipo del tablero (2026-09-26): entrada, trazo y punto. Solo
        `opacity`, `transform` y el trazo del SVG: nada que dispare layout, para
        que corra fluido en un celular de gama baja y con conexión lenta."""
        cuadros = self._keyframes()

        assert {"ph-sube", "ph-trazo", "ph-punto"} <= set(cuadros)
        for nombre, cuerpo in cuadros.items():
            props = set(re.findall(r"([\w-]+)\s*:", cuerpo))
            assert props <= {"opacity", "transform", "stroke-dashoffset"}, (nombre, props)

    def test_el_movimiento_se_apaga_con_reducir_movimiento(self) -> None:
        """Quien pide «reducir movimiento» al sistema ve todo de golpe: sin
        entrada, sin trazo dibujándose, sin desplazamientos."""
        css = self._css()
        inicio = css.index("@media (prefers-reduced-motion: reduce)")
        bloque = css[inicio : css.index("}\n  }", inicio)]

        assert ".ph-lista[data-entra] .ph-fila" in bloque
        assert ".ph-lista[data-entra] .ph-cel-spark path" in bloque
        assert "animation: none" in bloque
        assert "stroke-dashoffset: 0" in bloque

    def test_el_bloque_scripts_conserva_lo_del_padre(self) -> None:
        """Un `{% block %}` que redefine el del padre lo pisa entero. Hoy el
        `scripts` del padre está vacío, pero la vista que agrega HTMX o cualquier
        otro script lo hace desde ahí, y el buscador no debe llevárselo puesto."""
        raiz = Path(__file__).resolve().parents[1]
        fuente = (raiz / "src" / "saimc" / "web" / "templates" / "public_dashboard.html").read_text(
            encoding="utf-8"
        )

        assert "{% block scripts %}{{ super() }}" in fuente

    def test_no_quedan_reglas_de_la_tarjeta(self) -> None:
        """La tarjeta salió de la lista; su CSS suelto sería peso muerto que
        nadie borra porque nadie sabe si algo lo usa."""
        assert ".ph-tarjeta" not in self._css()


class TestCintillo:
    """CSS del cintillo de precios compartido por las tres vistas (prototipo
    aprobado el 2026-09-26). Vive en `saimc-public.css`: lo cargan todas."""

    def _hoja(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        fuente = (raiz / "src" / "saimc" / "web" / "static" / "saimc-public.css").read_text(
            encoding="utf-8"
        )
        return re.sub(r"/\*.*?\*/", "", fuente, flags=re.S)

    def _regla(self, selector: str) -> str:
        m = re.search(rf"(?:^|\}}|\n)\s*{re.escape(selector)}\s*\{{([^{{}}]*)\}}", self._hoja())
        assert m, f"no hay una regla para {selector}"
        return m.group(1)

    def test_el_cintillo_mide_44px_como_todo_lo_tocable(self) -> None:
        """El prototipo lo dibujó de 40 px; los enlaces del cintillo son
        tocables y la regla del sitio es 44 px o más."""
        assert re.search(r"height:\s*2\.75rem", self._regla(".ph-cintillo"))

    def test_la_cinta_solo_anima_transform(self) -> None:
        """Un grupo entero hacia la izquierda y vuelta a empezar: la copia queda
        exactamente donde estaba el original. Nada que dispare layout."""
        m = re.search(r"@keyframes ph-cinta\s*\{\s*to\s*\{([^}]*)\}\s*\}", self._hoja())

        assert m, "falta @keyframes ph-cinta"
        assert re.findall(r"([\w-]+)\s*:", m.group(1)) == ["transform"]
        assert "translateX(-50%)" in m.group(1)

    def test_se_detiene_al_pasar_el_cursor_o_enfocar(self) -> None:
        hoja = self._hoja()

        assert ".ph-cintillo-pista:hover .ph-cintillo-cinta" in hoja
        assert ".ph-cintillo-pista:focus-within .ph-cintillo-cinta" in hoja
        assert "animation-play-state: paused" in hoja

    def test_con_reducir_movimiento_la_cinta_queda_quieta_y_sin_copia(self) -> None:
        hoja = self._hoja()
        inicio = hoja.index("@media (prefers-reduced-motion: reduce)", hoja.index(".ph-cintillo"))
        bloque = hoja[inicio : hoja.index("\n}", inicio)]

        assert ".ph-cintillo-cinta" in bloque and "animation: none" in bloque
        assert '.ph-cintillo-grupo[aria-hidden="true"]' in bloque and "display: none" in bloque

    def test_sin_colores_a_mano(self) -> None:
        """Todo sale de tokens: claro y oscuro salen solos. La máscara del
        borde usa `black` solo por su canal alfa."""
        trozo = self._hoja()
        trozo = trozo[trozo.index(".ph-cintillo") :]
        trozo = trozo[: trozo.index("@media (prefers-reduced-motion: reduce)")]

        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", trozo)


class TestFuenteYFilaDeEvidencia:
    """Dos hallazgos más de la auditoría mobile-first del 2026-09-17: la
    fuente más chica del sitio (10px, en el cartelito del mapa) y la fila
    más chica del detalle de producto (65x15, el enlace «ver anuncio»)."""

    def _hoja(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return (
            raiz / "src" / "saimc" / "web" / "static" / "saimc-public.css"
        ).read_text(encoding="utf-8")

    def _home(self) -> str:
        raiz = Path(__file__).resolve().parents[1]
        return (
            raiz / "src" / "saimc" / "web" / "templates" / "public_home.html"
        ).read_text(encoding="utf-8")

    def test_la_fila_de_evidencia_sube_su_padding(self) -> None:
        """37px de alto medido con .5rem de padding, por debajo de 44px. Sube
        el padding de toda la fila —no solo del enlace— porque las filas van
        pegadas sin espacio: extender solo el enlace habría invadido la
        vecina."""
        css = self._hoja()
        inicio = css.index(".ph-coverage th, .ph-coverage td {")
        bloque = css[inicio : inicio + 140]

        assert "padding: .75rem" in bloque, (
            "con .5rem la fila que trae «ver anuncio» mide 37px de alto, por "
            "debajo del blanco táctil de 44px"
        )

    def test_ningun_texto_del_cartelito_del_mapa_baja_de_11px(self) -> None:
        """.625rem (10px) era la fuente más chica del sitio, medida en el
        cartelito flotante del mapa."""
        assert ".625rem" not in self._home().split("ph-map-badge-text")[1][:200]
