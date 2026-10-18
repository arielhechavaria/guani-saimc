"""Lote diario de tarjetas de marketing.

    python -m saimc.marketing.daily                    # en seco: dice qué armaría
    python -m saimc.marketing.daily --execute          # rasteriza y guarda los PNG
    python -m saimc.marketing.daily --day 2026-10-19   # un día puntual (default: hoy en La Habana)

Un temporizador de systemd lo corre a las 06:00 de La Habana
(`systemd/saimc-marketing.timer`): lee, escribe y termina, igual que el
pre-cálculo y el lote de Instagram. Sin cola ni proceso permanente (decisión
cerrada 3). **No publica nada**: deja los PNG en `var/marketing/AAAA-MM-DD/`
para que el operador los tome, y borra las carpetas de más de 30 días.

Qué tarjetas toca cada día (`cards_for_day`): «Dato del día» todos los días; los
lunes, además, «Cómo se movió» (el ritmo semanal); los jueves, «De dónde sale
este precio» (el método). Las demás piezas del canvas son branding fijo y no
cambian con los datos: no tiene sentido regenerarlas cada mañana.

Las cifras son siempre las del **día anterior** hacia atrás
(`build_context(hoy=día − 1)`), con tope: a las 06:00 la curaduría del día
todavía no empezó y lo que se cargue después no entra en la tarjeta. El producto
y la provincia rotan con el calendario (`candidate_order`); si el que toca no
tiene datos, se prueba el siguiente, y si ninguno tiene, la tarjeta se salta y
la salida lo dice. Nunca se publica una tarjeta vacía.

Códigos de salida: 0 si salió al menos una tarjeta (o, en seco, si hay al menos
una que armar); 1 si no hay Chromium o si ninguna tarjeta tenía datos.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jinja2
from sqlalchemy.orm import Session

import saimc.web
from saimc.db import session_scope
from saimc.marketing.context import BY_SLUG, SITE_URL_PLACEHOLDER, CardType, build_context
from saimc.marketing.fonts import font_face_css
from saimc.marketing.render import RendererUnavailable, render_png
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.product_nav import product_slug

#: Días que se conservan las carpetas diarias. Una carpeta de exactamente
#: este tiempo todavía se queda; la de un día más, se va.
RETENTION_DAYS = 30

DEFAULT_OUT_DIR = Path("var/marketing")

#: La que sale todos los días.
DAILY_CARDS: tuple[str, ...] = ("dato-del-dia",)

#: Las que suman ciertos días. Clave: `date.weekday()` (0 = lunes).
WEEKLY_CARDS: dict[int, tuple[str, ...]] = {
    0: ("como-se-movio",),
    3: ("de-donde-sale",),
}

_TEMPLATES = Path(saimc.web.__file__).parent / "templates"

_HAVANA = "America/Havana"


def cards_for_day(day: date) -> tuple[str, ...]:
    """Los slugs que toca generar el `day` (el día en que corre el lote)."""
    return DAILY_CARDS + WEEKLY_CARDS.get(day.weekday(), ())


def candidate_order[T](items: Sequence[T], day: date) -> list[T]:
    """`items` rotados según el día: el primero cambia cada día y recorre todos.

    Determinista: la misma fecha da siempre el mismo orden, así que volver a
    correr el lote no cambia de producto. Si el primero no tiene datos, el
    llamador sigue con el segundo, y así."""
    if not items:
        return []
    inicio = day.toordinal() % len(items)
    return list(items[inicio:]) + list(items[:inicio])


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
    #: «escrita», «prevista» (en seco) o «sin datos».
    estado: str
    product: str | None = None
    province: str | None = None
    path: Path | None = None


@dataclass
class DailyReport:
    day: date
    data_through: date
    cards: list[CardResult] = field(default_factory=list)
    purgados: list[Path] = field(default_factory=list)
    renderer_error: str | None = None

    @property
    def exit_code(self) -> int:
        if self.renderer_error is not None:
            return 1
        return 0 if any(c.estado != "sin datos" for c in self.cards) else 1


def havana_today(now: datetime | None = None) -> date:
    """El día en Cuba. Sin base de husos instalada, cae al día UTC (mismo
    criterio de «nunca bloquear» que `exchange_rate.service`)."""
    ahora = now or datetime.now(UTC)
    try:
        return ahora.astimezone(ZoneInfo(_HAVANA)).date()
    except ZoneInfoNotFoundError:
        return ahora.date()


def _has_data(slug: str, context: dict) -> bool:
    # «Dato del día» arma siempre la tarjeta, con una fila «sin datos» por
    # provincia; las otras devuelven `None` cuando no hay con qué armarla.
    if slug == "dato-del-dia":
        return any(row.has_data for row in context["card"].rows)
    return True


def _pick(
    kind: CardType, db: Session, day: date
) -> tuple[str | None, str | None, dict] | None:
    """Producto, provincia y contexto de la primera combinación con datos."""
    productos = candidate_order(pilot_product_names(db), day) if kind.needs_product else [None]
    provincias = candidate_order(PHASE0_PROVINCES, day) if kind.needs_province else [None]
    for producto in productos:
        for provincia in provincias:
            contexto = build_context(
                kind, db, product=producto, province=provincia, hoy=day - timedelta(days=1)
            )
            if contexto is not None and _has_data(kind.slug, contexto):
                return producto, provincia, contexto
    return None


def _html(kind: CardType, context: dict) -> str:
    # Entorno propio y no `app.state.templates`: este lote no levanta la app web.
    # Mismo cargador y mismo autoescape que Starlette, así que la plantilla sale igual.
    entorno = jinja2.Environment(loader=jinja2.FileSystemLoader(_TEMPLATES), autoescape=True)
    plantilla = entorno.get_template(kind.template)
    return plantilla.render(site_url=SITE_URL_PLACEHOLDER, font_css=font_face_css(), **context)


def _file_name(kind: CardType, product: str | None, province: str | None) -> str:
    partes = [kind.slug]
    if product:
        partes.append(product_slug(product))
    if province:
        partes.append(product_slug(province))
    return "-".join(partes) + ".png"


def run_daily(
    db: Session,
    day: date,
    out_root: Path,
    *,
    render: Callable[..., bytes] = render_png,
    execute: bool = False,
) -> DailyReport:
    """Arma las tarjetas que tocan el `day`. Sin `execute` no escribe, no
    levanta Chromium ni purga: solo dice qué haría."""
    report = DailyReport(day=day, data_through=day - timedelta(days=1))
    carpeta = out_root / day.isoformat()

    for slug in cards_for_day(day):
        kind = BY_SLUG[slug]
        elegida = _pick(kind, db, day)
        if elegida is None:
            report.cards.append(CardResult(slug, "sin datos"))
            continue
        producto, provincia, contexto = elegida
        destino = carpeta / _file_name(kind, producto, provincia)
        if not execute:
            report.cards.append(CardResult(slug, "prevista", producto, provincia, destino))
            continue
        try:
            png = render(_html(kind, contexto), width=kind.width, height=kind.height)
        except RendererUnavailable as error:
            report.renderer_error = str(error)
            break
        carpeta.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(png)
        report.cards.append(CardResult(slug, "escrita", producto, provincia, destino))

    if execute and report.renderer_error is None:
        report.purgados = purge_old(out_root, day)
    return report


def format_report(report: DailyReport, *, execute: bool) -> str:
    lineas = [
        f"{'EJECUTADO' if execute else 'EN SECO'} — tarjetas del {report.day.isoformat()} "
        f"(cifras hasta el {report.data_through.isoformat()})"
    ]
    for c in report.cards:
        detalle = " · ".join(x for x in (c.product, c.province) if x)
        destino = f" → {c.path}" if c.path else ""
        lineas.append(f"  {c.slug}: {c.estado}{f' ({detalle})' if detalle else ''}{destino}")
    if report.purgados:
        lineas.append(f"  carpetas purgadas: {', '.join(p.name for p in report.purgados)}")
    if report.renderer_error:
        lineas.append(f"  SIN RENDER: {report.renderer_error}")
    return "\n".join(lineas)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera las tarjetas de marketing del día. En seco por defecto; no publica."
    )
    parser.add_argument(
        "--execute", action="store_true", help="rasteriza y guarda los PNG, y purga lo viejo"
    )
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="AAAA-MM-DD; default: hoy en La Habana",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help=f"default: {DEFAULT_OUT_DIR}"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    day = args.day or havana_today()
    with session_scope() as session:
        report = run_daily(session, day, args.out_dir, execute=args.execute)
    print(format_report(report, execute=args.execute))
    if report.renderer_error:
        # También a stderr, para que `journalctl -p err` lo encuentre.
        print(report.renderer_error, file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
