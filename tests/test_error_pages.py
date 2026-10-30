"""Página 404 de la capa pública (2026-10-30).

Un navegador recibe la página con marca; lo que no es un navegador conserva el
JSON de siempre; y el resto de los errores no cambia.
"""

from fastapi.testclient import TestClient

from saimc.web.app import app

HTML = {"accept": "text/html,application/xhtml+xml"}


def test_navegador_recibe_la_pagina_con_marca() -> None:
    r = TestClient(app).get("/esto-no-existe", headers=HTML)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Página no encontrada" in r.text
    assert 'href="/"' in r.text
    assert "noindex" in r.text


def test_sin_accept_html_conserva_el_json() -> None:
    r = TestClient(app).get("/esto-no-existe", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_htmx_no_recibe_una_pagina_completa() -> None:
    r = TestClient(app).get("/esto-no-existe", headers={**HTML, "hx-request": "true"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_la_animacion_respeta_reduced_motion_y_no_usa_js() -> None:
    r = TestClient(app).get("/esto-no-existe", headers=HTML)
    assert "prefers-reduced-motion: reduce" in r.text
    assert "<script" not in r.text.split("</head>")[1].split("</body>")[0].replace("theme-toggle", "")
