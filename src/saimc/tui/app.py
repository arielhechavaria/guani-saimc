"""App Textual: árbol de comandos y logs a la izquierda, salida a la derecha.

La forma sigue a la de gentle-ai (marco doble, cursor `▸`, `j`/`k`, «Salir» al
final del menú); los colores son los tokens `--m3-` oscuros de
`design_tokens.css` (`saimc.tui.theme`) y el avatar es la imagen de la web,
mostrada con `textual-image` (Sixel o Kitty si la terminal los soporta; si no,
medios bloques).
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, RichLog, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode
from textual.worker import Worker
from textual_image.widget import Image

from saimc.tui import avatar, registry, theme
from saimc.tui.registry import Command, LogSource
from saimc.tui.runner import Finished, stream
from saimc.tui.runs import CUT, FAILED, OK, RUNNING, Run, RunBook
from saimc.tui.status import Snapshot, Stage, StatusLoader

_P = theme.load()
BG = _P["m3-bg"]
SURFACE = _P["m3-surface"]
TEXT = _P["m3-on-surface"]
MUTED = _P["m3-on-surface-variant"]
FRAME = _P["m3-outline"]
DIVIDER = _P["m3-outline-variant"]
PRIMARY = _P["m3-primary"]
SELECTED = _P["m3-primary-container"]
GROUP = _P["m3-tertiary"]
WARN = _P["m3-warn"]
ERROR = _P["m3-error"]
SUCCESS = _P["m3-success"]

QUIT = "__quit__"
# En celdas: 24 de ancho y 12 de alto se ven cuadradas (la celda es el doble de alta).
AVATAR_SIZE = 24
# Con menos filas la cabecera se esconde para que el menú no se quede sin lugar.
MIN_HEIGHT_FOR_HEADER = 36

HELP = "j/k: mover • enter: correr • tab: panel • x: cortar • c: limpiar • u: estado • q: salir"

HEALTH_COLOR = {"ok": SUCCESS, "warn": WARN, "err": ERROR, "off": MUTED}
# Cada cuánto se vuelve a leer el estado sin que nadie lo pida (segundos).
STATUS_REFRESH = 60

STATUS_COLOR = {RUNNING: PRIMARY, OK: SUCCESS, FAILED: ERROR, CUT: WARN}


class Menu(Tree[str]):
    """Árbol con las marcas de gentle-ai: `▸` en el comando elegido y grupos resaltados."""

    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "

    def render_label(self, node: TreeNode[str], base_style: Style, style: Style) -> Text:
        label = super().render_label(node, base_style, style)
        if node.allow_expand:
            return label
        marker = "▸ " if node is self.cursor_node else "  "
        return Text(marker) + label


class ConfirmScreen(ModalScreen[bool]):
    """Pregunta antes de hacer algo que escribe, gasta o corta una corrida."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "answer(True)", "Sí"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, title: str, display: str, notes: list[str], question: str) -> None:
        super().__init__()
        self._title = title
        self._display = display
        self._notes = notes
        self._question = question

    def compose(self) -> ComposeResult:
        body = Text()
        body.append(f"{self._title}\n", style=f"bold {PRIMARY}")
        if self._display:
            body.append(f"$ {self._display}\n", style=MUTED)
        body.append("\n")
        for note in self._notes:
            body.append(f"{note}\n", style=WARN)
        body.append(f"{self._question} ", style=TEXT)
        body.append("y", style=f"bold {PRIMARY}")
        body.append("/", style=MUTED)
        body.append("n", style=f"bold {PRIMARY}")
        yield Static(body, id="confirm")

    def action_answer(self, accepted: bool) -> None:
        self.dismiss(accepted)


class GuaniTUI(App[None]):
    TITLE = "Guani"
    CSS = f"""
    Screen {{ background: {BG}; color: {TEXT}; }}
    #menu, #output {{
        scrollbar-background: {BG}; scrollbar-background-hover: {BG};
        scrollbar-background-active: {BG}; scrollbar-color: {DIVIDER};
        scrollbar-color-hover: {FRAME}; scrollbar-color-active: {MUTED};
    }}
    #frame {{ border: double {FRAME}; padding: 0 1; height: 1fr; }}
    #header {{ height: auto; border-bottom: solid {DIVIDER}; }}
    #avatar {{ width: {AVATAR_SIZE}; height: {AVATAR_SIZE // 2}; margin-right: 2; }}
    #title {{ height: auto; padding: 1 0 0 1; }}
    #body {{ height: 1fr; }}
    #menu {{
        width: 46; background: {BG}; scrollbar-size-vertical: 1;
        border-right: solid {DIVIDER};
    }}
    #menu:focus {{ background-tint: transparent; }}
    #menu > .tree--guides, #menu > .tree--guides-hover, #menu > .tree--guides-selected {{
        color: {BG};
    }}
    #menu > .tree--cursor, #menu:focus > .tree--cursor {{
        background: {SELECTED}; color: {PRIMARY}; text-style: bold;
    }}
    #menu > .tree--highlight, #menu > .tree--highlight-line {{ background: {BG}; }}
    #menu > .tree--label {{ color: {TEXT}; }}
    #right {{ padding: 0 1; }}
    #detail {{ height: auto; max-height: 8; border-bottom: solid {DIVIDER}; }}
    #runs {{
        height: auto; max-height: 8; min-height: 1; border: none; padding: 0;
        background: {BG}; border-bottom: solid {DIVIDER}; scrollbar-size-vertical: 1;
        scrollbar-background: {BG}; scrollbar-color: {DIVIDER};
    }}
    #runs:focus {{ background-tint: transparent; }}
    #runs > .option-list--option {{ color: {TEXT}; }}
    #runs > .option-list--option-highlighted,
    #runs:focus > .option-list--option-highlighted {{
        background: {SELECTED}; color: {PRIMARY}; text-style: bold;
    }}
    #runs > .option-list--option-hover {{ background: {BG}; }}
    #output {{ background: {BG}; color: {TEXT}; scrollbar-size-vertical: 1; }}
    #help {{ color: {MUTED}; height: 1; }}
    ConfirmScreen {{ align: center middle; background: {BG} 80%; }}
    #confirm {{
        width: 70; height: auto; border: double {WARN}; padding: 1 2; background: {SURFACE};
    }}
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "cursor_down", "Abajo", show=False),
        Binding("k", "cursor_up", "Arriba", show=False),
        Binding("r", "run", "Correr", show=False),
        Binding("x", "stop", "Cortar", show=False),
        Binding("c", "clear", "Limpiar", show=False),
        Binding("u", "refresh_status", "Estado", show=False),
        Binding("q", "quit", "Salir", show=False),
    ]

    def __init__(
        self,
        commands: tuple[Command, ...] = registry.COMMANDS,
        logs: tuple[LogSource, ...] = registry.LOGS,
        cwd: Path = registry.REPO_ROOT,
        status_loader: StatusLoader | None = None,
    ) -> None:
        super().__init__()
        self._commands = {c.id: c for c in commands}
        self._logs = {log.id: log for log in logs}
        self._groups = tuple(dict.fromkeys(c.group for c in commands))
        self._cwd = cwd
        self.book = RunBook()
        self._shown: Run | None = None
        self._run_workers: dict[int, Worker[None]] = {}
        self._status_loader = status_loader
        self._snapshot = Snapshot()
        self._group_nodes: dict[str, TreeNode[str]] = {}
        self._warned_status_error = False

    def compose(self) -> ComposeResult:
        title = Text()
        title.append("Guani\n", style=f"bold {PRIMARY}")
        title.append("Terminal del operador\n\n", style=TEXT)
        title.append("Sistema Abierto de Inteligencia de Mercado", style=MUTED)
        with Vertical(id="frame"):
            with Horizontal(id="header"):
                yield Image(avatar.load(BG), id="avatar")
                yield Static(title, id="title")
            with Horizontal(id="body"):
                yield Menu("Guani", id="menu")
                with Vertical(id="right"):
                    yield Static("Elegí un comando.", id="detail")
                    yield OptionList(id="runs")
                    yield RichLog(id="output", wrap=True, markup=False)
            yield Static(HELP, id="help")

    def on_mount(self) -> None:
        tree = self.query_one(Menu)
        tree.show_root = False
        tree.guide_depth = 2
        for group in self._groups:
            node = tree.root.add(self._group_label(group), expand=True)
            self._group_nodes[group] = node
            for command in self._commands.values():
                if command.group == group:
                    marker = " ⚠" if command.needs_confirm else ""
                    node.add_leaf(command.title + marker, data=command.id)
        logs = tree.root.add("Logs en vivo", expand=True)
        for log in self._logs.values():
            logs.add_leaf(log.title, data=log.id)
        tree.root.add_leaf("Salir", data=QUIT)
        self.query_one("#runs", OptionList).border_title = "Corridas"
        self.set_interval(1, self._tick)
        if self._status_loader is not None:
            self.set_interval(STATUS_REFRESH, self.action_refresh_status)
            self.action_refresh_status()
        tree.focus()

    def on_resize(self) -> None:
        self.query_one("#header").display = self.size.height >= MIN_HEIGHT_FOR_HEADER

    def _lookup(self, key: str | None) -> Command | LogSource | None:
        if key is None:
            return None
        return self._commands.get(key) or self._logs.get(key)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        self._describe(event.node)

    def _describe(self, node: TreeNode[str]) -> None:
        item = self._lookup(node.data)
        detail = self.query_one("#detail", Static)
        text = Text()
        if isinstance(item, Command):
            text.append(item.title, style=f"bold {PRIMARY}")
            for name, on in (("escribe", item.writes), ("gasta", item.spends)):
                if on:
                    text.append(f"  [{name}]", style=f"bold {WARN}")
            text.append(f"\n{item.about}\n", style=TEXT)
            text.append(f"$ {item.display}", style=MUTED)
            detail.update(text)
        elif isinstance(item, LogSource):
            text.append(f"Log en vivo: {item.title}\n", style=f"bold {PRIMARY}")
            text.append(f"$ {item.display}\n", style=MUTED)
            text.append("x para cortar", style=MUTED)
            detail.update(text)
        elif node.data == QUIT:
            detail.update(Text("Cerrar la terminal (también con q).", style=MUTED))
        else:
            group = next((g for g, n in self._group_nodes.items() if n is node), None)
            if group is not None and self._status_loader is not None:
                detail.update(self._stage_detail(group))

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        # Enter sobre un grupo lo pliega (lo hace el árbol); sobre un comando, lo corre.
        if event.node.data is not None:
            self.action_run()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        run = self._run_of(event.option_id)
        if run is not None and run is not self._shown:
            self._show(run)

    # --- estado de las etapas ---------------------------------------------------

    def action_refresh_status(self) -> None:
        if self._status_loader is None:
            self.notify("Esta terminal se abrió sin lectura del estado.")
            return
        self.run_worker(self._load_status(self._status_loader), group="status", exclusive=True)

    async def _load_status(self, loader: StatusLoader) -> None:
        self._snapshot = await asyncio.to_thread(loader)
        if self._snapshot.error and not self._warned_status_error:
            self._warned_status_error = True
            self.notify(f"No pude leer el estado: {self._snapshot.error}", severity="warning")
        for group, node in self._group_nodes.items():
            node.set_label(self._group_label(group))
        cursor = self.query_one(Menu).cursor_node
        if cursor is not None:
            self._describe(cursor)

    def _group_label(self, group: str) -> Text:
        stage = self._snapshot.stage(group)
        label = Text()
        if stage is None:
            label.append(group, style=f"bold {GROUP}")
            return label
        label.append("● ", style=HEALTH_COLOR[stage.health])
        label.append(group, style=f"bold {GROUP}")
        if stage.summary:
            label.append(f"  {stage.summary}", style=MUTED)
        return label

    def _stage_detail(self, group: str) -> Text:
        text = Text()
        stage: Stage | None = self._snapshot.stage(group)
        if stage is None:
            reason = self._snapshot.error or "leyendo…"
            text.append(f"{group}\n", style=f"bold {PRIMARY}")
            text.append(f"Estado no disponible: {reason}", style=MUTED)
            return text
        text.append("● ", style=HEALTH_COLOR[stage.health])
        text.append(f"{group} — {stage.health_label}\n", style=f"bold {PRIMARY}")
        for line in (stage.summary, *stage.details, stage.foot):
            if line:
                text.append(f"{line}\n", style=TEXT)
        text.rstrip()
        return text

    # --- movimiento: j/k vale según qué panel tenga el foco ---------------------

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def _move(self, step: int) -> None:
        focused = self.focused
        if isinstance(focused, Tree | OptionList):
            focused.action_cursor_down() if step > 0 else focused.action_cursor_up()
        elif isinstance(focused, RichLog):
            focused.scroll_down() if step > 0 else focused.scroll_up()

    # --- correr -----------------------------------------------------------------

    def action_run(self) -> None:
        node = self.query_one(Menu).cursor_node
        if node is not None and node.data == QUIT:
            self.call_later(self.action_quit)
            return
        item = self._lookup(node.data if node else None)
        if item is None:
            return
        live = self.book.running(item.id)
        if live is not None:
            self._show(live)
            self.notify(f"«{item.title}» ya está corriendo; te muestro esa corrida.")
            return
        if isinstance(item, Command) and item.needs_confirm:
            notes = []
            if item.writes:
                notes.append("Este comando escribe en la base o en archivos.")
            if item.spends:
                notes.append("Este comando gasta plata en una API.")
            other = self.book.writing()
            if other is not None:
                notes.append(f"«{other.title}» sigue escribiendo: van a correr a la vez.")
            screen = ConfirmScreen(item.title, item.display, notes, "¿Correrlo?")
            self.push_screen(screen, lambda ok: self._start(item) if ok else None)
        else:
            self._start(item)

    def _start(self, item: Command | LogSource) -> None:
        run = self.book.start(
            key=item.id,
            title=item.title,
            display=item.display,
            kind="log" if isinstance(item, LogSource) else "command",
            writes=isinstance(item, Command) and item.writes,
            at=datetime.now(),
        )
        runs = self.query_one("#runs", OptionList)
        runs.add_option(Option(self._label(run), id=self._option_id(run)))
        for dropped in self.book.trim():
            runs.remove_option(self._option_id(dropped))
        self._show(run)
        runs.highlighted = runs.get_option_index(self._option_id(run))
        self._run_workers[run.number] = self.run_worker(self._execute(run, item.argv), exclusive=False)

    async def _execute(self, run: Run, argv: tuple[str, ...]) -> None:
        try:
            async for item in stream(argv, self._cwd):
                if isinstance(item, Finished):
                    run.finish(item.returncode, datetime.now())
                    self._refresh(run)
                    self._write(run, self._footer(run))
                    if run.writes and self._status_loader is not None:
                        self.action_refresh_status()
                else:
                    run.lines.append(item)
                    self._write(run, Text.from_ansi(item))
        except asyncio.CancelledError:
            # Cancelación desde afuera (al salir): la app ya se está cerrando.
            run.cut(datetime.now())
            raise
        finally:
            self._run_workers.pop(run.number, None)

    # --- cortar, limpiar, salir -------------------------------------------------

    def action_stop(self) -> None:
        run = self._shown
        if run is None or not run.running:
            self.notify("No hay nada corriendo en la corrida elegida.")
            return
        run.cut(datetime.now())
        worker = self._run_workers.get(run.number)
        if worker is not None:
            worker.cancel()
        self._refresh(run)
        self._write(run, self._footer(run))

    def action_clear(self) -> None:
        runs = self.query_one("#runs", OptionList)
        for removed in self.book.clear_finished():
            runs.remove_option(self._option_id(removed))
        if self._shown is not None and not self._shown.running and self._shown not in self.book.runs:
            self._shown = None
            self.query_one(RichLog).clear()

    async def action_quit(self) -> None:
        writer = self.book.writing()
        if writer is None:
            self.exit()
            return
        screen = ConfirmScreen(
            "Salir",
            "",
            [f"«{writer.title}» sigue escribiendo; al salir se corta."],
            "¿Salir igual?",
        )
        self.push_screen(screen, lambda ok: self.exit() if ok else None)

    # --- la corrida que se ve ---------------------------------------------------

    def _show(self, run: Run) -> None:
        log = self.query_one(RichLog)
        log.clear()
        self._shown = run
        log.write(Text(f"$ {run.display}", style=f"bold {PRIMARY}"))
        for line in run.lines:
            log.write(Text.from_ansi(line))
        if not run.running:
            log.write(self._footer(run))
        runs = self.query_one("#runs", OptionList)
        runs.highlighted = runs.get_option_index(self._option_id(run))

    def _write(self, run: Run, renderable: Text) -> None:
        if run is self._shown:
            self.query_one(RichLog).write(renderable)

    def _footer(self, run: Run) -> Text:
        return Text(run.footer(datetime.now()), style=STATUS_COLOR[run.status])

    def _run_of(self, option_id: str | None) -> Run | None:
        if option_id is None:
            return None
        return self.book.get(int(option_id.removeprefix("run-")))

    @staticmethod
    def _option_id(run: Run) -> str:
        return f"run-{run.number}"

    def _label(self, run: Run) -> Text:
        summary = run.summary(datetime.now())
        glyph, rest = summary[0], summary[1:]
        label = Text()
        label.append(glyph, style=f"bold {STATUS_COLOR[run.status]}")
        label.append(rest)
        return label

    def _refresh(self, run: Run) -> None:
        self.query_one("#runs", OptionList).replace_option_prompt(self._option_id(run), self._label(run))

    def _tick(self) -> None:
        for run in self.book.active():
            self._refresh(run)
