"""Entrada: `.venv/bin/python -m saimc.tui`."""

import sys


def main() -> None:
    try:
        from saimc.tui.app import GuaniTUI
    except ModuleNotFoundError as error:
        if error.name not in ("textual", "textual_image"):
            raise
        sys.exit("Falta Textual. Instalalo con: .venv/bin/pip install -e '.[tui]'")
    from saimc.tui.status import load

    GuaniTUI(status_loader=load).run()


if __name__ == "__main__":
    main()
