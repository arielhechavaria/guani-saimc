"""Estado de los 16 módulos del pipeline, leído de `docs/requisitos.md`.

Alimenta la sección «Hoy» de `/control`: qué módulo conviene atacar hoy en
diseño/implementación, sin mantener una segunda copia del inventario. La
fuente sigue siendo el documento (`docs/requisitos.md`, tabla «Estado de los
16 módulos»); este módulo solo la parsea para la vista.

Mismo criterio fail-soft que `read_h0_runs` (`saimc.extraction.measure`): el
documento es material de trabajo que un humano edita a mano, así que un
título movido, una fila mal formada o el archivo directamente ausente no
pueden tumbar `/control`. Se saltea lo que no se puede leer —la fila, o la
tabla entera si la sección no aparece— y se sigue con el resto.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Este módulo vive en `src/saimc/`, así que la raíz del repositorio está dos
# niveles arriba. Se resuelve desde `__file__` y no desde el cwd — mismo
# criterio que `docs_catalog.py` y `extraction/measure.py` usan para no
# depender de desde dónde se lanza uvicorn.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUISITOS_FILENAME = "requisitos.md"

# El encabezado exacto bajo el que vive la tabla. Si el documento lo mueve o
# lo renombra, la sección se da por no encontrada — no se adivina otra.
_SECTION_HEADING = "## Estado de los 16 módulos"


@dataclass(frozen=True)
class ModuleStatus:
    """Una fila de la tabla «Estado de los 16 módulos»."""

    number: str  # "01".."16", tal cual el doc
    name: str  # "Captura e ingesta"
    status: str  # "Construido", "Ausente" (sin ** de markdown), etc.
    blocked_by: str  # "—" si no hay bloqueo, o el texto del motivo
    is_actionable: bool  # ni terminado ni bloqueado: hay algo para hacer hoy


def _requisitos_path() -> Path:
    """`docs/requisitos.md` en la raíz del repo, resuelta desde `__file__`."""
    return _REPO_ROOT / "docs" / _REQUISITOS_FILENAME


def _table_row_cells(line: str) -> list[str] | None:
    """Las celdas de una fila de tabla markdown (`| a | b |`).

    `None` si la línea no tiene forma de fila de tabla —no arranca con `|`
    una vez sacado el espacio de los bordes—, que es como se reconoce que la
    tabla terminó (o que nunca empezó).
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator_cell(cell: str) -> bool:
    core = cell.strip(":")  # `:---`, `---:` y `:---:` alinean sin dejar de separar.
    return bool(core) and set(core) == {"-"}


def _is_separator_row(cells: list[str]) -> bool:
    """La fila `| --- | --- |` que separa el encabezado de los datos en una
    tabla markdown. No es una fila de datos."""
    return bool(cells) and all(_is_separator_cell(cell) for cell in cells)


def _clean_markdown_bold(text: str) -> str:
    """Una celda sin el `**` de negrita markdown (p.ej. `**Ausente**` →
    `Ausente`). El resto del formato —backticks, etc.— se conserva tal cual:
    solo la negrita se usa para remarcar el estado, y solo eso hay que limpiar."""
    return text.replace("**", "").strip()


def _is_done(status: str) -> bool:
    """«Terminado»: arranca con «Construido» (cubre la variante «con límite
    declarado») o es exactamente «Fase 0 completa»."""
    return status.startswith("Construido") or status == "Fase 0 completa"


def read_module_status(path: Path | None = None) -> list[ModuleStatus]:
    """Lee la tabla «Estado de los 16 módulos» de `docs/requisitos.md`.

    Tolera todo lo que un documento editado a mano acumula: que el archivo no
    exista, que la sección se haya movido o renombrado, que una fila no tenga
    las 4 columnas esperadas. Nada de eso puede tumbar `/control`.
    """
    target = path if path is not None else _requisitos_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return []

    lines = raw.splitlines()
    try:
        heading_index = next(
            i for i, line in enumerate(lines) if line.strip() == _SECTION_HEADING
        )
    except StopIteration:
        return []

    # Recorre lo que sigue al título: la primera fila de tabla que aparece es
    # el encabezado (se descarta), la siguiente suele ser la separadora
    # (también se descarta) y de ahí en más son datos. La tabla termina en la
    # primera línea que ya no tiene forma de fila —blanco, prosa, otro título.
    started = False
    row_cells: list[list[str]] = []
    for line in lines[heading_index + 1 :]:
        cells = _table_row_cells(line)
        if cells is None:
            if started or line.strip():
                break  # la tabla terminó, o nunca hubo tabla bajo el título
            continue  # línea en blanco antes de que arranque la tabla
        if not started:
            started = True  # ésta es la fila de encabezado; se descarta
            continue
        if _is_separator_row(cells):
            continue
        row_cells.append(cells)

    modules: list[ModuleStatus] = []
    for cells in row_cells:
        if len(cells) != 4:
            continue  # fila que no matchea el formato de tabla: se saltea
        number, name, status_raw, blocked_by_raw = cells
        status = _clean_markdown_bold(status_raw)
        blocked_by = _clean_markdown_bold(blocked_by_raw)
        modules.append(
            ModuleStatus(
                number=number,
                name=name,
                status=status,
                blocked_by=blocked_by,
                is_actionable=not _is_done(status) and blocked_by == "—",
            )
        )
    return modules
