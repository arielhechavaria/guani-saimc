"""Fábrica de clientes LLM. Único punto donde se elige el proveedor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

Provider = Literal["anthropic", "vercel"]

# Por proveedor: base URL por defecto y prefijo que exige el modelo.
_DEFAULTS: dict[str, dict[str, str | None]] = {
    "anthropic": {"base_url": None, "model_prefix": ""},
    "vercel": {
        "base_url": "https://ai-gateway.vercel.sh",
        "model_prefix": "anthropic/",
    },
}

# Clave específica a la que caer si SAIMC_LLM_API_KEY está vacía.
_API_KEY_ENV: dict[str, str] = {
    "anthropic": "SAIMC_ANTHROPIC_API_KEY",
    "vercel": "SAIMC_LLM_JEV_API_KEY",   # reusa la que ya tenés
}


@dataclass(frozen=True)
class LLMConfig:
    provider: Provider
    api_key: str
    base_url: str | None
    model: str
    timeout: float


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def resolve_config() -> LLMConfig:
    """Lee el entorno y devuelve la config efectiva del LLM.

    Levanta ValueError si el proveedor es desconocido o falta la clave.
    """
    provider = (_env("SAIMC_LLM_PROVIDER") or "anthropic").lower()
    if provider not in _DEFAULTS:
        raise ValueError(f"Proveedor LLM desconocido: {provider!r}")

    api_key = _env("SAIMC_LLM_API_KEY") or _env(_API_KEY_ENV[provider])
    if not api_key:
        raise ValueError(
            f"Sin API key para proveedor {provider!r}: definí "
            f"SAIMC_LLM_API_KEY o {_API_KEY_ENV[provider]}"
        )

    defaults = _DEFAULTS[provider]
    base_url = _env("SAIMC_LLM_BASE_URL") or defaults["base_url"]
    model_canon = _env("SAIMC_LLM_MODEL") or "claude-sonnet-5"
    prefix = defaults["model_prefix"] or ""
    model = (
        model_canon
        if not prefix or model_canon.startswith(prefix)
        else f"{prefix}{model_canon}"
    )

    timeout_raw = (
        _env("SAIMC_LLM_TIMEOUT")
        or _env("SAIMC_LLM_ASSIST_TIMEOUT")   # fallback deprecado
        or "30"
    )
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 30.0

    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
    )


def build_client(cfg: LLMConfig) -> Any:
    """Construye el cliente del SDK. Único punto de acople al proveedor."""
    if cfg.provider in ("anthropic", "vercel"):
        # Vercel AI Gateway expone la API de Anthropic tal cual, así que
        # el mismo SDK sirve para ambos: solo cambia base_url y api_key.
        import anthropic

        kwargs: dict[str, Any] = {"api_key": cfg.api_key, "timeout": cfg.timeout}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return anthropic.Anthropic(**kwargs)

    raise ValueError(f"Proveedor sin cliente implementado: {cfg.provider!r}")