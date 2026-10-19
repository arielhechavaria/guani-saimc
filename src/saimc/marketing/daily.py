"""Lote diario de marketing: crea los borradores del día en la fábrica.

    python -m saimc.marketing.daily                    # en seco: dice qué crearía
    python -m saimc.marketing.daily --execute          # crea los borradores y purga lo vencido
    python -m saimc.marketing.daily --day 2026-10-19   # un día puntual (default: hoy en La Habana)

Un temporizador de systemd lo corre a las 06:00 de La Habana
(`systemd/saimc-marketing.timer`): lee, escribe y termina, igual que el
pre-cálculo. Sin cola ni proceso permanente (decisión cerrada 3). **No publica
nada** y, desde F2 (`docs/spec-slice-fabrica-publicaciones.md`), tampoco
rasteriza: deja borradores en `/marketing` («Hoy») para que el operador los
revise, apruebe y publique desde el teléfono.

Qué toca cada día (`cards_for_day`): el carrusel «Datos del día» (portada →
una «Dato del día» por producto con datos, hasta cuatro → cierre, un solo
borrador) y el carrusel «Educativo» (una lección corta de
`saimc.marketing.lessons`, texto fijo, no depende de datos) todos los días; los
lunes, además, «Cómo se movió» (el ritmo semanal); los jueves, «De dónde sale
este precio» (el método). Las demás piezas son branding fijo.

**Idempotente por día y tipo**: si ya hay un borrador del lote para ese día y
ese tipo —en el estado que sea, descartado incluido— no se crea otro. Lo
garantiza `UNIQUE (planned_for, batch_slot)` en la base, también si dos
corridas se pisan.

Las cifras son siempre las del **día anterior** hacia atrás: el borrador lleva
`data_through = día − 1`, y la vista previa y la aprobación ponen ahí el tope.
El producto y la provincia rotan con el calendario (`candidate_order`); si el
que toca no tiene datos, se prueba el siguiente, y si ninguno tiene, el tipo se
salta y la salida lo dice. Nunca se crea un borrador vacío (ni una portada sola).

Con `--execute` corre además la retención: borradores y descartadas de más de
30 días se borran con su imagen; las publicadas se guardan siempre. Las
carpetas `var/marketing/AAAA-MM-DD/` del lote anterior se purgan con la regla de
siempre.

Códigos de salida: 0 si al menos un tipo quedó creado, previsto o ya existía; 1
si ninguno tenía datos.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.marketing.context import BY_SLUG, DATOS_MAX_PRODUCTS, CardType, build_context
from saimc.marketing.lessons import lesson_for_day
from saimc.marketing.posts import (
    RETENTION_DAYS,
    context_has_data,
    create_carousel_draft,
    create_draft,
    create_lesson_draft,
    purge_expired,
)
from saimc.models.marketing_post import MarketingPost, PostOrigin
from saimc.progress import PHASE0_PROVINCES, pilot_product_names

#: El turno educativo diario: un carrusel con la lección del día
#: (`lessons.lesson_for_day`). Sin producto ni provincia: siempre se crea.
EDU_SLOT = "educativo"
EDU_LABEL = "Educativo"

#: El turno de datos diario: un carrusel con la portada del día, una
#: «Dato del día» por producto con datos (`DATOS_MAX_PRODUCTS` como mucho) y el
#: cierre. Reemplaza, desde el 2026-10-19, a la tarjeta suelta «dato-del-dia»
#: y al carrusel de los miércoles.
DATOS_SLOT = "datos"
DATOS_LABEL = "Datos del día"
DATOS_COVER = "datos-portada"
DATOS_CLOSING = "carrusel-cierre"

#: Turnos viejos del lote que cubren el de datos: si el de este día ya existe
#: (la corrida de las 06:00 anterior al cambio), el lote no crea «datos» encima.
#: Y siguen mostrándose en «Hoy», la semana y el historial.
LEGACY_DATOS_SLOTS: tuple[str, ...] = ("dato-del-dia", "carrusel")

#: Las que salen todos los días.
DAILY_CARDS: tuple[str, ...] = (DATOS_SLOT, EDU_SLOT)

#: El carrusel «Carrusel» (portada fija → cómo leer → dato → cierre): ya no es
#: un turno del lote, pero el editor lo sigue ofreciendo a mano y las
#: publicaciones viejas del lote lo llevan como `batch_slot`.
CAROUSEL_SLOT = "carrusel"
CAROUSEL_LABEL = "Carrusel"
CAROUSEL_SLIDES: tuple[str, ...] = (
    "carrusel-portada",
    "carrusel-consejo",
    "dato-del-dia",
    "carrusel-cierre",
)

#: Las que suman ciertos días. Clave: `date.weekday()` (0 = lunes).
WEEKLY_CARDS: dict[int, tuple[str, ...]] = {
    0: ("como-se-movio",),
    3: ("de-donde-sale",),
}

_HAVANA = "America/Havana"


def cards_for_day(day: date) -> tuple[str, ...]:
    """Los slugs que toca generar el `day` (el día en que corre el lote)."""
    return DAILY_CARDS + WEEKLY_CARDS.get(day.weekday(), ())


def slot_label(slot: str) -> str:
    """Cómo se nombra un turno del calendario en «Hoy»."""
    if slot == EDU_SLOT:
        return EDU_LABEL
    if slot == DATOS_SLOT:
        return DATOS_LABEL
    return CAROUSEL_LABEL if slot == CAROUSEL_SLOT else BY_SLUG[slot].label


def candidate_order[T](items: Sequence[T], day: date) -> list[T]:
    """`items` rotados según el día: el primero cambia cada día y recorre todos.

    Determinista: la misma fecha da siempre el mismo orden, así que volver a
    correr el lote no cambia de producto. Si el primero no tiene datos, el
    llamador sigue con el segundo, y así."""
    if not items:
        return []
    inicio = day.toordinal() % len(items)
    return list(items[inicio:]) + list(items[:inicio])


def datos_products(db: Session, day: date) -> list[str]:
    """Los productos del piloto con datos hasta el día anterior a `day`, a
    partir del que toca por la rotación, hasta `DATOS_MAX_PRODUCTS`. Los que no
    tienen datos se saltan."""
    kind = BY_SLUG["dato-del-dia"]
    elegidos: list[str] = []
    for producto in candidate_order(pilot_product_names(db), day):
        contexto = build_context(
            kind, db, product=producto, province=None, hoy=day - timedelta(days=1)
        )
        if context_has_data(kind.slug, contexto):
            elegidos.append(producto)
            if len(elegidos) == DATOS_MAX_PRODUCTS:
                break
    return elegidos


def create_datos_draft(
    db: Session,
    day: date,
    products: Sequence[str],
    *,
    data_through: date,
    origin: PostOrigin = PostOrigin.OPERATOR,
    batch_slot: str | None = None,
) -> MarketingPost:
    """El carrusel de datos: portada → una «Dato del día» por producto de
    `products` → cierre. Sin `flush`, como `create_carousel_draft`."""
    slugs = (DATOS_COVER, *["dato-del-dia"] * len(products), DATOS_CLOSING)
    return create_carousel_draft(
        db, card_slugs=slugs, planned_for=day, product=None, province=None,
        products=(None, *products, None), data_through=data_through,
        origin=origin, batch_slot=batch_slot,
    )


def purge_old(out_root: Path, today: date, keep_days: int = RETENTION_DAYS) -> list[Path]:
    """Borra las carpetas `AAAA-MM-DD` de más de `keep_days` días.

    Solo toca directorios cuyo nombre es una fecha: cualquier otra cosa que el
    operador deje en la carpeta (notas, un archivo suelto) no se mira."""
    if not out_root.is_dir():
        return []
    borrados: list[Path] = []
    for hijo in sorted(out_root.iterdir()):
        if not hijo.is_dir():
            continue
        try:
            dia = date.fromisoformat(hijo.name)
        except ValueError:
            continue
        if (today - dia).days > keep_days:
            shutil.rmtree(hijo)
            borrados.append(hijo)
    return borrados


@dataclass(frozen=True)
class CardResult:
    slug: str
    #: «creada», «prevista» (en seco), «ya existía» o «sin datos».
    estado: str
    product: str | None = None
    province: str | None = None


@dataclass
class DailyReport:
    day: date
    data_through: date
    cards: list[CardResult] = field(default_factory=list)
    #: Carpetas `AAAA-MM-DD` del lote anterior que se borraron.
    purgados: list[Path] = field(default_factory=list)
    #: Publicaciones vencidas que se borraron (`posts.purge_expired`).
    posts_purgados: list[uuid.UUID] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        # El educativo no depende de datos: siempre sale, así que no cuenta
        # (si no, el 1 de «ninguna tarjeta con datos» nunca saltaría).
        con_datos = (c for c in self.cards if c.slug != EDU_SLOT)
        return 0 if any(c.estado != "sin datos" for c in con_datos) else 1


def to_havana(moment: datetime) -> datetime:
    """`moment` en la hora de Cuba (uno sin huso se toma como UTC, que es lo
    que guarda la base). Sin base de husos instalada, queda en UTC (mismo
    criterio de «nunca bloquear» que `exchange_rate.service`)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        return moment.astimezone(ZoneInfo(_HAVANA))
    except ZoneInfoNotFoundError:
        return moment


def havana_today(now: datetime | None = None) -> date:
    """El día en Cuba."""
    return to_havana(now or datetime.now(UTC)).date()


def default_marketing_dir() -> Path:
    return get_settings().marketing_dir


def _pick(kind: CardType, db: Session, day: date) -> tuple[str | None, str | None] | None:
    """Producto y provincia de la primera combinación con datos."""
    productos = candidate_order(pilot_product_names(db), day) if kind.needs_product else [None]
    provincias = candidate_order(PHASE0_PROVINCES, day) if kind.needs_province else [None]
    for producto in productos:
        for provincia in provincias:
            contexto = build_context(
                kind, db, product=producto, province=provincia, hoy=day - timedelta(days=1)
            )
            if context_has_data(kind.slug, contexto):
                return producto, provincia
    return None


def _exists(db: Session, day: date, slot: str) -> bool:
    slots = (slot, *LEGACY_DATOS_SLOTS) if slot == DATOS_SLOT else (slot,)
    return (
        db.scalar(
            select(MarketingPost.id).where(
                MarketingPost.planned_for == day, MarketingPost.batch_slot.in_(slots)
            )
        )
        is not None
    )


def run_daily(
    db: Session, day: date, marketing_dir: Path, *, execute: bool = False
) -> DailyReport:
    """Crea los borradores que tocan el `day`. Sin `execute` no escribe ni
    purga: solo dice qué haría. Con `execute` deja todo commiteado."""
    report = DailyReport(day=day, data_through=day - timedelta(days=1))

    for slug in cards_for_day(day):
        if _exists(db, day, slug):
            report.cards.append(CardResult(slug, "ya existía"))
            continue
        elegida: tuple[str | None, str | None] | None
        if slug == DATOS_SLOT:
            productos = datos_products(db, day)
            elegida = (", ".join(productos), None) if productos else None
        elif slug == EDU_SLOT:
            elegida = (None, None)
        else:
            elegida = _pick(BY_SLUG[slug], db, day)
        if elegida is None:
            report.cards.append(CardResult(slug, "sin datos"))
            continue
        producto, provincia = elegida
        if not execute:
            report.cards.append(CardResult(slug, "prevista", producto, provincia))
            continue
        try:
            # Un savepoint por borrador: si otra corrida lo creó entre la
            # consulta y el alta, la restricción única salta acá y solo se
            # deshace este.
            with db.begin_nested():
                if slug == EDU_SLOT:
                    create_lesson_draft(
                        db, lesson=lesson_for_day(day), planned_for=day,
                        origin=PostOrigin.BATCH, batch_slot=slug,
                    )
                elif slug == DATOS_SLOT:
                    create_datos_draft(
                        db, day, productos, data_through=report.data_through,
                        origin=PostOrigin.BATCH, batch_slot=slug,
                    )
                else:
                    create_draft(
                        db, card_slug=slug, planned_for=day, product=producto,
                        province=provincia, data_through=report.data_through,
                        origin=PostOrigin.BATCH, batch_slot=slug,
                    )
        except IntegrityError:
            # Solo la carrera con otra corrida es «ya existía»; cualquier otra
            # restricción que salte es un error de verdad y no se tapa.
            if not _exists(db, day, slug):
                raise
            report.cards.append(CardResult(slug, "ya existía"))
            continue
        report.cards.append(CardResult(slug, "creada", producto, provincia))

    if execute:
        report.posts_purgados = purge_expired(db, marketing_dir / "posts", day)
        report.purgados = purge_old(marketing_dir, day)
        db.commit()
    return report


def format_report(report: DailyReport, *, execute: bool) -> str:
    lineas = [
        f"{'EJECUTADO' if execute else 'EN SECO'} — borradores del {report.day.isoformat()} "
        f"(cifras hasta el {report.data_through.isoformat()})"
    ]
    for c in report.cards:
        detalle = " · ".join(x for x in (c.product, c.province) if x)
        lineas.append(f"  {c.slug}: {c.estado}{f' ({detalle})' if detalle else ''}")
    if report.posts_purgados:
        lineas.append(f"  publicaciones vencidas borradas: {len(report.posts_purgados)}")
    if report.purgados:
        lineas.append(f"  carpetas purgadas: {', '.join(p.name for p in report.purgados)}")
    return "\n".join(lineas)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crea los borradores de marketing del día. En seco por defecto; no publica."
    )
    parser.add_argument(
        "--execute", action="store_true", help="crea los borradores y purga lo vencido"
    )
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="AAAA-MM-DD; default: hoy en La Habana",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    day = args.day or havana_today()
    with session_scope() as session:
        report = run_daily(session, day, default_marketing_dir(), execute=args.execute)
    print(format_report(report, execute=args.execute))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
