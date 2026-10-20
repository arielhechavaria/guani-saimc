"""Opciones de F1 sobre las rutas sin estado (`/marketing/preview`,
`/marketing/card`): bloques, textos y JPEG. La página del editor y su vista
parcial pasaron a `/marketing/post/<id>` (F2, `test_marketing_post_router.py`).

Reusa los fixtures de `test_marketing_router.py` (arroz en La Habana y
Santiago, pollo fuera del foco).
"""

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_marketing_router import client, datos, sin_chromium  # noqa: F401


def _sin_estilos(html: str) -> str:
    """La fuente viaja en base64 dentro de `<style>`: ahí «77» o «999» aparecen
    por azar. Se quitan también los data URI de imagen por si alguna tarjeta
    vuelve a embeber una."""
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "", html)


class TestOpcionesEnLaVistaPrevia:
    def test_sin_parametros_estan_todos_los_bloques(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing/preview/dato-del-dia", params={"product": "Arroz"}).text

        for bloque in ("titulo", "rango", "barra", "mediana", "comparacion", "muestra", "fuente"):
            assert f'data-block="{bloque}"' in html, bloque

    def test_apagar_un_bloque_lo_saca_del_html_y_deja_los_demas(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get(
            "/marketing/preview/dato-del-dia", params={"product": "Arroz", "b_comparacion": "0"}
        ).text

        assert 'data-block="comparacion"' not in html
        assert 'data-block="barra"' in html

    def test_un_bloque_sin_interruptor_sigue_aunque_lo_pidan_apagado(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get(
            "/marketing/preview/dato-del-dia",
            params={"product": "Arroz", "b_titulo": "0", "b_rango": "0"},
        ).text

        assert 'data-block="titulo"' in html and 'data-block="rango"' in html
        assert "300" in html and "350" in html

    def test_ningun_parametro_cambia_una_cifra(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        base = client.get("/marketing/preview/dato-del-dia", params={"product": "Arroz"}).text
        raro = client.get(
            "/marketing/preview/dato-del-dia",
            params={
                "product": "Arroz", "b_barra": "0", "subtitle": "hola", "cta": "chau",
                "median_text": "999", "range_text": "1 – 2", "count": "77", "price": "5",
            },
        ).text

        base, raro = _sin_estilos(base), _sin_estilos(raro)
        for cifra in ("300 – 350", "mediana 320", "400"):
            assert cifra in base and cifra in raro, cifra
        assert "999" not in raro and "77" not in raro

    def test_subtitulo_y_llamada_se_pintan_escapados_y_con_tope(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get(
            "/marketing/preview/dato-del-dia",
            params={"product": "Arroz", "subtitle": "<b>hola</b>", "cta": "z" * 500},
        ).text

        assert "<b>hola</b>" not in html and "&lt;b&gt;hola&lt;/b&gt;" in html
        assert "z" * 61 not in html and "z" * 60 in html

    def test_sin_textos_no_deja_su_hueco(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing/preview/dato-del-dia", params={"product": "Arroz"}).text

        assert 'data-block="subtitulo"' not in html and 'data-block="cta"' not in html

    @pytest.mark.parametrize(
        ("slug", "params", "apagado"),
        [
            ("como-se-movio", {"product": "Arroz", "b_grafico": "0"}, "grafico"),
            (
                "de-donde-sale",
                {"product": "Arroz", "province": "La Habana", "b_anuncios": "0"},
                "anuncios",
            ),
        ],
    )
    def test_las_otras_dos_tarjetas_tambien_apagan_bloques(
        self, client: TestClient, datos: None, slug: str, params: dict, apagado: str  # noqa: F811
    ) -> None:
        html = client.get(f"/marketing/preview/{slug}", params=params).text

        assert f'data-block="{apagado}"' not in html
        assert 'data-block="titulo"' in html


class TestDescargaJPEG:
    def test_sin_chromium_degrada_a_503(
        self, client: TestClient, datos: None, sin_chromium: None  # noqa: F811
    ) -> None:
        respuesta = client.get("/marketing/card/dato-del-dia.jpg", params={"product": "Arroz"})

        assert respuesta.status_code == 503
        assert "playwright" in respuesta.text.lower()

    def test_producto_fuera_de_foco_da_404(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        respuesta = client.get("/marketing/card/dato-del-dia.jpg", params={"product": "Pollo"})

        assert respuesta.status_code == 404

    def test_entrega_un_jpeg_con_las_opciones_aplicadas(
        self,
        client: TestClient,  # noqa: F811
        datos: None,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vistos: dict = {}

        def _falso(html: str, *, width: int, height: int) -> bytes:
            vistos.update(html=html, width=width, height=height)
            return b"\xff\xd8\xff-jpeg"

        monkeypatch.setattr("saimc.web.routers.marketing.render_jpeg", _falso)

        respuesta = client.get(
            "/marketing/card/dato-del-dia.jpg", params={"product": "Arroz", "b_barra": "0"}
        )

        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "image/jpeg"
        assert respuesta.content.startswith(b"\xff\xd8")
        assert 'filename="guani-dato-del-dia-arroz.jpg"' in respuesta.headers["content-disposition"]
        assert (vistos["width"], vistos["height"]) == (1080, 1350)
        assert 'data-block="barra"' not in vistos["html"]
