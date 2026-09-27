"""Glosario del operador: reglas que se inyectan en el prompt de `propose_ad`.

Slice 3c del plan del asistente (`docs/spec-slice-3c.md`). La tabla
(`saimc.models.GlossaryEntry`) es editable desde `/catalog`; este módulo es
la lógica pura que consume `llm_propose_ad` — sin sesión ni cliente del LLM.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import GlossaryEntry


def active_glossary_entries(session: Session) -> list[GlossaryEntry]:
    """Las entradas activas, en el orden en que se inyectan en el prompt."""
    stmt = select(GlossaryEntry).where(GlossaryEntry.active.is_(True)).order_by(
        GlossaryEntry.term
    )
    return list(session.execute(stmt).scalars())


def _activas_ordenadas(entries: Sequence[GlossaryEntry]) -> list[GlossaryEntry]:
    # No confía en que quien llama ya filtró: una entrada desactivada no debe
    # poder colarse en el prompt ni en el hash de la versión por un descuido
    # del llamador.
    return sorted((e for e in entries if e.active), key=lambda e: e.term)


def glossary_prompt_section(entries: Sequence[GlossaryEntry]) -> str:
    """El bloque de texto a agregar al prompt, o `""` sin entradas activas.

    `""` y no un encabezado vacío: el requisito de «glosario corto» empieza
    por no gastar tokens cuando nadie cargó nada todavía.
    """
    activas = _activas_ordenadas(entries)
    if not activas:
        return ""
    lineas = "\n".join(f"- «{e.term}»: {e.definition}" for e in activas)
    return f"Glosario del operador (reglas que no salen del catálogo):\n{lineas}\n\n"


def effective_prompt_version(base: str, entries: Sequence[GlossaryEntry]) -> str:
    """`base` sin cambios si no hay glosario activo; si lo hay, `base` más un
    hash corto de su contenido.

    Reemplaza el requisito del plan («cada cambio del glosario cambia la
    versión del prompt») por un mecanismo automático: el operador edita desde
    `/catalog` sin acordarse de tocar `PROMPT_VERSION`, y dos corridas con
    distinto glosario quedan con versiones distintas y comparables en el
    backtest. El orden de inserción no importa: se ordena por `term` antes de
    hashear.
    """
    activas = _activas_ordenadas(entries)
    if not activas:
        return base
    contenido = "\n".join(f"{e.term}\x00{e.definition}" for e in activas)
    digest = hashlib.sha256(contenido.encode("utf-8")).hexdigest()[:8]
    return f"{base}+glossary:{digest}"
