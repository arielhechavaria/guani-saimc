"""Tests de `saimc.llm.provider`: resolución de config y armado de cliente."""
from __future__ import annotations

import sys
import types

import pytest

from saimc.llm import provider


# ---------- helpers -------------------------------------------------------

_VARS = (
    "SAIMC_LLM_PROVIDER",
    "SAIMC_LLM_API_KEY",
    "SAIMC_LLM_BASE_URL",
    "SAIMC_LLM_MODEL",
    "SAIMC_LLM_TIMEOUT",
    "SAIMC_LLM_ASSIST_TIMEOUT",
    "SAIMC_ANTHROPIC_API_KEY",
    "SAIMC_LLM_JEV_API_KEY",
)


@pytest.fixture
def env(monkeypatch):
    """Entorno limpio: borra toda variable que lea `provider`."""
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _FakeAnthropic:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def fake_sdk(monkeypatch):
    """Reemplaza `import anthropic` por un módulo falso."""
    mod = types.ModuleType("anthropic")
    mod.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


# ---------- proveedor -----------------------------------------------------

def test_default_provider_es_anthropic(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    assert provider.resolve_config().provider == "anthropic"


def test_provider_desconocido_levanta(env):
    env.setenv("SAIMC_LLM_PROVIDER", "openai")
    env.setenv("SAIMC_LLM_API_KEY", "x")
    with pytest.raises(ValueError, match="Proveedor LLM desconocido"):
        provider.resolve_config()


# ---------- clave ---------------------------------------------------------

def test_anthropic_usa_su_clave_especifica(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-abc")
    assert provider.resolve_config().api_key == "sk-ant-abc"


def test_vercel_usa_la_clave_de_jev(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-abc")
    assert provider.resolve_config().api_key == "vck-abc"


def test_llm_api_key_gana_sobre_la_especifica(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_API_KEY", "unificada")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-abc")
    assert provider.resolve_config().api_key == "unificada"


def test_sin_clave_levanta(env):
    with pytest.raises(ValueError, match="Sin API key"):
        provider.resolve_config()


# ---------- modelo --------------------------------------------------------

def test_anthropic_no_prefija_el_modelo(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    env.setenv("SAIMC_LLM_MODEL", "claude-sonnet-5")
    assert provider.resolve_config().model == "claude-sonnet-5"


def test_vercel_prefija_el_modelo(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-x")
    env.setenv("SAIMC_LLM_MODEL", "claude-sonnet-5")
    assert provider.resolve_config().model == "anthropic/claude-sonnet-5"


def test_vercel_no_duplica_prefijo(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-x")
    env.setenv("SAIMC_LLM_MODEL", "anthropic/claude-sonnet-5")
    assert provider.resolve_config().model == "anthropic/claude-sonnet-5"


def test_modelo_por_defecto(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    assert provider.resolve_config().model == "claude-sonnet-5"


# ---------- base_url ------------------------------------------------------

def test_anthropic_sin_base_url(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    assert provider.resolve_config().base_url is None


def test_vercel_usa_base_url_por_defecto(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-x")
    assert (
        provider.resolve_config().base_url
        == "https://ai-gateway.vercel.sh"
    )


def test_base_url_explicita_gana(env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-x")
    env.setenv("SAIMC_LLM_BASE_URL", "https://proxy.local")
    assert provider.resolve_config().base_url == "https://proxy.local"


# ---------- timeout -------------------------------------------------------

def test_timeout_por_defecto_es_30(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    assert provider.resolve_config().timeout == 30.0


def test_timeout_explicito(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    env.setenv("SAIMC_LLM_TIMEOUT", "45.5")
    assert provider.resolve_config().timeout == 45.5


def test_timeout_cae_al_asist_deprecado(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    env.setenv("SAIMC_LLM_ASSIST_TIMEOUT", "12")
    assert provider.resolve_config().timeout == 12.0


def test_timeout_invalido_cae_a_30(env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    env.setenv("SAIMC_LLM_TIMEOUT", "no-es-numero")
    assert provider.resolve_config().timeout == 30.0


# ---------- build_client --------------------------------------------------

def test_build_client_anthropic_sin_base_url(fake_sdk, env):
    env.setenv("SAIMC_ANTHROPIC_API_KEY", "sk-ant-x")
    client = provider.build_client(provider.resolve_config())
    assert "base_url" not in client.kwargs
    assert client.kwargs["api_key"] == "sk-ant-x"
    assert client.kwargs["timeout"] == 30.0


def test_build_client_vercel_pasa_base_url(fake_sdk, env):
    env.setenv("SAIMC_LLM_PROVIDER", "vercel")
    env.setenv("SAIMC_LLM_JEV_API_KEY", "vck-x")
    client = provider.build_client(provider.resolve_config())
    assert client.kwargs["base_url"] == "https://ai-gateway.vercel.sh"
    assert client.kwargs["api_key"] == "vck-x"