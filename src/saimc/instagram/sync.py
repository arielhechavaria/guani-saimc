"""Lote de seguimiento de la cuenta de Instagram de Guani.

    python -m saimc.instagram.sync              # en seco: lee y cuenta, no escribe
    python -m saimc.instagram.sync --execute    # escribe

`docs/spec-slice-instagram.md`. Un temporizador de systemd lo corre cada 3 h
(`systemd/saimc-instagram.timer`): lee, escribe y termina, como el pre-cálculo.
Sin cola ni proceso permanente (decisión cerrada 3).

Cada corrida:

1. Toma el token de `instagram_credentials`. Si la tabla está vacía lo siembra
   desde `SAIMC_INSTAGRAM_ACCESS_TOKEN`; sin ninguno de los dos, avisa y sale
   con 0: es una capacidad sin usar, no una falla.
2. Si el token venció, sale con 1 y lo dice: un token vencido no se renueva,
   hay que repetir el alta a mano. Si tiene más de 24 h de vida y le quedan
   menos de 15 días, lo renueva y guarda el nuevo antes de seguir.
3. Agrega una lectura de la cuenta (seguidores), da de alta o refresca cada
   publicación y agrega una fila de métricas por publicación. Nunca modifica
   una lectura anterior: el historial es lo que se quiere conservar.

En seco (sin `--execute`) hace las lecturas, que son gratis y no cambian nada,
pero no escribe en la base ni renueva el token: renovar emite un token nuevo en
Meta y no tiene vuelta atrás.

Códigos de salida: 0 si la corrida terminó (aunque una publicación suelta no
haya dado métricas: queda en la salida); 1 si el token venció o fue revocado, o
si no se pudo leer la cuenta.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.instagram.client import (
    GraphClient,
    GraphError,
    MediaItem,
    metrics_for,
    refresh_token,
)
from saimc.models import (
    InstagramAccountSnapshot,
    InstagramCredentials,
    InstagramMedia,
    InstagramMediaMetrics,
    InstagramMediaType,
)
from saimc.models.instagram_credentials import CREDENTIALS_ID

#: Meta no renueva un token de menos de 24 h.
REFRESH_MIN_AGE = timedelta(hours=24)
#: Se renueva cuando le quedan menos de 15 días. Con el temporizador cada 3 h,
#: son más de cien corridas de margen antes de que venza.
REFRESH_WINDOW = timedelta(days=15)
#: Vida que se le supone al token sembrado desde el `.env`: el de larga duración
#: dura 60 días y el alta pide uno recién generado. Si tenía días encima, la
#: ventana de 15 días absorbe la diferencia.
SEED_LIFETIME = timedelta(days=60)
#: Las stories solo tienen métricas mientras están vivas.
STORY_LIFETIME = timedelta(hours=24)

_ALTA_A_MANO = (
    "Hay que repetir el alta a mano: generar un token de larga duración nuevo, "
    "ponerlo en SAIMC_INSTAGRAM_ACCESS_TOKEN y vaciar instagram_credentials "
    "(docs/comandos.md, «Seguimiento de Instagram»)."
)


@dataclass
class SyncReport:
    """Resumen de una corrida, para la salida del comando. No se persiste."""

    exit_code: int = 0
    fatal: str | None = None
    credential_note: str = ""
    followers: int | None = None
    media_seen: int = 0
    media_new: int = 0
    metrics_rows: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _as_utc(value: datetime) -> datetime:
    """SQLite devuelve las fechas sin zona; PostgreSQL, con zona. Se tratan igual."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def needs_refresh(*, refreshed_at: datetime, expires_at: datetime, now: datetime) -> bool:
    """Si toca renovar: el token tiene más de 24 h y le quedan menos de 15 días."""
    return now - refreshed_at > REFRESH_MIN_AGE and expires_at - now < REFRESH_WINDOW


def _fatal(session: Session, report: SyncReport, message: str) -> SyncReport:
    # Lo que la corrida haya agregado sin terminar no se guarda. Un token ya
    # renovado no se pierde: se confirma apenas llega.
    session.rollback()
    report.exit_code = 1
    report.fatal = message
    return report


def _fatal_from(session: Session, report: SyncReport, error: GraphError, what: str) -> SyncReport:
    if error.token_invalid:
        return _fatal(
            session,
            report,
            f"Meta rechazó el token de Instagram al {what} (vencido o revocado): {error.message} "
            + _ALTA_A_MANO,
        )
    return _fatal(session, report, f"No se pudo {what}: {error.message}")


def _story_expired(item: MediaItem, now: datetime) -> bool:
    return (
        item.media_type == InstagramMediaType.STORY
        and item.posted_at is not None
        and now - item.posted_at > STORY_LIFETIME
    )


def run_sync(
    session: Session,
    *,
    env_token: str | None,
    now: datetime,
    execute: bool,
    timeout: float = 15.0,
    transport: httpx.BaseTransport | None = None,
) -> SyncReport:
    """Una corrida sobre `session`. Abrir y confirmar la sesión es de quien llama
    (`main` con `session_scope`, o el test); la renovación del token se confirma
    acá mismo, apenas llega, para no perderla si algo falla después."""
    report = SyncReport()

    cred = session.get(InstagramCredentials, CREDENTIALS_ID)
    if cred is None:
        if not env_token:
            report.credential_note = (
                "Sin token de Instagram: falta SAIMC_INSTAGRAM_ACCESS_TOKEN en el .env "
                "(alta manual, docs/comandos.md). No se leyó nada."
            )
            return report
        cred = InstagramCredentials(
            id=CREDENTIALS_ID,
            access_token=env_token,
            refreshed_at=now,
            expires_at=now + SEED_LIFETIME,
        )
        vence = f"{cred.expires_at:%Y-%m-%d}"
        if execute:
            session.add(cred)
            report.credential_note = (
                f"Token sembrado desde SAIMC_INSTAGRAM_ACCESS_TOKEN; se supone recién generado "
                f"y que vence el {vence}."
            )
        else:
            report.credential_note = (
                f"Se sembraría el token de SAIMC_INSTAGRAM_ACCESS_TOKEN (vencería el {vence})."
            )

    expires_at = _as_utc(cred.expires_at)
    if expires_at <= now:
        return _fatal(
            session,
            report,
            f"El token de Instagram venció el {expires_at:%Y-%m-%d}: un token vencido no se puede "
            f"renovar. {_ALTA_A_MANO}",
        )
    if not report.credential_note:
        report.credential_note = f"Token vigente hasta el {expires_at:%Y-%m-%d}."

    token = cred.access_token
    if needs_refresh(refreshed_at=_as_utc(cred.refreshed_at), expires_at=expires_at, now=now):
        if not execute:
            report.credential_note = (
                f"El token vence el {expires_at:%Y-%m-%d}: con --execute se renovaría."
            )
        else:
            renewed = refresh_token(token, now=now, timeout=timeout, transport=transport)
            if isinstance(renewed, GraphError):
                if renewed.token_invalid:
                    return _fatal_from(session, report, renewed, "renovar el token")
                report.errors.append(
                    f"No se pudo renovar el token; se reintenta en la próxima corrida: {renewed.message}"
                )
            else:
                cred.access_token = renewed.access_token
                cred.expires_at = renewed.expires_at
                cred.refreshed_at = now
                session.commit()
                token = renewed.access_token
                report.credential_note = f"Token renovado: vence el {renewed.expires_at:%Y-%m-%d}."

    client = GraphClient(token, timeout=timeout, transport=transport)
    account = client.account()
    if isinstance(account, GraphError):
        return _fatal_from(session, report, account, "leer la cuenta")
    media = client.media()
    if isinstance(media, GraphError):
        return _fatal_from(session, report, media, "listar las publicaciones")
    stories = client.stories()
    if isinstance(stories, GraphError):
        if stories.token_invalid:
            return _fatal_from(session, report, stories, "listar las stories")
        report.errors.append(f"No se pudieron leer las stories: {stories.message}")
        stories = []

    report.followers = account.followers_count
    if execute:
        session.add(
            InstagramAccountSnapshot(
                captured_at=now,
                followers_count=account.followers_count,
                media_count=account.media_count,
            )
        )

    items = [*media, *stories]
    known = {
        row.ig_media_id: row
        for row in session.scalars(
            select(InstagramMedia).where(InstagramMedia.ig_media_id.in_([i.ig_media_id for i in items]))
        )
    }
    for item in items:
        report.media_seen += 1
        row = known.get(item.ig_media_id)
        if row is None:
            report.media_new += 1
            if execute:
                row = InstagramMedia(
                    ig_media_id=item.ig_media_id,
                    media_type=item.media_type.value,
                    permalink=item.permalink,
                    caption=item.caption,
                    posted_at=item.posted_at,
                    first_seen_at=now,
                    last_synced_at=now,
                )
                session.add(row)
                session.flush()
                known[item.ig_media_id] = row
        elif execute:
            # Meta deja editar el texto: se refresca. Un dato que esta vez no
            # vino no borra el que ya estaba.
            row.permalink = item.permalink or row.permalink
            row.caption = item.caption or row.caption
            row.last_synced_at = now

        wanted = metrics_for(item)
        if wanted is None:
            report.skipped.append(f"{item.ig_media_id}: carrusel, Meta no da métricas")
            continue
        if _story_expired(item, now):
            report.skipped.append(f"{item.ig_media_id}: story de más de 24 h, ya sin métricas")
            continue
        metrics = client.insights(item.ig_media_id, wanted)
        if isinstance(metrics, GraphError):
            if metrics.token_invalid:
                return _fatal_from(session, report, metrics, "leer las métricas")
            report.errors.append(f"{item.ig_media_id}: sin métricas esta vez ({metrics.message})")
            continue
        report.metrics_rows += 1
        if execute and row is not None:
            session.add(InstagramMediaMetrics(media_id=row.id, captured_at=now, **asdict(metrics)))

    return report


def format_report(report: SyncReport, *, execute: bool) -> str:
    """La salida del comando. Nunca incluye el token."""
    lines = [report.credential_note] if report.credential_note else []
    if report.fatal:
        lines.append(f"ERROR: {report.fatal}")
        return "\n".join(lines)
    if report.followers is not None:
        verbo = "Guardadas" if execute else "Se guardarían"
        lines += [
            f"Seguidores: {report.followers}",
            f"Publicaciones leídas: {report.media_seen} ({report.media_new} nuevas)",
            f"{verbo}: {report.metrics_rows} lecturas de métricas",
        ]
    lines += [f"  salteada: {s}" for s in report.skipped]
    lines += [f"  aviso: {e}" for e in report.errors]
    if not execute and report.followers is not None:
        lines.append("En seco: no se escribió nada ni se renovó el token. Para escribir, --execute.")
    return "\n".join(lines)


def _default_transport() -> httpx.BaseTransport | None:
    """El transporte real (`None`: lo arma `httpx`). Seam para los tests de `main`."""
    return None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lee la cuenta de Instagram de Guani y guarda sus métricas. En seco por defecto."
    )
    parser.add_argument(
        "--execute", action="store_true", help="escribe en la base y renueva el token si toca"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    now = datetime.now(UTC)
    with session_scope() as session:
        report = run_sync(
            session,
            env_token=settings.instagram_access_token,
            now=now,
            execute=args.execute,
            timeout=settings.instagram_timeout,
            transport=_default_transport(),
        )
    print(format_report(report, execute=args.execute))
    if report.fatal:
        # También a stderr, para que `journalctl -p err` lo encuentre.
        print(report.fatal, file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
