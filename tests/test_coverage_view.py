"""Mosaico de cobertura del tablero (prototipo aprobado, 2026-09-26): las 16
provincias como casillas de un cartograma, llenas las que tienen dato y
punteadas las que siguen en recolección.

Posiciones escritas a mano, antes del código: el cartograma recorre Cuba de
oeste a este en dos filas. Fila 0: Pinar del Río, Artemisa, La Habana,
Mayabeque, Matanzas, Villa Clara, Sancti Spíritus, Ciego de Ávila, Camagüey,
Las Tunas y Holguín (columnas 0 a 10). Fila 1: Isla de la Juventud (1),
Cienfuegos (5), Granma (9), Santiago de Cuba (10) y Guantánamo (11).
"""

import pytest

from saimc.web.coverage_view import build_coverage
from saimc.web.map_view import MapProvince

NOMBRES = [
    "Pinar del Río", "Artemisa", "La Habana", "Mayabeque", "Matanzas", "Villa Clara",
    "Sancti Spíritus", "Ciego de Ávila", "Camagüey", "Las Tunas", "Holguín", "Cienfuegos",
    "Granma", "Santiago de Cuba", "Guantánamo", "Isla de la Juventud",
]  # fmt: skip


def _provincias(con_dato: dict[str, int] | None = None, publicadas: set[str] | None = None):
    con_dato = con_dato or {}
    publicadas = publicadas or set()
    return tuple(
        MapProvince(name=n, rows=con_dato.get(n, 0), is_published=n in publicadas) for n in NOMBRES
    )


def _casilla(mosaico, nombre: str):
    return next(c for c in mosaico if c.name == nombre)


class TestPosiciones:
    @pytest.mark.parametrize(
        ("nombre", "abrev", "col", "fila"),
        [
            ("Pinar del Río", "PRI", 0, 0),
            ("La Habana", "HAB", 2, 0),
            ("Holguín", "HOL", 10, 0),
            ("Isla de la Juventud", "IJV", 1, 1),
            ("Cienfuegos", "CFG", 5, 1),
            ("Santiago de Cuba", "SCU", 10, 1),
            ("Guantánamo", "GTM", 11, 1),
        ],
    )
    def test_cada_provincia_cae_en_su_casilla(self, nombre, abrev, col, fila) -> None:
        casilla = _casilla(build_coverage(_provincias(), {}), nombre)

        assert (casilla.abbr, casilla.col, casilla.row) == (abrev, col, fila)

    def test_son_dieciseis_sin_dos_en_la_misma_casilla(self) -> None:
        mosaico = build_coverage(_provincias(), {})

        assert len(mosaico) == 16
        assert len({(c.col, c.row) for c in mosaico}) == 16

    def test_van_de_oeste_a_este_fila_por_fila(self) -> None:
        mosaico = build_coverage(_provincias(), {})

        assert [(c.row, c.col) for c in mosaico] == sorted((c.row, c.col) for c in mosaico)


class TestEstado:
    def test_el_estado_es_el_del_mapa(self) -> None:
        mosaico = build_coverage(
            _provincias({"La Habana": 40, "Granma": 3}, publicadas={"Granma"}), {}
        )

        assert _casilla(mosaico, "La Habana").state == "en-ingesta"
        assert _casilla(mosaico, "Granma").state == "publicada"
        assert _casilla(mosaico, "Matanzas").state == "sin-datos"

    def test_la_descripcion_dice_provincia_y_estado(self) -> None:
        mosaico = build_coverage(_provincias({"La Habana": 40}), {})

        assert _casilla(mosaico, "La Habana").label == "La Habana: Recogiendo datos, todavía sin publicar"
        assert _casilla(mosaico, "Matanzas").label == "Matanzas: Sin datos todavía"

    def test_el_color_es_el_de_su_provincia_en_el_resto_de_la_pagina(self) -> None:
        """La Habana y Santiago llevan en el tablero un tono (slot 0 y 1): la
        casilla usa el mismo. Una provincia sin slot cae al neutro (9)."""
        mosaico = build_coverage(
            _provincias({"La Habana": 40, "Santiago de Cuba": 20}),
            {"La Habana": 0, "Santiago de Cuba": 1},
        )

        assert _casilla(mosaico, "La Habana").slot == 0
        assert _casilla(mosaico, "Santiago de Cuba").slot == 1
        assert _casilla(mosaico, "Matanzas").slot == 9


class TestNombresDesconocidos:
    def test_una_provincia_que_no_esta_en_el_cartograma_va_a_una_fila_propia(self) -> None:
        """Si el catálogo trajera un nombre nuevo, aparece igual y no pisa a
        ninguna: fila 2, de izquierda a derecha, con las tres primeras letras."""
        extra = (
            *_provincias(),
            MapProvince(name="Narnia", rows=5, is_published=False),
            MapProvince(name="Utopía", rows=0, is_published=False),
        )

        mosaico = build_coverage(extra, {})

        assert (_casilla(mosaico, "Narnia").row, _casilla(mosaico, "Narnia").col) == (2, 0)
        assert (_casilla(mosaico, "Utopía").row, _casilla(mosaico, "Utopía").col) == (2, 1)
        assert _casilla(mosaico, "Narnia").abbr == "NAR"
        assert len({(c.col, c.row) for c in mosaico}) == 18

    def test_un_nombre_con_otra_ortografia_encuentra_su_casilla(self) -> None:
        """Se empareja por slug, como el resto del sitio: sin tildes ni
        mayúsculas."""
        mosaico = build_coverage(
            (MapProvince(name="CAMAGUEY", rows=1, is_published=False),), {}
        )

        assert (mosaico[0].col, mosaico[0].row, mosaico[0].abbr) == (8, 0, "CMG")
