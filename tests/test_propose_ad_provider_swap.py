"""`propose_ad` con el proveedor elegido en la configuración (`llm=`).

Dos contratos:

- Con `llm`, manda `llm`: clave, URL, timeout y los dos nombres de modelo. A la
  llamada va `request_model` (con prefijo en Vercel); a todo lo que se registra
  o se tarifa, `model` (el canónico).
- Sin `llm`, `propose_ad` se comporta como antes de la fábrica: `api_key`,
  `model` y `timeout` explícitos, Anthropic directo, y sin clave degrada con
  `no_key`. No va a buscar configuración por su cuenta.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from saimc.config import Settings
from saimc.extraction.llm_assist import usage_cost_usd
from saimc.extraction.llm_propose_ad import propose_ad
from saimc.llm import provider

_GATEWAY = "https://ai-gateway.vercel.sh"


def _respuesta(payload: dict) -> object:
    return types.SimpleNamespace(
        usage=types.SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class _FakeClient:
    """Cliente que captura los kwargs de `messages.create`."""

    def __init__(self, **init_kwargs: object) -> None:
        self.init_kwargs = init_kwargs
        self.kwargs: dict = {}
        self.messages = self

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return _respuesta({"discard": False, "discard_reason": None, "rows": []})


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Entorno limpio: `Settings(_env_file=None)` igual lee el del proceso."""
    for name in (
        "SAIMC_LLM_PROVIDER",
        "SAIMC_LLM_MODEL",
        "SAIMC_LLM_TIMEOUT",
        "SAIMC_LLM_ASSIST_MODEL",
        "SAIMC_LLM_ASSIST_TIMEOUT",
        "SAIMC_ANTHROPIC_API_KEY",
        "SAIMC_LLM_JEV_API_KEY",
        "SAIMC_VERCEL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> list[_FakeClient]:
    """Reemplaza `import anthropic` y devuelve los clientes que se armaron.

    Para los tests que NO pasan `client`: así se ve con qué clave, URL y
    timeout se construye el cliente real, sin red.
    """
    armados: list[_FakeClient] = []

    def _anthropic(**kwargs: object) -> _FakeClient:
        cliente = _FakeClient(**kwargs)
        armados.append(cliente)
        return cliente

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return armados


def _vercel(**kw: object) -> provider.LLMConfig:
    ajustes = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="vercel", llm_jev_api_key="vck-test", **kw
    )
    return provider.resolve_config(ajustes)


def _lineas(ruta: Path) -> list[dict]:
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]


# ---------- con `llm` ------------------------------------------------------

def test_con_vercel_la_llamada_lleva_prefijo_y_el_registro_no(tmp_path: Path) -> None:
    """El defecto 3: con el prefijo en el registro, la tabla de tarifas no
    encuentra el modelo y el costo da `None`."""
    fake = _FakeClient()
    registro = tmp_path / "calls.jsonl"

    result = propose_ad(
        "pollo a 500", [], [], llm=_vercel(), client=fake, ingestion_id="i-1", log_path=registro
    )

    assert not result.degraded, result.failure
    assert fake.kwargs["model"] == "anthropic/claude-sonnet-5"
    (linea,) = _lineas(registro)
    assert linea["model"] == "claude-sonnet-5"
    # 1000 de entrada a USD 2 y 500 de salida a USD 10, por millón.
    costo = usage_cost_usd(linea["input_tokens"], linea["output_tokens"], linea["model"])
    assert costo == pytest.approx(0.007)


def test_con_vercel_y_modelo_prefijado_en_la_config_no_se_duplica(tmp_path: Path) -> None:
    fake = _FakeClient()
    registro = tmp_path / "calls.jsonl"

    propose_ad(
        "pollo a 500",
        [],
        [],
        llm=_vercel(llm_model="anthropic/claude-haiku-4-5"),
        client=fake,
        log_path=registro,
    )

    assert fake.kwargs["model"] == "anthropic/claude-haiku-4-5"
    assert _lineas(registro)[0]["model"] == "claude-haiku-4-5"


def test_con_llm_el_cliente_se_arma_con_su_clave_url_y_timeout(sdk: list[_FakeClient]) -> None:
    """El defecto 4, del lado de `llm`: lo que llega al cliente es lo de la
    config, aunque quien llama pase además los argumentos sueltos."""
    result = propose_ad(
        "pollo a 500",
        [],
        [],
        llm=_vercel(llm_timeout=45.5),
        api_key="sk-ant-NO-DEBE-VIAJAR",
        model="claude-opus-5",
        timeout=7.0,
    )

    assert not result.degraded, result.failure
    (cliente,) = sdk
    assert cliente.init_kwargs == {"api_key": "vck-test", "timeout": 45.5, "base_url": _GATEWAY}
    assert cliente.kwargs["model"] == "anthropic/claude-sonnet-5"


def test_con_llm_y_modelo_pisado_viaja_el_pisado(tmp_path: Path) -> None:
    """`--model` del pre-cálculo y del backtest."""
    fake = _FakeClient()
    registro = tmp_path / "calls.jsonl"

    propose_ad(
        "pollo a 500",
        [],
        [],
        llm=_vercel().with_overrides(model="claude-opus-5"),
        client=fake,
        log_path=registro,
    )

    assert fake.kwargs["model"] == "anthropic/claude-opus-5"
    assert _lineas(registro)[0]["model"] == "claude-opus-5"


def test_con_llm_anthropic_no_hay_prefijo_ni_base_url(sdk: list[_FakeClient]) -> None:
    ajustes = Settings(_env_file=None, anthropic_api_key="sk-ant-test")  # type: ignore[call-arg]

    propose_ad("pollo a 500", [], [], llm=provider.resolve_config(ajustes))

    (cliente,) = sdk
    assert cliente.init_kwargs == {"api_key": "sk-ant-test", "timeout": 30.0}
    assert cliente.kwargs["model"] == "claude-sonnet-5"


# ---------- sin `llm`: como antes de la fábrica ----------------------------

def test_sin_llm_y_sin_clave_degrada_con_no_key_aunque_el_entorno_tenga_una(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`propose_ad` no resuelve configuración por su cuenta: es de quien llama."""
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-del-entorno")
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-del-entorno")
    fake = _FakeClient()
    registro = tmp_path / "calls.jsonl"

    result = propose_ad("pollo a 500", [], [], api_key=None, client=fake, log_path=registro)

    assert result.degraded
    assert result.failure == "no_key"
    assert fake.kwargs == {}
    # Sin invocación no hay registro.
    assert not registro.exists()


def test_sin_llm_la_clave_explicita_llega_al_cliente(
    env: pytest.MonkeyPatch, sdk: list[_FakeClient]
) -> None:
    """El defecto 4: la clave explícita se calculaba pero el cliente se armaba
    con la de la configuración."""
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-del-entorno")

    result = propose_ad("pollo a 500", [], [], api_key="sk-ant-explicita", timeout=12.0)

    assert not result.degraded, result.failure
    (cliente,) = sdk
    # Anthropic directo: sin `base_url`.
    assert cliente.init_kwargs == {"api_key": "sk-ant-explicita", "timeout": 12.0}


def test_sin_llm_rigen_los_valores_por_defecto_de_siempre(sdk: list[_FakeClient]) -> None:
    propose_ad("pollo a 500", [], [], api_key="sk-ant-explicita")

    (cliente,) = sdk
    assert cliente.init_kwargs["timeout"] == 120.0
    assert cliente.kwargs["model"] == "claude-sonnet-5"


def test_sin_llm_el_modelo_explicito_viaja_y_se_registra_tal_cual(tmp_path: Path) -> None:
    fake = _FakeClient()
    registro = tmp_path / "calls.jsonl"

    propose_ad(
        "pollo a 500",
        [],
        [],
        api_key="sk-ant-explicita",
        model="claude-opus-5",
        client=fake,
        log_path=registro,
    )

    assert fake.kwargs["model"] == "claude-opus-5"
    assert _lineas(registro)[0]["model"] == "claude-opus-5"
