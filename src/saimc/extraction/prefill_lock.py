"""Candado entre corridas reales del pre-cálculo (2026-10-09).

Dos `prefill --execute` a la vez arman la misma lista de pendientes y la
recorren en el mismo orden: cada anuncio se paga dos veces. `Type=oneshot` solo
impide que systemd arranque dos veces su propia unidad; no ve una corrida
lanzada con `python -m ...` desde una terminal. Este candado sí.

Es un `flock` sobre un archivo bajo `data/`. El candado es del archivo abierto,
no del archivo en disco: cuando el proceso termina —bien, con una excepción o
de un `kill -9`— el núcleo cierra el archivo y lo suelta. Por eso no hay nada
que limpiar a mano y el archivo nunca se borra: borrarlo dejaría a dos corridas
tomando candados sobre archivos distintos con el mismo nombre.

No es una cola (decisión cerrada 3): nadie espera turno. La corrida que
encuentra el candado tomado se va enseguida.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def run_lock(path: Path) -> Iterator[bool]:
    """Intenta tomar el candado sin esperar. Da `True` si lo tomó.

    Con `False` hay otra corrida viva y quien llama no debe gastar nada. Al
    salir del `with` el candado se suelta, pase lo que pase adentro.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Modo "a": crea el archivo si falta y no trunca el de otra corrida.
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
