"""Editor de `/marketing`: bloques, textos, vista parcial y JPEG (F1 de
`docs/plan-fabrica-marketing.md`).

Reusa los fixtures de `test_marketing_router.py` (arroz en La Habana y
Santiago, pollo fuera del foco).
"""

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_marketing_router import client, datos, sin_chromium  # noqa: F401


def _sin_estilos(html: str) -> str:
    """La fuente viaja en base64 dentro de `<style>`: ahí «77» o «999» aparecen por azar."""
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


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


class TestPaginaEditor:
    def test_ya_no_tiene_la_seccion_de_instagram(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Arroz"}).text

        assert 'id="instagram"' not in html
        assert "Seguidores en el tiempo" not in html

    def test_un_interruptor_por_bloque_con_interruptor_y_ninguno_por_los_fijos(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Arroz"}).text

        assert 'name="b_barra"' in html and 'name="b_comparacion"' in html
        assert 'name="b_titulo"' not in html and 'name="b_rango"' not in html
        assert 'name="subtitle"' in html and 'name="cta"' in html

    def test_el_formulario_refresca_la_vista_con_htmx(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Arroz"}).text

        assert 'hx-get="/marketing/vista"' in html
        assert "/static/htmx.min.js" in html

    def test_trae_el_texto_del_post_con_los_botones(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Arroz"}).text

        assert "<textarea" in html and "#guani" in html
        assert "Copiar texto" in html and "Compartir" in html
        assert "/marketing/card/dato-del-dia.jpg?" in html

    def test_las_opciones_viajan_a_la_vista_previa_y_a_la_imagen(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        html = client.get(
            "/marketing",
            params={"kind": "dato-del-dia", "product": "Arroz", "b_barra": "0", "cta": "Entrá"},
        ).text

        assert "b_barra=0" in html and "cta=Entr" in html


class TestVistaParcial:
    def test_devuelve_solo_la_vista_y_actualiza_la_url_de_la_pagina(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        respuesta = client.get(
            "/marketing/vista",
            params={"kind": "dato-del-dia", "product": "Arroz", "b_barra": "0"},
        )

        assert respuesta.status_code == 200
        assert "<html" not in respuesta.text
        assert "/marketing/preview/dato-del-dia?" in respuesta.text
        assert "b_barra=0" in respuesta.text
        assert respuesta.headers["HX-Replace-Url"].startswith("/marketing?")
        assert "b_barra=0" in respuesta.headers["HX-Replace-Url"]

    def test_producto_fuera_del_foco_avisa_en_vez_de_armar_la_tarjeta(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        respuesta = client.get(
            "/marketing/vista", params={"kind": "dato-del-dia", "product": "Pollo"}
        )

        assert respuesta.status_code == 200
        assert "no está en el foco" in respuesta.text


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
