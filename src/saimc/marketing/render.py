"""Convierte el HTML de una tarjeta en un PNG (slice de marketing).

`playwright` es opcional (`pyproject.toml`, extra `marketing`): sin él —o
sin el Chromium que necesita— la vista previa en HTML de `/marketing` sigue
andando, solo no se puede descargar el PNG. Mismo patrón que el import
perezoso de `anthropic` en `llm_propose_ad.py`.
"""

from __future__ import annotations


class RendererUnavailable(RuntimeError):
    """Playwright, o el Chromium que necesita, no está instalado."""


_INSTALL_HINT = (
    'Instalá el paquete con `.venv/bin/pip install -e ".[marketing]"`, '
    "después `.venv/bin/playwright install chromium` (baja el navegador, sin "
    "sudo) y `sudo .venv/bin/playwright install-deps chromium` (librerías de "
    "sistema, con sudo)."
)


def render_png(html: str, *, width: int, height: int) -> bytes:
    """Renderiza `html` en una página de `width`×`height` y devuelve el PNG.

    Un Chromium sin cabeza por corrida: en el volumen de esta herramienta
    (unas pocas tarjetas por semana, decisión cerrada 3 — sin cola de
    trabajos) no se justifica mantener un navegador vivo entre pedidos.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RendererUnavailable(f"Falta el paquete playwright. {_INSTALL_HINT}") from error

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:
            raise RendererUnavailable(f"Chromium no arrancó. {_INSTALL_HINT}") from error
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="networkidle")
            return page.screenshot()
        finally:
            browser.close()
