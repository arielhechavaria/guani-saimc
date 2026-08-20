"""Agrega al catálogo del foco los alias que el filtro de la cola necesita.

`docs/decisions.md`, 2026-08-20: desde que la cola esconde el texto sin
producto del foco, un alias faltante ya no cuesta un match perdido sino un
anuncio que el operador nunca ve. Medido contra producción, sin estos alias
el filtro escondía 5 de 22 anuncios de WhatsApp que el operador SÍ había
curado —«MUSLO DE POLLO PILGRIM'S», «Muslo con contramuslo Mar-Jac», «Saco de
azúcar 25kg»—, todos ellos con precio ya registrado contra Pollo o Azúcar
blanca. Con los alias, esa pérdida baja a cero.

Idempotente: solo agrega lo que falta, respeta el orden existente y no toca
ningún otro producto. Correrlo dos veces no cambia nada.

    python scripts/add_focus_aliases.py           # muestra qué haría
    python scripts/add_focus_aliases.py --apply   # lo escribe
"""

import sys

from sqlalchemy import select

from saimc.db import session_scope
from saimc.models import Product

# Términos vistos en anuncios reales de WhatsApp que el matcher no resolvía.
# Los cortes ("muslo", "contramuslo") tienen que estar EN el alias: el motor
# desconfía del match genérico cuando la línea nombra un corte, y solo lo
# acepta si el nombre que matcheó incluye ese mismo corte
# (`_match_product`, `_PRODUCT_DISQUALIFYING_MODIFIERS`).
ALIASES_POR_PRODUCTO = {
    "Pollo": ["muslo de pollo", "muslo con contramuslo", "contramuslo"],
    "Azúcar blanca": ["saco de azucar", "azucar magdalena", "azucar refino"],
}


def main() -> int:
    apply = "--apply" in sys.argv
    with session_scope() as db:
        for nombre, nuevos in ALIASES_POR_PRODUCTO.items():
            producto = db.execute(
                select(Product).where(Product.name == nombre)
            ).scalar_one_or_none()
            if producto is None:
                print(f"AUSENTE  {nombre!r}: no está en el catálogo, se omite")
                continue
            faltantes = [alias for alias in nuevos if alias not in producto.aliases]
            if not faltantes:
                print(f"OK       {nombre!r}: ya tiene los {len(nuevos)} alias")
                continue
            print(f"AGREGA   {nombre!r}: {faltantes}")
            if apply:
                # Reasignación, no `.append()`: la columna es JSON y SQLAlchemy
                # no detecta la mutación in-place de una lista.
                producto.aliases = [*producto.aliases, *faltantes]
        if not apply:
            print("\nEnsayo. Nada se escribió — repetir con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
