"""Catálogo de los documentos que el gestor expone en `/docs`.

**Es una lista cerrada, escrita a mano, y esa es la propiedad de seguridad.**
El slug de la URL nunca se concatena a una ruta: se usa como clave de este
diccionario, y si no está, es 404. No hay forma de que `../../etc/passwd`
—ni `..%2f..%2fetc%2fpasswd`, ni un slug con separadores de Windows— llegue a
un `open()`, porque ningún texto del pedido participa nunca de la construcción
de una ruta. Sumar un documento es agregar una línea acá, a propósito: quién
puede leerse desde la web es una decisión, no una consecuencia de dónde quedó
un archivo.

Todo lo de acá va detrás de `require_operator` (ver `routers/docs.py`). Por eso
la lista incluye `pitch-v3.5.md`, que es el documento de inversión y no se
publica: el gestor es privado (docs/decisions.md, 2026-08-30). Si algún día
existe una página pública de metodología —lo que §6.4.1 exige y todavía no
existe— NO se hace abriendo esta lista: es otro contenido, escrito para afuera.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# `docs_catalog.py` vive en `src/saimc/web/`, así que la raíz del repositorio
# está tres niveles arriba. Se resuelve desde `__file__` y no desde el cwd para
# que funcione sin importar desde dónde se lance uvicorn — mismo criterio que
# `app.py` usa para `templates/` y `static/`.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DocEntry:
    """Un documento del catálogo.

    `relative_path` es texto fijo escrito en este módulo, nunca algo que venga
    de un pedido HTTP.
    """

    slug: str
    title: str
    summary: str
    relative_path: str
    # Cómo hay que leerlo, en las palabras que ya usa CLAUDE.md: qué manda, qué
    # está congelado y qué es material de trabajo interno.
    standing: str

    @property
    def path(self) -> Path:
        return REPO_ROOT / self.relative_path


# Orden deliberado: primero lo que rige, después lo que se consulta, y al final
# lo congelado. Es el orden en que conviene leerlos, no el alfabético.
CATALOG: tuple[DocEntry, ...] = (
    DocEntry(
        slug="spec",
        title="Especificación técnica",
        summary=(
            "El documento rector. Define el pipeline, el esquema, las reglas de "
            "validación, las fases y los riesgos. Tiene huecos de numeración a "
            "propósito: las secciones de inversión quedaron en el pitch."
        ),
        relative_path="docs/spec.md",
        standing="rige",
    ),
    DocEntry(
        slug="spec-map",
        title="Mapa de la especificación",
        summary=(
            "Traduce tema → sección de la especificación y marca qué está cerrado, "
            "qué es prosa abierta y de qué se apartó el proyecto. Se lee antes de "
            "proponer cualquier cosa."
        ),
        relative_path="docs/spec-map.md",
        standing="rige",
    ),
    DocEntry(
        slug="requisitos",
        title="Inventario de requisitos funcionales",
        summary=(
            "Todo requisito agrupado en 16 módulos de pipeline, con su cita, su "
            "fase, su fuerza normativa y su estado real contra el código. Incluye "
            "los huecos abiertos y la cadena de bloqueo entre módulos."
        ),
        relative_path="docs/requisitos.md",
        standing="rige",
    ),
    DocEntry(
        slug="decisiones",
        title="Log de decisiones",
        summary=(
            "Archivo de anexado, en orden cronológico: contexto, decisión, "
            "descartado y consecuencia de cada cambio no obvio. Es el más largo "
            "del repositorio y el que más se consulta hacia atrás."
        ),
        relative_path="docs/decisions.md",
        standing="rige",
    ),
    DocEntry(
        slug="adr-001",
        title="ADR 001 — Stack Python único",
        summary=(
            "El ADR que la especificación pide por nombre. Razona el camino "
            "Fase 0 → 1 → 2 y registra los apartamientos de la sección de stack."
        ),
        relative_path="docs/decisions/001-stack-python-unico.md",
        standing="rige",
    ),
    DocEntry(
        slug="instrucciones",
        title="Instrucciones del proyecto",
        summary=(
            "Las decisiones cerradas, las reglas de trabajo, el criterio de "
            "alcance y el contexto del entorno. Es lo que gobierna cómo se "
            "construye, no qué se construye."
        ),
        relative_path="CLAUDE.md",
        standing="rige",
    ),
    DocEntry(
        slug="readme",
        title="README",
        summary="Puerta de entrada al repositorio: qué es, cómo se corre, qué vistas hay.",
        relative_path="README.md",
        standing="rige",
    ),
    DocEntry(
        slug="runbook-h0",
        title="Runbook — Medir H0",
        summary=(
            "Los pasos para correr la medición de H0 y dejar registro de la "
            "corrida: precondiciones, cómo mirar el número sin ensuciar el "
            "histórico, cómo registrarlo y qué no hacer."
        ),
        relative_path="docs/runbook-medir-h0.md",
        standing="rige",
    ),
    DocEntry(
        slug="runbook-asistente-llm",
        title="Runbook — Poner en marcha el asistente de extracción",
        summary=(
            "Los pasos para activar el asistente por LLM, en orden y con su "
            "verificación cada uno: saldo, clave, dependencia, configuración, "
            "primera llamada y comprobación de que H0 sigue comparable. "
            "Incluye cómo apagarlo y qué vigilar."
        ),
        relative_path="docs/runbook-asistente-llm.md",
        standing="rige",
    ),
    DocEntry(
        slug="costos",
        title="Costos de operación",
        summary=(
            "Qué cuesta tener el sistema funcionando y qué costaría cada "
            "capacidad todavía no construida, con la cuenta a la vista para "
            "rehacerla cuando cambie el volumen. Lo que solo sabe el operador "
            "queda marcado a completar, nunca estimado."
        ),
        relative_path="docs/costos.md",
        standing="rige",
    ),
    DocEntry(
        slug="setup",
        title="Puesta en marcha",
        summary=(
            "Handoff histórico del arranque del proyecto. No es un log de "
            "decisiones pese a vivir en la carpeta que lo parece."
        ),
        relative_path="docs/decisions/SETUP.md",
        standing="interno",
    ),
    DocEntry(
        slug="backlog",
        title="Backlog",
        summary=(
            "Congelado. El seguimiento activo de tareas vive en el Project de "
            "GitHub desde el 2026-08-23; esto queda como registro."
        ),
        relative_path="docs/backlog.md",
        standing="congelado",
    ),
    DocEntry(
        slug="pitch",
        title="Pitch v3.5",
        summary=(
            "Documento de inversión, congelado y SIN VIGENCIA. Si contradice a la "
            "especificación, gana la especificación. No se cita como fuente de "
            "nada. Por esto el gestor es privado."
        ),
        relative_path="docs/pitch-v3.5.md",
        standing="congelado",
    ),
)

BY_SLUG: dict[str, DocEntry] = {entry.slug: entry for entry in CATALOG}


@dataclass(frozen=True)
class DocStat:
    """Lo que el índice muestra de cada documento sin renderizarlo.

    `missing` en vez de reventar: un documento que se renombró en el repo tiene
    que verse como faltante en la lista, no tirar un 500 en toda la vista.
    """

    entry: DocEntry
    exists: bool
    lines: int
    bytes_: int
    modified: datetime | None

    @property
    def kilobytes(self) -> int:
        return round(self.bytes_ / 1024)


def stat_document(entry: DocEntry) -> DocStat:
    path = entry.path
    if not path.is_file():
        return DocStat(entry=entry, exists=False, lines=0, bytes_=0, modified=None)

    raw = path.read_bytes()
    return DocStat(
        entry=entry,
        exists=True,
        lines=raw.count(b"\n") + (0 if raw.endswith(b"\n") or not raw else 1),
        bytes_=len(raw),
        modified=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
    )


def catalog_stats() -> list[DocStat]:
    return [stat_document(entry) for entry in CATALOG]


# Orden de los grupos en la vista: el mismo criterio deliberado que ordena
# `CATALOG` —primero lo que rige, al final lo congelado—, ahora explícito.
_STANDING_ORDER: tuple[str, ...] = ("rige", "interno", "congelado")

_STANDING_LABELS: dict[str, str] = {
    "rige": "Rige",
    "interno": "Material interno",
    "congelado": "Congelado",
}


@dataclass(frozen=True)
class DocGroup:
    """Un grupo de la vista: los documentos que comparten vigencia."""

    standing: str
    label: str
    stats: list[DocStat]


def grouped_stats() -> list[DocGroup]:
    """El catálogo agrupado por vigencia, y adentro lo más reciente primero.

    Dos criterios distintos, a propósito. **Entre** grupos manda la vigencia,
    que es el orden en que conviene leerlos y el que `CATALOG` ya fijaba a
    mano. **Dentro** de cada grupo manda la fecha de modificación: cuando el
    operador abre esta vista suele venir de generar o tocar un documento, y
    ese es el que quiere ver primero.

    Un documento que no está en el disco no tiene fecha; va al final de su
    grupo, donde no estorba.

    Un grupo sin documentos no se devuelve: encabezados vacíos son ruido.
    """
    por_vigencia: dict[str, list[DocStat]] = {}
    for stat in catalog_stats():
        por_vigencia.setdefault(stat.entry.standing, []).append(stat)

    grupos: list[DocGroup] = []
    for standing in _STANDING_ORDER:
        stats = por_vigencia.get(standing)
        if not stats:
            continue
        stats.sort(
            key=lambda s: (s.modified is not None, s.modified),
            reverse=True,
        )
        grupos.append(
            DocGroup(
                standing=standing,
                label=_STANDING_LABELS.get(standing, standing),
                stats=stats,
            )
        )
    return grupos


def find(slug: str) -> DocEntry | None:
    """Resuelve un slug del pedido contra la lista cerrada. Nunca toca el disco."""
    return BY_SLUG.get(slug)
