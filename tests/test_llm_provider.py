"""Tests de `saimc.llm.provider`: resolución de config y armado de cliente.

La fábrica lee de `Settings`, no de `os.environ`: pydantic-settings carga el
`.env` en el objeto y no lo exporta al entorno, así que una fábrica que mire
`os.environ` no ve nada de lo que el operador escribió ahí (defecto 1 de
`docs/decisions.md`, 2026-10-09).

Ningún test depende del `.env` de la máquina: `Settings(_env_file=None, ...)`,
el mismo patrón de `tests/test_prefill.py`.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

from saimc.config import Settings
from saimc.llm import provider

# ---------- helpers -------------------------------------------------------

_VARS = (
    "SAIMC_LLM_PROVIDER",
    "SAIMC_LLM_MODEL",
    "SAIMC_LLM_TIMEOUT",
    "SAIMC_LLM_ASSIST_MODEL",
    "SAIMC_LLM_ASSIST_TIMEOUT",
    "SAIMC_ANTHROPIC_API_KEY",
    "SAIMC_LLM_JEV_API_KEY",
    "SAIMC_VERCEL_API_KEY",
)

_GATEWAY = "https://ai-gateway.vercel.sh"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Entorno limpio. `_env_file=None` saca el `.env`, pero `Settings` sigue
    leyendo las variables del proceso: si la terminal del operador trae alguna,
    acá se borra."""
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _ajustes(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type, call-arg]


def _desde_env(tmp_path: Path, contenido: str) -> Settings:
    """`Settings` cargado de un `.env` temporal, como en producción."""
    ruta = tmp_path / ".env"
    ruta.write_text(contenido, encoding="utf-8")
    return Settings(_env_file=ruta)  # type: ignore[call-arg]


class _FakeAnthropic:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Reemplaza `import anthropic` por un módulo falso."""
    mod = types.ModuleType("anthropic")
    mod.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


# ---------- proveedor -----------------------------------------------------

def test_default_provider_es_anthropic() -> None:
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="sk-ant-x"))
    assert cfg.provider == "anthropic"


def test_provider_desconocido_levanta() -> None:
    with pytest.raises(ValueError, match="Proveedor LLM desconocido"):
        provider.resolve_config(_ajustes(llm_provider="openai", anthropic_api_key="x"))


def test_provider_vacio_es_anthropic() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider="", anthropic_api_key="sk-ant-x"))
    assert cfg.provider == "anthropic"


def test_provider_tolera_mayusculas_y_espacios() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider=" Vercel ", vercel_api_key="vck-x"))
    assert cfg.provider == "vercel"


# ---------- clave ---------------------------------------------------------

def test_anthropic_usa_su_clave_especifica() -> None:
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="sk-ant-abc"))
    assert cfg.api_key == "sk-ant-abc"
    assert cfg.api_key_source == "SAIMC_ANTHROPIC_API_KEY"


def test_vercel_usa_la_clave_del_gateway() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", vercel_api_key="vck-nueva"))
    assert cfg.api_key == "vck-nueva"
    assert cfg.api_key_source == "SAIMC_VERCEL_API_KEY"


def test_vercel_sigue_aceptando_el_nombre_anterior_de_la_clave() -> None:
    """`SAIMC_LLM_JEV_API_KEY` es el nombre anterior de la clave del gateway:
    el `.env` de producción todavía la tiene escrita así."""
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="vck-abc"))
    assert cfg.api_key == "vck-abc"
    assert cfg.api_key_source == "SAIMC_LLM_JEV_API_KEY"


def test_con_los_dos_nombres_gana_la_clave_nueva() -> None:
    cfg = provider.resolve_config(
        _ajustes(llm_provider="vercel", vercel_api_key="vck-nueva", llm_jev_api_key="vck-vieja")
    )
    assert cfg.api_key == "vck-nueva"
    assert cfg.api_key_source == "SAIMC_VERCEL_API_KEY"


@pytest.mark.parametrize("vacia", ["", "   "], ids=["vacía", "espacios"])
def test_la_clave_nueva_vacia_deja_regir_a_la_anterior(vacia: str) -> None:
    """Vacío es ausente: `SAIMC_VERCEL_API_KEY=` escrita sin valor no tapa a
    la anterior."""
    cfg = provider.resolve_config(
        _ajustes(llm_provider="vercel", vercel_api_key=vacia, llm_jev_api_key="vck-vieja")
    )
    assert cfg.api_key == "vck-vieja"
    assert cfg.api_key_source == "SAIMC_LLM_JEV_API_KEY"


def test_vercel_no_usa_la_clave_de_anthropic() -> None:
    """La clave de Anthropic no sirve en el gateway: mandarla ahí sería un
    rechazo seguro, y además entregarle a un tercero una clave que no es suya."""
    with pytest.raises(ValueError, match="Sin API key"):
        provider.resolve_config(_ajustes(llm_provider="vercel", anthropic_api_key="sk-ant-abc"))


def test_anthropic_usa_solo_su_clave_aunque_haya_clave_del_gateway() -> None:
    """Un interruptor y, según el proveedor, su clave: la de un proveedor
    nunca sirve para el otro."""
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="sk-ant-abc", vercel_api_key="vck-x", llm_jev_api_key="vck-y")
    )
    assert cfg.api_key == "sk-ant-abc"
    assert cfg.api_key_source == "SAIMC_ANTHROPIC_API_KEY"


def test_anthropic_no_usa_la_clave_del_gateway() -> None:
    with pytest.raises(ValueError, match="Sin API key") as info:
        provider.resolve_config(_ajustes(vercel_api_key="vck-x", llm_jev_api_key="vck-y"))
    assert "SAIMC_ANTHROPIC_API_KEY" in str(info.value)
    assert "SAIMC_VERCEL_API_KEY" not in str(info.value)


def test_vercel_usa_solo_la_clave_del_gateway_aunque_haya_clave_de_anthropic() -> None:
    cfg = provider.resolve_config(
        _ajustes(llm_provider="vercel", anthropic_api_key="sk-ant-abc", vercel_api_key="vck-x")
    )
    assert cfg.api_key == "vck-x"
    assert cfg.api_key_source == "SAIMC_VERCEL_API_KEY"


def test_sin_clave_levanta() -> None:
    with pytest.raises(ValueError, match="Sin API key"):
        provider.resolve_config(_ajustes())


def test_el_error_de_sin_clave_nombra_las_variables_y_no_un_valor() -> None:
    with pytest.raises(ValueError) as info:
        provider.resolve_config(_ajustes(llm_provider="vercel"))
    # La que hay que definir es la del proveedor elegido (con su nombre
    # nuevo, no el anterior), y ninguna otra.
    assert "SAIMC_VERCEL_API_KEY" in str(info.value)
    assert "SAIMC_ANTHROPIC_API_KEY" not in str(info.value)
    assert "SAIMC_LLM_API_KEY" not in str(info.value)


def test_la_clave_no_sale_en_el_repr() -> None:
    """Un `LLMConfig` que termine en un log o en un volcado de pytest no puede
    llevar la clave."""
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="sk-ant-secreta-999"))
    assert "sk-ant-secreta-999" not in repr(cfg)


# ---------- try_resolve_config --------------------------------------------

def test_try_resolve_sin_clave_devuelve_none() -> None:
    assert provider.try_resolve_config(_ajustes()) is None


def test_try_resolve_con_proveedor_desconocido_devuelve_none() -> None:
    assert provider.try_resolve_config(_ajustes(llm_provider="openai", anthropic_api_key="x")) is None


def test_try_resolve_con_config_valida_devuelve_la_config() -> None:
    cfg = provider.try_resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="vck-x"))
    assert cfg is not None
    assert cfg.provider == "vercel"


# ---------- modelo --------------------------------------------------------

def test_anthropic_no_prefija_el_modelo() -> None:
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="k", llm_model="claude-sonnet-5"))
    assert cfg.model == "claude-sonnet-5"
    assert cfg.request_model == "claude-sonnet-5"


def test_vercel_prefija_solo_el_modelo_que_se_manda() -> None:
    """El canónico es el que se registra y se tarifa; el prefijo solo viaja."""
    cfg = provider.resolve_config(
        _ajustes(llm_provider="vercel", llm_jev_api_key="k", llm_model="claude-sonnet-5")
    )
    assert cfg.model == "claude-sonnet-5"
    assert cfg.request_model == "anthropic/claude-sonnet-5"


def test_vercel_no_duplica_prefijo() -> None:
    cfg = provider.resolve_config(
        _ajustes(llm_provider="vercel", llm_jev_api_key="k", llm_model="anthropic/claude-sonnet-5")
    )
    assert cfg.model == "claude-sonnet-5"
    assert cfg.request_model == "anthropic/claude-sonnet-5"


def test_anthropic_con_modelo_prefijado_lo_manda_sin_prefijo() -> None:
    """Volver de vercel a anthropic sin limpiar el modelo no puede romper."""
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="k", llm_model="anthropic/claude-sonnet-5")
    )
    assert cfg.model == "claude-sonnet-5"
    assert cfg.request_model == "claude-sonnet-5"


def test_modelo_por_defecto() -> None:
    assert provider.resolve_config(_ajustes(anthropic_api_key="k")).model == "claude-sonnet-5"


def test_llm_model_gana_sobre_el_nombre_anterior() -> None:
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="k", llm_model="claude-opus-5", llm_assist_model="claude-haiku-4-5")
    )
    assert cfg.model == "claude-opus-5"


def test_sin_llm_model_rige_el_nombre_anterior() -> None:
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="k", llm_assist_model="claude-haiku-4-5"))
    assert cfg.model == "claude-haiku-4-5"


def test_llm_model_vacio_es_ausente() -> None:
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="k", llm_model="", llm_assist_model="claude-haiku-4-5")
    )
    assert cfg.model == "claude-haiku-4-5"


def test_effective_model_no_necesita_clave() -> None:
    """El dry-run del pre-cálculo estima el costo sin clave."""
    assert provider.effective_model(_ajustes(llm_model="anthropic/claude-opus-5")) == "claude-opus-5"


def test_with_overrides_recalcula_el_modelo_que_se_manda() -> None:
    """`--model` del pre-cálculo y del backtest."""
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="k"))
    otro = cfg.with_overrides(model="claude-haiku-4-5", timeout=120.0)
    assert otro.model == "claude-haiku-4-5"
    assert otro.request_model == "anthropic/claude-haiku-4-5"
    assert otro.timeout == 120.0
    assert otro.api_key == "k"
    # El original no cambia: es inmutable.
    assert cfg.model == "claude-sonnet-5"


def test_with_overrides_sin_argumentos_no_cambia_nada() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="k"))
    assert cfg.with_overrides(model=None, timeout=None) == cfg


def test_with_overrides_con_modelo_prefijado_no_duplica() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="k"))
    otro = cfg.with_overrides(model="anthropic/claude-opus-5")
    assert otro.model == "claude-opus-5"
    assert otro.request_model == "anthropic/claude-opus-5"


# ---------- base_url ------------------------------------------------------

def test_anthropic_sin_base_url() -> None:
    assert provider.resolve_config(_ajustes(anthropic_api_key="k")).base_url is None


def test_vercel_usa_base_url_por_defecto() -> None:
    cfg = provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="k"))
    assert cfg.base_url == _GATEWAY


def test_las_variables_retiradas_en_el_env_no_rompen_ni_cambian_la_config(tmp_path: Path) -> None:
    """`SAIMC_LLM_API_KEY` y `SAIMC_LLM_BASE_URL` se retiraron, pero el `.env`
    de producción todavía las tiene escritas: se ignoran. Son justo las dos
    mezclas que ocurrieron: una clave de relleno que pisaba a la buena y la
    URL del gateway con proveedor `anthropic`."""
    ajustes = _desde_env(
        tmp_path,
        "SAIMC_LLM_PROVIDER=anthropic\n"
        "SAIMC_LLM_API_KEY=clave-de-relleno\n"
        "SAIMC_LLM_BASE_URL=https://ai-gateway.vercel.sh\n"
        "SAIMC_ANTHROPIC_API_KEY=sk-ant-buena\n",
    )

    cfg = provider.resolve_config(ajustes)

    assert cfg.provider == "anthropic"
    assert cfg.api_key == "sk-ant-buena"
    assert cfg.api_key_source == "SAIMC_ANTHROPIC_API_KEY"
    assert cfg.base_url is None


# ---------- timeout -------------------------------------------------------

def test_timeout_por_defecto_es_30() -> None:
    assert provider.resolve_config(_ajustes(anthropic_api_key="k")).timeout == 30.0


def test_llm_timeout_gana_sobre_el_nombre_anterior() -> None:
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="k", llm_timeout=45.5, llm_assist_timeout=12.0)
    )
    assert cfg.timeout == 45.5


def test_sin_llm_timeout_rige_el_nombre_anterior() -> None:
    cfg = provider.resolve_config(_ajustes(anthropic_api_key="k", llm_assist_timeout=12.0))
    assert cfg.timeout == 12.0


@pytest.mark.parametrize("valor", ["", "   ", "no-es-numero", "0", "-5", "nan", "inf"])
def test_llm_timeout_invalido_no_rompe_la_carga_y_rige_el_anterior(valor: str) -> None:
    """Un `.env` mal escrito no puede tumbar la aplicación entera."""
    cfg = provider.resolve_config(
        _ajustes(anthropic_api_key="k", llm_timeout=valor, llm_assist_timeout=12.0)
    )
    assert cfg.timeout == 12.0


def test_llm_timeout_vacio_no_avisa_y_uno_mal_escrito_si(caplog: pytest.LogCaptureFixture) -> None:
    """Vacío es «ausente», que es como va escrito en producción: no es un
    error. Un texto que no es número sí se avisa, sin copiar el valor."""
    with caplog.at_level(logging.WARNING, logger="saimc.config"):
        _ajustes(llm_timeout="")
    assert caplog.records == []

    with caplog.at_level(logging.WARNING, logger="saimc.config"):
        _ajustes(llm_timeout="treinta-secreto")
    assert "SAIMC_LLM_TIMEOUT" in caplog.text
    assert "treinta-secreto" not in caplog.text


# ---------- el `.env` llega de verdad (defecto 1) --------------------------

def test_un_env_con_vercel_llega_a_la_fabrica(tmp_path: Path) -> None:
    """El defecto 1: lo escrito en `.env` no llegaba porque se leía
    `os.environ`. Acá el `.env` es la única fuente y tiene que alcanzar."""
    ajustes = _desde_env(
        tmp_path,
        "SAIMC_LLM_PROVIDER=vercel\n"
        "SAIMC_LLM_MODEL=claude-sonnet-5\n"
        "SAIMC_LLM_TIMEOUT=45.0\n"
        "SAIMC_ANTHROPIC_API_KEY=\n"
        "SAIMC_VERCEL_API_KEY=vck-del-env\n",
    )

    cfg = provider.resolve_config(ajustes)

    assert cfg.provider == "vercel"
    assert cfg.api_key == "vck-del-env"
    assert cfg.api_key_source == "SAIMC_VERCEL_API_KEY"
    assert cfg.base_url == _GATEWAY
    assert cfg.model == "claude-sonnet-5"
    assert cfg.request_model == "anthropic/claude-sonnet-5"
    assert cfg.timeout == 45.0


def test_un_env_con_timeout_vacio_carga_igual(tmp_path: Path) -> None:
    ajustes = _desde_env(
        tmp_path,
        "SAIMC_LLM_PROVIDER=\nSAIMC_LLM_TIMEOUT=\nSAIMC_LLM_MODEL=\nSAIMC_ANTHROPIC_API_KEY=sk-ant-env\n",
    )

    cfg = provider.resolve_config(ajustes)

    assert (cfg.provider, cfg.model, cfg.timeout) == ("anthropic", "claude-sonnet-5", 30.0)


def test_los_nombres_anteriores_del_env_siguen_valiendo(tmp_path: Path) -> None:
    ajustes = _desde_env(
        tmp_path,
        "SAIMC_ANTHROPIC_API_KEY=sk-ant-env\n"
        "SAIMC_LLM_ASSIST_MODEL=claude-haiku-4-5\n"
        "SAIMC_LLM_ASSIST_TIMEOUT=12\n",
    )

    cfg = provider.resolve_config(ajustes)

    assert (cfg.model, cfg.timeout) == ("claude-haiku-4-5", 12.0)


def test_los_nombres_nuevos_del_env_ganan_sobre_los_anteriores(tmp_path: Path) -> None:
    ajustes = _desde_env(
        tmp_path,
        "SAIMC_ANTHROPIC_API_KEY=sk-ant-env\n"
        "SAIMC_LLM_ASSIST_MODEL=claude-haiku-4-5\n"
        "SAIMC_LLM_ASSIST_TIMEOUT=12\n"
        "SAIMC_LLM_MODEL=claude-opus-5\n"
        "SAIMC_LLM_TIMEOUT=60\n",
    )

    cfg = provider.resolve_config(ajustes)

    assert (cfg.model, cfg.timeout) == ("claude-opus-5", 60.0)


# ---------- build_client --------------------------------------------------

def test_build_client_anthropic_sin_base_url(fake_sdk: types.ModuleType) -> None:
    client = provider.build_client(provider.resolve_config(_ajustes(anthropic_api_key="sk-ant-x")))
    assert "base_url" not in client.kwargs
    assert client.kwargs["api_key"] == "sk-ant-x"
    assert client.kwargs["timeout"] == 30.0


def test_build_client_vercel_pasa_base_url(fake_sdk: types.ModuleType) -> None:
    client = provider.build_client(
        provider.resolve_config(_ajustes(llm_provider="vercel", llm_jev_api_key="vck-x"))
    )
    assert client.kwargs["base_url"] == _GATEWAY
    assert client.kwargs["api_key"] == "vck-x"


# ---------- comando de comprobación ---------------------------------------

def _comprobar(
    ajustes: Settings, *, env_file: Path | None = None, environ: dict[str, str] | None = None
) -> tuple[int, str]:
    """Corre el comando. Sin `env_file` ni `environ` no mira ni el `.env` de
    la máquina ni el entorno del proceso."""
    lineas: list[str] = []
    codigo = provider.main(
        [],
        settings=ajustes,
        out=lineas.append,
        env_files=[env_file] if env_file else [],
        environ=environ or {},
    )
    return codigo, "\n".join(lineas)


@pytest.mark.parametrize(
    ("campo", "variable"),
    [
        ("vercel_api_key", "SAIMC_VERCEL_API_KEY"),
        ("llm_jev_api_key", "SAIMC_LLM_JEV_API_KEY"),
    ],
    ids=["nombre nuevo", "nombre anterior"],
)
def test_el_comando_muestra_la_config_efectiva_sin_la_clave(
    fake_sdk: types.ModuleType, campo: str, variable: str
) -> None:
    clave = "vck-CLAVE-RECONOCIBLE-1234567890"

    codigo, salida = _comprobar(_ajustes(llm_provider="vercel", **{campo: clave}))

    assert codigo == 0
    assert "vercel" in salida
    assert "claude-sonnet-5" in salida
    assert "anthropic/claude-sonnet-5" in salida
    assert _GATEWAY in salida
    assert "30" in salida
    assert variable in salida
    # Ni la clave ni un pedazo: ni el principio ni el final.
    assert clave not in salida
    assert clave[:6] not in salida
    assert clave[-6:] not in salida


def test_el_comando_no_arma_cliente_ni_toca_la_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comprobar no gasta: ni siquiera se construye el cliente del SDK."""

    def _prohibido(cfg: object) -> object:
        raise AssertionError("el comando de comprobación no debe armar un cliente")

    monkeypatch.setattr(provider, "build_client", _prohibido)

    codigo, _ = _comprobar(_ajustes(anthropic_api_key="sk-ant-x"))

    assert codigo == 0


def test_el_comando_sin_clave_sale_con_1_y_dice_el_motivo() -> None:
    codigo, salida = _comprobar(_ajustes(llm_provider="vercel"))

    assert codigo == 1
    assert "SAIMC_VERCEL_API_KEY" in salida


def test_el_comando_con_proveedor_desconocido_sale_con_1() -> None:
    codigo, salida = _comprobar(_ajustes(llm_provider="openai", anthropic_api_key="x"))

    assert codigo == 1
    assert "openai" in salida


# ---------- variables retiradas -------------------------------------------

_RELLENO = "valor-RECONOCIBLE-de-relleno-987"


def test_el_comando_avisa_de_las_variables_retiradas_del_env_sin_su_valor(tmp_path: Path) -> None:
    """`Settings` las ignora en silencio (`extra="ignore"`): el aviso es lo
    único que le dice al operador que lo que escribió ahí ya no rige."""
    ruta = tmp_path / ".env"
    ruta.write_text(
        "# SAIMC_LLM_BASE_URL=comentada-no-cuenta\n"
        f"SAIMC_LLM_API_KEY={_RELLENO}\n"
        "SAIMC_ANTHROPIC_API_KEY=sk-ant-x\n",
        encoding="utf-8",
    )

    codigo, salida = _comprobar(_ajustes(anthropic_api_key="sk-ant-x"), env_file=ruta)

    # El aviso no cambia el código de salida.
    assert codigo == 0
    assert "SAIMC_LLM_API_KEY ya no se usa" in salida
    assert "SAIMC_LLM_BASE_URL" not in salida
    assert _RELLENO not in salida


def test_el_comando_avisa_de_las_variables_retiradas_del_entorno_sin_su_valor() -> None:
    codigo, salida = _comprobar(
        _ajustes(anthropic_api_key="sk-ant-x"),
        environ={"SAIMC_LLM_BASE_URL": _RELLENO, "PATH": "/usr/bin"},
    )

    assert codigo == 0
    assert "SAIMC_LLM_BASE_URL ya no se usa" in salida
    assert "SAIMC_LLM_API_KEY" not in salida
    assert _RELLENO not in salida


def test_el_comando_avisa_de_las_retiradas_aunque_no_haya_config_valida(tmp_path: Path) -> None:
    """Es el caso en que más importa: el operador puso la clave en la
    variable retirada y por eso no hay asistente."""
    ruta = tmp_path / ".env"
    ruta.write_text(f"export SAIMC_LLM_API_KEY = {_RELLENO}\n", encoding="utf-8")

    codigo, salida = _comprobar(_ajustes(llm_provider="vercel"), env_file=ruta)

    assert codigo == 1
    assert "SAIMC_LLM_API_KEY ya no se usa" in salida
    assert _RELLENO not in salida


def test_el_comando_sin_variables_retiradas_no_avisa(tmp_path: Path) -> None:
    ruta = tmp_path / ".env"
    ruta.write_text("SAIMC_LLM_PROVIDER=anthropic\n", encoding="utf-8")

    _, salida = _comprobar(
        _ajustes(anthropic_api_key="sk-ant-x"), env_file=ruta, environ={"HOME": "/x"}
    )

    assert "ya no se usa" not in salida


def test_el_comando_tolera_que_el_env_no_exista(tmp_path: Path) -> None:
    codigo, salida = _comprobar(
        _ajustes(anthropic_api_key="sk-ant-x"), env_file=tmp_path / "no-existe.env"
    )

    assert codigo == 0
    assert "ya no se usa" not in salida


def test_el_comando_mira_por_defecto_el_env_que_carga_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin que se lo digan, mira el archivo de `Settings.model_config` (`.env`
    del directorio actual) y el entorno del proceso."""
    (tmp_path / ".env").write_text(f"SAIMC_LLM_API_KEY={_RELLENO}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAIMC_LLM_BASE_URL", _RELLENO)
    lineas: list[str] = []

    provider.main([], settings=_ajustes(anthropic_api_key="sk-ant-x"), out=lineas.append)

    salida = "\n".join(lineas)
    assert "SAIMC_LLM_API_KEY ya no se usa" in salida
    assert "SAIMC_LLM_BASE_URL ya no se usa" in salida
    assert _RELLENO not in salida
