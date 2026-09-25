"""`saimc.extraction.llm_propose_ad` — la propuesta de UN anuncio completo.

Sin red: el cliente es un doble. Las respuestas esperadas están escritas a mano
antes que el código y ningún test pregunta qué *debería* contestar un modelo
real (eso se mide contra el corpus curado, `docs/plan-asistente-llm-cola-completa.md`).
Lo que se prueba es la defensa: qué pasa con lo que no valida, con las fallas y
con el borde de privacidad.
"""

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from saimc.extraction import llm_assist, llm_propose_ad
from saimc.models import Product, Province


@pytest.fixture
def products() -> list[Product]:
    return [
        Product(name="Arroz", category="granos", unit="lb", aliases=["arroz blanco"]),
        Product(name="Pollo", category="carnes", unit="lb", aliases=[]),
    ]


@pytest.fixture
def provinces() -> list[Province]:
    return [
        Province(code="LH", name="La Habana"),
        Province(code="SCU", name="Santiago de Cuba"),
    ]


class ClienteFalso:
    """Doble de `anthropic.Anthropic`: solo `messages.create`."""

    def __init__(self, respuesta: object = None, error: Exception | None = None):
        self._respuesta = respuesta
        self._error = error
        self.llamadas: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._respuesta


def _respuesta(cuerpo: object, entrada: int = 100, salida: int = 40):
    texto = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=texto)],
        usage=SimpleNamespace(input_tokens=entrada, output_tokens=salida),
    )


def _fila(**campos) -> dict:
    base = {
        "product": None,
        "price": None,
        "currency": None,
        "quantity": None,
        "unit": None,
        "province": None,
        "evidence": None,
        "currency_explicit": False,
    }
    base.update(campos)
    return base


def _proponer(cliente, texto, products, provinces, **kw):
    return llm_propose_ad.propose_ad(
        texto, products, provinces, api_key="clave-falsa", client=cliente, **kw
    )


def test_respuesta_multifila_valida_devuelve_una_propuesta_por_fila(
    products, provinces
) -> None:
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(
                product="Arroz",
                price=17500,
                currency="CUP",
                quantity=25,
                unit="kg",
                province="Santiago de Cuba",
            ),
            _fila(product="pollo", price="330.5", currency="USD", quantity=1, unit="lb"),
        ],
    }
    cliente = ClienteFalso(_respuesta(cuerpo, entrada=900, salida=120))

    r = _proponer(cliente, "lista", products, provinces)

    assert r.degraded is False
    assert r.discard is False
    assert len(r.rows) == 2
    arroz, pollo = r.rows
    assert arroz.product is products[0]
    assert arroz.price == Decimal("17500")
    assert arroz.currency == "CUP"
    assert (arroz.quantity, arroz.unit) == (Decimal("25"), "kg")
    assert arroz.province is provinces[1]
    assert pollo.product is products[1]
    assert pollo.price == Decimal("330.5")
    assert pollo.currency == "USD"
    assert pollo.province is None
    assert (r.input_tokens, r.output_tokens) == (900, 120)
    # UNA sola llamada por anuncio, y con margen para varias filas.
    assert len(cliente.llamadas) == 1
    assert cliente.llamadas[0]["max_tokens"] >= 4096


def test_conserva_la_respuesta_cruda_del_modelo(products, provinces) -> None:
    """Slice 3a: la respuesta tal cual se guarda para ver en qué se van los
    tokens de salida. También cuando no parsea: es justo cuando más sirve."""
    texto = json.dumps({"discard": True, "discard_reason": "no_data", "rows": []})
    ok = _proponer(ClienteFalso(_respuesta(texto)), "anuncio", products, provinces)
    roto = _proponer(ClienteFalso(_respuesta("no es json")), "anuncio", products, provinces)
    fallo = _proponer(
        ClienteFalso(error=TimeoutError("x")), "anuncio", products, provinces
    )

    assert ok.raw_response == texto
    assert roto.degraded is True
    assert roto.raw_response == "no es json"
    assert fallo.raw_response is None


def test_producto_irresoluble_descarta_la_fila_y_provincia_o_unidad_solo_el_campo(
    products, provinces
) -> None:
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(product="Yuca", price=100, currency="CUP"),  # fuera del catálogo
            _fila(
                product="Arroz",
                price=200,
                currency="CUP",
                quantity=1,
                unit="quintal",  # unidad inventada
                province="Atlantida",  # provincia inventada
            ),
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "x", products, provinces)

    assert len(r.rows) == 1
    fila = r.rows[0]
    assert fila.product is products[0]
    assert fila.price == Decimal("200")
    assert fila.unit is None
    assert fila.province is None
    # 1 fila entera + 1 unidad + 1 provincia.
    assert r.dropped == 3


def test_precio_invalido_y_moneda_desconocida_no_se_inventan(
    products, provinces
) -> None:
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [_fila(product="Arroz", price=-5, currency="PESOS", quantity=0)],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "x", products, provinces)

    fila = r.rows[0]
    assert fila.price is None
    assert fila.quantity is None
    # La moneda sin marca válida cae a CUP, igual que las reglas (spec §6.1).
    assert fila.currency == "CUP"


def test_veredicto_de_descarte_con_motivo(products, provinces) -> None:
    cuerpo = {"discard": True, "discard_reason": "off_focus", "rows": []}

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "x", products, provinces)

    assert r.discard is True
    assert r.discard_reason == "off_focus"
    assert r.rows == []
    assert r.degraded is False


def test_motivo_de_descarte_fuera_del_vocabulario_se_ignora(
    products, provinces
) -> None:
    cuerpo = {"discard": True, "discard_reason": "porque si", "rows": []}

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "x", products, provinces)

    assert r.discard is True
    assert r.discard_reason is None


@pytest.mark.parametrize(
    "cliente_factory",
    [
        lambda: ClienteFalso(error=TimeoutError("timed out")),
        lambda: ClienteFalso(error=RuntimeError("boom")),
        lambda: ClienteFalso(_respuesta("{esto no es json")),
        lambda: ClienteFalso(_respuesta('{"discard": false, "rows": [{"product": "Arr')),
        lambda: ClienteFalso(_respuesta(["no", "es", "objeto"])),
        lambda: ClienteFalso(SimpleNamespace(content=[], usage=None)),
    ],
    ids=["timeout", "error", "json-roto", "truncado", "no-objeto", "sin-contenido"],
)
def test_nunca_levanta_y_degrada(cliente_factory, products, provinces) -> None:
    r = _proponer(cliente_factory(), "x", products, provinces)

    assert r.degraded is True
    assert r.rows == []
    assert r.discard is None
    assert r.failure != ""


def test_clasifica_la_falla(products, provinces) -> None:
    r = _proponer(
        ClienteFalso(error=TimeoutError("timed out")), "x", products, provinces
    )
    assert r.failure == "timeout"


def test_sin_clave_no_llama_ni_registra(products, provinces, tmp_path: Path) -> None:
    cliente = ClienteFalso(_respuesta({}))
    log = tmp_path / "calls.jsonl"

    r = llm_propose_ad.propose_ad(
        "x", products, provinces, api_key=None, client=cliente, log_path=log
    )

    assert r.degraded is True
    assert cliente.llamadas == []
    assert not log.exists()


def test_el_telefono_no_llega_al_cliente(products, provinces) -> None:
    cliente = ClienteFalso(
        _respuesta({"discard": True, "discard_reason": "no_data", "rows": []})
    )

    _proponer(
        cliente, "Arroz a 500, llamar al +53 52345678 o 55551234", products, provinces
    )

    enviado = json.dumps(cliente.llamadas[0], ensure_ascii=False)
    assert "52345678" not in enviado
    assert "55551234" not in enviado


def test_el_prompt_explica_la_tarea_y_lleva_los_catalogos(
    products, provinces
) -> None:
    cliente = ClienteFalso(
        _respuesta({"discard": True, "discard_reason": "no_data", "rows": []})
    )

    _proponer(cliente, "Arroz a 500", products, provinces)

    llamada = cliente.llamadas[0]
    system = llamada["system"].lower()
    usuario = llamada["messages"][0]["content"]
    assert "null" in system  # abstenerse
    assert "oferta" in system  # precio de la oferta, no derivado
    assert "Arroz" in usuario and "Pollo" in usuario
    assert "Santiago de Cuba" in usuario
    assert "kg" in usuario  # unidades válidas
    assert "Arroz a 500" in usuario
    assert llamada["output_config"]["format"]["type"] == "json_schema"


def test_registra_cada_invocacion_en_su_propio_archivo(
    products, provinces, tmp_path: Path
) -> None:
    log = tmp_path / "calls.jsonl"
    ok = ClienteFalso(
        _respuesta({"discard": True, "discard_reason": "no_data", "rows": []}, 10, 5)
    )
    roto = ClienteFalso(error=TimeoutError("timed out"))

    _proponer(ok, "a", products, provinces, ingestion_id="id-1", log_path=log)
    _proponer(roto, "b", products, provinces, ingestion_id="id-2", log_path=log)

    lineas = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["ingestion_id"] for x in lineas] == ["id-1", "id-2"]
    assert lineas[0]["degraded"] is False
    assert (lineas[0]["input_tokens"], lineas[0]["output_tokens"]) == (10, 5)
    assert lineas[1]["degraded"] is True
    assert lineas[1]["failure"] == "timeout"


def test_cada_linea_del_registro_lleva_la_version_del_prompt(
    products, provinces, tmp_path: Path
) -> None:
    log = tmp_path / "calls.jsonl"
    ok = ClienteFalso(
        _respuesta({"discard": True, "discard_reason": "no_data", "rows": []})
    )
    roto = ClienteFalso(error=TimeoutError("timed out"))

    _proponer(ok, "a", products, provinces, ingestion_id="id-1", log_path=log)
    _proponer(roto, "b", products, provinces, ingestion_id="id-2", log_path=log)

    lineas = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["prompt_version"] for x in lineas] == ["2026-09-25.4"] * 2
    assert llm_propose_ad.PROMPT_VERSION == "2026-09-25.4"


def test_el_prompt_pide_una_fila_por_oferta_y_el_lado_cup() -> None:
    """Prueba débil y honesta: solo verifica que las reglas ESTÁN escritas.

    Que el modelo las obedezca se mide re-corriendo el backtest
    (`docs/runbook-asistente-llm.md`, «Backtest»), no acá.
    """
    prompt = llm_propose_ad._SYSTEM_PROMPT

    assert "dos monedas" in prompt  # una oferta en dos monedas = UNA fila
    assert "el mayor" in prompt  # el lado CUP es el número mayor
    assert "marca de moneda explícita" in prompt  # con marca, gana la marcada


def test_el_prompt_pide_el_peso_del_envase_solo_con_unidades_validas() -> None:
    """Prueba débil y honesta: verifica el texto de la regla, no la conducta.

    El efecto real (40 lb en vez de 1 caja) se mide re-corriendo el backtest.
    """
    prompt = llm_propose_ad._SYSTEM_PROMPT

    assert "envase" in prompt and "caja" in prompt
    assert "peso" in prompt and "null" in prompt  # sin peso declarado, se abstiene
    assert "40 lb" in prompt  # el ejemplo usa una unidad del vocabulario cerrado
    assert llm_propose_ad.canonical_unit("lb") == "lb"


def test_su_registro_no_contamina_los_asistidos_del_operador(
    products, provinces, tmp_path: Path
) -> None:
    """El backtest jamás debe sacar anuncios de la medición de H0."""
    log_backtest = tmp_path / "backtest.jsonl"
    log_operador = tmp_path / "assist.jsonl"
    cliente = ClienteFalso(
        _respuesta({"discard": True, "discard_reason": "no_data", "rows": []})
    )

    _proponer(cliente, "a", products, provinces, ingestion_id="x", log_path=log_backtest)

    assert llm_assist.assisted_ingestion_ids(log_operador) == set()


def test_texto_vacio_no_llama(products, provinces) -> None:
    cliente = ClienteFalso(_respuesta({}))

    r = _proponer(cliente, "   ", products, provinces)

    assert r.degraded is True
    assert r.failure == "empty_text"
    assert cliente.llamadas == []


def _propiedades_con_enum(nodo: object) -> list[dict]:
    """Todos los nodos del esquema que declaran `enum`, a cualquier profundidad."""
    encontrados: list[dict] = []
    if isinstance(nodo, dict):
        if "enum" in nodo:
            encontrados.append(nodo)
        for valor in nodo.values():
            encontrados.extend(_propiedades_con_enum(valor))
    elif isinstance(nodo, list):
        for valor in nodo:
            encontrados.extend(_propiedades_con_enum(valor))
    return encontrados


def test_esquema_no_combina_enum_con_tipo_en_lista() -> None:
    """La API rechaza `{"type": ["string", "null"], "enum": [...]}`.

    Medido contra la API real el 2026-09-25 (HTTP 400: «Enum value 'off_focus'
    does not match declared type '['string', 'null']'»). Un cliente falso no
    puede ver esto, así que la restricción queda escrita como invariante del
    esquema: un `enum` se declara con un único tipo, y la nulabilidad va aparte
    con `anyOf`.
    """
    nodos = _propiedades_con_enum(llm_propose_ad.response_schema())

    assert nodos, "el esquema debería declarar al menos un enum"
    for nodo in nodos:
        assert not isinstance(nodo.get("type"), list), nodo


@pytest.mark.skipif(
    os.environ.get("SAIMC_LLM_SMOKE") != "1"
    or not os.environ.get("SAIMC_ANTHROPIC_API_KEY"),
    reason="requiere SAIMC_LLM_SMOKE=1 y la clave — cuesta plata",
)
def test_smoke_real_respeta_el_contrato(products, provinces) -> None:
    r = llm_propose_ad.propose_ad(
        "Sacos de arroz 25 kg a 17500 cup, Santiago de Cuba",
        products,
        provinces,
        api_key=os.environ["SAIMC_ANTHROPIC_API_KEY"],
    )

    assert isinstance(r, llm_propose_ad.AdProposal)
    for fila in r.rows:
        assert fila.product in products


# --- v3: evidencia, moneda explícita y regla de doble precio ---------------


def test_el_prompt_v3_trae_los_dos_ejemplos_de_doble_precio() -> None:
    """Prueba débil y honesta: verifica que los ejemplos ESTÁN escritos.

    Que el modelo elija USD 42 en «42 usd o 28560» y CUP 27400 en «40 o 27400» se
    mide re-corriendo el backtest (`docs/runbook-asistente-llm.md`, «Backtest»),
    no acá.
    """
    prompt = llm_propose_ad._SYSTEM_PROMPT

    assert "42 usd o 28560" in prompt
    assert "40 o 27400" in prompt


def test_el_esquema_pide_evidencia_y_moneda_explicita_por_fila() -> None:
    fila = llm_propose_ad.response_schema()["properties"]["rows"]["items"]

    assert "evidence" in fila["required"] and "currency_explicit" in fila["required"]
    assert fila["properties"]["currency_explicit"] == {"type": "boolean"}
    # Nulabilidad con anyOf: la API rechaza enum junto a un tipo en lista.
    assert fila["properties"]["evidence"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }


def test_row_meta_lleva_evidencia_moneda_y_verificacion(products, provinces) -> None:
    texto = "Arroz 25 lb a 30.885 cup"
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(
                product="Arroz", price=30885, currency="CUP", quantity=25, unit="lb",
                evidence="arroz 25 lb a 30.885 cup", currency_explicit=True,
            ),
            _fila(
                product="Pollo", price=500, currency="CUP",
                evidence="pollo 500 fresco", currency_explicit=False,
            ),
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), texto, products, provinces)

    assert len(r.row_meta) == len(r.rows) == 2
    arroz, pollo = r.row_meta
    assert (arroz.evidence, arroz.currency_explicit) == ("arroz 25 lb a 30.885 cup", True)
    assert (arroz.evidence_found, arroz.price_in_evidence) == (True, True)
    # El fragmento del pollo no está en el anuncio: fila sospechosa.
    assert (pollo.currency_explicit, pollo.evidence_found) == (False, False)
    # `price_in_evidence` mira el fragmento citado, no el anuncio: 500 sí está en él.
    assert pollo.price_in_evidence is True


def test_meta_malformada_degrada_a_none_y_false_sin_levantar(products, provinces) -> None:
    sin_claves = _fila(product="Pollo", price=200)
    sin_claves.pop("evidence")
    sin_claves.pop("currency_explicit")
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(product="Arroz", price=100, evidence=123, currency_explicit="si"),
            sin_claves,
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "Arroz 100 Pollo 200", products, provinces)

    assert r.degraded is False and len(r.rows) == 2
    for meta in r.row_meta:
        assert meta.evidence is None
        assert meta.currency_explicit is False
        assert meta.evidence_found is False


# --- slice 3d: formato de venta, base del precio y envase ------------------
#
# Casos escritos antes del código. La respuesta del cliente falso es la que un
# operador daría para el anuncio real «Pollo Muslo 4x10: 27300 O 39»: 4
# paquetes de 10 lb, la caja pesa 40 lb, 27300 es el precio de la caja entera
# y un hogar la compra (minorista). No se pregunta qué contestaría un modelo
# real: eso se mide con el backtest.


def test_esquema_pide_formato_base_y_envase_nulables_con_anyof() -> None:
    fila = llm_propose_ad.response_schema()["properties"]["rows"]["items"]

    for campo in ("sale_format", "price_basis", "packaging"):
        assert campo in fila["required"], campo
    assert fila["properties"]["sale_format"] == {
        "anyOf": [{"type": "string", "enum": ["retail", "wholesale"]}, {"type": "null"}]
    }
    assert fila["properties"]["price_basis"] == {
        "anyOf": [{"type": "string", "enum": ["unit", "total"]}, {"type": "null"}]
    }
    assert fila["properties"]["packaging"] == {
        "anyOf": [
            {
                "type": "string",
                "enum": ["paquete", "saco", "caja", "botella", "pomo", "a granel"],
            },
            {"type": "null"},
        ]
    }


def test_todo_enum_del_esquema_admite_null_solo_por_anyof() -> None:
    """Invariante: cada `enum` de fila vive dentro de un `anyOf` con `null`.

    Los enums de fila son todos nulables (abstenerse es válido), y el único
    camino que la API acepta para eso es `anyOf` (`_enum_o_null`).
    """
    fila = llm_propose_ad.response_schema()["properties"]["rows"]["items"]

    for nombre, propiedad in fila["properties"].items():
        if not _propiedades_con_enum(propiedad):
            continue
        assert set(propiedad) == {"anyOf"}, nombre
        assert {"type": "null"} in propiedad["anyOf"], nombre


def test_version_del_prompt_3d() -> None:
    assert llm_propose_ad.PROMPT_VERSION == "2026-09-25.4"


def test_pollo_muslo_4x10_trae_caja_total_y_minorista(products, provinces) -> None:
    texto = "Pollo Muslo 4x10: 27300 O 39"
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(
                product="Pollo", price=27300, currency="CUP", quantity=40, unit="lb",
                evidence="Pollo Muslo 4x10: 27300", currency_explicit=False,
                sale_format="retail", price_basis="total", packaging="caja",
            )
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), texto, products, provinces)

    (fila,) = r.rows
    assert (fila.price, fila.quantity, fila.unit) == (Decimal("27300"), Decimal("40"), "lb")
    assert fila.sale_format == "retail"
    assert fila.price_basis == "total"
    assert fila.packaging == "caja"
    assert r.dropped == 0


def test_valores_fuera_de_vocabulario_caen_a_none_y_cuentan_como_perdidos(
    products, provinces
) -> None:
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(
                product="Arroz", price=100,
                sale_format="mayorista", price_basis="por_lote", packaging="jaba",
            ),
            # Abstenerse (null) no es perder.
            _fila(product="Pollo", price=200, sale_format=None, price_basis=None, packaging=None),
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "Arroz 100 Pollo 200", products, provinces)

    assert r.degraded is False and len(r.rows) == 2
    for fila in r.rows:
        assert (fila.sale_format, fila.price_basis, fila.packaging) == (None, None, None)
    assert r.dropped == 3


def test_envase_se_canoniza_y_tipos_raros_no_levantan(products, provinces) -> None:
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            _fila(product="Arroz", price=100, packaging=" Saco ", sale_format=1, price_basis=[]),
            _fila(product="Pollo", price=200),  # respuesta vieja, sin los tres campos
        ],
    }

    r = _proponer(ClienteFalso(_respuesta(cuerpo)), "Arroz 100 Pollo 200", products, provinces)

    arroz, pollo = r.rows
    assert arroz.packaging == "saco"
    assert (arroz.sale_format, arroz.price_basis) == (None, None)
    assert (pollo.sale_format, pollo.price_basis, pollo.packaging) == (None, None, None)


def test_el_prompt_define_mayorista_base_y_envase_como_el_formulario() -> None:
    """Prueba débil y honesta: las definiciones del operador ESTÁN escritas.

    La copia sale del texto de ayuda de `_price_row_card.html`; que el modelo
    las aplique se mide con el backtest.
    """
    prompt = llm_propose_ad._SYSTEM_PROMPT

    assert "Mayorista es escala de contenedor, no vender un saco o paquete individual" in prompt
    assert "eso lo ofrece cualquier minorista desde su casa" in prompt
    assert "Pollo Muslo 4x10: 27300" in prompt
    assert "price_basis" in prompt and "packaging" in prompt and "sale_format" in prompt


def test_filled_fields_cuenta_los_tres_campos_nuevos(products) -> None:
    from saimc.extraction.engine import ExtractionProposal

    fila = ExtractionProposal(
        product=products[1], price=Decimal("27300"), quantity=Decimal("40"), unit="lb",
        sale_format="retail", price_basis="total", packaging="caja",
    )
    sin_nada = ExtractionProposal(product=products[1])

    assert {"sale_format", "price_basis", "packaging"} <= set(
        llm_propose_ad.filled_fields(fila)
    )
    assert llm_propose_ad.filled_fields(sin_nada) == ["product"]
    assert {"sale_format", "price_basis", "packaging"} <= set(llm_propose_ad.ASSIST_FIELDS)
