"""El token vigente de la API de Instagram: una sola fila.

Slice de seguimiento de Instagram (`docs/spec-slice-instagram.md`, «Decisión:
el token vive en la base»). El token de larga duración dura 60 días desde cada
renovación y cada renovación devuelve uno nuevo; un `.env` no se reescribe
solo, así que el lote lo renueva y lo guarda acá. `SAIMC_INSTAGRAM_ACCESS_TOKEN`
solo siembra esta fila la primera vez.

Fila única por construcción: el id es siempre 1 y un CHECK lo exige. Es un
secreto: no se muestra en ninguna página ni se imprime en ningún registro.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now

#: El único id posible.
CREDENTIALS_ID = 1


class InstagramCredentials(Base):
    __tablename__ = "instagram_credentials"
    __table_args__ = (CheckConstraint("id = 1", name="ck_instagram_credentials_single_row"),)

    # Sin autoincremento: no hay secuencia, el id es fijo.
    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=CREDENTIALS_ID
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Cuándo se obtuvo este token (la siembra o la última renovación). Meta no
    # renueva un token de menos de 24 h.
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
