"""`render_jpeg`: la misma tarjeta que el PNG, en JPEG sRGB (sin Chromium real)."""

import sys
import types

import pytest

from saimc.marketing.render import render_jpeg


@pytest.fixture
def playwright_falso(monkeypatch: pytest.MonkeyPatch) -> dict:
    visto: dict = {}

    class _Page:
        def set_content(self, html: str, wait_until: str) -> None:
            visto["html"] = html

        def screenshot(self, **kwargs: object) -> bytes:
            visto["screenshot"] = kwargs
            return b"jpeg"

    class _Browser:
        def new_page(self, **kwargs: object) -> _Page:
            visto["viewport"] = kwargs["viewport"]
            return _Page()

        def close(self) -> None:
            visto["cerrado"] = True

    class _Chromium:
        def launch(self, **kwargs: object) -> _Browser:
            visto["launch"] = kwargs
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

        def __enter__(self) -> "_Playwright":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = _Playwright  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    return visto


def test_saca_un_jpeg_con_calidad_y_perfil_srgb(playwright_falso: dict) -> None:
    resultado = render_jpeg("<p>x</p>", width=1080, height=1350)

    assert resultado == b"jpeg"
    assert playwright_falso["screenshot"]["type"] == "jpeg"
    assert 0 < playwright_falso["screenshot"]["quality"] <= 100
    assert "--force-color-profile=srgb" in playwright_falso["launch"]["args"]
    assert playwright_falso["viewport"] == {"width": 1080, "height": 1350}
    assert playwright_falso["cerrado"] is True
