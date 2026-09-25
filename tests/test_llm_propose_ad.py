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
