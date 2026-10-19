"""Ocultar provincias en las tarjetas con una fila por provincia («Dato del
día» y «Cómo se movió»), y la URL del sitio en las piezas.

Es presentación: las cifras de las demás provincias no cambian. Los casos
esperados se escribieron a mano: de las dos provincias del foco, ocultar una
deja la otra con sus mismas cifras; ocultar las dos se ignora.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, QueryParams

from saimc.marketing.context import BY_SLUG, SITE_URL
from saimc.marketing.options import (
    CardOptions,
    options_from_dict,
    options_to_dict,
    parse_options,
)
from saimc.marketing.posts import approve, create_draft, save_draft, slide_html
from saimc.models.marketing_post import PostStatus
from tests.test_marketing_post_router import HOY, _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client, datos  # noqa: F401

DATO = BY_SLUG["dato-del-dia"]
MOVIO = BY_SLUG["como-se-movio"]
LIBRE = BY_SLUG["libre"]
HABANA, SANTIAGO = "La Habana", "Santiago de Cuba"


def _hide(*nombres: str) -> FormData:
    return FormData([("hide", n) for n in nombres])


def _quitar_datos_binarios(html: str) -> str:
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"data:[a-z/0-9+-]+;base64,[A-Za-z0-9+/=]+", "", html)


class TestOpciones:
    def test_ocultar_una_provincia_se_lee_de_los_parametros_repetidos(self) -> None:
        for tarjeta in (DATO, MOVIO):
            assert parse_options(tarjeta, _hide(SANTIAGO)).hidden_provinces == (SANTIAGO,)

    def test_las_desconocidas_se_descartan_y_no_se_repite(self) -> None:
        opciones = parse_options(DATO, _hide("Narnia", SANTIAGO, SANTIAGO, ""))

        assert opciones.hidden_provinces == (SANTIAGO,)

    def test_siempre_queda_una_visible_si_piden_ocultarlas_todas(self) -> None:
        opciones = parse_options(DATO, _hide(HABANA, SANTIAGO))

        assert opciones.hidden_provinces == ()

    def test_una_tarjeta_sin_filas_por_provincia_no_oculta_nada(self) -> None:
        assert parse_options(LIBRE, _hide(HABANA)).hidden_provinces == ()
        assert parse_options(BY_SLUG["de-donde-sale"], _hide(HABANA)).hidden_provinces == ()

    def test_sin_parametros_no_hay_ocultas_ni_con_un_dict_plano(self) -> None:
        assert parse_options(DATO, {}).hidden_provinces == ()
        assert parse_options(DATO, {"hide": SANTIAGO}).hidden_provinces == ()  # sin getlist

    def test_viaja_en_la_url_y_en_lo_guardado_ida_y_vuelta(self) -> None:
        opciones = parse_options(DATO, _hide(SANTIAGO))

        assert ("hide", SANTIAGO) in opciones.query_items()
        assert parse_options(DATO, QueryParams(opciones.query_items())) == opciones
        guardado = options_to_dict(opciones)
        assert guardado["hide"] == [SANTIAGO]
        assert options_from_dict(DATO, guardado) == opciones

    def test_sin_ocultas_no_aparece_en_lo_guardado(self) -> None:
        assert "hide" not in options_to_dict(CardOptions(show={}))

    def test_lo_guardado_pasa_otra_vez_por_la_validacion(self) -> None:
        guardado = {"show": {}, "hide": ["Narnia", HABANA, SANTIAGO]}

        assert options_from_dict(DATO, guardado).hidden_provinces == ()


class TestVistaPrevia:
    @pytest.mark.parametrize("slug", ["dato-del-dia", "como-se-movio"])
    def test_la_provincia_oculta_no_se_pinta_y_la_otra_queda_igual(
        self, client: TestClient, datos: None, slug: str  # noqa: F811
    ) -> None:
        completa = client.get(f"/marketing/preview/{slug}", params={"product": "Arroz"}).text
        sin_santiago = client.get(
            f"/marketing/preview/{slug}", params={"product": "Arroz", "hide": SANTIAGO}
        ).text

        assert SANTIAGO in completa and HABANA in completa
        assert SANTIAGO not in sin_santiago
        assert HABANA in sin_santiago

    def test_dato_del_dia_conserva_las_cifras_de_la_provincia_visible(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        completa = client.get("/marketing/preview/dato-del-dia", params={"product": "Arroz"}).text
        sin_santiago = client.get(
            "/marketing/preview/dato-del-dia", params={"product": "Arroz", "hide": SANTIAGO}
        ).text

        # Habana: 300 – 350 (mediana 320); «mediana más baja» no se recalcula.
        for texto in ("300 – 350", "mediana 320"):
            assert texto in completa and texto in sin_santiago
        assert "mediana más baja" in completa and "mediana más baja" in sin_santiago
        assert completa.count('class="mc-panel"') - sin_santiago.count('class="mc-panel"') == 1

    def test_ocultar_la_mas_barata_no_pasa_el_chip_a_otra_provincia(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        """«Mediana más baja» es una cifra: no se recalcula sobre las visibles."""
        sin_habana = client.get(
            "/marketing/preview/dato-del-dia", params={"product": "Arroz", "hide": HABANA}
        ).text

        assert HABANA not in sin_habana and SANTIAGO in sin_habana
        assert "mediana más baja" not in sin_habana

    def test_como_se_movio_saca_la_linea_la_leyenda_y_el_panel(
        self, client: TestClient, datos: None  # noqa: F811
    ) -> None:
        completa = client.get(
            "/marketing/preview/como-se-movio", params={"product": "Arroz"}
        ).text
        sin_santiago = client.get(
            "/marketing/preview/como-se-movio", params={"product": "Arroz", "hide": SANTIAGO}
        ).text

        # Cada provincia con línea dibuja una recta y dos puntos en el gráfico.
        assert completa.count("<circle") - sin_santiago.count("<circle") in (0, 2)
        assert SANTIAGO not in sin_santiago


class TestUrlDelSitio:
    def test_la_url_de_las_piezas_es_guani_app(self) -> None:
        assert SITE_URL == "guani.app"

    @pytest.mark.parametrize("slug", ["dato-del-dia", "como-se-movio"])
    def test_las_tarjetas_la_imprimen_y_no_queda_el_texto_viejo(
        self, client: TestClient, datos: None, slug: str  # noqa: F811
    ) -> None:
        html = client.get(f"/marketing/preview/{slug}", params={"product": "Arroz"}).text

        assert '<div class="mc-url">guani.app</div>' in html
        assert "próximamente" not in html


class TestAprobacion:
    def test_aprobar_congela_lo_oculto_y_la_imagen_sale_de_lo_mismo_que_la_vista(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        post = create_draft(
            session, card_slug="dato-del-dia", planned_for=HOY, product="Arroz",
            province=None, data_through=HOY, options=parse_options(DATO, _hide(SANTIAGO)),
        )
        session.commit()
        vista = slide_html(session, post.slides[0])
        visto: list[str] = []

        def _captura(html: str, *, width: int, height: int) -> bytes:
            visto.append(html)
            return b"JPEG falso"

        approve(session, post, images_dir=tmp_path / "posts", render=_captura)

        assert post.status == PostStatus.APPROVED
        assert post.slides[0].snapshot["options"]["hide"] == [SANTIAGO]
        assert SANTIAGO not in visto[0] and HABANA in visto[0]
        assert vista is not None and SANTIAGO not in vista
        # Aprobada, la vista sale del snapshot y sigue sin la provincia.
        aprobada = slide_html(session, post.slides[0])
        assert aprobada is not None and SANTIAGO not in aprobada
        assert _quitar_datos_binarios(aprobada) == _quitar_datos_binarios(visto[0])

    def test_guardar_en_el_editor_persiste_y_mostrar_otra_vez_las_deja_todas(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = create_draft(
            session, card_slug="dato-del-dia", planned_for=HOY, product="Arroz",
            province=None, data_through=HOY,
        )
        session.commit()
        base = {"product": "Arroz", "data_through": HOY.isoformat(), "position": "1"}

        client.post(f"/marketing/post/{post.id}", data={**base, "hide": SANTIAGO})
        session.expire_all()
        assert post.slides[0].options["hide"] == [SANTIAGO]

        editor = client.get(f"/marketing/post/{post.id}").text
        assert f'name="hide" value="{SANTIAGO}" checked' in editor
        assert f'name="hide" value="{HABANA}"' in editor

        client.post(f"/marketing/post/{post.id}", data=base)  # ninguna casilla marcada
        session.expire_all()
        assert "hide" not in post.slides[0].options

    def test_guardar_con_todas_ocultas_se_ignora(
        self, session: Session, datos: None  # noqa: F811
    ) -> None:
        post = create_draft(
            session, card_slug="dato-del-dia", planned_for=HOY, product="Arroz",
            province=None, data_through=HOY,
        )
        save_draft(
            session, post, product="Arroz", province=None, data_through=HOY,
            options=parse_options(DATO, _hide(HABANA, SANTIAGO)), caption=None,
        )

        assert "hide" not in post.slides[0].options
