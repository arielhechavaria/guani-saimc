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
from textual.widgets import Footer, Input, OptionList, RichLog, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode
from textual.worker import Worker
from textual_image.widget import Image

from saimc.tui import avatar, registry, theme
from saimc.tui.registry import Command, LogSource, matches, matches_text
from saimc.tui.runner import Finished, stream
from saimc.tui.runs import CUT, FAILED, OK, RUNNING, Run, RunBook, format_duration
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

HEALTH_COLOR = {"ok": SUCCESS, "warn": WARN, "err": ERROR, "off": MUTED}
HEALTH_WORD = {"ok": "al día", "warn": "mirar", "err": "atrasado", "off": "apagado"}
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


class HelpScreen(ModalScreen[None]):
    """Las teclas y qué significa cada marca. Se cierra con Esc, Enter o `?`."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,enter,question_mark", "close", "Cerrar")]

    KEYS = (
        ("j / k", "mover (en la salida, desplazar)"),
        ("Enter", "correr (en seco, si hay versión que escribe)"),
        ("e", "correr la versión que escribe (pide y/n)"),
        ("Tab", "cambiar de panel: menú, corridas, salida"),
        ("x", "cortar la corrida elegida"),
        ("c", "quitar las corridas terminadas"),
        ("u", "actualizar el estado de las etapas"),
        ("/", "buscar un comando (Esc limpia)"),
        ("m", "mostrar u ocultar el menú"),
        ("q", "salir"),
    )
    MARKS = (
        ("●", "salud: verde al día, ámbar mirar, rojo atrasado"),
        ("● ✓ ✗ ■", "corrida: en curso, bien, falló, cortada"),
        ("⚠e", "tiene versión que escribe o gasta (tecla e)"),
        ("⚠", "escribe o gasta: pide y/n antes"),
    )

    def compose(self) -> ComposeResult:
        body = Text()
        body.append("Teclas\n", style=f"bold {PRIMARY}")
        for key, what in self.KEYS:
            body.append(f"  {key:<10}", style=f"bold {GROUP}")
            body.append(f"{what}\n", style=TEXT)
        body.append("\nMarcas\n", style=f"bold {PRIMARY}")
        for mark, what in self.MARKS:
            body.append(f"  {mark:<10}", style=f"bold {GROUP}")
            body.append(f"{what}\n", style=TEXT)
        yield Static(body, id="helpbox")

    def action_close(self) -> None:
        self.dismiss(None)


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
        width: 44%; min-width: 40; max-width: 50; background: {BG}; scrollbar-size-vertical: 1;
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
    #detail {{ height: auto; max-height: 7; border-bottom: solid {DIVIDER}; }}
    #runs {{
        height: auto; max-height: 6; min-height: 1; border: none; padding: 0;
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
    #brief {{ height: 2; padding: 0 1; border-bottom: solid {DIVIDER}; }}
    #search {{
        display: none; height: 1; border: none; padding: 0 1; background: {SURFACE};
        color: {TEXT};
    }}
    #search:focus {{ border: none; background-tint: transparent; }}
    Footer {{ background: {BG}; color: {MUTED}; }}
    Footer FooterKey {{ background: {BG}; color: {MUTED}; }}
    Footer FooterKey .footer-key--key {{ background: {BG}; color: {PRIMARY}; text-style: bold; }}
    Footer FooterKey .footer-key--description {{ background: {BG}; color: {MUTED}; }}
    HelpScreen {{ align: center middle; background: {BG} 80%; }}
    #helpbox {{
        width: 90; max-width: 100%; height: auto; border: double {FRAME}; padding: 1 2;
        background: {SURFACE};
    }}
    ConfirmScreen {{ align: center middle; background: {BG} 80%; }}
    #confirm {{
        width: 70; height: auto; border: double {WARN}; padding: 1 2; background: {SURFACE};
    }}
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "cursor_down", "Abajo", show=False),
        Binding("k", "cursor_up", "Arriba", show=False),
        Binding("r", "run", "Correr", show=False),
        Binding("e", "run_real", "Real"),
        Binding("x", "stop", "Cortar"),
        Binding("c", "clear", "Limpiar"),
        Binding("u", "refresh_status", "Estado"),
        Binding("slash", "search", "Buscar"),
        Binding("m", "toggle_menu", "Menú"),
        Binding("question_mark", "help", "Ayuda"),
        Binding("escape", "close_search", "Cerrar búsqueda", show=False),
        Binding("q", "quit", "Salir"),
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
        # Las versiones que escriben no tienen entrada propia: cuelgan de su seco (tecla `e`).
        self._reals = {c.real for c in commands if c.real}
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
            yield Static(id="brief")
            with Horizontal(id="body"):
                yield Menu("Guani", id="menu")
                with Vertical(id="right"):
                    yield Static("Elegí un comando.", id="detail")
                    yield OptionList(id="runs")
                    yield RichLog(id="output", wrap=True, markup=False)
            yield Input(id="search", placeholder="Buscar comando… (Enter elige, Esc limpia)")
        yield Footer(show_command_palette=False)

    def on_mount(self) -> None:
        tree = self.query_one(Menu)
        tree.show_root = False
        tree.guide_depth = 2
        self._build_menu()
        self.query_one("#runs", OptionList).border_title = "Corridas"
        self.set_interval(1, self._tick)
        if self._status_loader is not None:
            self.set_interval(STATUS_REFRESH, self.action_refresh_status)
            self.action_refresh_status()
        tree.focus()

    def _build_menu(self, query: str = "", *, reset_cursor: bool = False) -> None:
        """Arma el árbol; con `query`, solo lo que coincide (y siempre desplegado)."""
        tree = self.query_one(Menu)
        tree.clear()
        self._group_nodes = {}
        for group in self._groups:
            entries = [
                c
                for c in self._commands.values()
                if c.group == group
                and c.id not in self._reals
                and matches(c, query, self._commands.get(c.real) if c.real else None)
            ]
            if not entries:
                continue
            node = tree.root.add(self._group_label(group), expand=True)
            self._group_nodes[group] = node
            for command in entries:
                node.add_leaf(self._command_label(command), data=command.id)
        logs = [log for log in self._logs.values() if matches_text(f"log en vivo {log.title}", query)]
        if logs:
            node = tree.root.add("Logs en vivo", expand=True)
            for log in logs:
                node.add_leaf(log.title, data=log.id)
        if not query.strip():
            tree.root.add_leaf("Salir", data=QUIT)
        if reset_cursor:
            # El árbol recién armado no tiene líneas hasta el próximo refresco.
            tree.call_after_refresh(self._cursor_to_first)

    def _cursor_to_first(self) -> None:
        tree = self.query_one(Menu)
        if tree.root.children:
            first = tree.root.children[0]
            tree.move_cursor(first)
            # Si la línea no cambió, el árbol no avisa: se describe a mano.
            self._describe(first)

    @staticmethod
    def _command_label(command: Command) -> Text:
        label = Text(command.title)
        if command.real:
            label.append(" ⚠e", style=WARN)
        elif command.needs_confirm:
            label.append(" ⚠", style=WARN)
        return label

    def on_resize(self) -> None:
        self.query_one("#header").display = self.size.height >= MIN_HEIGHT_FOR_HEADER
        self._update_brief()

    def _update_brief(self) -> None:
        """Con la cabecera escondida (terminal baja), una línea con el estado en resumen."""
        brief = self.query_one("#brief", Static)
        brief.display = not self.query_one("#header").display
        text = Text()
        text.append("Guani", style=f"bold {PRIMARY}")
        text.append("  Terminal del operador", style=MUTED)
        if self._status_loader is not None:
            text.append("   ")
            if self._snapshot.error:
                text.append("estado no disponible", style=WARN)
            elif not self._snapshot.stages:
                text.append("leyendo el estado…", style=MUTED)
            for level, count in self._snapshot.counts().items():
                text.append("● ", style=HEALTH_COLOR[level])
                text.append(f"{count} {HEALTH_WORD[level]}  ", style=TEXT)
        brief.update(text)

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
            real = self._commands.get(item.real) if item.real else None
            if real is None:
                text.append(f"$ {item.display}", style=MUTED)
            else:
                text.append(f"Enter  $ {item.display}\n", style=MUTED)
                text.append(f"e      $ {real.display}", style=MUTED)
                for name, on in (("escribe", real.writes), ("gasta", real.spends)):
                    if on:
                        text.append(f"  [{name}]", style=f"bold {WARN}")
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

    # --- búsqueda, ayuda y menú -------------------------------------------------

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.display = True
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._build_menu(event.value, reset_cursor=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter deja el filtro puesto y devuelve el foco al menú, sobre el primer resultado.
        if event.input.id != "search":
            return
        tree = self.query_one(Menu)
        first = next((n for g in self._group_nodes.values() for n in g.children), None)
        if first is None:
            first = next((n for n in tree.root.children if n.data is not None), None)
        tree.focus()
        if first is not None:
            tree.move_cursor(first)

    def action_close_search(self) -> None:
        box = self.query_one("#search", Input)
        if not box.display:
            return
        box.value = ""
        box.display = False
        self._build_menu(reset_cursor=True)
        self.query_one(Menu).focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_menu(self) -> None:
        menu = self.query_one(Menu)
        menu.display = not menu.display
        (menu if menu.display else self.query_one("#runs", OptionList)).focus()

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
        self._update_brief()
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
        if item is not None:
            self._launch(item)

    def action_run_real(self) -> None:
        node = self.query_one(Menu).cursor_node
        item = self._lookup(node.data if node else None)
        if not isinstance(item, Command) or item.real is None:
            self.notify("Este comando no tiene una versión que escriba.")
            return
        self._launch(self._commands[item.real])

    def _dry_note(self, real: Command) -> str | None:
        """Qué dio el seco de este comando la última vez, para decidir con eso a la vista."""
        dry = next((c for c in self._commands.values() if c.real == real.id), None)
        if dry is None:
            return None
        last = self.book.last_finished(dry.id)
        if last is None or last.ended_at is None:
            return f"Todavía no corriste «{dry.title}» en esta sesión."
        ago = format_duration((datetime.now() - last.ended_at).total_seconds())
        result = {OK: "salió bien", FAILED: "falló", CUT: "se cortó"}[last.status]
        note = f"Último «{dry.title}»: hace {ago}, {result}."
        tail = next((ln for ln in reversed(last.lines) if ln.strip()), "")
        if tail:
            note += f"\n  {Text.from_ansi(tail).plain.strip()[:90]}"
        return note

    def _launch(self, item: Command | LogSource) -> None:
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
            dry_note = self._dry_note(item)
            if dry_note is not None:
                notes.append(dry_note)
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
