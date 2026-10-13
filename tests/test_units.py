"""`canonical_unit()` — vocabulario cerrado de unidades para almacenamiento.

Casos sintéticos, definidos a mano antes de escribir `src/saimc/units.py`
(CLAUDE.md: en la capa de extracción/validación el caso esperado se escribe
antes que el código). No es el vocabulario de reconocimiento de
`engine.py:_UNIT_WORDS` — ver docstring del módulo bajo prueba.
"""

from decimal import Decimal

import pytest

from saimc.units import (
    PACKAGING,
    UNIT_NOUNS,
    UNITS,
    canonical_packaging,
    canonical_unit,
    convert_price_per_unit,
    convert_quantity,
    unit_noun,
)


class TestVocabulario:
    def test_el_vocabulario_es_exactamente_el_acordado(self) -> None:
        """El conjunto se fija acá, escrito a mano, no derivado de `UNITS`.

        El test parametrizado de abajo recorre `UNITS` y por lo tanto no puede
        fallar para ningún contenido de `UNITS`: sacar una unidad pasaría
        inadvertido. `ml` en particular entró por decisión explícita de este
        cambio, aunque el extractor todavía no lo reconozca."""
        assert UNITS == ("lb", "kg", "g", "l", "ml", "unidad", "docena", "caja")


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


class TestConvertPricePerUnitMasa:
    """Valores calculados a mano desde la definición: la libra internacional
    son 0,45359237 kg **exactos** por acuerdo de 1959. No es un factor
    estimado ni un umbral de los que §12.6 manda calibrar.

    Se eligen precios que hacen la cuenta verificable de cabeza: si el kilo
    cuesta 1000, la libra cuesta lo que la libra pesa en gramos.
    """

    def test_de_kilo_a_libra(self) -> None:
        assert convert_price_per_unit(Decimal("1000"), frm="kg", to="lb") == Decimal(
            "453.59237"
        )

    def test_de_libra_a_kilo(self) -> None:
        assert convert_price_per_unit(
            Decimal("453.59237"), frm="lb", to="kg"
        ) == Decimal("1000")

    def test_ida_y_vuelta_devuelve_el_valor_original(self) -> None:
        ida = convert_price_per_unit(Decimal("750"), frm="kg", to="lb")
        assert ida is not None
        assert convert_price_per_unit(ida, frm="lb", to="kg") == Decimal("750")


class TestConvertPricePerUnitVolumen:
    def test_de_litro_a_mililitro(self) -> None:
        assert convert_price_per_unit(Decimal("1000"), frm="l", to="ml") == Decimal("1")

    def test_de_mililitro_a_litro(self) -> None:
        assert convert_price_per_unit(Decimal("1"), frm="ml", to="l") == Decimal("1000")


class TestConvertPricePerUnitIdentidad:
    @pytest.mark.parametrize("valor", list(UNITS))
    def test_la_misma_unidad_devuelve_el_precio_intacto(self, valor: str) -> None:
        """Vale incluso para las unidades sin magnitud (`caja`, `unidad`,
        `docena`): convertir algo a sí mismo no necesita saber cuánto pesa."""
        assert convert_price_per_unit(Decimal("123.45"), frm=valor, to=valor) == Decimal(
            "123.45"
        )


class TestConvertPricePerUnitNoConvertible:
    """Nunca inventa, misma convención que `canonical_unit()`."""

    def test_masa_a_volumen_devuelve_none(self) -> None:
        """Un kilo a litros exigiría la densidad del producto, que no tenemos."""
        assert convert_price_per_unit(Decimal("750"), frm="kg", to="l") is None

    def test_conteo_a_masa_devuelve_none(self) -> None:
        """`caja` no dice cuánto pesa la caja."""
        assert convert_price_per_unit(Decimal("750"), frm="caja", to="lb") is None

    def test_docena_a_unidad_no_convierte_todavia(self) -> None:
        """Doce unidades por docena es una definición cierta, pero ninguna fila
        del corpus usa esas unidades: agregarla sería vocabulario sin dato que
        lo respalde. Queda deliberadamente afuera."""
        assert convert_price_per_unit(Decimal("120"), frm="docena", to="unidad") is None

    def test_unidad_fuera_del_vocabulario_devuelve_none(self) -> None:
        assert convert_price_per_unit(Decimal("750"), frm="quintal", to="lb") is None

    def test_unidad_origen_none_devuelve_none(self) -> None:
        assert convert_price_per_unit(Decimal("750"), frm=None, to="lb") is None

    def test_unidad_destino_none_devuelve_none(self) -> None:
        assert convert_price_per_unit(Decimal("750"), frm="kg", to=None) is None


class TestPackagingVocabulario:
    def test_el_vocabulario_es_exactamente_el_acordado(self) -> None:
        """Fijado a mano, no derivado de `PACKAGING`. `contenedor` y `paca`
        quedan fuera a propósito: son vasijas de transporte de contenido
        variable, no presentaciones (design.md, D3)."""
        assert PACKAGING == ("paquete", "saco", "caja", "botella", "pomo", "a granel")


class TestCanonicalPackagingFormasCanonicas:
    @pytest.mark.parametrize("valor", list(PACKAGING))
    def test_cada_forma_canonica_pasa_intacta(self, valor: str) -> None:
        assert canonical_packaging(valor) == valor


class TestCanonicalPackagingNormalizacion:
    def test_caja_capitalizada_normaliza(self) -> None:
        assert canonical_packaging("Caja") == "caja"

    def test_caja_mayuscula_con_espacio_normaliza(self) -> None:
        assert canonical_packaging("CAJA ") == "caja"

    def test_a_granel_capitalizado_normaliza(self) -> None:
        assert canonical_packaging("A granel") == "a granel"


class TestCanonicalPackagingFueraDeVocabulario:
    def test_contenedor_no_esta_en_el_vocabulario_devuelve_none(self) -> None:
        assert canonical_packaging("contenedor") is None

    def test_contenedor_capitalizado_tambien_devuelve_none(self) -> None:
        assert canonical_packaging("Contenedor") is None

    def test_paca_no_esta_en_el_vocabulario_devuelve_none(self) -> None:
        assert canonical_packaging("paca") is None

    def test_texto_arbitrario_devuelve_none(self) -> None:
        assert canonical_packaging("Arroz importado a 320 la libra") is None


class TestCanonicalPackagingVacio:
    def test_cadena_vacia_devuelve_none(self) -> None:
        assert canonical_packaging("") is None

    def test_none_devuelve_none(self) -> None:
        assert canonical_packaging(None) is None


class TestConvertQuantity:
    """Convierte una CANTIDAD, no un precio: el factor va al derecho, al revés
    que `convert_price_per_unit`. Diez kilos son MÁS libras (22,05), mientras
    que un precio por kilo es MENOS por libra."""

    def test_de_kilo_a_libra_da_mas_libras(self) -> None:
        # 10 kg = 10 * 1000 g / 453,59237 g = 22,046...
        resultado = convert_quantity(Decimal("10"), frm="kg", to="lb")
        assert resultado is not None
        assert resultado.quantize(Decimal("0.001")) == Decimal("22.046")

    def test_de_libra_a_kilo_da_menos_kilos(self) -> None:
        resultado = convert_quantity(Decimal("40"), frm="lb", to="kg")
        assert resultado is not None
        assert resultado.quantize(Decimal("0.001")) == Decimal("18.144")

    def test_es_el_inverso_de_convert_price_per_unit(self) -> None:
        """La relación que hace fácil equivocarse: el precio va con el factor
        invertido respecto de la cantidad."""
        precio = convert_price_per_unit(Decimal("1000"), frm="kg", to="lb")
        cantidad = convert_quantity(Decimal("1"), frm="kg", to="lb")
        assert precio is not None and cantidad is not None
        # Precio por kilo x cantidad de kilos == precio por libra x cantidad de libras.
        assert (Decimal("1000") * Decimal("1")).quantize(Decimal("0.01")) == (
            precio * cantidad
        ).quantize(Decimal("0.01"))

    def test_de_litro_a_mililitro(self) -> None:
        assert convert_quantity(Decimal("1"), frm="l", to="ml") == Decimal("1000")

    @pytest.mark.parametrize("valor", list(UNITS))
    def test_la_misma_unidad_devuelve_la_cantidad_intacta(self, valor: str) -> None:
        assert convert_quantity(Decimal("40"), frm=valor, to=valor) == Decimal("40")

    def test_magnitudes_distintas_devuelve_none(self) -> None:
        assert convert_quantity(Decimal("1"), frm="kg", to="l") is None

    def test_unidad_sin_magnitud_devuelve_none(self) -> None:
        assert convert_quantity(Decimal("1"), frm="caja", to="lb") is None

    def test_unidad_none_devuelve_none(self) -> None:
        assert convert_quantity(Decimal("1"), frm=None, to="lb") is None
        assert convert_quantity(Decimal("1"), frm="lb", to=None) is None


class TestUnitNoun:
    """El nombre con el que la unidad se escribe dentro de una frase («no por
    libra»). Los pares se fijan acá a mano, no se derivan del módulo."""

    def test_cada_unidad_del_vocabulario_tiene_nombre(self) -> None:
        assert {u: unit_noun(u) for u in UNITS} == {
            "lb": "libra",
            "kg": "kilo",
            "g": "gramo",
            "l": "litro",
            "ml": "mililitro",
            "unidad": "unidad",
            "docena": "docena",
            "caja": "caja",
        }

    def test_no_sobra_ningun_nombre(self) -> None:
        """`UNIT_NOUNS` no nombra unidades que no se almacenan."""
        assert set(UNIT_NOUNS) == set(UNITS)

    def test_fuera_del_vocabulario_no_inventa(self) -> None:
        assert unit_noun("onza") is None
        assert unit_noun("") is None
        assert unit_noun(None) is None



class TestGramo:
    """`g` entra al vocabulario por pedido del operador (2026-10-13): hay
    productos que se venden por gramos y no había cómo cargarlos."""

    @pytest.mark.parametrize("raw", ["g", "G", "gr", "grs", "gramo", "Gramos"])
    def test_las_formas_habituales_normalizan_a_g(self, raw: str) -> None:
        assert canonical_unit(raw) == "g"

    def test_el_gramo_se_nombra_en_una_frase(self) -> None:
        assert unit_noun("g") == "gramo"

    def test_mil_gramos_cuestan_lo_que_un_kilo(self) -> None:
        # Un precio por kilo de 1000 es 1 por gramo: el gramo es 1000 veces menos producto.
        assert convert_price_per_unit(Decimal("1000"), frm="kg", to="g") == Decimal("1")
        assert convert_price_per_unit(Decimal("1"), frm="g", to="kg") == Decimal("1000")

    def test_el_gramo_y_la_libra_se_convierten_entre_si(self) -> None:
        assert convert_quantity(Decimal("453.59237"), frm="g", to="lb") == Decimal("1")

    def test_el_gramo_no_se_convierte_a_volumen(self) -> None:
        assert convert_price_per_unit(Decimal("1"), frm="g", to="ml") is None
        assert convert_quantity(Decimal("1"), frm="g", to="l") is None
