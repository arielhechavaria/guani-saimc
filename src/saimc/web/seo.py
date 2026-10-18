"""SEO básico de la capa pública (slice `seo-basico`, `docs/plan-bloque-mvp.md` T10).

No es un «módulo SEO»: es lo mínimo para que un buscador y una vista previa de
WhatsApp/Telegram entiendan las páginas. Acá se decide **qué URL es la
canónica**, **qué dice `robots.txt`**, **qué entra al sitemap** y **qué JSON-LD
lleva cada página**; las plantillas (`_seo_head.html` y los bloques
`seo_jsonld`) solo lo pintan.

**Dónde está el sitio.** Las URL de `canonical`, `og:*` y del sitemap tienen que
ser absolutas. `SAIMC_SITE_URL` fija el origen; sin él se usa el de la petición,
que detrás de un proxy es el interno (`http://127.0.0.1:8000`) y por eso en
producción hay que ponerlo.

**Canonical sin filtros.** Siempre es el origen más la ruta, sin query: el
tablero tiene decenas de combinaciones de `?producto=&provincia=&dias=…` y todas
son la misma página para un buscador (`/producto/<slug>` y `/precios`).

**Privacidad.** `/anuncio/<id>` es evidencia con datos del emisor
(CLAUDE.md, «Datos»): no entra al sitemap y `robots.txt` lo cierra.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

from markupsafe import Markup
from starlette.requests import Request

from saimc.config import get_settings
from saimc.web.landing_view import build_footer_links
from saimc.web.product_nav import HOME_PATH, product_slug
from saimc.web.site_pages import CONTACT_PATH, SITE_PAGES

#: La tarjeta de compartir (1200×630, `tools/build_seo_assets.py`). Es el
#: tamaño que WhatsApp, Facebook y X muestran sin recortar.
OG_IMAGE_PATH = "/static/seo/og-guani.png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630
OG_IMAGE_ALT = "Guani: precios del mercado informal cubano, verificados anuncio por anuncio"

#: El logo para el JSON-LD de `Organization`: el ícono de 180 px, cuadrado.
LOGO_PATH = "/static/seo/apple-touch-icon.png"

SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"

#: Prefijos que un buscador no tiene por qué recorrer. `/datos.csv` NO está:
#: el `Dataset` del JSON-LD apunta ahí.
ROBOTS_DISALLOW: tuple[str, ...] = (
    "/anuncio/",
    "/acceso",
    "/curation",
    "/control",
    "/catalog",
    "/marketing",
    "/docs",
    "/evidence",
    "/webhooks/",
)


def site_base(request: Request) -> str:
    """El origen del sitio, sin barra final."""
    configured = (get_settings().site_url or "").strip().rstrip("/")
    if configured.startswith(("http://", "https://")):
        return configured
    return str(request.base_url).rstrip("/")


def absolute_url(request: Request, path: str) -> str:
    return f"{site_base(request)}{path}"


def canonical_url(request: Request) -> str:
    """Origen + ruta de la petición, sin query ni fragmento."""
    return absolute_url(request, request.url.path)


def json_ld_tag(data: dict[str, Any]) -> Markup:
    """El `<script type="application/ld+json">` de `data`.

    `<`, `>` y `&` salen como escapes `\\uXXXX`: un nombre de producto viene de
    la base y un `</script>` dentro del JSON cerraría la etiqueta. `json.loads`
    devuelve el texto original, así que para el lector no cambia nada.
    """
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(f'<script type="application/ld+json">{body}</script>')  # noqa: S704


def _context(data: dict[str, Any]) -> dict[str, Any]:
    return {"@context": "https://schema.org", **data}


def site_json_ld(request: Request) -> Markup:
    """`Organization` y `WebSite`, para la landing."""
    home = absolute_url(request, "/")
    organization: dict[str, Any] = {
        "@type": "Organization",
        "name": "Guani",
        "url": home,
        "logo": absolute_url(request, LOGO_PATH),
        "parentOrganization": {"@type": "Organization", "name": "Leira Studio"},
    }
    # Solo las redes que el operador configuró (`SAIMC_LINK_*`): nunca una
    # lista inventada ni vacía.
    social = [link.href for link in build_footer_links(get_settings()).social]
    if social:
        organization["sameAs"] = social
    website = {
        "@type": "WebSite",
        "name": "Guani",
        "url": home,
        "inLanguage": "es",
    }
    return Markup("\n").join(json_ld_tag(_context(data)) for data in (organization, website))


def dataset_json_ld(request: Request) -> Markup:
    """`Dataset` del CSV público, para `/precios`.

    Sin `license`: `/uso-de-datos` escribe la regla en palabras llanas y no
    nombra una licencia (`docs/decisions.md`, 2026-10-05); acá no se inventa
    una. `usageInfo` apunta a esa página.
    """
    dataset = {
        "@type": "Dataset",
        "name": "Precios del mercado informal cubano",
        "description": (
            "Precios de productos por provincia, sacados de anuncios públicos "
            "del mercado informal cubano y verificados anuncio por anuncio. "
            "Cada fila trae su fecha, su moneda y su procedencia."
        ),
        "url": absolute_url(request, HOME_PATH),
        "inLanguage": "es",
        "spatialCoverage": "Cuba",
        "creator": {"@type": "Organization", "name": "Guani", "url": absolute_url(request, "/")},
        "usageInfo": absolute_url(request, "/uso-de-datos"),
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "text/csv",
                "contentUrl": absolute_url(request, "/datos.csv"),
            }
        ],
    }
    return json_ld_tag(_context(dataset))


def product_path(product_name: str) -> str:
    return f"/producto/{quote(product_slug(product_name), safe='-')}"


def breadcrumb_json_ld(request: Request, product_name: str) -> Markup:
    """`BreadcrumbList` Inicio → Precios → producto."""
    trail = (
        ("Inicio", "/"),
        ("Precios", HOME_PATH),
        (product_name, product_path(product_name)),
    )
    items = [
        {"@type": "ListItem", "position": i, "name": name, "item": absolute_url(request, path)}
        for i, (name, path) in enumerate(trail, start=1)
    ]
    return json_ld_tag(_context({"@type": "BreadcrumbList", "itemListElement": items}))


def robots_txt(request: Request) -> str:
    lines = ["User-agent: *", "Allow: /", *(f"Disallow: {path}" for path in ROBOTS_DISALLOW)]
    lines += ["", f"Sitemap: {absolute_url(request, '/sitemap.xml')}", ""]
    return "\n".join(lines)


def sitemap_paths(product_names: Sequence[str]) -> list[str]:
    """Las rutas indexables: landing, precios, textos, contacto y un producto
    piloto por URL. Nunca `/anuncio/<id>`."""
    paths = ["/", HOME_PATH, *(page.path for page in SITE_PAGES)]
    if build_footer_links(get_settings()).any:
        paths.append(CONTACT_PATH)
    paths += [product_path(name) for name in product_names]
    return paths


def sitemap_xml(request: Request, product_names: Sequence[str]) -> str:
    entries = "".join(
        f"<url><loc>{escape(absolute_url(request, path))}</loc></url>"
        for path in sitemap_paths(product_names)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{SITEMAP_NAMESPACE}">{entries}</urlset>'
