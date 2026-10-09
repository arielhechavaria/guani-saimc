"""Fábrica de clientes LLM. Único punto donde se elige el proveedor.

El asistente («Completar con IA», el pre-cálculo y el backtest) puede llamar al
modelo por dos caminos que hablan la misma API de Messages de Anthropic:

- `anthropic`: directo.
- `vercel`: Vercel AI Gateway. Cambian la URL, la clave y el nombre con que se
  pide el modelo (`anthropic/<id>`); el SDK es el mismo.

El proveedor es el único interruptor: fija la URL y de qué variable sale la
clave. No hay clave unificada ni URL configurable.

Tres reglas, cada una por un defecto que ya ocurrió (docs/decisions.md,
2026-10-09):

- **`Settings` es la única fuente.** La configuración no se lee de
  `os.environ`: pydantic-settings carga el `.env` en el objeto `Settings` y no
  lo exporta al entorno, así que lo escrito en `.env` nunca llegaría.
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
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

# `TYPE_CHECKING` vale False al ejecutar y True solo para mypy: el import sirve
# para las anotaciones y no se ejecuta. Así este módulo no carga `saimc.config`
# (ni pydantic) al importarse; solo `main` lo necesita de verdad.
if TYPE_CHECKING:
    from saimc.config import Settings

Provider = Literal["anthropic", "vercel"]

# El gateway nombra los modelos `<creador>/<id>`. Solo se usan los de Anthropic.
_MODEL_PREFIX = "anthropic/"

# Por proveedor: base URL (`None` = la del SDK) y prefijo que exige el modelo.
_DEFAULTS: dict[str, dict[str, str | None]] = {
    "anthropic": {"base_url": None, "model_prefix": ""},
    "vercel": {
        "base_url": "https://ai-gateway.vercel.sh",
        "model_prefix": _MODEL_PREFIX,
    },
}

def _clean(value: object) -> str | None:
    """Texto sin espacios alrededor, o `None` si no hay nada.

    Vacío es «ausente»: en `env.example` la clave del proveedor que no se
    usa va escrita así, sin valor.
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
        # Se canonicaliza antes de preguntar: «anthropic/» o « » no son un
        # modelo, y una corrida con modelo vacío fallaría en cada llamada.
        if model and canonical_model(model):
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
    # Un `llm_model` que es solo el prefijo canonicaliza a vacío: cae al
    # nombre anterior igual que si estuviera vacío.
    return canonical_model(_clean(settings.llm_model) or "") or canonical_model(
        settings.llm_assist_model
    )


def _provider_api_key(settings: Settings, provider: str) -> tuple[str | None, str]:
    """La clave del proveedor y su variable de `.env`. No hay otra fuente.

    La de `vercel` es la del gateway, la misma que usa Jev: entre su nombre
    nuevo y el anterior decide `Settings.gateway_api_key`. La de un proveedor
    nunca sirve de respaldo para el otro: sería un rechazo seguro y mandarle a
    un tercero una clave que no es suya.
    """
    if provider == "vercel":
        return settings.gateway_api_key()
    return _clean(settings.anthropic_api_key), "SAIMC_ANTHROPIC_API_KEY"


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

    api_key, source = _provider_api_key(settings, provider)
    if not api_key:
        # Acá `source` es la variable que habría que definir.
        raise ValueError(f"Sin API key para proveedor {provider!r}: definí {source}")

    model = effective_model(settings)
    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        api_key=api_key,
        base_url=_DEFAULTS[provider]["base_url"],
        model=model,
        request_model=request_model(provider, model),
        # `Settings` ya dejó en `None` un `llm_timeout` vacío o inválido.
        timeout=settings.llm_timeout or settings.llm_assist_timeout,
        api_key_source=source,
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

# Variables que existieron y se retiraron (docs/decisions.md, 2026-10-09).
# `Settings` las ignora sin decir nada; el comando avisa si siguen escritas.
_RETIRED_VARS = ("SAIMC_LLM_API_KEY", "SAIMC_LLM_BASE_URL")


def _env_file_names(path: Path) -> set[str]:
    """Los NOMBRES de las variables de un `.env`. Los valores no se guardan.

    Un archivo que no existe o no se puede leer no tiene ninguna: esto es un
    aviso de cortesía, no puede hacer fallar el comando.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for line in lines:
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        # `partition` corta en el primer `=`: lo de la izquierda es el nombre.
        name = line.partition("=")[0].strip()
        names.add(name.removeprefix("export ").strip())
    return names


def retired_vars_present(env_files: Iterable[Path], environ: Mapping[str, str]) -> list[str]:
    """Las variables retiradas que siguen escritas, en un `.env` o en el entorno.

    Mira solo nombres. Es la única lectura del entorno de este módulo, y no
    configura nada: sirve para avisar.
    """
    present = set(environ)
    for path in env_files:
        present |= _env_file_names(path)
    return [name for name in _RETIRED_VARS if name in present]


def _settings_env_files(settings: Settings) -> list[Path]:
    """Los `.env` que `Settings` carga, según su `model_config`."""
    env_file = settings.model_config.get("env_file")
    if env_file is None:
        return []
    # pydantic-settings admite una ruta o una lista de rutas.
    if isinstance(env_file, str | os.PathLike):
        return [Path(env_file)]
    return [Path(item) for item in env_file]


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Callable[[str], None] = print,
    env_files: Iterable[Path] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Imprime la configuración efectiva del asistente. No llama a nada.

    De la clave dice solo si está y de qué variable salió: nunca la clave ni un
    fragmento. Devuelve 1, con el motivo, si no hay configuración válida.
    Con o sin configuración, avisa de las variables retiradas que sigan
    escritas; el aviso no cambia el código de salida.

    `argv` no se usa (no hay opciones); está por simetría con los otros
    comandos. `settings`, `out`, `env_files` y `environ` son para los tests.
    """
    if settings is None:
        from saimc.config import get_settings

        settings = get_settings()
    if env_files is None:
        env_files = _settings_env_files(settings)
    code = _show_config(settings, out)
    for name in retired_vars_present(env_files, os.environ if environ is None else environ):
        out(f"Aviso: {name} ya no se usa: borrala del .env.")
    return code


def _show_config(settings: Settings, out: Callable[[str], None]) -> int:
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
