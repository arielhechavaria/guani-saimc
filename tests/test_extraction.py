"""Tests del motor de reglas de extracción — casos sintéticos.

Los casos están inventados a mano, con la forma que un anuncio real del mercado
informal cubano suele tener (el primero es literalmente el ejemplo de la
especificación, `spec.md` §6.1, Etapa 2). Cada caso fija la respuesta correcta
antes de que exista el
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
    mentions_any_product,
    probable_discard_reasons,
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
    """El ejemplo textual de la especificación, campo por campo, en un solo caso."""

    def test_ejemplo_de_la_especificacion(
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
            # Precio en dos monedas ("USD o CUP"), sin ningún otro marcador —
            # las tres líneas reales que motivaron el fix (Groupej Morales
            # Surl, canal de WhatsApp del operador, 2026-08-17). Se toma el
            # mayor de los dos números (CUP, por escala, no por posición —
            # ver por qué en el caso "orden invertido" más abajo). Verificado
            # a mano contra la tasa informal implícita del propio vendedor
            # (~710 CUP/USD, consistente en las tres): 30885/43.5, 7810/11 y
            # 688.7/0.97 dan ese mismo número.
            ("🍚Arroz grano dorado 100 lb  43.5 o 30885", Decimal("30885")),
            ("🦴 Carapacho de pollo: 10kg : 11  o 7810", Decimal("7810")),
            # El punto suelto pegado al número de la izquierda y la "O"
            # mayúscula son variantes reales del mismo canal, mismo mensaje.
            ("🌟Arroz grano dorado 1 kg. 0,97 o 688,7", Decimal("688.7")),
            ("Bicarbonato de sodio. 100 g 0,40. O 280", Decimal("280")),
            # Orden invertido ("CUP o USD"): mismo canal, mismo producto,
            # mensajes de otros días — el vendedor no es consistente. Si se
            # asumiera "el de la derecha es CUP" (como el primer intento de
            # este fix, corregido antes de commitear tras medir contra más
            # mensajes reales), estas tres darían 43.5, 23.5 y 0.97 — el lado
            # USD tomado como si fuera CUP.
            ("🍚Arroz Grano Dorado 100 lb : 🤑30885 o 43.5", Decimal("30885")),
            ("🍚Arroz Argentino  25 kg : 🤑16685 o 23.5", Decimal("16685")),
            ("🍚Arroz Grano Dorado  1Kg: 🤑688.7 o 0.97", Decimal("688.7")),
            # Negativo: "o" es letra normal de "Jugo", sin dígitos pegados a
            # los dos lados — no debe confundirse con el patrón de arriba, y
            # el precio real sigue viniendo del "a" de siempre.
            ("Jugo de tomate a 180", Decimal("180")),
            # Límite deliberadamente fuera de alcance (docs/decisions.md,
            # 2026-08-17): con moneda ya marcada explícita, gana el camino
            # viejo — precio y moneda quedan consistentes entre sí (25 USD),
            # en vez de forzar el lado CUP sin forzar también la moneda.
            ("Cemento - 25 Usd o 17175", Decimal("25")),
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
            # Precio en dos monedas: ya venía en CUP por default (sin marca
            # explícita) antes de este fix — lo nuevo es que ahora el precio
            # que la acompaña es el correcto, no None. Ver TestPrecio.
            ("🍚Arroz grano dorado 100 lb  43.5 o 30885", "CUP"),
            # Límite fuera de alcance (ver TestPrecio): con "Usd" explícito,
            # la moneda sigue siendo la del número que de verdad se devuelve.
            ("Cemento - 25 Usd o 17175", "USD"),
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


class TestMonedaPegadaAlNumero:
    """La marca de moneda escrita sin espacio: "90.00cup", "4.45USD", "725cup".

    `_PRICE_BEFORE_CURRENCY_RE` siempre reconoció esa forma para ubicar el
    número (su `\\s*` admite cero espacios), pero `_CURRENCY_RE` exigía una
    frontera de palabra ANTES del marcador, y entre "5" y "u" no hay ninguna.
    Los dos regex discrepaban sobre el mismo texto: el precio salía del "usd"
    y la moneda caía al default CUP, un error de ~700× en este mercado.

    Frecuencia medida sobre el corpus curado (2026-08-21): 29 de 132 anuncios
    traen un marcador pegado a un dígito, 22 de ellos en evidencia de texto.
    Los casos de abajo son formas reales encontradas ahí.
    """

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("Pasta para frijoles 90.00cup", "CUP"),
            ("Frijol Colorado paquete de 1kg a1210cup", "CUP"),
            ("Mas de 5 pacas a 725cup la unidad", "CUP"),
            ("Contenedor de aceite. Precio: 4.45USD c/u", "USD"),
            ("Arroz Guyanes de 50kg, en 43 usd o 30100mn el saco", "USD"),
            # El marcador pegado no debe inventar moneda dentro de una palabra:
            # "column" termina en "mn" y no dice nada sobre moneda nacional.
            ("column de precios sin moneda 500", "CUP"),
        ],
    )
    def test_moneda(
        self,
        texto: str,
        esperado: str,
        products: list[Product],
        provinces: list[Province],
    ) -> None:
        assert propose(texto, products, provinces).currency == esperado

    def test_el_precio_y_la_moneda_no_se_contradicen(
        self, products: list[Province], provinces: list[Province]
    ) -> None:
        """Anuncio real del corpus: el número sale del "usd" pegado, así que la
        moneda tiene que ser esa misma y no el default."""
        propuesta = propose(
            "Contenedor de aceite. Precio: 4.45USD c/u", products, provinces
        )
        assert propuesta.price == Decimal("4.45")
        assert propuesta.currency == "USD"


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

    @pytest.mark.parametrize(
        "texto",
        [
            "aceite para bebe 100 ML 1120.00 cup",
            "crema para bebe 100 ML 710.00 cup",
        ],
    )
    def test_los_articulos_para_bebe_no_matchean_el_generico(
        self, products: list[Product], provinces: list[Province], texto: str
    ) -> None:
        """Caso real: "Aceite para bebé 100 ML" se cargó como el producto
        "Aceite" (aceite de cocina, producto piloto). No es un corte ni un
        derivado del mismo bien: es otro artículo que comparte la palabra.

        Lo que se fija acá es que NO contamine el genérico, y que el nombre
        sugerido deje ver de qué se trata. El recorte exacto de esa frase es
        incidental —hoy arrastra el "100 ML" porque ML no se reconoce como
        unidad de cantidad— y pinearlo convertiría un detalle de presentación
        en un contrato.
        """
        propuesta = propose(texto, products, provinces)
        assert propuesta.product is None
        assert "bebe" in (propuesta.product_name_guess or "")

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

    def test_matchea_hashtag_con_guiones_bajos(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Caso real: un canal comercial abre el anuncio con
        «#Santiago_de_Cuba». El guion bajo es un carácter de palabra para
        regex (`\\w` lo incluye), así que sin normalizarlo antes de buscar,
        «santiago_de_cuba» es una sola palabra larga que nunca calza con el
        alias «santiago de cuba», que lleva espacios."""
        propuesta = propose("#Santiago_de_Cuba\npollo a 100", products, provinces)
        assert propuesta.province is provinces[1]

    def test_matchea_hashtag_de_una_sola_palabra(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("#Habana\npollo a 100", products, provinces)
        assert propuesta.province is provinces[0]


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


class TestProposeAllPreciosEscalonados:
    """Un anuncio mayorista de un solo producto con precio por volumen.

    Forma real, medida contra un anuncio del operador (2026-08-20): el
    producto lo declara el encabezado, el formato de venta lo declara otra
    línea, y las líneas de precio de abajo heredan los dos. NINGUNA línea
    sola tiene la información completa, así que el modelo "una línea = una
    oferta" no puede representarlo.

    El escalón intermedio se descarta a propósito (decisión del operador,
    2026-08-20): se guarda el techo del anuncio y su mejor precio mayorista,
    que son los dos extremos que describen la oferta.
    """

    ALITAS = (
        "ALITAS DE POLLO\n"
        "CAJA DE 40 lb\n"
        "La caja 28,5 usd o 25650 cup.\n"
        "Más de 10 cajas 28\n"
        "Más de 100 cajas 27,5.\n"
    )

    def test_anuncio_escalonado_da_base_y_mejor_mayorista(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultados = propose_all(self.ALITAS, products, provinces)

        assert len(resultados) == 2

        base, mayorista = resultados

        # Las dos filas describen la misma oferta, así que hablan del mismo
        # producto — sea uno del catálogo o, como acá, uno todavía sin dar de
        # alta (las alitas no son Pollo: ver `TestCortesQueNoSonElProductoGenerico`).
        assert base.product is mayorista.product
        assert base.price == Decimal("28.5")
        assert base.currency == "USD"
        assert base.quantity == Decimal("40")
        assert base.unit == "lb"
        assert base.min_purchase_quantity is None

        assert mayorista.price == Decimal("27.5")
        assert mayorista.currency == "USD"
        assert mayorista.quantity == Decimal("40")
        assert mayorista.unit == "lb"
        # 100 cajas de 40 lb. El mínimo se guarda en la misma unidad que
        # `unit` (ver el comentario de `min_purchase_quantity` en el modelo):
        # "100 cajas" no es comparable entre anuncios, 4000 lb sí.
        assert mayorista.min_purchase_quantity == Decimal("4000")

    def test_no_propone_dar_de_alta_un_producto_llamado_la_caja(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """La línea del precio base empieza con "La caja", que el motor
        línea-por-línea tomaba como nombre de producto candidato. Bajo la
        lectura de bloque el nombre sale del encabezado, así que el envase
        nunca puede terminar propuesto como producto."""
        resultados = propose_all(self.ALITAS, products, provinces)

        assert all(r.product_name_guess != "La caja" for r in resultados)

    def test_minimo_dicho_en_la_unidad_del_lote_no_se_multiplica(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Cuando el escalón ya viene en la unidad canónica no hay conversión
        que hacer: 500 lb son 500 lb, no 500 por el tamaño del lote."""
        texto = "pollo entero\nsaco de 40 lb\nel saco a 500\nMás de 500 lb a 480\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 2
        assert resultados[1].price == Decimal("480")
        assert resultados[1].min_purchase_quantity == Decimal("500")

    def test_escalon_sin_lote_conocido_no_inventa_el_minimo(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Sin una línea que diga cuánto trae una caja, "más de 10 cajas" no
        se puede expresar en `unit` — y un escalón sin mínimo es una segunda
        fila con otro precio y ninguna explicación, peor que no proponerla.
        Se cae a la fila base sola en vez de escribir un número inventado.
        """
        texto = "pollo entero a 500\nMás de 10 cajas 480\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 1
        assert resultados[0].price == Decimal("500")
        assert resultados[0].min_purchase_quantity is None

    def test_anuncio_multiproducto_con_escalon_sigue_siendo_linea_por_linea(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """La lectura de bloque asume UN producto para todo el anuncio. Con
        dos productos distintos esa herencia sería falsa, así que no aplica.
        """
        texto = "pollo entero 40lb a 3000\narroz blanco 1 caja a 500\n"

        resultados = propose_all(texto, products, provinces)

        assert len(resultados) == 2
        assert resultados[0].product is products[0]
        assert resultados[1].product is products[1]

    def test_anuncio_sin_escalones_no_cambia(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultados = propose_all("pollo congelado 40lb a 500 habana", products, provinces)

        assert len(resultados) == 1
        assert resultados[0].price == Decimal("500")
        assert resultados[0].min_purchase_quantity is None

    def test_producto_fuera_del_catalogo_propone_el_encabezado_como_nombre(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Las alitas no son Pollo (decisión del operador, 2026-08-20) y
        todavía no existen como producto propio. El bloque no puede
        deshacerse por eso: sigue siendo un anuncio de un solo producto con
        precio por volumen, y lo único que falta es el alta.

        El encabezado es el nombre candidato porque en esta forma de anuncio
        ES el producto — `_guess_product_phrase` no lo ve, porque adivina a
        partir del texto que precede a un precio y el encabezado no tiene
        ninguno.
        """
        resultados = propose_all(self.ALITAS, products, provinces)

        assert len(resultados) == 2
        assert all(r.product is None for r in resultados)
        assert all(r.product_name_guess == "ALITAS DE POLLO" for r in resultados)
        assert resultados[0].price == Decimal("28.5")
        assert resultados[1].price == Decimal("27.5")
        assert resultados[1].min_purchase_quantity == Decimal("4000")

    def test_una_vez_dado_de_alta_el_producto_gana_al_nombre_adivinado(
        self, provinces: list[Province]
    ) -> None:
        """Cuando el operador da de alta las alitas, el bloque deja de
        adivinar: el nombre propio ya no descalifica nada porque contiene el
        mismo corte que la lista marca."""
        catalogo = [
            Product(name="Alitas de pollo", category="carnes", unit="lb", aliases=[])
        ]

        resultados = propose_all(self.ALITAS, catalogo, provinces)

        assert len(resultados) == 2
        assert all(r.product is catalogo[0] for r in resultados)
        assert all(r.product_name_guess is None for r in resultados)


class TestCortesQueNoSonElProductoGenerico:
    """Qué corte cuenta como el producto genérico y cuál no es criterio del
    operador, no del motor (2026-08-20): muslo y contramuslo SÍ son Pollo,
    las alitas no.

    Muslo y contramuslo se resuelven con alias en /catalog, no acá — por eso
    siguen en la lista de modificadores: la lista protege el match genérico
    de CUALQUIER producto, y un alias explícito es lo que la levanta para uno
    en concreto (ver el `has_modifier` de `_match_product`).
    """

    def test_alitas_no_es_pollo_generico(self, provinces: list[Province]) -> None:
        pollo = [Product(name="Pollo", category="carnes", unit="lb", aliases=["pollo entero"])]

        assert propose_all("alitas de pollo a 300", pollo, provinces)[0].product is None

    def test_alas_tampoco_es_pollo_generico(self, provinces: list[Province]) -> None:
        """Mismo corte, otra palabra. Que el mismo anuncio caiga en productos
        distintos según cómo lo escriba el vendedor es exactamente el error
        que la lista existe para evitar."""
        pollo = [Product(name="Pollo", category="carnes", unit="lb", aliases=["pollo entero"])]

        assert propose_all("alas de pollo a 300", pollo, provinces)[0].product is None

    def test_muslo_con_alias_explicito_si_es_pollo(self, provinces: list[Province]) -> None:
        """Es el mecanismo que el operador ya usa en producción: el alias
        levanta la descalificación para este producto y solo para este."""
        pollo = [
            Product(
                name="Pollo",
                category="carnes",
                unit="lb",
                aliases=["muslo de pollo", "contramuslo"],
            )
        ]

        assert propose_all("muslo de pollo a 500", pollo, provinces)[0].product is pollo[0]
        assert propose_all("contramuslo a 400", pollo, provinces)[0].product is pollo[0]

    def test_muslo_sin_alias_sigue_sin_ser_pollo_generico(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """La fixture no trae los alias de producción: sin ellos, la lista
        sigue protegiendo el match genérico como siempre."""
        assert propose_all("muslo de pollo a 500", products, provinces)[0].product is None

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("muslos de pollo a 700", "Pollo"),
            ("contramuslos a 400", "Pollo"),
            ("cajas de muslos de pollo 40lb", "Pollo"),
        ],
    )
    def test_muslo_y_contramuslo_en_plural_siguen_siendo_pollo(
        self, provinces: list[Province], texto: str, esperado: str
    ) -> None:
        """Mismo mecanismo que `test_muslo_con_alias_explicito_si_es_pollo`,
        contra la forma en que el vendedor realmente escribe una caja
        mayorista: en plural. Antes de esta regla, "muslo" (singular, en la
        lista de modificadores) no reconocía "muslos" como el mismo
        modificador, así que `has_modifier` daba `False` y el match
        genérico de "Pollo" pasaba de pura casualidad, no por el mecanismo
        de alias que este test class documenta."""
        pollo = [
            Product(
                name="Pollo",
                category="carnes",
                unit="lb",
                aliases=["muslo de pollo", "contramuslo"],
            )
        ]

        assert propose_all(texto, pollo, provinces)[0].product is pollo[0]

    def test_alitas_en_singular_tampoco_es_pollo_generico(
        self, provinces: list[Province]
    ) -> None:
        """`test_alitas_no_es_pollo_generico` ya cubre el plural (que es como
        se anuncia en la práctica); esto fija que la forma singular —que la
        lista de modificadores declara como canónica— tampoco cuela."""
        pollo = [Product(name="Pollo", category="carnes", unit="lb", aliases=["pollo entero"])]

        assert propose_all("alita de pollo a 300", pollo, provinces)[0].product is None


class TestPluralDeModificadoresDescalificantes:
    """La lista de modificadores (`_PRODUCT_DISQUALIFYING_MODIFIERS`) declara
    cada palabra en su forma canónica singular; el reconocimiento debe cubrir
    el plural sin necesidad de listar cada variante a mano —el mismo defecto
    que obligó a listar "ala"/"alas" y "alita"/"alitas" por separado, medido
    contra la cola real el 2026-08-21: "Higados de pollo" e "Pechugas de
    pollo" pasaban como Pollo genérico porque "higados"/"pechugas" no
    coincidían con el "higado"/"pechuga" singular de la lista.
    """

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("higados de pollo a 400", "higados de pollo"),
            ("picadillos de pollo a 500", "picadillos de pollo"),
            ("pechugas de pollo a 900", "pechugas de pollo"),
            ("costillas de pollo a 300", "costillas de pollo"),
            ("mollejas de pollo a 250", "mollejas de pollo"),
            ("corazones de pollo a 200", "corazones de pollo"),
        ],
    )
    def test_cortes_en_plural_no_matchean_el_generico(
        self, products: list[Product], provinces: list[Province], texto: str, esperado: str
    ) -> None:
        propuesta = propose(texto, products, provinces)
        assert propuesta.product is None
        assert propuesta.product_name_guess == esperado

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("calditos de pollo a 50", "calditos de pollo"),
            ("sazones de pollo a 120", "sazones de pollo"),
            ("sopas de pollo a 90", "sopas de pollo"),
            ("cubitos de pollo a 40", "cubitos de pollo"),
            ("consomes de pollo a 75", "consomes de pollo"),
        ],
    )
    def test_derivados_en_plural_no_matchean_el_generico(
        self, products: list[Product], provinces: list[Province], texto: str, esperado: str
    ) -> None:
        propuesta = propose(texto, products, provinces)
        assert propuesta.product is None
        assert propuesta.product_name_guess == esperado

    def test_bebes_en_plural_tampoco_matchea_el_generico(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose("aceite para bebes 100 ML 1120.00 cup", products, provinces)
        assert propuesta.product is None
        assert "bebes" in (propuesta.product_name_guess or "")


class TestPluralDeAliasMultipalabra:
    """El mismo defecto de número, del lado del alias en vez de la lista de
    modificadores: un alias de dos palabras escrito en singular
    ("saco de azucar") no reconocía la forma en que el vendedor realmente
    anuncia un mayorista, en plural ("Sacos de azúcar"). Caso real
    encontrado en la cola de producción el 2026-08-21, en un anuncio de
    Groupej Morales Surl que quedaba oculto por el filtro de foco."""

    def test_alias_singular_reconoce_el_texto_en_plural(
        self, provinces: list[Province]
    ) -> None:
        azucar = Product(
            name="Azúcar blanca",
            category="abarrotes",
            unit="lb",
            aliases=["saco de azucar"],
        )

        propuesta = propose(
            "Sacos de azúcar 25 kg    37  o 25160 más de 10 a 36 usd", [azucar], provinces
        )

        assert propuesta.product is azucar

    def test_alias_singular_sigue_reconociendo_el_texto_en_singular(
        self, provinces: list[Province]
    ) -> None:
        """Guarda de regresión: tolerar el plural no debe exigirlo."""
        azucar = Product(
            name="Azúcar blanca",
            category="abarrotes",
            unit="lb",
            aliases=["saco de azucar"],
        )

        propuesta = propose("Saco de azucar 25 kg a 37 usd", [azucar], provinces)

        assert propuesta.product is azucar


class TestMencionaAlgunProducto:
    """`mentions_any_product` es la señal barata que ordena la cola de
    curaduría por relevancia (docs/decisions.md, 2026-08-18): responde "¿este
    anuncio habla de algún producto del foco?" sin construir la propuesta
    completa. Va línea por línea, igual que `propose_all`, porque el texto
    entero no es equivalente: un modificador descalificante en un renglón
    descartaría también los productos limpios de los otros.
    """

    def test_anuncio_que_menciona_un_producto_del_catalogo(
        self, products: list[Product]
    ) -> None:
        assert mentions_any_product("arroz blanco a 30000 la libra", products) is True

    def test_anuncio_de_producto_ajeno_al_catalogo(self, products: list[Product]) -> None:
        texto = "cemento a 500 el saco\ncabilla de media a 1200\n"

        assert mentions_any_product(texto, products) is False

    def test_producto_limpio_en_otra_linea_que_el_modificador_descalificante(
        self, products: list[Product]
    ) -> None:
        """El caso que obliga a mirar línea por línea: "hígado de pollo" no es
        Pollo, pero el mismo anuncio ofrece arroz en el renglón siguiente. Si
        la señal se calculara sobre el texto entero, el modificador de la
        primera línea descartaría también el arroz de la segunda.
        """
        texto = "higado de pollo a 900\narroz a 30000\n"

        assert mentions_any_product(texto, products) is True

    def test_solo_un_corte_descalificado_no_cuenta_como_mencion(
        self, products: list[Product]
    ) -> None:
        """Coherente con lo que la cola mostraría: `propose_all` tampoco le
        asigna Pollo a esta línea, así que el anuncio no aporta al foco."""
        assert mentions_any_product("higado de pollo a 900", products) is False

    def test_sin_tildes_igual_reconoce(self) -> None:
        """El texto real llega sin tildes (OCR, tipeo apurado) y el producto
        piloto en la base sí las tiene: "Azúcar blanca"."""
        catalogo = [Product(name="Azúcar blanca", category="abarrotes", unit="lb", aliases=[])]

        assert mentions_any_product("azucar blanca a 22500", catalogo) is True

    def test_texto_vacio(self, products: list[Product]) -> None:
        assert mentions_any_product("", products) is False


class TestProbableDiscardReasons:
    """Sugerencia asistida de descarte en la cola de curaduría (docs/decisions.md,
    2026-08-22). NO auto-descarta nada: es una señal para que el operador
    confirme con un clic. Medida contra 159 descartes reales de producción —
    el ruido no es texto repetido entre sí (0 grupos casi-idénticos), es
    estructural: sin mención de producto del foco, "agotado", o venta
    mayorista. Un fuzzy-match por similitud de texto se descartó por esa
    medición, no por intuición.
    """

    def test_sin_mencion_de_producto_del_foco(self, products: list[Product]) -> None:
        assert probable_discard_reasons("cemento a 500 el saco", products) == [
            "no menciona ningún producto del foco"
        ]

    def test_agotado_con_producto_del_foco(self, products: list[Product]) -> None:
        assert probable_discard_reasons("Arroz blanco AGOTADO", products) == [
            "parece indicar agotado"
        ]

    def test_agotado_por_emoji_sin_la_palabra(self, products: list[Product]) -> None:
        assert probable_discard_reasons("⛔ Pollo", products) == ["parece indicar agotado"]

    def test_venta_mayorista_por_contenedor(self, products: list[Product]) -> None:
        assert probable_discard_reasons("Arroz por contenedor, consultar", products) == [
            "parece venta mayorista/por volumen"
        ]

    def test_venta_mayorista_por_cajones(self, products: list[Product]) -> None:
        assert probable_discard_reasons("Pollo por cajones, precio a convenir", products) == [
            "parece venta mayorista/por volumen"
        ]

    def test_caja_como_unidad_normal_no_dispara_mayorista(
        self, products: list[Product]
    ) -> None:
        """"Caja" es unidad canónica válida (`saimc.units.UNITS`) — solo
        "contenedor"/"cajones"/"mayorista" son señal de venta al por mayor."""
        assert probable_discard_reasons("Arroz a 30000 la caja", products) == []

    def test_anuncio_normal_sin_senales(self, products: list[Product]) -> None:
        assert probable_discard_reasons("Arroz blanco a 30000 la libra", products) == []

    def test_combina_varias_senales_en_orden_fijo(self, products: list[Product]) -> None:
        razones = probable_discard_reasons("cemento AGOTADO por contenedor", products)

        assert razones == [
            "no menciona ningún producto del foco",
            "parece indicar agotado",
            "parece venta mayorista/por volumen",
        ]

    def test_texto_vacio_no_menciona_producto(self, products: list[Product]) -> None:
        """Coherente con `mentions_any_product("", ...)`: un texto vacío no
        menciona nada, así que la única señal que dispara es esa — en la
        práctica `curation.py` nunca llama a esta función sin texto."""
        assert probable_discard_reasons("", products) == ["no menciona ningún producto del foco"]


class TestProvinciaDelCanal:
    """docs/decisions.md, 2026-08-17 — "el canal siempre manda".

    La regla ya vivía en la cola de curaduría (`curation.py`), donde pisaba la
    provincia DESPUÉS de que el motor propusiera. Acá entra al motor mismo,
    que es lo que la medición de H0 re-corre: sin esto, `measure.py` evaluaba
    un extractor ciego a un dato que en producción sí se aplica, y la
    precisión de provincia salía sistemáticamente por debajo de la real.

    Es override, no fallback: el operador conoce la provincia del canal de
    antemano, y un mensaje puede no mencionarla o mencionar otra por error.
    """

    def test_propone_la_provincia_del_canal_cuando_el_texto_no_la_dice(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = propose(
            "pollo congelado 40lb a 500", products, provinces, channel_province=provinces[0]
        )

        assert propuesta.province is provinces[0]

    def test_la_provincia_del_canal_pisa_la_del_texto(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """El texto dice Habana, el canal es de Santiago: gana el canal."""
        propuesta = propose(
            "pollo congelado 40lb a 500 habana",
            products,
            provinces,
            channel_province=provinces[1],
        )

        assert propuesta.province is provinces[1]

    def test_sin_canal_la_provincia_sigue_saliendo_del_texto(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """El camino de siempre no cambia: una fuente sin canal (bandeja,
        pegado) sigue dependiendo de lo que el texto diga."""
        propuesta = propose("pollo congelado 40lb a 500 habana", products, provinces)

        assert propuesta.province is provinces[0]

    def test_canal_sin_provincia_cargada_no_pisa_nada(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """`channel_province=None` es el caso de un canal recién dado de alta
        sin provincia fija: no debe borrar lo que el texto sí dijo."""
        propuesta = propose(
            "pollo congelado 40lb a 500 habana", products, provinces, channel_province=None
        )

        assert propuesta.province is provinces[0]

    def test_propose_all_aplica_el_canal_a_todas_las_lineas(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        texto = "pollo congelado 40lb a 500\narroz blanco 5lb a 300 santiago de cuba\n"

        propuestas = propose_all(
            texto, products, provinces, channel_province=provinces[0]
        )

        assert len(propuestas) == 2
        assert [p.province for p in propuestas] == [provinces[0], provinces[0]]

    def test_propose_all_sin_canal_no_cambia(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Sin canal sigue rigiendo la caída al texto completo que `propose_all`
        ya documenta: la línea del pollo no nombra provincia, así que hereda la
        única que el anuncio menciona."""
        texto = "pollo congelado 40lb a 500\narroz blanco 5lb a 300 santiago de cuba\n"

        propuestas = propose_all(texto, products, provinces)

        assert [p.province for p in propuestas] == [provinces[1], provinces[1]]
