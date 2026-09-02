"""Retro-completado de `price_basis` en las filas anteriores al campo.

**Por qué existe.** `price_basis` entró el 2026-08-21 y se captura al 100%
desde el 22. Las filas cargadas entre el 10 y el 21 de agosto quedaron con el
campo nulo, y `cells.py` —que nunca asume— las deja en su propia celda con
`unresolved_basis=True`. El efecto medido el 2026-09-02: Arroz/La Habana
aparecía como **dos series** de `kg` minorista, n=23 mediana 688,70 y n=20
mediana 18.460,00, iguales en los cinco ejes de la celda y separadas solo por
el campo que falta. La matriz de `/control` se leía como si hubiera dos
productos distintos donde hay uno.

**Por qué no se lee del anuncio.** `engine.py:_declared_price_basis()` ya
resuelve el caso cuando el anuncio lo declara —«Sale $318.20 Lb»— y es la
fuente preferible porque sale de la evidencia. Medida contra las 110 filas
pendientes resolvió **cero**: esos avisos viejos escriben «sacos de 110 lb a
33 000 cup» y nunca el precio unitario. La regla no es estrecha de más; el
dato simplemente no está declarado.

**Cómo decide, y por qué no hay ningún umbral.** Igual que
`cells.py:detect_basis_suspects()`, del que este módulo es el gemelo que
escribe: para cada fila se calculan las dos lecturas posibles del mismo
número —`as_unit` y `as_total = price / quantity`—, ambas convertidas a la
unidad canónica del producto, y gana la que queda **proporcionalmente más
cerca** de la mediana de las filas no ambiguas de su propio vecindario. No hay
corte de precio, ni margen mínimo de confianza, ni muestra mínima: `spec.md`
§12.6 prohíbe inventar cualquiera de los tres. Sin vecindario **no hay
veredicto** y la fila se queda nula, que es distinto de un veredicto por
defecto.

**Lo que la cantidad no puede decidir.** «Picadillo de pollo americano 40 lb a
525» tiene cantidad 40 y precio por libra; «Cajas de Picadillo 50 Lb $25,750»
tiene cantidad 50 y precio del lote. Misma forma, lecturas opuestas
(docs/decisions.md, 2026-08-25). Por eso la regla mira el vecindario y nunca
la cantidad.

**Vecindario = producto × provincia × formato de venta × unidad canónica.** El
formato entra porque comprar al por mayor sale más barato por unidad y eso es
economía real, no ruido: usar filas minoristas como referencia de una fila
mayorista afirmaría una equivalencia que `cells.py` separa desde el
2026-08-12. **Fila no ambigua** es la que no puede estar mal leída de esta
forma: `quantity == 1` —unitario y total son el mismo número— o `price_basis`
ya resuelto por el operador con el anuncio delante.

**El valor inferido no es un valor curado.** Escribirlo en `price_basis` a
secas lo volvería indistinguible de lo que decidió el operador, así que cada
escritura deja su rastro en un log de solo-anexado bajo `data/`. Es el mismo
criterio que `data/llm-assist-log.jsonl` (design.md, D6): la procedencia vive
fuera de la fila, y por eso no hace falta migración ni columna nueva para que
el dato siga siendo auditable. Hoy `measure.py` no compara `price_basis`, así
que el log no filtra ninguna métrica todavía; existe para que el día que lo
haga la respuesta esté guardada y no haya que reconstruirla de memoria.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

# `_proportional_distance` se importa en vez de duplicarse: es la misma
# comparación que usa el detector de sospechosas, y dos definiciones separadas
# de «cuál de estas dos lecturas encaja mejor» podrían derivar y hacer que el
# que señala y el que escribe dejaran de coincidir.
from saimc.cells import _proportional_distance
from saimc.models import Currency, PriceHistory, Product, Province
from saimc.units import canonical_unit, convert_price_per_unit

#: Marca de procedencia que va en cada línea del log.
INFERENCE_SOURCE = "inferred_vs_curated_median"


def infer_basis(
    price: Decimal,
    quantity: Decimal | None,
    *,
    row_unit: str | None,
    target_unit: str | None,
    reference: Decimal | None,
) -> str | None:
    """`'unit'`, `'total'` o `None`. Nunca inventa.

    Devuelve `None` —misma convención que `canonical_unit()` y
    `convert_price_per_unit()`— cuando la pregunta no tiene respuesta:
    cantidad ausente o igual a uno (la pregunta es aritméticamente inerte),
    precio no positivo, unidad no convertible, o vecindario ausente.

    El empate exacto también devuelve `None`: dos lecturas igual de lejos no
    eligen ninguna.
    """
    if quantity is None or quantity == 0 or quantity == 1:
        return None
    if price <= 0 or reference is None or reference <= 0:
        return None

    frm = canonical_unit(row_unit)
    to = canonical_unit(target_unit)
    as_unit = convert_price_per_unit(price, frm=frm, to=to)
    as_total = convert_price_per_unit(price / quantity, frm=frm, to=to)
    if as_unit is None or as_total is None:
        return None

    distancia_unit = _proportional_distance(as_unit, reference)
    distancia_total = _proportional_distance(as_total, reference)
    if distancia_total == distancia_unit:
        return None
    return "total" if distancia_total < distancia_unit else "unit"


@dataclass(frozen=True)
class BackfillEntry:
    """Una fila resuelta, con todo lo que hace falta para auditarla después."""

    price_id: UUID
    product_name: str
    province_name: str | None
    basis: str
    #: Mediana del vecindario contra la que se comparó.
    reference: Decimal
    #: Cuántas filas no ambiguas formaron esa mediana.
    reference_count: int
    #: Las dos lecturas, en la unidad canónica, que produjeron el veredicto.
    as_unit: Decimal
    as_total: Decimal
    unit: str


#: producto × provincia × formato de venta × unidad canónica del producto.
_Neighbourhood = tuple[str, str | None, str | None, str | None]


def _effective_price(currency: str, price: Decimal, price_cup: Decimal | None) -> Decimal | None:
    """Precio en CUP, o `None` si la conversión no está resuelta (§6.3 E)."""
    if currency == Currency.CUP.value:
        return price
    return price_cup


def plan_backfill(session: Session) -> list[BackfillEntry]:
    """Qué filas se pueden resolver y con qué evidencia. **No escribe nada.**

    Separado de `apply_backfill` a propósito: el plan se puede imprimir y
    revisar antes de tocar la base, que es lo mínimo que merece una escritura
    masiva sobre datos curados a mano.
    """
    stmt = (
        select(
            PriceHistory.id,
            Product.name,
            Product.unit,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.sale_format,
            PriceHistory.quantity,
            PriceHistory.price,
            PriceHistory.price_cup,
            PriceHistory.price_basis,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
    )
    rows = session.execute(stmt).all()

    vecindarios: dict[_Neighbourhood, list[Decimal]] = {}
    candidatas: list[tuple[_Neighbourhood, dict[str, object]]] = []

    for (
        price_id,
        product_name,
        product_unit,
        province_name,
        currency,
        row_unit,
        sale_format,
        quantity,
        price,
        price_cup,
        price_basis,
    ) in rows:
        target_unit = canonical_unit(product_unit)
        key: _Neighbourhood = (product_name, province_name, sale_format, target_unit)

        efectivo = _effective_price(currency, price, price_cup)
        if efectivo is None:
            continue

        ambigua = price_basis is None and quantity is not None and quantity not in (0, 1)
        if not ambigua:
            # Referencia: solo la fila que **no puede** estar mal leída de esta
            # forma, misma definición que `detect_basis_suspects`:
            # `quantity == 1` —unitario y total son el mismo número— o
            # `price_basis == 'total'` —el operador ya lo resolvió, y en el
            # sentido opuesto al error—. Una fila `price_basis == 'unit'` con
            # `quantity != 1` NO vota: es exactamente la forma que se puede
            # haber curado mal (medido el 2026-09-02: un `24800 / 40 lb` de
            # Pollo marcado `unit` envenenaba la mediana de su celda de 710 a
            # 12.755 CUP/lb). Excluirla acá es lo que evita propagar ese error.
            es_referencia = quantity == 1 or price_basis == "total"
            if not es_referencia or quantity is None or quantity == 0:
                continue
            unitario = efectivo if quantity == 1 else efectivo / quantity
            convertido = convert_price_per_unit(unitario, frm=canonical_unit(row_unit), to=target_unit)
            if convertido is not None and convertido > 0:
                vecindarios.setdefault(key, []).append(convertido)
            continue

        candidatas.append(
            (
                key,
                {
                    "price_id": price_id,
                    "product_name": product_name,
                    "province_name": province_name,
                    "row_unit": row_unit,
                    "target_unit": target_unit,
                    "quantity": quantity,
                    "price": efectivo,
                },
            )
        )

    entradas: list[BackfillEntry] = []
    for key, fila in candidatas:
        muestras = vecindarios.get(key)
        if not muestras:
            continue
        reference = statistics.median(muestras)

        price = fila["price"]
        quantity = fila["quantity"]
        row_unit = fila["row_unit"]
        target_unit = fila["target_unit"]
        assert isinstance(price, Decimal) and isinstance(quantity, Decimal)
        assert isinstance(target_unit, str) or target_unit is None

        basis = infer_basis(
            price,
            quantity,
            row_unit=row_unit,  # type: ignore[arg-type]
            target_unit=target_unit,
            reference=reference,
        )
        if basis is None:
            continue

        frm = canonical_unit(row_unit)  # type: ignore[arg-type]
        as_unit = convert_price_per_unit(price, frm=frm, to=target_unit)
        as_total = convert_price_per_unit(price / quantity, frm=frm, to=target_unit)
        assert as_unit is not None and as_total is not None  # infer_basis ya lo garantizó

        entradas.append(
            BackfillEntry(
                price_id=fila["price_id"],  # type: ignore[arg-type]
                product_name=fila["product_name"],  # type: ignore[arg-type]
                province_name=fila["province_name"],  # type: ignore[arg-type]
                basis=basis,
                reference=reference,
                reference_count=len(muestras),
                as_unit=as_unit,
                as_total=as_total,
                unit=target_unit or "",
            )
        )

    entradas.sort(key=lambda e: (e.product_name, e.province_name or "", str(e.price_id)))
    return entradas


def apply_backfill(
    session: Session, entries: Sequence[BackfillEntry], *, log_path: Path
) -> int:
    """Escribe el `price_basis` de cada entrada y anexa su rastro. Devuelve cuántas.

    El log se abre en modo `"a"` y nunca se reescribe: una corrida no puede
    borrar la procedencia de otra anterior.
    """
    if not entries:
        return 0

    log_path.parent.mkdir(parents=True, exist_ok=True)
    momento = datetime.now(UTC).isoformat()
    escritas = 0

    with log_path.open("a", encoding="utf-8") as log:
        for entrada in entries:
            fila = session.get(PriceHistory, entrada.price_id)
            if fila is None or fila.price_basis is not None:
                # Alguien la resolvió entre el plan y la escritura: lo curado
                # gana siempre sobre lo inferido.
                continue
            fila.price_basis = entrada.basis
            escritas += 1
            log.write(
                json.dumps(
                    {
                        "price_id": str(entrada.price_id),
                        "basis": entrada.basis,
                        "source": INFERENCE_SOURCE,
                        "product": entrada.product_name,
                        "province": entrada.province_name,
                        "unit": entrada.unit,
                        "reference": str(entrada.reference),
                        "reference_count": entrada.reference_count,
                        "as_unit": str(entrada.as_unit),
                        "as_total": str(entrada.as_total),
                        "at": momento,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    session.commit()
    return escritas


def _format_plan(entries: Sequence[BackfillEntry]) -> str:
    """Plan en texto, para revisarlo antes de escribir."""
    if not entries:
        return "Nada que retro-completar: ninguna fila con basis nulo tiene vecindario."
    lineas = [f"{len(entries)} fila(s) a resolver:\n"]
    for e in entries:
        provincia = e.province_name or "—"
        lineas.append(
            f"  {e.basis:5}  {e.product_name} / {provincia}  "
            f"(ref {e.reference} CUP/{e.unit}, n={e.reference_count}; "
            f"unit={e.as_unit}  total={e.as_total})"
        )
    return "\n".join(lineas)


def main() -> None:
    """Punto de entrada: `python -m saimc.basis_backfill`.

    Sin `--apply` solo imprime el plan y no toca la base — es el modo por
    defecto a propósito, porque escribe sobre datos curados a mano.
    """
    import argparse

    from saimc.config import get_settings
    from saimc.db import session_scope

    parser = argparse.ArgumentParser(
        prog="python -m saimc.basis_backfill",
        description=(
            "Retro-completa price_basis en las filas anteriores al campo, "
            "por inferencia contra la mediana del vecindario (docs/decisions.md, "
            "2026-09-02)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Escribir los valores inferidos y anexar el log. Sin esto solo muestra el plan.",
    )
    args = parser.parse_args()

    log_path = get_settings().basis_backfill_log_path
    with session_scope() as session:
        plan = plan_backfill(session)
        print(_format_plan(plan))
        if not args.apply:
            print(f"\n(simulación — corré con --apply para escribir; log en {log_path})")
            return
        escritas = apply_backfill(session, plan, log_path=log_path)
        print(f"\n{escritas} fila(s) escritas. Procedencia en {log_path}.")


if __name__ == "__main__":
    main()
