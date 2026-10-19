"""Cabeceras de seguridad en toda respuesta (`docs/decisions.md`, 2026-10-19).

Se prueba lo que recibe el navegador, no el middleware por dentro. El caso
esperado se define acá: ninguna respuesta (ni un 404, ni un 401) sale sin ellas,
y HSTS solo viaja cuando la petición llegó por HTTPS.
"""

from fastapi.testclient import TestClient

from saimc.web.app import app

client = TestClient(app)

# Una ruta que existe, es pública y no toca la base: el robots.txt.
RUTA = "/robots.txt"


def test_toda_respuesta_lleva_las_cabeceras_base() -> None:
    headers = client.get(RUTA).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert "camera=()" in headers["permissions-policy"]
    assert "microphone=()" in headers["permissions-policy"]


def test_tambien_los_errores_las_llevan() -> None:
    # 404 de una ruta inexistente y 401 de una del operador sin credenciales.
    for ruta in ("/no-existe-esta-ruta", "/control"):
        headers = client.get(ruta).headers
        assert headers["x-content-type-options"] == "nosniff", ruta


def test_csp_cierra_framing_externo_y_objetos_sin_tocar_scripts() -> None:
    csp = client.get(RUTA).headers["content-security-policy"]
    # `frame-ancestors 'self'` y no 'none': /marketing incrusta sus propias
    # tarjetas en un <iframe> del mismo origen.
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    # Sin script-src/default-src: htmx y las plantillas usan código en línea y
    # una política estricta rompería el sitio. Es una decisión registrada.
    assert "script-src" not in csp
    assert "default-src" not in csp


def test_hsts_solo_con_https() -> None:
    assert "strict-transport-security" not in client.get(RUTA).headers

    # Detrás del proxy, el esquema llega en X-Forwarded-Proto.
    headers = client.get(RUTA, headers={"x-forwarded-proto": "https"}).headers
    assert headers["strict-transport-security"] == "max-age=31536000"

    https = TestClient(app, base_url="https://testserver")
    assert "strict-transport-security" in https.get(RUTA).headers
