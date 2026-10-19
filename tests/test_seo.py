"""SEO básico de la capa pública (slice `seo-basico`, plan-bloque-mvp T10).

Lo que estos casos protegen:

- `/robots.txt` y `/sitemap.xml` responden sin credenciales, aun con la puerta
  del sitio encendida (un buscador no tiene contraseña).
- El sitemap sale de `Product` (solo el foco piloto), lista las páginas de
  texto y **nunca** `/anuncio/<id>`: el anuncio es evidencia con datos del
  emisor y no se ofrece a un buscador (privacidad, CLAUDE.md «Datos»).
- Cada página pública trae canonical sin parámetros de filtro, og:image,
  Twitter Cards e íconos; el JSON-LD es JSON válido con el tipo esperado.
- `/static` manda `Cache-Control`.

Los valores esperados están escritos a mano a partir del contrato del slice,
no leídos de la salida del código.
"""

import json
import re
import struct
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.models import Product
from saimc.web import access, seo
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.routers import public

BASE = "https://guani.example"
STATIC = Path(__file__).resolve().parent.parent / "src" / "saimc" / "web" / "static"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

#: Las páginas de texto que existen siempre (`site_pages.SITE_PAGES`).
PAGINAS_DE_TEXTO = (
    "/quienes-somos",
    "/metodologia",
    "/preguntas",
    "/terminos",
    "/privacidad",
    "/uso-de-datos",
)


def _settings(**valores: object) -> Settings:
    """Configuración sin leer ningún `.env`."""
    return Settings(_env_file=None, **{"site_url": BASE, **valores})  # type: ignore[arg-type, call-arg]


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    ajustes = _settings()
    monkeypatch.setattr(seo, "get_settings", lambda: ajustes)
    monkeypatch.setattr(public, "get_settings", lambda: ajustes)
    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        test_client.cookies.set("vista", "mapa")
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def productos(session: Session) -> None:
    """Dos del foco piloto y uno fuera de él."""
    session.add_all(
        [
            Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True),
            Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[], is_pilot=True),
            Product(name="Cemento", category="construccion", unit="saco", aliases=[], is_pilot=False),
        ]
    )
    session.commit()


def _etiquetas(html: str, nombre: str) -> list[dict[str, str]]:
    """Los atributos de cada `<nombre ...>` del documento."""
    return [
        dict(re.findall(r'([\w:-]+)="([^"]*)"', atributos))
        for atributos in re.findall(rf"<{nombre}\b([^>]*)>", html)
    ]


def _meta(html: str, clave: str) -> str | None:
    """El `content` del `<meta property|name=clave>`, o `None`."""
    for atributos in _etiquetas(html, "meta"):
        if clave in (atributos.get("property"), atributos.get("name")):
            return atributos.get("content")
    return None


def _canonical(html: str) -> list[str]:
    return [a["href"] for a in _etiquetas(html, "link") if a.get("rel") == "canonical"]


def _jsonld(html: str) -> list[dict]:
    bloques = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)
    return [json.loads(bloque) for bloque in bloques]


def _tipos(html: str) -> list[str]:
    return [dato["@type"] for dato in _jsonld(html)]


def _dimensiones_png(ruta: Path) -> tuple[int, int]:
    datos = ruta.read_bytes()
    assert datos[:8] == b"\x89PNG\r\n\x1a\n", f"{ruta.name} no es un PNG"
    return struct.unpack(">II", datos[16:24])  # type: ignore[return-value]


class TestVerificacionGoogle:
    def test_sirve_el_archivo_exacto_que_pide_search_console(self, client: TestClient) -> None:
        respuesta = client.get("/google06eec30faab9a5d5.html")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("text/html")
        assert respuesta.text == "google-site-verification: google06eec30faab9a5d5.html"


class TestRobots:
    def test_responde_texto_plano(self, client: TestClient) -> None:
        respuesta = client.get("/robots.txt")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("text/plain")

    def test_permite_la_capa_publica_y_apunta_al_sitemap(self, client: TestClient) -> None:
        cuerpo = client.get("/robots.txt").text
        assert "User-agent: *" in cuerpo
        assert f"Sitemap: {BASE}/sitemap.xml" in cuerpo
        # No hay un `Disallow: /` a secas: eso apagaría todo el sitio.
        assert not re.search(r"^Disallow:\s*/\s*$", cuerpo, flags=re.M)

    @pytest.mark.parametrize(
        "ruta",
        [
            "/anuncio/",
            "/acceso",
            "/curation",
            "/control",
            "/catalog",
            "/marketing",
            "/docs",
            "/evidence",
            "/webhooks/",
        ],
    )
    def test_cierra_lo_que_no_es_para_un_buscador(self, client: TestClient, ruta: str) -> None:
        assert f"Disallow: {ruta}" in client.get("/robots.txt").text.splitlines()

    def test_no_cierra_el_csv_de_datos(self, client: TestClient) -> None:
        # El Dataset del JSON-LD apunta a `/datos.csv`: bloquearlo lo vuelve inútil.
        assert "Disallow: /datos.csv" not in client.get("/robots.txt").text

    def test_sin_site_url_usa_el_host_de_la_peticion(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(seo, "get_settings", lambda: _settings(site_url=None))
        cuerpo = client.get("/robots.txt").text
        assert "Sitemap: http://testserver/sitemap.xml" in cuerpo

    def test_abierto_con_la_puerta_del_sitio_encendida(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cerrada = _settings(site_gate_enabled=True, curation_password="clave")  # noqa: S106
        monkeypatch.setattr(access, "get_settings", lambda: cerrada)
        assert client.get("/robots.txt").status_code == 200
        assert client.get("/sitemap.xml").status_code == 200
        # Y la puerta sigue cerrada para lo demás: el control sí detecta el cambio.
        assert client.get("/precios", follow_redirects=False).status_code == 303


class TestSitemap:
    def _urls(self, client: TestClient) -> list[str]:
        raiz = ElementTree.fromstring(client.get("/sitemap.xml").text)  # noqa: S314 — parsea la salida propia
        return [e.text or "" for e in raiz.findall("sm:url/sm:loc", SITEMAP_NS)]

    def test_responde_xml_con_el_namespace_del_protocolo(self, client: TestClient, productos: None) -> None:
        respuesta = client.get("/sitemap.xml")
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("application/xml")
        assert ElementTree.fromstring(respuesta.text).tag == f"{{{SITEMAP_NS['sm']}}}urlset"  # noqa: S314

    def test_lista_la_landing_la_home_de_precios_y_las_paginas_de_texto(
        self, client: TestClient, productos: None
    ) -> None:
        urls = self._urls(client)
        esperadas = [f"{BASE}/", f"{BASE}/precios", *(f"{BASE}{ruta}" for ruta in PAGINAS_DE_TEXTO)]
        for esperada in esperadas:
            assert esperada in urls

    def test_un_producto_piloto_por_url_con_su_slug(self, client: TestClient, productos: None) -> None:
        urls = self._urls(client)
        assert f"{BASE}/producto/arroz" in urls
        assert f"{BASE}/producto/azucar-blanca" in urls

    def test_un_producto_fuera_del_foco_no_entra(self, client: TestClient, productos: None) -> None:
        assert f"{BASE}/producto/cemento" not in self._urls(client)

    def test_nunca_lista_un_anuncio(self, client: TestClient, productos: None) -> None:
        assert not [u for u in self._urls(client) if "/anuncio/" in u]

    def test_no_lista_el_csv_ni_el_formulario_de_acceso(self, client: TestClient, productos: None) -> None:
        urls = self._urls(client)
        assert f"{BASE}/datos.csv" not in urls
        assert f"{BASE}/acceso" not in urls

    def test_ninguna_url_lleva_parametros_de_filtro(self, client: TestClient, productos: None) -> None:
        assert not [u for u in self._urls(client) if "?" in u]

    def test_sin_productos_sigue_siendo_un_sitemap_valido(self, client: TestClient) -> None:
        urls = self._urls(client)
        assert f"{BASE}/" in urls
        assert not [u for u in urls if "/producto/" in u]

    def test_contacto_entra_solo_si_existe(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        assert f"{BASE}/contacto" not in self._urls(client)
        con_correo = _settings(contact_email="hola@guani.example")
        monkeypatch.setattr(seo, "get_settings", lambda: con_correo)
        assert f"{BASE}/contacto" in self._urls(client)

    def test_escapa_los_caracteres_de_xml(self, client: TestClient, session: Session) -> None:
        session.add(Product(name="Pollo & cia", category="x", unit="lb", aliases=[], is_pilot=True))
        session.commit()
        # El `&` del nombre sale codificado en la URL (`%26`), y el XML se
        # parsea: si algo quedara sin escapar, `fromstring` fallaría antes.
        assert f"{BASE}/producto/pollo-%26-cia" in self._urls(client)


class TestCabecera:
    """Etiquetas del `<head>` en las páginas que usan `_public_shell.html`."""

    PAGINAS = ("/", "/precios", "/quienes-somos", "/uso-de-datos", "/metodologia")

    @pytest.mark.parametrize("ruta", PAGINAS)
    def test_un_solo_canonical_absoluto_de_la_misma_ruta(self, client: TestClient, ruta: str) -> None:
        assert _canonical(client.get(ruta).text) == [f"{BASE}{ruta}"]

    def test_el_canonical_descarta_los_parametros_de_filtro(
        self, client: TestClient, productos: None
    ) -> None:
        html = client.get("/precios?producto=arroz&provincia=la-habana&dias=7&vista=mapa").text
        assert _canonical(html) == [f"{BASE}/precios"]

    def test_el_canonical_de_un_producto_es_su_ruta_sin_query(
        self, client: TestClient, productos: None
    ) -> None:
        html = client.get("/producto/arroz?provincia=la-habana&capa=mayorista").text
        assert _canonical(html) == [f"{BASE}/producto/arroz"]

    @pytest.mark.parametrize("ruta", PAGINAS)
    def test_og_url_coincide_con_el_canonical(self, client: TestClient, ruta: str) -> None:
        html = client.get(f"{ruta}?utm_source=x").text
        assert _meta(html, "og:url") == f"{BASE}{ruta}"

    @pytest.mark.parametrize("ruta", PAGINAS)
    def test_og_image_absoluta_con_tamano_y_texto_alternativo(self, client: TestClient, ruta: str) -> None:
        html = client.get(ruta).text
        assert _meta(html, "og:image") == f"{BASE}/static/seo/og-guani.png"
        assert _meta(html, "og:image:width") == "1200"
        assert _meta(html, "og:image:height") == "630"
        assert _meta(html, "og:image:type") == "image/png"
        assert _meta(html, "og:image:alt")

    @pytest.mark.parametrize("ruta", PAGINAS)
    def test_twitter_card_grande_con_titulo_descripcion_e_imagen(self, client: TestClient, ruta: str) -> None:
        html = client.get(ruta).text
        assert _meta(html, "twitter:card") == "summary_large_image"
        assert _meta(html, "twitter:title") == _meta(html, "og:title")
        assert _meta(html, "twitter:description") == _meta(html, "og:description")
        assert _meta(html, "twitter:image") == f"{BASE}/static/seo/og-guani.png"
        assert _meta(html, "twitter:image:alt") == _meta(html, "og:image:alt")

    def test_la_descripcion_propia_de_la_pagina_sigue_ganando(self, client: TestClient) -> None:
        # El parcial no pisa los bloques `og_description` de cada plantilla.
        assert "Guani es un sitio gratuito" in (
            _meta(client.get("/quienes-somos").text, "twitter:description") or ""
        )

    @pytest.mark.parametrize("ruta", PAGINAS)
    def test_iconos_declarados(self, client: TestClient, ruta: str) -> None:
        enlaces = _etiquetas(client.get(ruta).text, "link")
        iconos = {(a.get("rel"), a.get("sizes"), a.get("type")): a["href"] for a in enlaces}
        assert iconos[("icon", "48x48", "image/png")] == "/static/seo/favicon-48.png"
        assert iconos[("icon", "32x32", "image/png")] == "/static/seo/favicon-32.png"
        assert iconos[("apple-touch-icon", "180x180", None)] == "/static/seo/apple-touch-icon.png"

    @pytest.mark.parametrize("ruta", ["/precios", "/quienes-somos", "/metodologia"])
    def test_las_paginas_publicas_pintan_la_foto_del_avatar(
        self, client: TestClient, ruta: str
    ) -> None:
        html = client.get(ruta).text
        assert 'class="ph-brand-avatar" src="/static/brand/guani-avatar.webp"' in html
        assert (STATIC / "brand" / "guani-avatar.webp").is_file()

    def test_la_pagina_de_acceso_tambien_lleva_la_cabecera(self, client: TestClient) -> None:
        # Usa el mismo shell; no debe romper al renderizar el parcial.
        html = client.get("/acceso").text
        assert _canonical(html) == [f"{BASE}/acceso"]


class TestJsonLd:
    def test_la_landing_declara_organization_y_website(self, client: TestClient) -> None:
        html = client.get("/").text
        assert sorted(_tipos(html)) == ["Organization", "WebSite"]
        datos = {d["@type"]: d for d in _jsonld(html)}
        assert all(d["@context"] == "https://schema.org" for d in datos.values())
        assert datos["Organization"]["name"] == "Guani"
        assert datos["Organization"]["url"] == f"{BASE}/"
        assert datos["Organization"]["logo"] == f"{BASE}/static/seo/apple-touch-icon.png"
        assert datos["WebSite"]["name"] == "Guani"
        assert datos["WebSite"]["url"] == f"{BASE}/"
        assert datos["WebSite"]["inLanguage"] == "es"

    def test_website_no_promete_una_busqueda_que_no_existe(self, client: TestClient) -> None:
        sitio = next(d for d in _jsonld(client.get("/").text) if d["@type"] == "WebSite")
        assert "potentialAction" not in sitio

    def test_organization_firma_la_marca_y_no_inventa_redes(self, client: TestClient) -> None:
        org = next(d for d in _jsonld(client.get("/").text) if d["@type"] == "Organization")
        # Sin redes configuradas no hay `sameAs`; nunca una lista vacía ni inventada.
        assert "sameAs" not in org
        # La capa pública se firma «Guani, por Leira Studio», nunca una persona.
        assert org.get("parentOrganization", {}).get("name", "Leira Studio") == "Leira Studio"

    def test_organization_enlaza_las_redes_configuradas(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        con_redes = _settings(link_facebook="https://facebook.com/guani", link_telegram="https://t.me/guani")
        monkeypatch.setattr(seo, "get_settings", lambda: con_redes)
        org = next(d for d in _jsonld(client.get("/").text) if d["@type"] == "Organization")
        assert org["sameAs"] == ["https://facebook.com/guani", "https://t.me/guani"]

    def test_las_demas_paginas_no_repiten_organization(self, client: TestClient) -> None:
        assert "Organization" not in _tipos(client.get("/quienes-somos").text)

    def test_precios_declara_el_dataset_de_datos_csv(self, client: TestClient) -> None:
        datasets = [d for d in _jsonld(client.get("/precios").text) if d["@type"] == "Dataset"]
        assert len(datasets) == 1
        dataset = datasets[0]
        assert dataset["name"]
        assert dataset["description"]
        assert dataset["url"] == f"{BASE}/precios"
        assert dataset["distribution"] == [
            {
                "@type": "DataDownload",
                "encodingFormat": "text/csv",
                "contentUrl": f"{BASE}/datos.csv",
            }
        ]
        assert dataset["creator"]["name"] == "Guani"
        assert dataset["inLanguage"] == "es"
        assert dataset["spatialCoverage"] == "Cuba"

    def test_el_dataset_no_nombra_licencia(self, client: TestClient) -> None:
        # `/uso-de-datos` escribe la regla en palabras llanas y NO nombra una
        # licencia (docs/decisions.md, 2026-10-05): el JSON-LD no puede
        # inventar una. Apunta a esa página como condiciones de uso.
        dataset = next(d for d in _jsonld(client.get("/precios").text) if d["@type"] == "Dataset")
        assert "license" not in dataset
        assert dataset["usageInfo"] == f"{BASE}/uso-de-datos"

    def test_la_pagina_de_un_producto_declara_su_migas_de_pan(
        self, client: TestClient, productos: None
    ) -> None:
        html = client.get("/producto/azucar-blanca?provincia=la-habana").text
        migas = [d for d in _jsonld(html) if d["@type"] == "BreadcrumbList"]
        assert len(migas) == 1
        assert migas[0]["itemListElement"] == [
            {"@type": "ListItem", "position": 1, "name": "Inicio", "item": f"{BASE}/"},
            {"@type": "ListItem", "position": 2, "name": "Precios", "item": f"{BASE}/precios"},
            {
                "@type": "ListItem",
                "position": 3,
                "name": "Azúcar blanca",
                "item": f"{BASE}/producto/azucar-blanca",
            },
        ]

    def test_el_producto_no_declara_dataset_ni_organization(
        self, client: TestClient, productos: None
    ) -> None:
        assert _tipos(client.get("/producto/arroz").text) == ["BreadcrumbList"]

    def test_un_valor_con_marcas_html_no_cierra_el_script(self) -> None:
        # Un nombre de producto sale de la base: no puede cerrar el `<script>`
        # ni abrir otro. `<` y `&` salen como escapes JSON, que `json.loads`
        # devuelve intactos.
        etiqueta = seo.json_ld_tag({"@type": "Thing", "name": "</script><b>x & y"})
        cuerpo = etiqueta.removeprefix('<script type="application/ld+json">').removesuffix("</script>")
        assert "<" not in cuerpo and "&" not in cuerpo
        assert json.loads(cuerpo)["name"] == "</script><b>x & y"


class TestIconosYTarjeta:
    def test_og_guani_es_un_png_de_1200_por_630(self) -> None:
        assert _dimensiones_png(STATIC / "seo" / "og-guani.png") == (1200, 630)

    def test_apple_touch_icon_es_180_por_180(self) -> None:
        assert _dimensiones_png(STATIC / "seo" / "apple-touch-icon.png") == (180, 180)

    def test_favicon_32_es_32_por_32(self) -> None:
        assert _dimensiones_png(STATIC / "seo" / "favicon-32.png") == (32, 32)

    def test_favicon_48_es_48_por_48(self) -> None:
        assert _dimensiones_png(STATIC / "seo" / "favicon-48.png") == (48, 48)

    def test_no_queda_el_favicon_svg_gris(self) -> None:
        assert not (STATIC / "seo" / "favicon.svg").exists()

    @pytest.mark.parametrize(
        "ruta",
        [
            "og-guani.png",
            "apple-touch-icon.png",
            "favicon-32.png",
            "favicon-48.png",
        ],
    )
    def test_se_sirven_desde_static(self, client: TestClient, ruta: str) -> None:
        assert client.get(f"/static/seo/{ruta}").status_code == 200

    def test_la_tarjeta_de_compartir_es_un_tipo_de_tarjeta_de_marketing(self) -> None:
        # `og-guani.png` sale de una tarjeta de marketing (no de un diseño
        # aparte): queda en /marketing y se regenera con la misma plantilla.
        from saimc.web.routers.marketing import CARD_TYPES

        compartir = next(t for t in CARD_TYPES if t.slug == "og-compartir")
        assert (compartir.width, compartir.height) == (1200, 630)
        assert not compartir.needs_product


class TestCacheStatic:
    def test_un_archivo_con_version_en_la_query_es_inmutable(self, client: TestClient) -> None:
        respuesta = client.get("/static/saimc-public.css?v=123")
        assert respuesta.headers["cache-control"] == "public, max-age=31536000, immutable"

    def test_un_archivo_sin_version_se_cachea_un_dia(self, client: TestClient) -> None:
        respuesta = client.get("/static/seo/og-guani.png")
        assert respuesta.headers["cache-control"] == "public, max-age=86400"

    def test_un_404_de_static_no_se_cachea_como_si_existiera(self, client: TestClient) -> None:
        respuesta = client.get("/static/no-existe.css?v=1")
        assert respuesta.status_code == 404
        assert "immutable" not in respuesta.headers.get("cache-control", "")

    def test_las_paginas_no_heredan_el_cache_de_static(self, client: TestClient) -> None:
        assert "max-age" not in client.get("/quienes-somos").headers.get("cache-control", "")
