"""Canal de origen de una ingesta, y la provincia fija que ese canal impone.

Las dos reglas vivían dentro de `web/routers/curation.py` como funciones
privadas, porque la cola de curaduría era su único consumidor. Dejaron de
serlo: la medición de H0 (`extraction/measure.py`) necesita aplicar exactamente
la misma regla para evaluar el extractor que corre de verdad, y la capa de
extracción no puede importar de un router web sin invertir la dependencia.

Se movieron sin cambiarles el comportamiento. `channel_province` devuelve el
objeto `Province` en vez del id como string: el string era una conveniencia
del formulario, no parte de la regla.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import Channel, Province, RawIngestion, SourceType


def resolve_origin(db: Session, ingestion: RawIngestion) -> Channel | None:
    """Canal de origen de esta ingesta, o `None` si no se puede saber con certeza.

    Desde que `idx_channels_name` dejó de ser único (migración 0014,
    docs/decisions.md 2026-08-19), dos filas de `channels` pueden compartir
    nombre — el caso real fue "Ventas Mayoristas", canal y grupo a la vez.
    `channel_id` (`raw_ingestions`, migración 0015) es la identidad real
    cuando está presente; a falta de él, el nombre sigue siendo el único
    rastro que existe para las filas creadas antes de esa migración, y ahí
    puede resultar en 0, 1 o 2+ coincidencias.

    `.limit(2)` + `.scalars().all()` en vez de `scalar_one_or_none()`: esto
    último *lanza* `MultipleResultsFound` en el caso de 2+, que es
    exactamente el crash que esta función existe para eliminar. También se
    descartó `.first()` / `.limit(1)`, que adivinarían en silencio cuál de
    las dos filas es la correcta — el operador rechazó cualquier
    desempate automático (design.md, D4). `limit(2)` alcanza para distinguir
    "una" de "dos o más" sin traer el resto.

    Un nombre ambiguo resuelve en `None`, igual que un nombre sin ninguna
    coincidencia: los consumidores tratan los dos casos igual, y adivinar cuál
    de las dos filas es la correcta está descartado.
    """
    if ingestion.channel_id is not None:
        return db.get(Channel, ingestion.channel_id)
    if not ingestion.source_reference:
        return None
    coincidencias = list(
        db.execute(
            select(Channel).where(Channel.name == ingestion.source_reference).limit(2)
        ).scalars().all()
    )
    if len(coincidencias) == 1:
        return coincidencias[0]
    return None


def channel_province(db: Session, ingestion: RawIngestion) -> Province | None:
    """Provincia fija del canal automatizado que originó este anuncio.

    "El canal siempre manda" (docs/decisions.md, 2026-08-17): si el operador
    cargó la provincia de un canal en /catalog, esa gana sobre lo que
    `_match_province` adivine del texto del mensaje — el operador la conoce
    de antemano, y un mensaje puede no mencionarla o mencionar otra por
    error.

    Solo aplica a `SourceType.WHATSAPP_CHANNEL`: ahí `source_reference` es
    el nombre exacto del canal, escrito por código (el webhook y el
    backfill, nunca el operador a mano) — para el resto de las fuentes
    (bandeja, pegado) el campo es texto libre y no hay canal que resolver
    con certeza. La resolución en sí pasa por `resolve_origin` (design.md,
    D4): un origen ambiguo resuelve en `None`, igual que una fuente sin
    canal registrado, nunca en un error.
    """
    if ingestion.source_type != SourceType.WHATSAPP_CHANNEL or not ingestion.source_reference:
        return None
    channel = resolve_origin(db, ingestion)
    if channel is None or channel.province_id is None:
        return None
    return db.get(Province, channel.province_id)
