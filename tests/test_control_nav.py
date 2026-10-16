"""Navegación de `/control` por subpáginas: menú, insignias y enlaces viejos."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.web import control_nav
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db
from saimc.web.health_strip import Indicator


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _indicador(key: str, level: str, anchor: str) -> Indicator:
    return Indicator(key=key, label=key, value="x", level=level, anchor=anchor)


class TestComputeBadges:
    def test_un_indicador_roto_cuenta_en_su_pagina_y_en_hoy(self) -> None:
        badges = control_nav.compute_badges([_indicador("rate", "down", "#tasa")], rubber_stamp_alert=False)

        assert badges["mercado"] == control_nav.Badge(count=1, level="down")
        assert badges["hoy"] == control_nav.Badge(count=1, level="down")
        assert "ingesta" not in badges

    def test_ok_y_off_no_cuentan(self) -> None:
        badges = control_nav.compute_badges(
            [_indicador("a", "ok", "#ingesta"), _indicador("b", "off", "#asistente")],
            rubber_stamp_alert=False,
        )

        assert badges == {}

    def test_la_peor_gravedad_manda(self) -> None:
        badges = control_nav.compute_badges(
            [_indicador("a", "warn", "#ingesta"), _indicador("b", "down", "#ingesta")],
            rubber_stamp_alert=False,
        )

        assert badges["ingesta"] == control_nav.Badge(count=2, level="down")

    def test_la_alerta_de_aceptacion_sin_revisar_suma_a_calidad(self) -> None:
        badges = control_nav.compute_badges([], rubber_stamp_alert=True)

        assert badges["calidad"] == control_nav.Badge(count=1, level="warn")
        assert badges["hoy"] == control_nav.Badge(count=1, level="warn")


class TestAnchorUrls:
    @pytest.mark.parametrize(
        ("ancla", "destino"),
        [
            ("#asistente", "/control/fase-0#asistente"),
            ("#rubber-stamp", "/control/calidad#rubber-stamp"),
            ("#ingesta", "/control/ingesta#ingesta"),
            ("#canales", "/control/ingesta#canales"),
            ("#horas", "/control/ingesta#horas"),
            ("#metrica-fase-0", "/control/fase-0#metrica-fase-0"),
            ("#validacion", "/control/calidad#validacion"),
            ("#carril-1", "/control/calidad#carril-1"),
            ("#jev", "/control/calidad#jev"),
            ("#controles", "/control/calidad#controles"),
            ("#tasa", "/control/mercado#tasa"),
        ],
    )
    def test_cada_ancla_vieja_tiene_su_subpagina(self, ancla: str, destino: str) -> None:
        assert control_nav.anchor_urls()[ancla] == destino


class TestSubpaginas:
    @pytest.mark.parametrize("pagina", control_nav.PAGES, ids=lambda p: p.key)
    def test_responde_y_marca_su_entrada_del_menu(
        self, client: TestClient, pagina: control_nav.ControlPage
    ) -> None:
        respuesta = client.get(pagina.url)

        assert respuesta.status_code == 200
        activos = re.findall(r'<a class="m3-ctl-link is-active" href="([^"]+)"', respuesta.text)
        assert activos == [pagina.url]
        assert 'aria-current="page"' in respuesta.text

    @pytest.mark.parametrize("pagina", control_nav.PAGES, ids=lambda p: p.key)
    def test_el_menu_enlaza_a_las_cinco_vistas(
        self, client: TestClient, pagina: control_nav.ControlPage
    ) -> None:
        texto = client.get(pagina.url).text

        for destino in control_nav.PAGES:
            assert f'href="{destino.url}"' in texto

    @pytest.mark.parametrize("pagina", control_nav.PAGES, ids=lambda p: p.key)
    def test_cada_seccion_del_menu_existe_en_su_pagina(
        self, client: TestClient, pagina: control_nav.ControlPage
    ) -> None:
        texto = client.get(pagina.url).text

        # `asistente`, `rubber-stamp` y `canales` solo se dibujan si hay datos que mostrar.
        condicionales = {"asistente", "rubber-stamp", "canales"}
        for seccion in pagina.sections:
            if seccion.anchor in condicionales:
                continue
            assert f'id="{seccion.anchor}"' in texto, seccion.anchor

    def test_hoy_tiene_tarjetas_a_las_otras_cuatro_vistas(self, client: TestClient) -> None:
        texto = client.get("/control").text

        tarjetas = re.findall(r'class="m3-ctl-card-title" href="([^"]+)"', texto)
        assert tarjetas == ["/control/ingesta", "/control/calidad", "/control/fase-0", "/control/mercado"]

    def test_la_franja_enlaza_a_la_subpagina_del_detalle(self, client: TestClient) -> None:
        texto = client.get("/control").text

        assert 'href="/control/mercado#tasa"' in texto
        assert 'class="m3-health-link" href="#tasa"' not in texto


class TestEnlacesViejos:
    @pytest.mark.parametrize(
        ("url", "destino"),
        [
            ("/control?province=Camag%C3%BCey", "/control/calidad?province=Camag%C3%BCey"),
            ("/control?inactivos=1", "/control/ingesta?inactivos=1"),
            ("/control?days=30", "/control/mercado?days=30"),
            ("/control?inactivos=1&days=30", "/control/ingesta?inactivos=1&days=30"),
        ],
    )
    def test_la_query_vieja_va_a_la_subpagina_que_la_usa(
        self, client: TestClient, url: str, destino: str
    ) -> None:
        respuesta = client.get(url, follow_redirects=False)

        assert respuesta.status_code in (302, 307)
        assert respuesta.headers["location"] == destino

    def test_control_pelado_no_redirige(self, client: TestClient) -> None:
        assert client.get("/control", follow_redirects=False).status_code == 200

    def test_los_marcadores_con_ancla_se_resuelven_en_el_navegador(self, client: TestClient) -> None:
        # El `#ancla` no llega al servidor: «Hoy» lleva el mapa de destinos.
        texto = client.get("/control").text

        assert "window.location.replace" in texto
        assert '"#tasa": "/control/mercado#tasa"' in texto
