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

import os
from decimal import Decimal

import pytest

from saimc.extraction import llm_assist
from saimc.extraction.engine import ExtractionProposal
from saimc.models import Product, Province


def _asistente_real_disponible() -> bool:
    """El smoke real corre solo con una VARIABLE PROPIA, no con la clave.

    Tener la clave configurada en el servidor es lo normal cuando el asistente
    está en uso; que eso además hiciera gastar plata al correr la suite sería
    un cobro que el operador nunca pidió. Correrlo es una decisión aparte:

        SAIMC_LLM_SMOKE=1 .venv/bin/pytest tests/test_llm_assist.py
    """
    if os.environ.get("SAIMC_LLM_SMOKE") != "1":
        return False
    if not os.environ.get("SAIMC_ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


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

    def test_todo_campo_pedido_es_requerido(self) -> None:
        """Con `additionalProperties: False`, declarar todo requerido deja la
        forma completamente determinada. Abstenerse sigue siendo posible: el
        campo viene, con `null` adentro."""
        esquema = llm_assist.response_schema(frozenset({"product", "price"}))

        assert set(esquema["required"]) == {"product", "price"}


class _ClienteFalso:
    """Imita lo único que este módulo le pide al SDK: `messages.create(...)`
    devolviendo algo con `.content` de bloques con `.type` y `.text`."""

    def __init__(self, texto: str | None = None, error: Exception | None = None):
        self.texto = texto
        self.error = error
        self.llamadas: list[dict] = []
        self.messages = self

    def create(self, **kwargs: object) -> object:
        self.llamadas.append(kwargs)
        if self.error is not None:
            raise self.error
        bloque = type("Bloque", (), {"type": "text", "text": self.texto})()
        return type("Respuesta", (), {"content": [bloque]})()


class TestLlamadaYDegradacion:
    """Toda falla esperable degrada a «sin propuesta». Nunca levanta."""

    def test_sin_clave_no_llama_ni_propone(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto='{"quantity": 25}')

        resultado = llm_assist.propose_missing(
            "arroz 25 kg",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key=None,
            client=cliente,
        )

        assert resultado.answered == frozenset()
        assert cliente.llamadas == []

    def test_sin_huecos_no_llama(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto='{"quantity": 25}')

        resultado = llm_assist.propose_missing(
            "arroz 25 kg",
            frozenset(),
            products,
            provinces,
            api_key="k",
            client=cliente,
        )

        assert resultado.answered == frozenset()
        assert cliente.llamadas == []

    @pytest.mark.parametrize(
        "error",
        [RuntimeError("boom"), TimeoutError("expiró"), OSError("red caída")],
    )
    def test_un_error_del_cliente_degrada(
        self, error: Exception, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.propose_missing(
            "arroz 25 kg",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(error=error),
        )

        assert resultado.answered == frozenset()

    @pytest.mark.parametrize("cuerpo", ["no es json", "", "[1,2]", "null"])
    def test_un_cuerpo_no_parseable_degrada(
        self, cuerpo: str, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.propose_missing(
            "arroz 25 kg",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(texto=cuerpo),
        )

        assert resultado.answered == frozenset()

    def test_una_respuesta_sin_bloques_de_texto_degrada(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto=None)
        cliente.create = lambda **kw: type("R", (), {"content": []})()  # type: ignore[method-assign]

        resultado = llm_assist.propose_missing(
            "arroz 25 kg",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=cliente,
        )

        assert resultado.answered == frozenset()

    def test_una_respuesta_valida_propone(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        resultado = llm_assist.propose_missing(
            "sacos de arroz 25 kg",
            frozenset({"quantity", "unit"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(texto='{"quantity": 25, "unit": "kg"}'),
        )

        assert resultado.quantity == Decimal("25")
        assert resultado.unit == "kg"

    def test_lo_que_no_resuelve_sigue_descartandose(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        """La validación del núcleo puro no se saltea por venir de la red."""
        resultado = llm_assist.propose_missing(
            "algo",
            frozenset({"product"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(texto='{"product": "Harina de yuca"}'),
        )

        assert resultado.product is None
        assert "product" in resultado.dropped


class TestSoloSaleTextoRedactado:
    def test_el_telefono_no_viaja(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto='{"quantity": 25}')

        llm_assist.propose_missing(
            "Sacos de arroz 25 kg\n+53 5 4728658\nllame ya",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=cliente,
        )

        enviado = str(cliente.llamadas[0]["messages"])
        assert "4728658" not in enviado
        assert "[teléfono removido]" in enviado

    def test_el_texto_del_anuncio_si_viaja(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto='{"quantity": 25}')

        llm_assist.propose_missing(
            "Sacos de arroz 25 kg",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=cliente,
        )

        assert "Sacos de arroz 25 kg" in str(cliente.llamadas[0]["messages"])

    def test_se_pide_solo_lo_que_falta(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto='{"quantity": 25}')

        llm_assist.propose_missing(
            "algo",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=cliente,
        )

        esquema = cliente.llamadas[0]["output_config"]["format"]["schema"]
        assert set(esquema["properties"]) == {"quantity"}

    def test_se_respeta_el_modelo_y_el_timeout_configurados(
        self, products: list[Product], provinces: list[Province]
    ) -> None:
        cliente = _ClienteFalso(texto="{}")

        llm_assist.propose_missing(
            "algo",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=cliente,
            model="claude-haiku-4-5",
        )

        assert cliente.llamadas[0]["model"] == "claude-haiku-4-5"


@pytest.mark.skipif(
    not _asistente_real_disponible(),
    reason="requiere SAIMC_LLM_SMOKE=1, la clave y el extra `llm` — cuesta plata",
)
def test_smoke_asistente_real_devuelve_algo_valido(
    products: list[Product], provinces: list[Province]
) -> None:
    """Único test que llama a la API de verdad, y por eso **cuesta plata**
    (centavos, ver `docs/costos.md`). Se saltea sin clave, igual que los smoke
    de Tesseract y RapidOCR en `tests/test_ocr.py`.

    Assertea que la respuesta es del tipo correcto y que respeta el contrato
    del módulo — NUNCA qué debería haber contestado el modelo. Esa pregunta se
    responde midiendo contra la cola real (`design.md`, D9).
    """
    resultado = llm_assist.propose_missing(
        "Sacos de arroz 25 kg a 17500 cup, Santiago de Cuba",
        frozenset({"quantity", "unit"}),
        products,
        provinces,
        api_key=os.environ["SAIMC_ANTHROPIC_API_KEY"],
    )

    assert isinstance(resultado, llm_assist.AssistedFields)
    # Lo que responda tiene que haber pasado la validación: nada fuera del
    # vocabulario puede salir de acá.
    if resultado.unit is not None:
        assert resultado.unit in ("lb", "kg", "l", "ml", "unidad", "docena", "caja")
    if resultado.quantity is not None:
        assert resultado.quantity > 0
    assert resultado.answered <= frozenset({"quantity", "unit"})


class TestRegistroDeInvocaciones:
    """Ninguna invocación puede ocurrir sin dejar rastro (`design.md`, D6).

    El registro es lo que después permite medir H0 sobre el conjunto NO
    asistido, que es el número comparable con todo el histórico de
    `docs/h0-runs.jsonl`. Sin él, una respuesta del modelo confirmada de apuro
    entra en la verdad contra la que se miden las reglas y nadie se entera.
    """

    def test_una_llamada_exitosa_deja_una_linea(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        log = tmp_path / "assist.jsonl"

        llm_assist.propose_missing(
            "sacos de arroz 25 kg",
            frozenset({"quantity", "unit"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(texto='{"quantity": 25, "unit": "quintal"}'),
            ingestion_id="ing-1",
            log_path=log,
        )

        corridas = llm_assist.read_assist_runs(log)
        assert len(corridas) == 1
        assert corridas[0].ingestion_id == "ing-1"
        assert corridas[0].requested == ["quantity", "unit"]
        assert corridas[0].answered == ["quantity"]
        assert corridas[0].dropped == ["unit"]
        assert corridas[0].degraded is False

    def test_una_llamada_degradada_tambien_deja_rastro(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        log = tmp_path / "assist.jsonl"

        llm_assist.propose_missing(
            "algo",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(error=TimeoutError("expiró")),
            ingestion_id="ing-2",
            log_path=log,
        )

        corridas = llm_assist.read_assist_runs(log)
        assert len(corridas) == 1
        assert corridas[0].degraded is True
        assert corridas[0].answered == []

    def test_dos_invocaciones_suman_dos_lineas(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        """Solo-anexado: la segunda no pisa a la primera."""
        log = tmp_path / "assist.jsonl"
        for ident in ("ing-1", "ing-2"):
            llm_assist.propose_missing(
                "algo",
                frozenset({"quantity"}),
                products,
                provinces,
                api_key="k",
                client=_ClienteFalso(texto='{"quantity": 5}'),
                ingestion_id=ident,
                log_path=log,
            )

        assert [c.ingestion_id for c in llm_assist.read_assist_runs(log)] == [
            "ing-1",
            "ing-2",
        ]

    def test_sin_clave_no_se_registra_nada(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        """No hubo invocación: no hay nada que registrar. El registro cuenta
        intentos reales, no configuraciones ausentes."""
        log = tmp_path / "assist.jsonl"

        llm_assist.propose_missing(
            "algo",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key=None,
            client=_ClienteFalso(texto='{"quantity": 5}'),
            ingestion_id="ing-1",
            log_path=log,
        )

        assert llm_assist.read_assist_runs(log) == []

    def test_un_archivo_inexistente_se_lee_vacio(self, tmp_path) -> None:
        assert llm_assist.read_assist_runs(tmp_path / "no-existe.jsonl") == []

    def test_una_linea_rota_no_tumba_la_lectura(self, tmp_path) -> None:
        """Mismo criterio tolerante que `measure.read_h0_runs`: un archivo de
        solo-anexado acumula líneas a medio escribir y eso no puede romper a
        quien lo lee."""
        log = tmp_path / "assist.jsonl"
        log.write_text(
            '{"ingestion_id": "ok-1", "requested": [], "answered": [], '
            '"dropped": [], "degraded": false, "model": "m", "timestamp": "t"}\n'
            "\n"
            "{esto no es json\n"
            "[1, 2]\n"
            '{"ingestion_id": "ok-2", "requested": [], "answered": [], '
            '"dropped": [], "degraded": false, "model": "m", "timestamp": "t"}\n',
            encoding="utf-8",
        )

        assert [c.ingestion_id for c in llm_assist.read_assist_runs(log)] == [
            "ok-1",
            "ok-2",
        ]

    def test_los_ids_asistidos_se_pueden_consultar(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        """Es lo que el commit 4 necesita para medir H0 sobre lo no asistido."""
        log = tmp_path / "assist.jsonl"
        for ident in ("ing-1", "ing-2", "ing-1"):
            llm_assist.propose_missing(
                "algo",
                frozenset({"quantity"}),
                products,
                provinces,
                api_key="k",
                client=_ClienteFalso(texto='{"quantity": 5}'),
                ingestion_id=ident,
                log_path=log,
            )

        assert llm_assist.assisted_ingestion_ids(log) == {"ing-1", "ing-2"}

    def test_un_fallo_al_escribir_el_registro_no_rompe_la_asistencia(
        self, tmp_path, products: list[Product], provinces: list[Province]
    ) -> None:
        """El registro es telemetría: si no se puede escribir, la curaduría
        sigue. Mismo criterio que `measure._git`, que degrada en vez de
        reventar la corrida."""
        inaccesible = tmp_path / "no-existe" / "sub" / "assist.jsonl"
        (tmp_path / "no-existe").write_text("soy un archivo, no un directorio")

        resultado = llm_assist.propose_missing(
            "algo",
            frozenset({"quantity"}),
            products,
            provinces,
            api_key="k",
            client=_ClienteFalso(texto='{"quantity": 5}'),
            ingestion_id="ing-1",
            log_path=inaccesible,
        )

        assert resultado.quantity == Decimal("5")
