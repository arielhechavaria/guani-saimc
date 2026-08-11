#!/usr/bin/env python
"""Backfill puntual: quita teléfonos del texto ya almacenado.

Los teléfonos entraron a `raw_ingestions.original_text` antes de que la
curaduría redactara al escribir (`saimc.privacy`, docs/decisions.md
2026-08-11). Esto limpia lo que ya está guardado.

Es DML, no DDL: no es una migración de Alembic, mismo criterio que la limpieza
de productos duplicados del 2026-08-08.

Alcance: **toda ingesta**, imagen y texto. Para imágenes nunca hubo conflicto
(docs/decisions.md 2026-08-07: `original_text` es un campo nulable que la
curaduría completa; lo intocable es `file_path`, `content_hash`, `captured_at`,
`content_type`). Para texto sí lo hubo, y se resolvió a favor de la privacidad
por decisión explícita del operador: la columna se redacta, el archivo
preservado en `data/raw/` no se toca. La evidencia literal sigue intacta en
disco y `content_hash` la sigue describiendo — lo que cambia es la copia
indexable en base, que es la que entra a los backups y la que lee la aplicación.

Uso:
    python scripts/redact_existing.py            # dry-run, no escribe nada
    python scripts/redact_existing.py --apply    # escribe
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from saimc.db import session_scope
from saimc.models import ContentType, RawIngestion
from saimc.privacy import redact_personal_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escribe los cambios; sin esta bandera solo informa",
    )
    args = parser.parse_args()

    with session_scope() as session:
        ingestions = list(
            session.execute(select(RawIngestion)).scalars().all()
        )

        por_tipo: dict[str, int] = {ContentType.IMAGE: 0, ContentType.TEXT: 0}

        for ingestion in ingestions:
            limpio_texto = redact_personal_data(ingestion.original_text)
            limpio_ref = redact_personal_data(ingestion.source_reference)

            if (
                limpio_texto == ingestion.original_text
                and limpio_ref == ingestion.source_reference
            ):
                continue

            ingestion.original_text = limpio_texto
            ingestion.source_reference = limpio_ref
            por_tipo[ingestion.content_type] = por_tipo.get(ingestion.content_type, 0) + 1

        cambiadas = sum(por_tipo.values())
        print(f"Ingestas revisadas:            {len(ingestions)}")
        print(f"Con teléfono, a redactar:      {cambiadas}")
        print(f"  de imagen:                   {por_tipo[ContentType.IMAGE]}")
        print(f"  de texto:                    {por_tipo[ContentType.TEXT]}")

        if args.apply:
            session.commit()
            print("\nEscrito.")
        else:
            session.rollback()
            print("\nDry-run: no se escribió nada. Volvé a correr con --apply.")


if __name__ == "__main__":
    main()
