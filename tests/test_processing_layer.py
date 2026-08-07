"""Processing Layer — `provinces`, `products`, `prices_history`.

Slice de esquema puro (§7.2 del whitepaper): estructura, nullability y
restricciones (CHECK/FK/único). Ningún caso evalúa exactitud de extracción
ni H0 — eso corresponde a la capa de extracción, no a esta.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


class TestGuardianInfraestructura:
    def test_sqlite_enforces_foreign_keys(self, session: Session) -> None:
        """Sin este PRAGMA activo por conexión, SQLite ignora las FKs y todos
        los casos de `ON DELETE RESTRICT` de este archivo pasarían en falso.

        Usa la misma conexión que la fixture `session` (StaticPool: una sola
        conexión compartida) para detectar el gotcha documentado en el
        diseño: si el listener del PRAGMA se registra después de
        `create_all`, la conexión ya existe y el listener nunca corre.
        """
        resultado = session.execute(text("PRAGMA foreign_keys")).scalar()
        assert resultado == 1
