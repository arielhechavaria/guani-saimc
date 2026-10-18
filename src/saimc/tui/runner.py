"""Ejecuta un comando y transmite su salida línea por línea."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finished:
    returncode: int


async def stream(argv: tuple[str, ...], cwd: Path) -> AsyncIterator[str | Finished]:
    """Rinde cada línea (stdout y stderr mezclados) y al final un `Finished`.

    Si la tarea se cancela, el proceso se mata: así `journalctl -f` no queda
    huérfano al cortar. Un binario inexistente no levanta excepción: termina
    con código 127, como el shell.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        yield f"{argv[0]}: no se encontró el programa"
        yield Finished(127)
        return
    stdout = process.stdout
    if stdout is None:  # no pasa con PIPE; deja a mypy tranquilo sin `assert`
        raise RuntimeError("el proceso no abrió stdout")
    try:
        async for raw in stdout:
            yield raw.decode(errors="replace").rstrip("\n")
        yield Finished(await process.wait())
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
