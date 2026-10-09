"""Fábrica de clientes LLM. Único punto donde se elige el proveedor.

El asistente («Completar con IA», el pre-cálculo y el backtest) puede llamar al
modelo por dos caminos que hablan la misma API de Messages de Anthropic:

- `anthropic`: directo.
- `vercel`: Vercel AI Gateway. Cambian la URL, la clave y el nombre con que se
  pide el modelo (`anthropic/<id>`); el SDK es el mismo.

Tres reglas, cada una por un defecto que ya ocurrió (docs/decisions.md,
2026-10-09):

- **`Settings` es la única fuente.** Acá no se lee `os.environ`: pydantic-settings
  carga el `.env` en el objeto `Settings` y no lo exporta al entorno, así que
  lo escrito en `.env` nunca llegaría.
- **Dos nombres de modelo.** `model` es el canónico (`claude-sonnet-5`): el que
  se guarda en `llm_proposals` y en el registro, y con el que se busca la
  tarifa. `request_model` es el que viaja en la llamada. Con el prefijo en el
  registro, la tabla de tarifas no encuentra el modelo y el costo desaparece.
- **Quien llama resuelve.** `propose_ad` recibe el `LLMConfig` ya armado; no
  viene a buscarlo acá por su cuenta.

`python -m saimc.llm.provider` muestra la configuración efectiva sin hacer
ninguna llamada (docs/comandos.md).
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

# `TYPE_CHECKING` vale False al ejecutar y True solo para mypy: el import sirve
# para las anotaciones y no se ejecuta. Así este módulo no carga `saimc.config`
# (ni pydantic) al importarse; solo `main` lo necesita de verdad.
if TYPE_CHECKING:
    from saimc.config import Settings

Provider = Literal["anthropic", "vercel"]

# El gateway nombra los modelos `<creador>/<id>`. Solo se usan los de Anthropic.
_MODEL_PREFIX = "anthropic/"

# Por proveedor: base URL por defecto y prefijo que exige el modelo.
_DEFAULTS: dict[str, dict[str, str | None]] = {
    "anthropic": {"base_url": None, "model_prefix": ""},
    "vercel": {
        "base_url": "https://ai-gateway.vercel.sh",
        "model_prefix": _MODEL_PREFIX,
    },
}

# Campo de `Settings` con la clave específica de cada proveedor, a la que se
# cae si `llm_api_key` está vacía. La de `vercel` es la misma clave del gateway
# que ya usa Jev. La de Anthropic NO sirve de respaldo para `vercel`: sería un
# rechazo seguro y mandarle a un tercero una clave que no es suya.
_API_KEY_FIELD: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "vercel": "llm_jev_api_key",
}


def _env_name(field_name: str) -> str:
    """El nombre de la variable de `.env` de un campo de `Settings`."""
    return f"SAIMC_{field_name.upper()}"


def _clean(value: object) -> str | None:
    """Texto sin espacios alrededor, o `None` si no hay nada.

    Vacío es «ausente»: en `env.example` y en producción `SAIMC_LLM_API_KEY=`
    y `SAIMC_LLM_BASE_URL=` van escritas así, sin valor.
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None


def canonical_model(name: str) -> str:
    """El nombre con que se registra y se tarifa: sin prefijo de proveedor."""
    # `removeprefix` (Python 3.9+) saca el prefijo solo si está al principio;
    # si no está, devuelve el texto igual. No es `lstrip`, que saca caracteres.
    return name.strip().removeprefix(_MODEL_PREFIX)


def request_model(provider: str, model: str) -> str:
    """El nombre que viaja en la llamada: con el prefijo que pida el proveedor."""
    prefix = _DEFAULTS[provider]["model_prefix"] or ""
    return f"{prefix}{canonical_model(model)}"


@dataclass(frozen=True)
class LLMConfig:
    """Todo lo que hace falta para una llamada, ya resuelto.

    `frozen=True` lo hace inmutable (como un objeto `readonly` de PHP): para
    cambiarle algo se arma otro con `with_overrides`.
    """

    provider: Provider
    # `repr=False`: la clave no aparece si el objeto termina en un log, en un
    # mensaje de error o en el volcado de un test que falla.
    api_key: str = field(repr=False)
    base_url: str | None
    # Canónico: lo que se registra y se tarifa.
    model: str
    # Lo que se manda. Igual a `model` salvo en `vercel`.
    request_model: str
    timeout: float
    # Nombre de la variable de la que salió la clave. Solo para mostrarlo en
    # el comando de comprobación; vacío si la config no salió de `Settings`.
    api_key_source: str = ""

    def with_overrides(
        self, *, model: str | None = None, timeout: float | None = None
    ) -> LLMConfig:
        """Otra config con el modelo o el timeout pisados (`--model`, `--timeout`).

        Pisar el modelo recalcula `request_model`: si no, se registraría un
        modelo y se llamaría a otro.
        """
        cfg = self
        if model:
            cfg = dataclasses.replace(
                cfg,
                model=canonical_model(model),
                request_model=request_model(self.provider, model),
            )
        if timeout is not None:
            cfg = dataclasses.replace(cfg, timeout=timeout)
        return cfg


def effective_model(settings: Settings) -> str:
    """El modelo canónico de la configuración. No necesita clave.

    `llm_model` gana; sin él rige `llm_assist_model`, el nombre anterior. Lo
    usa también el dry-run del pre-cálculo, que estima el costo sin clave.
    """
    return canonical_model(_clean(settings.llm_model) or settings.llm_assist_model)


def resolve_config(settings: Settings) -> LLMConfig:
    """La config efectiva del asistente, a partir de `Settings`.

    Levanta `ValueError` si el proveedor es desconocido o falta la clave. El
    mensaje nombra variables, nunca un valor. Para preguntar «¿hay asistente?»
    sin atajar la excepción, `try_resolve_config`.
    """
    provider = (_clean(settings.llm_provider) or "anthropic").lower()
    if provider not in _DEFAULTS:
        raise ValueError(
            f"Proveedor LLM desconocido: {provider!r} (SAIMC_LLM_PROVIDER admite "
            f"{' o '.join(sorted(_DEFAULTS))})"
        )

    specific = _API_KEY_FIELD[provider]
    api_key = _clean(settings.llm_api_key)
    source = "llm_api_key"
    if not api_key:
        # `getattr(obj, "nombre")` es `obj.nombre` con el nombre en una variable.
        api_key = _clean(getattr(settings, specific))
        source = specific
    if not api_key:
        raise ValueError(
            f"Sin API key para proveedor {provider!r}: definí "
            f"SAIMC_LLM_API_KEY o {_env_name(specific)}"
        )

    model = effective_model(settings)
    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        api_key=api_key,
        base_url=_clean(settings.llm_base_url) or _DEFAULTS[provider]["base_url"],
        model=model,
        request_model=request_model(provider, model),
        # `Settings` ya dejó en `None` un `llm_timeout` vacío o inválido.
        timeout=settings.llm_timeout or settings.llm_assist_timeout,
        api_key_source=_env_name(source),
    )


def try_resolve_config(settings: Settings) -> LLMConfig | None:
    """La config, o `None` si no hay asistente configurado.

    Es la pregunta de la cola, de `/control` y de las corridas: sin asistente
    todo sigue funcionando a mano, así que la ausencia es un estado soportado
    y no un error. El motivo concreto lo da `resolve_config` (y el comando de
    comprobación de este módulo).
    """
    try:
        return resolve_config(settings)
    except ValueError:
        return None


def build_client(cfg: LLMConfig) -> Any:
    """Construye el cliente del SDK. Único punto de acople al proveedor."""
    if cfg.provider in ("anthropic", "vercel"):
        # Vercel AI Gateway expone la API de Anthropic tal cual, así que
        # el mismo SDK sirve para ambos: solo cambia base_url y api_key.
        # El import va acá adentro porque `anthropic` es opcional
        # (`pip install -e ".[llm]"`): sin el paquete, falla esta llamada y no
        # el arranque de la aplicación.
        import anthropic

        kwargs: dict[str, Any] = {"api_key": cfg.api_key, "timeout": cfg.timeout}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return anthropic.Anthropic(**kwargs)

    raise ValueError(f"Proveedor sin cliente implementado: {cfg.provider!r}")


# --- comando de comprobación -------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Imprime la configuración efectiva del asistente. No llama a nada.

    De la clave dice solo si está y de qué variable salió: nunca la clave ni un
    fragmento. Devuelve 1, con el motivo, si no hay configuración válida.
    `argv` no se usa (no hay opciones); está por simetría con los otros
    comandos. `settings` y `out` son para los tests.
    """
    if settings is None:
        from saimc.config import get_settings

        settings = get_settings()
    try:
        cfg = resolve_config(settings)
    except ValueError as error:
        out(f"Sin asistente: {error}.")
        return 1

    out(f"Proveedor:           {cfg.provider}")
    out(f"Modelo (registro):   {cfg.model}")
    out(f"Modelo que se manda: {cfg.request_model}")
    out(f"Base URL:            {cfg.base_url or '(la del SDK de Anthropic)'}")
    out(f"Timeout de la cola:  {cfg.timeout:g} s (pre-cálculo y backtest: su --timeout)")
    out(f"Clave:               presente, de {cfg.api_key_source}")
    out("No se hizo ninguna llamada: esto solo lee la configuración.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
