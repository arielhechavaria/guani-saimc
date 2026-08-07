"""Procesa la bandeja de entrada.

    python -m saimc.ingestion

Pensado para correrse a mano al empezar la sesión de curaduría, antes de abrir
la cola. En Fase 0 no hay ni scheduler ni watcher: no hay carga que lo justifique.
"""

import argparse
from pathlib import Path

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.ingestion.inbox import ingest_inbox


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Ingesta de la bandeja de entrada")
    parser.add_argument("--inbox", type=Path, default=settings.inbox_dir)
    parser.add_argument("--store", type=Path, default=settings.raw_store_dir)
    args = parser.parse_args()

    with session_scope() as session:
        report = ingest_inbox(session, args.inbox, args.store)

    print(f"Ingeridos:  {len(report.ingested)}")
    print(f"Repetidos:  {len(report.duplicates)}")
    print(f"Salteados:  {len(report.skipped)}")

    for name in report.skipped:
        print(f"  salteado (extensión no reconocida): {name}")


if __name__ == "__main__":
    main()
