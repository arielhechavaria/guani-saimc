"""Tests del motor de reglas de extracción — casos sintéticos.

Los casos están inventados a mano, con la forma que un anuncio real del mercado
informal cubano suele tener (el primero es literalmente el ejemplo del whitepaper,
§6.1, líneas 601-609). Cada caso fija la respuesta correcta antes de que exista el
código que la produce — la regla de CLAUDE.md para esta capa: un caso sintético vale
como RED siempre que la verdad se defina antes e independientemente del extractor.

Estos tests NO miden H0. H0 se mide después, contra el conjunto real de 300+
anuncios curados (docs/decisions.md, 2026-08-07).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from saimc.extraction.engine import (
    guess_published_date,
    guess_source_reference,
    propose,
    propose_all,
)
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

    def test_matchea_ignorando_tildes(self, provinces: list[Province]) -> None:
        """Caso real: "azucar blanca" (texto de OCR/tipeo sin tilde) no
        matcheaba contra el producto "Azúcar blanca" ya cargado — cada
        variante terminaba dando de alta un producto duplicado."""
        azucar = Product(
            name="Azúcar blanca", category="sin categoría", unit="unidad", aliases=[]
        )
        propuesta = propose("azucar blanca a 100", [azucar], provinces)
        assert propuesta.product is azucar


class TestModificadoresQueDesambiguanProducto:
    """Un corte o parte específica no es el mismo producto que el genérico,
    aunque comparta la palabra: "hígado de pollo" no es "Pollo". Detectar
    esto en general es un problema de NLP que no resolvemos acá — la
    solución es una lista curada de modificadores conocidos que, si
    aparecen, invalidan el match genérico y en cambio se propone dar de
    alta un producto nuevo con el nombre adivinado.
    """

    def test_higado_de_pollo_no_matchea_el_generico(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("higado de pollo 40lb a 3000", products, provinces)
        assert propuesta.product is None
        assert propuesta.product_name_guess == "higado de pollo"

    def test_picadillo_de_pollo_no_matchea_el_generico(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("picadillo de pollo 21.5kg a 3295", products, provinces)
        assert propuesta.product is None
        assert propuesta.product_name_guess == "picadillo de pollo"

    def test_pollo_congelado_sigue_matcheando_normal(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """"congelado" es un estado, no un corte distinto: no debe desambiguar."""
        propuesta = propose("pollo congelado 40lb a 500 habana", products, provinces)
        assert propuesta.product is products[0]
        assert propuesta.product_name_guess is None

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("caldito de pollo 50.00cup", "caldito de pollo"),
            ("sazon fresko de pollo a 120 cup", "sazon fresko de pollo"),
            ("sopa de pollo a 90 cup", "sopa de pollo"),
            ("cubito de pollo 40 cup", "cubito de pollo"),
            ("consome de pollo a 75 cup", "consome de pollo"),
            ("pastillita de pollo ahumado a 30 cup", "pastillita de pollo ahumado"),
        ],
    )
    def test_los_derivados_no_matchean_el_generico(
        self,
        products: list[Product],
        provinces: list[Province],
        texto: str,
        esperado: str,
    ) -> None:
        """Un derivado industrial no es el bien fresco, aunque lo nombre.

        Caso real: un caldito de pollo vale ~50 CUP la unidad y un pollo
        ~950 CUP la libra. Mezclarlos en la misma serie destruye la mediana
        del producto piloto sin que se note en el dashboard.
        """
        propuesta = propose(texto, products, provinces)
        assert propuesta.product is None
        assert propuesta.product_name_guess == esperado

    def test_pollo_entero_sigue_matcheando_el_generico(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Guarda de regresión: "entero" describe el mismo bien, no otro."""
        propuesta = propose("pollo entero a 950 cup la libra", products, provinces)
        assert propuesta.product is products[0]
        assert propuesta.product_name_guess is None

    def test_propose_all_prellena_el_nombre_adivinado_sin_match(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultados = propose_all("higado de pollo 40lb a 3000\n", products, provinces)
        assert len(resultados) == 1
        assert resultados[0].product is None
        assert resultados[0].product_name_guess == "higado de pollo"
        assert resultados[0].price == Decimal("3000")
        assert resultados[0].quantity == Decimal("40")

    def test_recorta_vineta_de_lista_con_parentesis(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Caso real: "4) Azúcar blanca a 22500" — el "4)" es una viñeta de
        lista que deja el OCR pegada al nombre; no debe terminar siendo
        parte del nombre del producto nuevo (motivo de duplicados reales en
        la base, ver docs/decisions.md)."""
        propuesta = propose("4) Azucar blanca a 22500", products, provinces)
        assert propuesta.product_name_guess == "Azucar blanca"

    def test_recorta_numero_de_lista_sin_puntuacion(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("4 Azucar parda a 25500", products, provinces)
        assert propuesta.product_name_guess == "Azucar parda"


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


class TestCanalOrigen:
    """`guess_source_reference` adivina quién publicó a partir del patrón
    típico de redes (docs/backlog.md): "Nombre - Ayer a las H:MM" / "Nombre -
    D de mes a las H:MM".
    """

    def test_nombre_con_ayer_a_las(self) -> None:
        texto = "Juan Pérez - Ayer a las 14:35\npollo a 100 habana"
        assert guess_source_reference(texto) == "Juan Pérez"

    def test_nombre_con_fecha_de_mes(self) -> None:
        texto = "María López - 3 de agosto a las 9:12\narroz a 200"
        assert guess_source_reference(texto) == "María López"

    def test_nombre_con_hoy_a_las(self) -> None:
        texto = "Grupo Ofertas Habana - Hoy a las 08:00\npollo a 300"
        assert guess_source_reference(texto) == "Grupo Ofertas Habana"

    def test_sin_patron_da_none(self) -> None:
        assert guess_source_reference("pollo congelado a 100 habana") is None

    def test_texto_vacio_da_none(self) -> None:
        assert guess_source_reference("") is None

    def test_nombre_en_segunda_linea_cuando_la_primera_es_ruido_de_ocr(self) -> None:
        """Caso real (ingestion con OCR): la línea 0 suele ser el nombre de la
        página/grupo o basura de OCR, no de quien publica — el patrón
        "Nombre - fecha a las H:MM" aparece recién en la línea siguiente.
        """
        texto = (
            "29] 360 Santiago de Cuba Compra y Venta, Cuba\n"
            "Proyecto Aguilera - 30 de julio a las 9:49p. m. - Y\n"
            "pollo a 100 stgo"
        )
        assert guess_source_reference(texto) == "Proyecto Aguilera"


class TestFechaPublicacion:
    """`guess_published_date` adivina el día de publicación reutilizando el
    grupo de fecha (`when`) que `_SOURCE_REFERENCE_RE` ya matchea para
    `guess_source_reference` (design.md, D3/D4). Resuelve tokens relativos
    contra `captured_at` de la ingesta y nunca devuelve una fecha posterior
    a `captured_at.date()`.
    """

    def test_hoy_resuelve_contra_captured_at(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        texto = "Juan Pérez - Hoy a las 14:35\npollo a 100 habana"
        assert guess_published_date(texto, captured_at) == date(2026, 8, 10)

    def test_ayer_resta_un_dia(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        texto = "Juan Pérez - Ayer a las 14:35\npollo a 100 habana"
        assert guess_published_date(texto, captured_at) == date(2026, 8, 9)

    def test_dia_de_mes_mismo_anio(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        texto = "María López - 3 de agosto a las 9:12\narroz a 200"
        assert guess_published_date(texto, captured_at) == date(2026, 8, 3)

    def test_rollover_diciembre_a_enero(self) -> None:
        """Un post de diciembre capturado en enero: resolver "30 de diciembre"
        con el año de `captured_at` (2026) daría una fecha futura respecto a
        la captura — se resta un año."""
        captured_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        texto = "Grupo Ofertas - 30 de diciembre a las 20:00\npollo a 100"
        assert guess_published_date(texto, captured_at) == date(2025, 12, 30)

    def test_mes_inexistente_da_none(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        texto = "Juan - 5 de trueno a las 10:00\npollo a 100"
        assert guess_published_date(texto, captured_at) is None

    def test_31_de_febrero_da_none(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        texto = "Juan - 31 de febrero a las 10:00\npollo a 100"
        assert guess_published_date(texto, captured_at) is None

    def test_sin_patron_da_none(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        assert guess_published_date("pollo congelado a 100 habana", captured_at) is None

    def test_texto_vacio_da_none(self) -> None:
        captured_at = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        assert guess_published_date("", captured_at) is None


class TestProposeAllMultiProducto:
    """Un anuncio real puede ofrecer varios productos a la vez, uno por línea
    (patrón observado en anuncios reales de venta mayorista/TCP, cada oferta
    en su propio renglón con su propio precio).
    """

    def test_una_propuesta_por_linea_con_producto_o_precio(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        texto = (
            "pollo entero 40lb a 3000 stgo\n"
            "arroz blanco 1 caja a 500\n"
            "sin nada util en esta linea de puro relleno\n"
        )
        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 2

        assert resultados[0].product is products[0]
        assert resultados[0].price == Decimal("3000")
        assert resultados[0].quantity == Decimal("40")
        assert resultados[0].unit == "lb"
        assert resultados[0].province is provinces[1]

        assert resultados[1].product is products[1]
        assert resultados[1].price == Decimal("500")
        assert resultados[1].quantity == Decimal("1")
        assert resultados[1].unit == "caja"
        # La línea de arroz no menciona provincia: cae al texto completo,
        # que sí la menciona en la línea del pollo.
        assert resultados[1].province is provinces[1]

    def test_texto_de_un_solo_producto_da_lista_de_un_elemento(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultados = propose_all("pollo congelado 40lb a 500 habana", products, provinces)

        assert len(resultados) == 1
        assert resultados[0].product is products[0]
        assert resultados[0].price == Decimal("500")

    def test_texto_sin_nada_util_da_lista_vacia(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        assert propose_all("hola como estas todo bien por alla", products, provinces) == []

    def test_moneda_por_linea_cae_al_texto_completo_si_la_linea_no_la_menciona(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        texto = "todo en usd:\npollo a 500\narroz a 200 usd\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 2
        # "pollo a 500" no trae marca propia, pero el texto completo sí menciona "usd".
        assert resultados[0].currency == "USD"
        assert resultados[1].currency == "USD"

    def test_precio_suelto_al_inicio_de_linea_sin_producto_ni_nombre_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Caso real observado: una fecha tipo "a 8/4/2026 at 11:48" matchea
        como precio por el patrón "a <número>", pero al estar al principio de
        la línea no queda texto para adivinar un nombre — ni producto ni
        `product_name_guess`, nada que el operador pueda revisar. Antes esa
        línea generaba una tarjeta con precio prellenado y producto en blanco
        que solo servía para eliminar; ahora no se propone.
        """
        texto = "a 8/4/2026 at 11:48\npollo a 100 habana\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 1
        assert resultados[0].product is products[0]

    def test_precio_con_simbolo_al_inicio_sin_producto_ni_nombre_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        texto = "$23,500.Sale 427.27Lb\npollo a 100 habana\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 1
        assert resultados[0].product is products[0]

    def test_linea_de_puro_url_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Caso real: un anuncio que invita a un grupo de WhatsApp deja una
        línea que es solo el link. No aporta nada y arriesga matchear texto
        de la URL como si fuera precio/cantidad."""
        texto = (
            "pollo a 100 habana\n"
            "https://chatwhatsapp.com/GDjcSQyw7qwCyxmL4wHWsx?s=sh8p=a...\n"
        )

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 1
        assert resultados[0].product is products[0]

    def test_linea_de_puro_simbolo_o_emoji_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Sin ninguna letra ni dígito, una línea no puede aportar producto
        ni precio — es ruido de OCR (separadores, emojis sueltos)."""
        texto = "pollo a 100 habana\n•••\n🐔🐔🐔\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 1
        assert resultados[0].product is products[0]
