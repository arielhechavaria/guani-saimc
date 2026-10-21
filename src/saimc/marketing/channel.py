"""Publica en el canal de WhatsApp de Guani lo ya aprobado del día.

    python -m saimc.marketing.channel --slot datos              # en seco: dice qué mandaría
    python -m saimc.marketing.channel --slot datos --execute    # lo manda
    python -m saimc.marketing.channel --slot educativo --day 2026-10-20

`docs/decisions.md`, 2026-10-20. Dos temporizadores de systemd lo corren por
turno, hora de La Habana: 08:00 los «datos» y 10:00 el «educativo»
(`systemd/saimc-canal-datos.timer`, `systemd/saimc-canal-educativo.timer`).

**Solo manda lo que el operador ya aprobó** (`approved`, o `published` si ya
lo subió a Instagram, que implica aprobada): la publicación del lote de ese
día y ese turno. La aceptación sigue siendo manual (decisión cerrada 2); esto
solo automatiza el envío de algo aceptado. Lo que no se aprobó a tiempo no sale.

Sale por una **segunda sesión de WAHA** (`SAIMC_WAHA_CHANNEL_SESSION`), con un
número aparte del de la ingesta: si WhatsApp banea al que publica, la ingesta
sigue. Por eso se niega a publicar si las dos sesiones son la misma.

Cada lámina es un mensaje: el JPEG que rasterizó Aprobar, en base64, y el texto
del post (sin los hashtags de Instagram) solo en la primera. Entre lámina y
lámina hay una pausa corta, para que lleguen en orden y no parezca una ráfaga.

**Idempotente**: `marketing_posts.channel_slides_sent` cuenta las láminas que
ya salieron y se guarda después de cada una. Si WAHA falla a mitad, la
repetición sigue desde la siguiente; `channel_sent_at` se fija al completar, y
con eso la publicación ya no se vuelve a mandar.

Sin cola ni proceso permanente (decisión cerrada 3): lee, manda y termina.

Códigos de salida: 0 si se mandó, se mandaría (en seco) o ya estaba enviada;
1 si no hay nada aprobado, falta configuración, falta una imagen o WAHA
falló. El 1 deja la unidad en «failed» y se ve en `systemctl --failed`.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.marketing.daily import DATOS_SLOT, EDU_SLOT, default_marketing_dir, havana_today
from saimc.marketing.posts import effective_caption
from saimc.models.marketing_post import MarketingPost, PostStatus

#: Los turnos que se publican en el canal.
SLOTS: tuple[str, ...] = (DATOS_SLOT, EDU_SLOT)

#: Segundos entre lámina y lámina.
PAUSE_SECONDS = 1.5

# Un hashtag al final de la línea (y los que lo siguen): `#` pegado a una
# letra, precedido de espacio o del comienzo. `#3` no es hashtag.
_TRAILING_HASHTAGS = re.compile(r"(?:(?<!\S)#[^\W\d_]\w*\s*)+$")


class ChannelSendError(RuntimeError):
    """WAHA no aceptó el envío (error de red o status de error)."""


@dataclass(frozen=True)
class ChannelConfig:
    base_url: str | None
    api_key: str | None
    #: La sesión de WAHA que publica (número aparte).
    session: str
    #: El JID `…@newsletter` del canal. Vacío = apagado.
    chat_id: str
    timeout: float = 30.0
    #: La sesión de la ingesta, para negarse a publicar con ella.
    ingest_session: str | None = None


def channel_config() -> ChannelConfig:
    settings = get_settings()
    return ChannelConfig(
        base_url=settings.waha_base_url,
        api_key=settings.waha_api_key,
        session=settings.waha_channel_session,
        chat_id=settings.waha_channel_id,
        timeout=settings.waha_timeout,
        ingest_session=settings.waha_session,
    )


def build_channel_text(caption: str) -> str:
    """El pie de Instagram en su variante de WhatsApp: sin hashtags.

    En el canal no sirven para nada y ensucian el mensaje. Se quitan los que
    cierran una línea (el bloque final que agrega `caption.with_hashtags`, o
    los pegados al final de una frase); uno en medio de la oración es una
    palabra y se deja. Una línea que queda vacía se va, y no quedan más de una
    línea en blanco seguidas.
    """
    lineas = []
    for linea in caption.splitlines():
        limpia = _TRAILING_HASHTAGS.sub("", linea).rstrip()
        if not limpia and linea.strip():
            continue  # era solo hashtags
        lineas.append(limpia)
    texto = "\n".join(lineas)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def send_image(
    config: ChannelConfig,
    *,
    jpeg: bytes,
    filename: str,
    caption: str | None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Manda una imagen al canal por `POST /api/sendImage` de WAHA.

    Levanta `ChannelSendError` ante cualquier falla: a diferencia de los
    clientes de lectura (`whatsapp_health`), acá el llamador necesita saber que
    no salió para no contarla como enviada."""
    body: dict[str, object] = {
        "session": config.session,
        "chatId": config.chat_id,
        "file": {
            "mimetype": "image/jpeg",
            "filename": filename,
            "data": base64.b64encode(jpeg).decode(),
        },
    }
    if caption:
        body["caption"] = caption
    url = f"{(config.base_url or '').rstrip('/')}/api/sendImage"
    try:
        with httpx.Client(transport=transport, timeout=config.timeout) as client:
            response = client.post(url, json=body, headers={"X-Api-Key": config.api_key or ""})
    except httpx.HTTPError as error:
        raise ChannelSendError(f"WAHA no responde: {error}") from error
    if not response.is_success:
        raise ChannelSendError(f"WAHA respondió {response.status_code}: {response.text[:200]}")


@dataclass
class ChannelReport:
    day: date
    slot: str
    execute: bool
    exit_code: int
    message: str


def _config_problem(config: ChannelConfig) -> str | None:
    if not config.chat_id:
        return "SAIMC_WAHA_CHANNEL_ID vacío: la publicación en el canal está apagada."
    if not config.base_url or not config.api_key:
        return "Falta SAIMC_WAHA_BASE_URL o SAIMC_WAHA_API_KEY."
    if config.ingest_session is not None and config.session == config.ingest_session:
        return (
            f"SAIMC_WAHA_CHANNEL_SESSION es «{config.session}», la misma sesión de la "
            "ingesta: el canal se publica con un número aparte."
        )
    return None


def _approved_post(db: Session, day: date, slot: str) -> MarketingPost | None:
    """La publicación del lote de ese día y turno, si está aprobada.

    `UNIQUE (planned_for, batch_slot)`: hay a lo sumo una."""
    return db.scalars(
        select(MarketingPost).where(
            MarketingPost.planned_for == day,
            MarketingPost.batch_slot == slot,
            MarketingPost.status.in_((PostStatus.APPROVED, PostStatus.PUBLISHED)),
        )
    ).one_or_none()


def run_channel(
    db: Session,
    day: date,
    slot: str,
    images_dir: Path,
    *,
    execute: bool = False,
    config: ChannelConfig,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> ChannelReport:
    """Manda al canal la publicación aprobada de `day` y `slot`.

    `images_dir` es `<marketing_dir>/posts`, contra la que se resuelve
    `slide.image_path`. Sin `execute` no llama a WAHA ni escribe. Con
    `execute` guarda el progreso después de cada lámina."""

    def _fin(exit_code: int, message: str) -> ChannelReport:
        return ChannelReport(day, slot, execute, exit_code, message)

    problema = _config_problem(config)
    if problema is not None:
        return _fin(1, f"{problema} No se manda nada.")

    post = _approved_post(db, day, slot)
    if post is None:
        return _fin(1, f"No hay nada aprobado para «{slot}» del {day.isoformat()}: no se manda nada.")
    if post.channel_sent_at is not None:
        return _fin(0, f"Publicación ya enviada al canal ({post.channel_sent_at.isoformat()}): no se repite.")

    total = len(post.slides)
    pendientes = post.slides[post.channel_slides_sent :]
    desde = (
        f" (siguiendo desde la lámina {post.channel_slides_sent + 1} de {total})"
        if post.channel_slides_sent
        else ""
    )

    if not execute:
        faltan = [
            s.position
            for s in pendientes
            if s.image_path is None or not (images_dir / s.image_path).is_file()
        ]
        if faltan:
            return _fin(1, f"Faltan las imágenes de las láminas {faltan}: no se podría mandar.")
        return _fin(0, f"Se mandarían {len(pendientes)} láminas{desde} de la publicación {post.id}.")

    texto = build_channel_text(effective_caption(db, post)) or None
    for indice, slide in enumerate(pendientes):
        if indice:
            sleep(PAUSE_SECONDS)
        ruta = images_dir / slide.image_path if slide.image_path else None
        if ruta is None or not ruta.is_file():
            return _fin(
                1,
                f"Falta la imagen de la lámina {slide.position} ({ruta}): se mandaron "
                f"{post.channel_slides_sent} de {total}.",
            )
        try:
            send_image(
                config,
                jpeg=ruta.read_bytes(),
                filename=f"guani-{day.isoformat()}-{slot}-{slide.position}.jpg",
                caption=texto if slide.position == 1 else None,
                transport=transport,
            )
        except ChannelSendError as error:
            return _fin(
                1,
                f"Falló la lámina {slide.position}: {error}. Se mandaron "
                f"{post.channel_slides_sent} de {total}; la repetición sigue desde ahí.",
            )
        # Se guarda lámina por lámina: si el proceso muere acá, la próxima
        # corrida no repite lo que ya salió.
        post.channel_slides_sent += 1
        db.commit()

    post.channel_sent_at = now or datetime.now(UTC)
    db.commit()
    return _fin(0, f"Enviadas {len(pendientes)} láminas{desde} al canal.")


def format_report(report: ChannelReport) -> str:
    modo = "EJECUTADO" if report.execute else "EN SECO"
    return f"{modo} — canal de WhatsApp, «{report.slot}» del {report.day.isoformat()}\n  {report.message}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manda al canal de WhatsApp de Guani lo aprobado del día. En seco por defecto."
    )
    parser.add_argument("--slot", required=True, choices=SLOTS, help="el turno a publicar")
    parser.add_argument("--execute", action="store_true", help="manda de verdad")
    parser.add_argument(
        "--day", type=date.fromisoformat, default=None, help="AAAA-MM-DD; default: hoy en La Habana"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    day = args.day or havana_today()
    with session_scope() as session:
        report = run_channel(
            session,
            day,
            args.slot,
            default_marketing_dir() / "posts",
            execute=args.execute,
            config=channel_config(),
        )
    print(format_report(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
