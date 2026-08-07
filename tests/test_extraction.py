"""Tests del motor de reglas de extracción — casos sintéticos.

Los casos están inventados a mano, con la forma que un anuncio real del mercado
informal cubano suele tener (el primero es literalmente el ejemplo del whitepaper,
§6.1, líneas 601-609). Cada caso fija la respuesta correcta antes de que exista el
código que la produce — la regla de CLAUDE.md para esta capa: un caso sintético vale
como RED siempre que la verdad se defina antes e independientemente del extractor.

Estos tests NO miden H0. H0 se mide después, contra el conjunto real de 300+
anuncios curados (docs/decisions.md, 2026-08-07).
"""

from decimal import Decimal

import pytest

from saimc.extraction.engine import propose
from saimc.models import Product, Province


@pytest.fixture
def products() -> list[Product]:
    return [
        Product(
            name="Pollo",
            category="carnes",
            unit="lb",
            aliases=["pollo congelado", "pollo entero"],
        ),
        Product(name="Arroz", category="granos", unit="lb", aliases=["arroz blanco"]),
    ]


@pytest.fixture
def provinces() -> list[Province]:
    return [
        Province(code="LH", name="La Habana"),
        Province(code="SCU", name="Santiago de Cuba"),
    ]


class TestPropuestaCompleta:
    """El ejemplo textual del whitepaper, campo por campo, en un solo caso."""

    def test_ejemplo_del_whitepaper(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        texto = "🐔 pollo congelado cajas de 40lb a 12000 stgo entrega dom"
        propuesta = propose(texto, products, provinces)

        assert propuesta.product is products[0]
        assert propuesta.price == Decimal("12000")
        assert propuesta.currency == "CUP"
        assert propuesta.quantity == Decimal("40")
        assert propuesta.unit == "lb"
        assert propuesta.province is provinces[1]


class TestPrecio:
    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("a 12000 stgo entrega dom", Decimal("12000")),
            ("950 CUP", Decimal("950")),
            ("20 usd la libra", Decimal("20")),
            ("12.000 cup", Decimal("12000")),
            ("sin precio visible aqui", None),
            ("azucar $23,500 el saco", Decimal("23500")),
            ("aceite $500", Decimal("500")),
            # Caso real de OCR: separador de miles con coma y ruido de puntuación
            # pegado al número (el "." antes de "Sale" no debe colarse en el precio).
            ("$23,500.Sale 427.27Lb", Decimal("23500")),
        ],
    )
    def test_casos(
        self,
        texto: str,
        esperado: Decimal | None,
        products: list[Product],
        provinces: list[Province],
    ) -> None:
        assert propose(texto, products, provinces).price == esperado


class TestMoneda:
    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("12000 stgo", "CUP"),  # sin marca explícita, se infiere CUP
            ("950 CUP", "CUP"),
            ("20 usd la libra", "USD"),
            ("50 mlc", "MLC"),
            ("30 eur", "EUR"),
            ("100 mn", "CUP"),  # "moneda nacional", jerga habitual para CUP
        ],
    )
    def test_casos(
        self,
        texto: str,
        esperado: str,
        products: list[Product],
        provinces: list[Province],
    ) -> None:
        assert propose(texto, products, provinces).currency == esperado


class TestCantidad:
    @pytest.mark.parametrize(
        ("texto", "cantidad", "unidad"),
        [
            ("cajas de 40lb a 12000", Decimal("40"), "lb"),
            ("1 caja a 500", Decimal("1"), "caja"),
            ("sin cantidad visible", None, None),
        ],
    )
    def test_casos(
        self,
        texto: str,
        cantidad: Decimal | None,
        unidad: str | None,
        products: list[Product],
        provinces: list[Province],
    ) -> None:
        propuesta = propose(texto, products, provinces)
        assert propuesta.quantity == cantidad
        assert propuesta.unit == unidad


class TestProducto:
    def test_matchea_por_alias(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("pollo congelado a 100", products, provinces)
        assert propuesta.product is products[0]

    def test_matchea_por_nombre(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("arroz blanco a 100", products, provinces)
        assert propuesta.product is products[1]

    def test_sin_match(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose(
            "algo que no esta en el catalogo a 100", products, provinces
        )
        assert propuesta.product is None


class TestProvincia:
    def test_matchea_por_abreviatura(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("pollo a 100 stgo", products, provinces)
        assert propuesta.province is provinces[1]

    def test_matchea_por_nombre_completo(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("pollo a 100 la habana", products, provinces)
        assert propuesta.province is provinces[0]

    def test_sin_match(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("pollo a 100 en algun lugar", products, provinces)
        assert propuesta.province is None
