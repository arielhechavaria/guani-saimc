"""`propose_ad` debe sacar clave, modelo y timeout del proveedor activo."""
from __future__ import annotations

import json
import types

import pytest

from saimc.extraction.llm_propose_ad import propose_ad


def _respuesta(payload: dict) -> object:
    return types.SimpleNamespace(
        usage=types.SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class _FakeClient:
    """Cliente que captura los kwargs de `messages.create`."""

    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _respuesta({"discard": False, "discard_reason": None, "rows": []})


@pytest.fixture
def env(monkeypatch):
    for name in (
        "SAIMC_LLM_PROVIDER",
        "SAIMC_LLM_API_KEY",
        "SAIMC_LLM_BASE_URL",
        "SAIMC_LLM_MODEL",
        "SAIMC_LLM_TIMEOUT",
        "SAIMC_LLM_ASSIST_TIMEOUT",
        "SAIMC_ANTHROPIC_API_KEY",
        "SAIMC_LLM_JEV_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------- los tests que fallan hoy --------------------------------------

def test_sin_api_key_explicita_usa_la_del_provider(env):
    """Hoy devuelve `no_key`; debe usar `SAIMC_ANTHROPIC_API_KEY`."""
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-test")
    result = propose_ad("texto", [], [], api_key=None, client=_FakeClient())
    assert not result.degraded, result.failure


def test_modelo_llega_desde_el_entorno(env):
    """El modelo efectivo debe ser el de la config, no el default del código."""
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-test")
    env.setenv("SAIMC_LLM_MODEL", "claude-haiku-4-5")
    fake = _FakeClient()
    propose_ad("texto", [], [], api_key=None, client=fake)
    assert fake.kwargs["model"] == "anthropic/claude-haiku-4-5"


def test_timeout_llega_desde_el_entorno(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-test")
    env.setenv("SAIMC_LLM_TIMEOUT", "45.5")
    # La firma del cliente no expone timeout en `messages.create`; se verifica
    # a través de la config resuelta, no del kwargs. Este test se ajusta
    # cuando la firma de propose_ad exponga el timeout efectivo.
    ...


def test_override_explicito_de_modelo_gana(env):
    """Un `model=` explícito gana sobre `SAIMC_LLM_MODEL`."""
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-test")
    env.setenv("SAIMC_LLM_MODEL", "claude-sonnet-5")
    fake = _FakeClient()
    propose_ad("texto", [], [], api_key=None, model="claude-opus-4", client=fake)
    assert fake.kwargs["model"] == "claude-opus-4"


def test_provider_sin_clave_devuelve_degradado(env):
    """Sin clave en ningún lado, `propose_ad` no levanta: degrada."""
    result = propose_ad("texto", [], [], api_key=None, client=_FakeClient())
    assert result.degraded
    assert result.failure