"""Mapa SVG de las 16 provincias para el panel derecho de «Explorar» (P1,
2026-09-21). Sale de la MISMA geometría que ya sirve el mapa Leaflet
(`static/cuba_provincias.geojson`), proyectada a un `viewBox` fijo: sin tiles,
sin Leaflet y sin una segunda fuente de geometría que se desincronice.

La proyección es equirectangular corregida por la latitud media (el grado de
longitud mide menos que el de latitud), la misma que ya usan los prototipos.
"""

import json
import re
from pathlib import Path

from saimc.web.cuba_svg import build_cuba_map
from saimc.web.product_nav import product_slug

GEOJSON = (
    Path(__file__).resolve().parent.parent / "src" / "saimc" / "web" / "static" / "cuba_provincias.geojson"
)


def _features() -> list[dict]:
    features: list[dict] = json.loads(GEOJSON.read_text(encoding="utf-8"))["features"]
    return features


def _por_slug(mapa):
    return {p.slug: p for p in mapa.provinces}


class TestProvincias:
    def test_estan_las_dieciseis(self) -> None:
        assert len(build_cuba_map().provinces) == 16

    def test_los_slugs_son_los_del_geojson_y_los_de_la_aplicacion(self) -> None:
        """El mapa se cruza con las filas de la tabla por slug: si un nombre no
        coincide, esa provincia queda muda sin que nada avise."""
        mapa = build_cuba_map()

        assert {p.slug for p in mapa.provinces} == {f["properties"]["slug"] for f in _features()}
        assert all(p.slug == product_slug(p.name) for p in mapa.provinces)

    def test_los_nombres_son_los_del_geojson(self) -> None:
        assert {p.name for p in build_cuba_map().provinces} == {f["properties"]["name"] for f in _features()}


class TestGeometria:
    def test_el_ancho_es_el_pedido_y_el_alto_guarda_la_proporcion_de_cuba(self) -> None:
        """Cuba es muy alargada, unos 2,9 a 1: a 340 de ancho, unos 117 de alto."""
        mapa = build_cuba_map(340)

        assert mapa.width == 340.0
        assert 0.30 < mapa.height / mapa.width < 0.40

    def test_cada_trazo_es_un_camino_cerrado_dentro_del_lienzo(self) -> None:
        mapa = build_cuba_map(340)

        for p in mapa.provinces:
            assert p.d.startswith("M") and p.d.endswith("Z"), p.slug
            for x, y in re.findall(r"(-?\d+\.\d),(-?\d+\.\d)", p.d):
                assert -0.5 <= float(x) <= mapa.width + 0.5, p.slug
                assert -0.5 <= float(y) <= mapa.height + 0.5, p.slug

    def test_el_oeste_queda_a_la_izquierda_y_el_norte_arriba(self) -> None:
        por = _por_slug(build_cuba_map(340))

        assert por["pinar-del-rio"].cx < por["guantanamo"].cx
        assert por["la-habana"].cy < por["isla-de-la-juventud"].cy

    def test_el_centro_de_cada_provincia_cae_dentro_del_lienzo(self) -> None:
        mapa = build_cuba_map(340)

        for p in mapa.provinces:
            assert 0 <= p.cx <= mapa.width and 0 <= p.cy <= mapa.height, p.slug

    def test_escalar_el_ancho_escala_todo(self) -> None:
        chico, grande = _por_slug(build_cuba_map(340)), _por_slug(build_cuba_map(680))

        assert abs(grande["camaguey"].cx - 2 * chico["camaguey"].cx) < 0.3
        assert abs(grande["camaguey"].cy - 2 * chico["camaguey"].cy) < 0.3

    def test_se_calcula_una_sola_vez_por_ancho(self) -> None:
        assert build_cuba_map(340) is build_cuba_map(340)
