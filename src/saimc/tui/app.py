"""App Textual: árbol de comandos y logs a la izquierda, salida a la derecha.

La forma sigue a la de gentle-ai (marco doble, cursor `▸`, `j`/`k`, «Salir» al
final del menú); los colores son los tokens `--m3-` oscuros de
`design_tokens.css` (`saimc.tui.theme`) y el avatar es la imagen de la web,
mostrada con `textual-image` (Sixel o Kitty si la terminal los soporta; si no,
medios bloques).
"""

from pathlib import Path
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static, Tree
from textual.widgets.tree import TreeNode
from textual_image.widget import Image

from saimc.tui import avatar, registry, theme
from saimc.tui.registry import Command, LogSource
from saimc.tui.runner import Finished, stream

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

HELP = "j/k: mover • enter: correr/plegar • x: cortar • c: limpiar • q: salir"


class Menu(Tree[str]):
    """Árbol con las marcas de gentle-ai: `▸` en el comando elegido y grupos resaltados."""

    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "

    def render_label(self, node: TreeNode[str], base_style: Style, style: Style) -> Text:
        label = super().render_label(node, base_style, style)
        if node.allow_expand:
            label.stylize(f"bold {GROUP}")
            return label
        marker = "▸ " if node is self.cursor_node else "  "
        return Text(marker) + label


class ConfirmScreen(ModalScreen[bool]):
    """Pregunta antes de correr algo que escribe o gasta."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "answer(True)", "Sí"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, command: Command) -> None:
        super().__init__()
        self._command = command

    def compose(self) -> ComposeResult:
        what = []
        if self._command.writes:
            what.append("escribe en la base o en archivos")
        if self._command.spends:
            what.append("gasta plata en una API")
        body = Text()
        body.append(f"{self._command.title}\n", style=f"bold {PRIMARY}")
        body.append(f"$ {self._command.display}\n\n", style=MUTED)
        body.append(f"Este comando {' y '.join(what)}.\n", style=WARN)
        body.append("¿Correrlo? ", style=TEXT)
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
        Binding("q", "quit", "Salir", show=False),
    ]

    def __init__(
        self,
        commands: tuple[Command, ...] = registry.COMMANDS,
        logs: tuple[LogSource, ...] = registry.LOGS,
        cwd: Path = registry.REPO_ROOT,
    ) -> None:
        super().__init__()
        self._commands = {c.id: c for c in commands}
        self._logs = {log.id: log for log in logs}
        self._groups = tuple(dict.fromkeys(c.group for c in commands))
        self._cwd = cwd

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
                    yield RichLog(id="output", wrap=True, markup=False)
            yield Static(HELP, id="help")

    def on_mount(self) -> None:
        tree = self.query_one(Menu)
        tree.show_root = False
        tree.guide_depth = 2
        for group in self._groups:
            node = tree.root.add(group, expand=True)
            for command in self._commands.values():
                if command.group == group:
                    marker = " ⚠" if command.needs_confirm else ""
                    node.add_leaf(command.title + marker, data=command.id)
        logs = tree.root.add("Logs en vivo", expand=True)
        for log in self._logs.values():
            logs.add_leaf(log.title, data=log.id)
        tree.root.add_leaf("Salir", data=QUIT)
        tree.focus()

    def on_resize(self) -> None:
        self.query_one("#header").display = self.size.height >= MIN_HEIGHT_FOR_HEADER

    def _lookup(self, key: str | None) -> Command | LogSource | None:
        if key is None:
            return None
        return self._commands.get(key) or self._logs.get(key)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        item = self._lookup(event.node.data)
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
        elif event.node.data == QUIT:
            detail.update(Text("Cerrar la terminal (también con q).", style=MUTED))

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        # Enter sobre un grupo lo pliega (lo hace el árbol); sobre un comando, lo corre.
        if event.node.data is not None:
            self.action_run()

    def action_cursor_down(self) -> None:
        self.query_one(Menu).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(Menu).action_cursor_up()

    def action_run(self) -> None:
        node = self.query_one(Menu).cursor_node
        if node is not None and node.data == QUIT:
            self.exit()
            return
        item = self._lookup(node.data if node else None)
        if isinstance(item, Command) and item.needs_confirm:
            self.push_screen(ConfirmScreen(item), lambda ok: self._run(item) if ok else None)
        elif item is not None:
            self._run(item)

    def _run(self, item: Command | LogSource) -> None:
        self.workers.cancel_group(self, "run")
        self.query_one(RichLog).write(Text(f"$ {item.display}", style=f"bold {PRIMARY}"))
        self.run_worker(self._execute(item.argv), group="run", exclusive=True)

    async def _execute(self, argv: tuple[str, ...]) -> None:
        log = self.query_one(RichLog)
        async for item in stream(argv, self._cwd):
            if isinstance(item, Finished):
                color = SUCCESS if item.returncode == 0 else ERROR
                log.write(Text(f"[salió con código {item.returncode}]", style=color))
            else:
                log.write(item)

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "run")
        self.query_one(RichLog).write(Text("[cortado]", style=WARN))

    def action_clear(self) -> None:
        self.query_one(RichLog).clear()
