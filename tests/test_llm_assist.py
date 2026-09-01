"""`saimc.extraction.llm_assist` — la mitad pura, sin red.

Estos tests fijan la respuesta correcta **antes** de que exista el código, y
ninguno pregunta qué *debería* contestar el modelo: eso se responde midiendo
contra la cola real, nunca fijándolo en un unit test (`CLAUDE.md`, capa de
extracción; `design.md`, D9).

Lo que sí se prueba exhaustivamente es la defensa: qué pasa cuando la respuesta
trae un producto que no existe, un tipo equivocado o un campo que nadie pidió.
Esa es la mitad de este módulo que puede hacer daño, y es la que se puede
probar entera sin salir de la máquina.
"""

from decimal import Decimal

import pytest

from saimc.extraction import llm_assist
from saimc.extraction.engine import ExtractionProposal
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
        Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[]),
    ]


@pytest.fixture
def provinces() -> list[Province]:
    return [
        Province(code="LH", name="La Habana"),
        Province(code="SCU", name="Santiago de Cuba"),
    ]


class TestCamposFaltantes:
    """Qué se le pide al modelo: los huecos que dejaron las reglas, nada más."""

    def test_una_propuesta_completa_no_deja_huecos(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = ExtractionProposal(
            product=products[0],
            price=Decimal("500"),
            quantity=Decimal("40"),
            unit="lb",
            province=provinces[0],
        )

        assert llm_assist.missing_fields(propuesta) == frozenset()

    def test_un_solo_hueco_se_pide_solo(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        propuesta = ExtractionProposal(
            product=products[0],
            price=Decimal("500"),
            quantity=None,
            unit="lb",
            province=provinces[0],
        )

        assert llm_assist.missing_fields(propuesta) == frozenset({"quantity"})

    def test_una_propuesta_vacia_deja_los_cinco(self) -> None:
        assert llm_assist.missing_fields(ExtractionProposal()) == frozenset(
            {"product", "price", "quantity", "unit", "province"}
        )

    def test_la_moneda_nunca_es_un_hueco(self) -> None:
        """§6.1 manda inferir CUP cuando el anuncio no la declara, así que
        `currency` nunca está vacía y no hay nada que preguntar."""
        propuesta = ExtractionProposal()

        assert propuesta.currency == "CUP"
        assert "currency" not in llm_assist.missing_fields(propuesta)

    def test_el_nombre_adivinado_no_cuenta_como_producto_resuelto(
        self, provinces: list[Province]
    ) -> None:
        """`product_name_guess` es texto para que el operador dé de alta algo,
        no un producto del catálogo: el hueco sigue abierto."""
        propuesta = ExtractionProposal(
            product=None, product_name_guess="Papas prefritas"
        )

        assert "product" in llm_assist.missing_fields(propuesta)


class TestResolucionContraElCatalogo:
    """Lo que el modelo responde vale solo si ya existe. Nada se da de alta."""

    def test_un_producto_por_su_nombre_resuelve(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"product": "Pollo"}, frozenset({"product"}), products, provinces
        )

        assert resultado.product is products[0]

    def test_un_producto_por_su_alias_resuelve(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"product": "pollo entero"}, frozenset({"product"}), products, provinces
        )

        assert resultado.product is products[0]

    def test_resuelve_ignorando_tildes_y_capitalizacion(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"product": "AZUCAR BLANCA"}, frozenset({"product"}), products, provinces
        )

        assert resultado.product is products[1]

    def test_un_producto_inventado_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"product": "Harina de yuca"},
            frozenset({"product"}),
            products,
            provinces,
        )

        assert resultado.product is None
        assert "product" in resultado.dropped

    def test_una_provincia_valida_resuelve(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"province": "Santiago de Cuba"},
            frozenset({"province"}),
            products,
            provinces,
        )

        assert resultado.province is provinces[1]

    def test_una_provincia_inventada_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"province": "Miami"}, frozenset({"province"}), products, provinces
        )

        assert resultado.province is None
        assert "province" in resultado.dropped

    @pytest.mark.parametrize(
        ("respondido", "esperado"),
        [("lb", "lb"), ("kg", "kg"), ("litros", "l"), ("cajas", "caja")],
    )
    def test_una_unidad_se_canonicaliza(
        self,
        respondido: str,
        esperado: str,
        products: list[Product],
        provinces: list[Province],
    ) -> None:
        resultado = llm_assist.parse_response(
            {"unit": respondido}, frozenset({"unit"}), products, provinces
        )

        assert resultado.unit == esperado

    def test_una_unidad_fuera_del_vocabulario_se_descarta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"unit": "quintal"}, frozenset({"unit"}), products, provinces
        )

        assert resultado.unit is None
        assert "unit" in resultado.dropped


class TestNumeros:
    @pytest.mark.parametrize("valor", [500, 500.5, "500", Decimal("500")])
    def test_acepta_las_formas_numericas_razonables(
        self, valor: object, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"price": valor}, frozenset({"price"}), products, provinces
        )

        assert resultado.price == Decimal(str(valor))

    @pytest.mark.parametrize("valor", [0, -1, "-40"])
    def test_un_numero_no_positivo_se_descarta(
        self, valor: object, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"quantity": valor}, frozenset({"quantity"}), products, provinces
        )

        assert resultado.quantity is None
        assert "quantity" in resultado.dropped

    def test_un_booleano_no_es_un_numero(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """`True` es `int` en Python: sin guarda explícita entraría como 1.
        Mismo cuidado que `measure._as_float` ya tiene por el mismo motivo."""
        resultado = llm_assist.parse_response(
            {"price": True}, frozenset({"price"}), products, provinces
        )

        assert resultado.price is None
        assert "price" in resultado.dropped

    @pytest.mark.parametrize("valor", ["muchos", [500], {"n": 500}, ""])
    def test_lo_que_no_es_numero_se_descarta(
        self, valor: object, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"price": valor}, frozenset({"price"}), products, provinces
        )

        assert resultado.price is None
        assert "price" in resultado.dropped


class TestFormaDeLaRespuesta:
    def test_un_campo_que_nadie_pidio_se_ignora(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Se pidió la cantidad y el modelo además opinó del precio. Ese
        precio no se usa: las reglas ya lo habían resuelto y por eso no
        estaba en el pedido (`design.md`, D1)."""
        resultado = llm_assist.parse_response(
            {"quantity": 25, "price": 999},
            frozenset({"quantity"}),
            products,
            provinces,
        )

        assert resultado.quantity == Decimal("25")
        assert resultado.price is None
        assert "price" not in resultado.dropped

    def test_un_campo_extra_desconocido_se_ignora(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"quantity": 25, "confianza": "alta"},
            frozenset({"quantity"}),
            products,
            provinces,
        )

        assert resultado.quantity == Decimal("25")

    def test_null_es_abstencion_y_no_un_descarte(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """El modelo diciendo "no sé" no es lo mismo que el modelo inventando
        algo que no resuelve. El registro de invocaciones distingue las dos
        (`design.md`, D6), así que el parser también."""
        resultado = llm_assist.parse_response(
            {"quantity": None}, frozenset({"quantity"}), products, provinces
        )

        assert resultado.quantity is None
        assert resultado.dropped == frozenset()

    def test_un_objeto_vacio_no_propone_nada(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {}, frozenset({"quantity", "price"}), products, provinces
        )

        assert resultado.answered == frozenset()
        assert resultado.dropped == frozenset()

    @pytest.mark.parametrize("cuerpo", [None, "texto suelto", [1, 2], 42])
    def test_un_cuerpo_que_no_es_objeto_no_propone_nada(
        self, cuerpo: object, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            cuerpo, frozenset({"quantity"}), products, provinces
        )

        assert resultado.answered == frozenset()

    def test_answered_lista_solo_lo_que_sobrevivio(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.parse_response(
            {"quantity": 25, "unit": "lb", "province": "Miami"},
            frozenset({"quantity", "unit", "province"}),
            products,
            provinces,
        )

        assert resultado.answered == frozenset({"quantity", "unit"})
        assert resultado.dropped == frozenset({"province"})


class TestNadaSeDaDeAlta:
    """La garantía central de esta capa, probada de forma explícita."""

    def test_ni_productos_ni_provincias_se_crean_desde_la_respuesta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        productos_antes = list(products)
        provincias_antes = list(provinces)

        llm_assist.parse_response(
            {
                "product": "Harina de yuca",
                "province": "Miami",
                "unit": "quintal",
            },
            frozenset({"product", "province", "unit"}),
            products,
            provinces,
        )

        assert products == productos_antes
        assert provinces == provincias_antes

    def test_el_producto_devuelto_es_el_del_catalogo_no_una_copia(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """Se devuelve la instancia del catálogo, no un objeto nuevo con el
        mismo nombre: lo que se persiste después es su `id`."""
        resultado = llm_assist.parse_response(
            {"product": "Pollo"}, frozenset({"product"}), products, provinces
        )

        assert resultado.product is products[0]


class TestEsquemaDeRespuesta:
    def test_el_esquema_solo_declara_los_campos_pedidos(self) -> None:
        esquema = llm_assist.response_schema(frozenset({"quantity", "unit"}))

        assert set(esquema["properties"]) == {"quantity", "unit"}

    def test_el_esquema_no_admite_campos_extra(self) -> None:
        esquema = llm_assist.response_schema(frozenset({"quantity"}))

        assert esquema["additionalProperties"] is False

    def test_todo_campo_admite_null_como_abstencion(self) -> None:
        esquema = llm_assist.response_schema(frozenset({"product", "price"}))

        for campo in esquema["properties"].values():
            assert "null" in campo["type"]
