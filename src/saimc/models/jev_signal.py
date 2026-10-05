"""Lo que contestó Jev sobre un anuncio, guardado tal cual (slice A6b).

`docs/spec-slice-A6b.md`. Tabla derivada y de solo altas, como `llm_proposals`
y `ocr_transcriptions`: el código no tiene camino de escritura sobre una fila
existente ni de borrado, y no toca `raw_ingestions` (que sigue inmutable).

Una fila por anuncio y por huella de preguntas (`questions_hash`). Si cambia
una instrucción, una opción o el modelo, la huella cambia, se pregunta de nuevo
y se AGREGA una fila; la vieja queda. «La señal de un anuncio» es siempre la
fila de la huella vigente.

Solo se guarda una respuesta 200 con `answers`: un fallo no deja fila, así que
la corrida siguiente lo reintenta sola. No hay columnas ya interpretadas: la
lectura es una función pura sobre `answers` y `product_keys`
(`validation.jev_signal.read_signal`), y corregirla no obliga a pagar de nuevo.

`answers` no trae el texto del anuncio ni la clave: solo claves de opción y
probabilidades.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class JevSignal(Base):
    __tablename__ = "jev_signals"
    __table_args__ = (
        # Sin índice aparte por `ingestion_id`: el del UNIQUE empieza por esa
        # columna y ya sirve para buscar la señal de un anuncio.
        UniqueConstraint("ingestion_id", "questions_hash", name="uq_jev_signals_ingestion_hash"),
        CheckConstraint("source IN ('prefill', 'backfill')", name="ck_jev_signals_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("raw_ingestions.id", ondelete="RESTRICT", name="fk_jev_signals_ingestion_id"),
        nullable=False,
    )
    # El modelo que informa el gateway; si no lo informa, el que se pidió.
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # Huella del modelo, las instrucciones y las opciones (`jev_client.questions_hash`).
    questions_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    # El objeto `answers` de la respuesta, tal cual llegó.
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # `{"arroz": "<uuid>", ...}`: qué producto era cada opción al preguntar.
    product_keys: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # El que informa el gateway; si falta, estimado por tokens. Ocho decimales:
    # una llamada cuesta del orden de 0,00002 USD.
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    # `prefill`: se preguntó con el anuncio pendiente. `backfill`: ya decidido.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
