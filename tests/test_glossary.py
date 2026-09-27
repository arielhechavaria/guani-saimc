"""`saimc.glossary` — reglas del operador que se inyectan en el prompt.

Puras: sin sesión, sin cliente del LLM. Los casos están escritos antes que el
código (`docs/spec-slice-3c.md`).
"""

from saimc.glossary import effective_prompt_version, glossary_prompt_section
from saimc.models import GlossaryEntry


def _entrada(term: str, definition: str, *, active: bool = True) -> GlossaryEntry:
    return GlossaryEntry(term=term, definition=definition, active=active)


def test_sin_entradas_la_seccion_es_vacia() -> None:
    assert glossary_prompt_section([]) == ""


def test_con_una_entrada_activa_arma_la_seccion() -> None:
    seccion = glossary_prompt_section([_entrada("4x10", "4 paquetes de 10 lb")])

    assert "4x10" in seccion
    assert "4 paquetes de 10 lb" in seccion


def test_una_entrada_inactiva_no_aparece_en_la_seccion() -> None:
    seccion = glossary_prompt_section(
        [_entrada("4x10", "4 paquetes de 10 lb", active=False)]
    )

    assert seccion == ""


def test_sin_entradas_la_version_efectiva_es_la_base() -> None:
    assert effective_prompt_version("2026-09-27.1", []) == "2026-09-27.1"


def test_una_entrada_inactiva_no_cambia_la_version() -> None:
    version = effective_prompt_version(
        "2026-09-27.1", [_entrada("4x10", "4 paquetes de 10 lb", active=False)]
    )

    assert version == "2026-09-27.1"


def test_mismas_entradas_en_otro_orden_dan_la_misma_version() -> None:
    a = effective_prompt_version(
        "2026-09-27.1",
        [_entrada("4x10", "4 paquetes de 10 lb"), _entrada("6x5", "6 de 5 lb")],
    )
    b = effective_prompt_version(
        "2026-09-27.1",
        [_entrada("6x5", "6 de 5 lb"), _entrada("4x10", "4 paquetes de 10 lb")],
    )

    assert a == b
    assert a != "2026-09-27.1"


def test_una_entrada_editada_cambia_la_version() -> None:
    original = effective_prompt_version(
        "2026-09-27.1", [_entrada("4x10", "4 paquetes de 10 lb")]
    )
    editada = effective_prompt_version(
        "2026-09-27.1", [_entrada("4x10", "4 paquetes de 10 lb, caja 40 lb")]
    )

    assert original != editada
