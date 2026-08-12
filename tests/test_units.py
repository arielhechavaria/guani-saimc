"""`canonical_unit()` — vocabulario cerrado de unidades para almacenamiento.

Casos sintéticos, definidos a mano antes de escribir `src/saimc/units.py`
(CLAUDE.md: en la capa de extracción/validación el caso esperado se escribe
antes que el código). No es el vocabulario de reconocimiento de
`engine.py:_UNIT_WORDS` — ver docstring del módulo bajo prueba.
"""

import pytest

from saimc.units import UNITS, canonical_unit


class TestVocabulario:
    def test_el_vocabulario_es_exactamente_el_acordado(self) -> None:
        """El conjunto se fija acá, escrito a mano, no derivado de `UNITS`.

        El test parametrizado de abajo recorre `UNITS` y por lo tanto no puede
        fallar para ningún contenido de `UNITS`: sacar una unidad pasaría
        inadvertido. `ml` en particular entró por decisión explícita de este
        cambio, aunque el extractor todavía no lo reconozca."""
        assert UNITS == ("lb", "kg", "l", "ml", "unidad", "docena", "caja")


class TestCanonicalUnitFormasCanonicas:
    @pytest.mark.parametrize("valor", list(UNITS))
    def test_cada_forma_canonica_pasa_intacta(self, valor: str) -> None:
        assert canonical_unit(valor) == valor


class TestCanonicalUnitAlias:
    def test_litro_capitalizado_normaliza_a_l(self) -> None:
        assert canonical_unit("Litro") == "l"

    def test_litros_plural_normaliza_a_l(self) -> None:
        assert canonical_unit("litros") == "l"

    def test_cajas_plural_normaliza_a_caja(self) -> None:
        assert canonical_unit("cajas") == "caja"

    def test_lb_con_espacio_y_mayuscula_normaliza(self) -> None:
        assert canonical_unit("LB ") == "lb"


class TestCanonicalUnitFueraDeVocabulario:
    def test_quintal_no_esta_en_el_vocabulario_devuelve_none(self) -> None:
        assert canonical_unit("quintal") is None


class TestCanonicalUnitVacio:
    def test_cadena_vacia_devuelve_none(self) -> None:
        assert canonical_unit("") is None

    def test_none_devuelve_none(self) -> None:
        assert canonical_unit(None) is None
