"""App Textual: árbol de comandos y logs a la izquierda, salida a la derecha."""

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, RichLog, Static, Tree

from saimc.tui import registry
from saimc.tui.registry import Command, LogSource
from saimc.tui.runner import Finished, stream


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
        yield Static(
            f"[b]{self._command.title}[/b]\n"
            f"{self._command.display}\n\n"
            f"Este comando {' y '.join(what)}.\n¿Correrlo? [b]y[/b]/[b]n[/b]",
            id="confirm",
        )

    def action_answer(self, accepted: bool) -> None:
        self.dismiss(accepted)


class GuaniTUI(App[None]):
    TITLE = "Guani"
    CSS = """
    #menu { width: 42; border-right: solid $primary; }
    #detail { height: auto; max-height: 8; padding: 0 1; border-bottom: solid $primary; }
    #output { padding: 0 1; }
    ConfirmScreen { align: center middle; }
    #confirm {
        width: 70; height: auto; border: thick $warning; padding: 1 2; background: $surface;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r,enter", "run", "Correr"),
        Binding("x", "stop", "Cortar"),
        Binding("c", "clear", "Limpiar"),
        Binding("q", "quit", "Salir"),
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
        yield Header()
        with Horizontal():
            yield Tree("Guani", id="menu")
            with Vertical():
                yield Static("Elegí un comando.", id="detail")
                yield RichLog(id="output", wrap=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.show_root = False
        for group in self._groups:
            node = tree.root.add(group, expand=True)
            for command in self._commands.values():
                if command.group == group:
                    marker = " ⚠" if command.needs_confirm else ""
                    node.add_leaf(command.title + marker, data=command.id)
        logs = tree.root.add("Logs en vivo", expand=True)
        for log in self._logs.values():
            logs.add_leaf(log.title, data=log.id)
        tree.focus()

    def _lookup(self, key: str | None) -> Command | LogSource | None:
        if key is None:
            return None
        return self._commands.get(key) or self._logs.get(key)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        item = self._lookup(event.node.data)
        detail = self.query_one("#detail", Static)
        if isinstance(item, Command):
            flags = [name for name, on in (("escribe", item.writes), ("gasta", item.spends)) if on]
            tag = f"  [{', '.join(flags)}]" if flags else ""
            detail.update(f"{item.title}{tag}\n{item.about}\n$ {item.display}")
        elif isinstance(item, LogSource):
            detail.update(f"Log en vivo: {item.title}\n$ {item.display}\n(x para cortar)")

    def action_run(self) -> None:
        node = self.query_one(Tree).cursor_node
        item = self._lookup(node.data if node else None)
        if isinstance(item, Command) and item.needs_confirm:
            self.push_screen(ConfirmScreen(item), lambda ok: self._run(item) if ok else None)
        elif item is not None:
            self._run(item)

    def _run(self, item: Command | LogSource) -> None:
        self.workers.cancel_group(self, "run")
        self.query_one(RichLog).write(f"$ {item.display}")
        self.run_worker(self._execute(item.argv), group="run", exclusive=True)

    async def _execute(self, argv: tuple[str, ...]) -> None:
        log = self.query_one(RichLog)
        async for item in stream(argv, self._cwd):
            if isinstance(item, Finished):
                log.write(f"[salió con código {item.returncode}]")
            else:
                log.write(item)

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "run")
        self.query_one(RichLog).write("[cortado]")

    def action_clear(self) -> None:
        self.query_one(RichLog).clear()
