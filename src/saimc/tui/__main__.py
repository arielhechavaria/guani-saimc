"""Entrada: `.venv/bin/python -m saimc.tui`."""

import sys


def main() -> None:
    try:
        from saimc.tui.app import GuaniTUI
    except ModuleNotFoundError as error:
        if error.name != "textual":
            raise
        sys.exit("Falta Textual. Instalalo con: .venv/bin/pip install -e '.[tui]'")
    GuaniTUI().run()


if __name__ == "__main__":
    main()
