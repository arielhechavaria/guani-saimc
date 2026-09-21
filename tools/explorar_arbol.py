"""Imprime el árbol de «Explorar» de un producto y lo contrasta con lo que ya se publica.

Herramienta de verificación, no parte del paquete que se despliega. Lee la base
que apunte `SAIMC_DATABASE_URL` (o la de `.env`) y, por cada producto, muestra
provincia → formato con mediana, cambio, muestra, rango, último día y las
semanas del calor. Después compara cada fila de provincia con la lista del
tablero (`build_month_vs_month`) y cada fila de formato con el panel del mapa
(`build_headlines`): si algún número difiere, lo marca y sale con código 1.

    python tools/explorar_arbol.py Arroz Pollo
    python tools/explorar_arbol.py --dias 90        # todos los productos piloto
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from sqlalchemy import select

from saimc.cells import compute_retail_observations
from saimc.db import session_scope
from saimc.models import Province
from saimc.progress import pilot_product_names
from saimc.web.explore_view import WEEKS, build_explore_products
from saimc.web.price_charts import build_headlines, build_month_vs_month


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("productos", nargs="*", help="nombres de producto; sin ninguno, todos los piloto")
    parser.add_argument("--dias", type=int, default=30, choices=(7, 30, 90), help="ventana en días")
    args = parser.parse_args()

    hoy = date.today()
    desde = hoy - timedelta(days=args.dias)
    with session_scope() as session:
        piloto = pilot_product_names(session)
        provincias = sorted(session.scalars(select(Province.name)))
        cubre = max(args.dias * 2, WEEKS * 7) + 1
        obs = compute_retail_observations(session, days=cubre, hoy=hoy).observations
    activos = args.productos or list(piloto)
    actual = [o for o in obs if desde <= o.day <= hoy]
    previo = [o for o in obs if desde - timedelta(days=args.dias) <= o.day < desde]
    slopes = build_month_vs_month(actual, previo, window_days=args.dias, hoy=hoy)
    cifras = build_headlines(actual, previo)

    difiere = False
    for p in build_explore_products(activos, obs, window_days=args.dias, hoy=hoy, provinces=provincias):
        if not p.has_data:
            print(f"\n{p.name}: sin anuncios en los últimos {args.dias} días")
            continue
        print(
            f"\n{p.name} · {p.currency}/{p.unit} · {p.province_count} provincias · {p.count} anuncios · "
            f"de {p.low_text} a {p.high_text} · semanas {p.week_labels[0]}…{p.week_labels[-1]}"
        )
        grafico = next((g for g in slopes if g.product_name == p.name), None)
        for n in p.nodes:
            linea = next((x for x in grafico.lines if x.province_name == n.name), None) if grafico else None
            igual = linea is not None and (n.median_text, n.count, n.change_text) == (
                linea.now_text,
                linea.now_count,
                linea.change_text,
            )
            difiere |= not igual
            print(
                f"  {n.name:<20} {n.median_text:>7}  {n.change_text:>5}  n={n.count:<4} "
                f"{n.minimum_text}–{n.maximum_text}  {n.last_text:<9} "
                f"{'ok' if igual else 'DIFIERE del tablero'}"
            )
            print(f"  {'':<20} semanas: {[w.text or '·' for w in n.weeks]}")
            for h in n.children:
                cifra = next(
                    (
                        c
                        for c in cifras
                        if (c.product_name, c.province_name) == (p.name, n.name)
                        and (c.format_label or "").capitalize() in (h.name, "")
                        and c.median_text == h.median_text
                    ),
                    None,
                )
                igual_h = cifra is not None and (h.count, h.change_text) == (cifra.count, cifra.change_text)
                difiere |= not igual_h
                print(
                    f"      └ {h.name:<16} {h.median_text:>7}  {h.change_text:>5}  n={h.count:<4} "
                    f"anuncios={len(h.ads):<3} {'ok' if igual_h else 'DIFIERE del panel del mapa'}"
                )
        if p.sin_datos:
            print(f"  sin anuncios: {', '.join(p.sin_datos)}")
    print("\n" + ("HAY DIFERENCIAS" if difiere else "Coherente con el tablero y con el panel del mapa."))
    return 1 if difiere else 0


if __name__ == "__main__":
    sys.exit(main())
