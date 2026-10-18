#!/usr/bin/env python
"""¿Jev acierta la unidad y la cantidad de una fila? (medición, no decide nada)

Hoy Jev no opina sobre unidad ni cantidad (`jev_client.build_questions`), y es
justo donde el modelo se abstiene y donde fallan las filas del carril 1
(`docs/spec-slice-jev-decisor.md` §1.4). Este script mide, **por fila curada**,
si Jev las acierta. La verdad es lo que el operador guardó en `prices_history`
(`provenance = human`), nunca la propuesta del modelo.

Por qué por fila y no por anuncio: un anuncio trae varios productos, cada uno
con su unidad y su cantidad. Se le nombra a Jev el producto de la fila.

- **Unidad**: elección entre las 8 unidades canónicas más «el anuncio no lo
  dice». Acierta si es una de las unidades guardadas para ese producto en ese
  anuncio.
- **Cantidad**: sí/no sobre un candidato, con la unidad verdadera dada. Se
  mide con positivos (la cantidad guardada) y negativos (la de otra fila del
  mismo anuncio, o la guardada ×10 si no hay otra). Un P(sí) bajo en los
  negativos y alto en los positivos es lo que haría falta para usarlo como
  condición del carril 1. La unidad se da como verdadera para aislar la
  cantidad: no mide las dos juntas.
- **Modelo** (línea base): la propuesta guardada de `llm_proposals`, mismas
  filas, mismo criterio; `None` cuenta como abstención.

Fuera de línea: nada de `src/saimc` lo importa y no escribe en la base. Deja
el caché y el reporte bajo `data/jev_envase_eval/` (`data/` no va a git). El
texto sale redactado (`jev_client.build_request`).

Uso (desde la raíz del repo):
    python scripts/jev_envase_eval.py                 # en seco: n y costo estimado
    python scripts/jev_envase_eval.py --execute       # llama a Jev (tope: --max-cost)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from sqlalchemy import select

from saimc.db import session_scope
from saimc.models import LlmProposal, PriceHistory, Product, RawIngestion
from saimc.units import UNIT_NOUNS, canonical_unit
from saimc.validation import jev_client
from saimc.validation.stats import format_rate

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "jev_envase_eval"
CACHE_PATH = OUTPUT_DIR / "responses.jsonl"
REPORT_PATH = OUTPUT_DIR / "report.md"

NO_DATA = "sin_dato"
UNIT = "unit"
QTY = "qty"
THRESHOLDS = (0.5, 0.7, 0.9)
DEFAULT_MAX_COST = 0.50


@dataclass
class Group:
    """Un producto de un anuncio, con lo que el operador guardó para él."""

    ingestion_id: str
    text: str
    product_id: str
    product: str
    units: set[str] = field(default_factory=set)
    quantities: set[Decimal] = field(default_factory=set)
    model_units: set[str | None] = field(default_factory=set)
    model_quantities: set[Decimal | None] = field(default_factory=set)
    model_rows: int = 0


def fmt_qty(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def unit_question(product: str) -> dict[str, Any]:
    criteria = {key: noun for key, noun in UNIT_NOUNS.items()}
    criteria[NO_DATA] = "El anuncio no lo dice"
    return {
        "type": "choice",
        "instructions": (
            f"En este anuncio, ¿en qué unidad se expresa la presentación o la cantidad "
            f"con que se vende «{product}»? Si se vende por pieza, elegí unidad; si por "
            "caja, caja."
        ),
        "criteria": criteria,
    }


def qty_question(product: str, qty: Decimal, unit: str) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": (
            f"En este anuncio, ¿el precio de «{product}» corresponde a {fmt_qty(qty)} "
            f"{UNIT_NOUNS[unit]} (esa cantidad exacta)?"
        ),
    }


# --- Muestra ------------------------------------------------------------------


def load_groups() -> list[Group]:
    """Filas curadas a mano de textos, agrupadas por (anuncio, producto)."""
    groups: dict[tuple[str, str], Group] = {}
    with session_scope() as session:
        rows = session.execute(
            select(
                RawIngestion.id,
                RawIngestion.original_text,
                PriceHistory.product_id,
                Product.name,
                PriceHistory.unit,
                PriceHistory.quantity,
            )
            .join(PriceHistory, PriceHistory.source_id == RawIngestion.id)
            .join(Product, Product.id == PriceHistory.product_id)
            .where(
                RawIngestion.content_type == "text",
                PriceHistory.provenance == "human",
                RawIngestion.original_text.is_not(None),
            )
        ).all()
        for ing, text, pid, name, unit, qty in rows:
            unit = canonical_unit(unit)
            if unit is None or qty is None or qty <= 0:
                continue  # sin verdad completa no hay contra qué medir
            key = (str(ing), str(pid))
            group = groups.setdefault(key, Group(str(ing), text, str(pid), name))
            group.units.add(unit)
            group.quantities.add(Decimal(qty))
        proposals = session.execute(
            select(LlmProposal.ingestion_id, LlmProposal.rows)
            .where(LlmProposal.status == "ok", LlmProposal.ingestion_id.in_([g.ingestion_id for g in groups.values()]))
            .order_by(LlmProposal.created_at)
        ).all()
        latest = {str(ing): rows for ing, rows in proposals}  # la última gana
    for group in groups.values():
        for row in latest.get(group.ingestion_id) or []:
            if str(row.get("product_id")) != group.product_id:
                continue
            group.model_rows += 1
            group.model_units.add(canonical_unit(row.get("unit")))
            raw = row.get("quantity")
            group.model_quantities.add(Decimal(str(raw)) if raw is not None else None)
    return sorted(groups.values(), key=lambda g: (g.ingestion_id, g.product_id))


def decoy(group: Group, by_ad: dict[str, list[Group]]) -> Decimal:
    """Una cantidad falsa: la de otra fila del anuncio, o la verdadera ×10."""
    truth = group.quantities
    for other in by_ad[group.ingestion_id]:
        for q in sorted(other.quantities):
            if q not in truth:
                return q
    return max(truth) * 10


# --- Pedidos y caché ----------------------------------------------------------


def requests_for(group: Group, by_ad: dict[str, list[Group]]) -> dict[str, dict[str, Any]]:
    """Clave de pedido → cuerpo. `pos` lleva unidad + cantidad verdadera; `neg`, el señuelo."""
    unit = sorted(group.units)[0]
    pos_qty = sorted(group.quantities)[0]
    return {
        "pos": jev_client.build_request(
            group.text,
            {UNIT: unit_question(group.product), QTY: qty_question(group.product, pos_qty, unit)},
        ),
        "neg": jev_client.build_request(
            group.text, {QTY: qty_question(group.product, decoy(group, by_ad), unit)}
        ),
    }


def request_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.is_file():
        return {}
    out = {}
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        out[entry["key"]] = entry
    return out


def load_api_key() -> str | None:
    file_values = dotenv_values(REPO_ROOT / ".env")
    for name in ("SAIMC_VERCEL_API_KEY", "SAIMC_LLM_JEV_API_KEY"):
        value = os.environ.get(name) or file_values.get(name)
        if value:
            return value
    return None


# --- Métricas -----------------------------------------------------------------


def jev_unit(entry: dict[str, Any]) -> str | None:
    answer = jev_client.parse_answer(unit_question("x"), entry["answers"].get(UNIT))
    return None if answer.invalid else answer.choice


def jev_p(entry: dict[str, Any]) -> float | None:
    answer = jev_client.parse_answer({"type": "noul"}, entry["answers"].get(QTY))
    return None if answer.invalid else answer.p


def report(groups: list[Group], cache: dict[str, dict[str, Any]], by_ad: dict[str, list[Group]]) -> str:
    unit_ok = unit_nodata = unit_n = 0
    wrong: Counter[tuple[str, str]] = Counter()
    pos: list[float] = []
    neg: list[float] = []
    model_unit = [0, 0, 0]  # acierta, abstiene, n
    model_qty = [0, 0, 0]
    for group in groups:
        reqs = requests_for(group, by_ad)
        pos_entry = cache.get(request_key(reqs["pos"]))
        neg_entry = cache.get(request_key(reqs["neg"]))
        if group.model_rows:
            model_unit[2] += 1
            model_qty[2] += 1
            if not (group.model_units - {None}):
                model_unit[1] += 1
            elif group.model_units & group.units:
                model_unit[0] += 1
            if not (group.model_quantities - {None}):
                model_qty[1] += 1
            elif group.model_quantities & group.quantities:
                model_qty[0] += 1
        if pos_entry:
            choice = jev_unit(pos_entry)
            if choice is not None:
                unit_n += 1
                if choice in group.units:
                    unit_ok += 1
                elif choice == NO_DATA:
                    unit_nodata += 1
                else:
                    wrong[(sorted(group.units)[0], choice)] += 1
            p = jev_p(pos_entry)
            if p is not None:
                pos.append(p)
        if neg_entry:
            p = jev_p(neg_entry)
            if p is not None:
                neg.append(p)

    lines = ["# Jev: unidad y cantidad (medición)", ""]
    lines.append(f"Filas (anuncio × producto) con verdad completa: {len(groups)}. "
                 f"Con propuesta del modelo para ese producto: {model_unit[2]}.")
    lines += ["", "## Unidad", ""]
    lines.append(f"- Jev acierta: {format_rate(unit_ok, unit_n)}")
    lines.append(f"- Jev dice «no lo dice»: {unit_nodata}/{unit_n}")
    lines.append(f"- Jev se equivoca de unidad: {sum(wrong.values())}/{unit_n}")
    for (truth, said), n in wrong.most_common(8):
        lines.append(f"  - verdad {truth} → Jev {said}: {n}")
    lines.append(f"- Modelo acierta: {format_rate(model_unit[0], model_unit[2])}")
    lines.append(f"- Modelo se abstiene (sin unidad): {model_unit[1]}/{model_unit[2]}")
    lines += ["", "## Cantidad (sí/no sobre un candidato, con la unidad verdadera dada)", ""]
    lines.append(f"- Positivos medidos: {len(pos)}; negativos medidos: {len(neg)}")
    for t in THRESHOLDS:
        tp = sum(1 for p in pos if p >= t)
        fp = sum(1 for p in neg if p >= t)
        lines.append(
            f"- P(sí) ≥ {t}: acepta la cantidad verdadera {format_rate(tp, len(pos))}; "
            f"acepta el señuelo {format_rate(fp, len(neg))}"
        )
    lines.append(f"- Modelo acierta cantidad: {format_rate(model_qty[0], model_qty[2])}")
    lines.append(f"- Modelo se abstiene (sin cantidad): {model_qty[1]}/{model_qty[2]}")
    lines += ["", "Verdad = filas `human` de `prices_history`. No decide nada: el umbral y el "
              "uso (si alguno) son del operador y van a `docs/decisions.md`."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", help="llama a Jev y escribe el caché")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    parser.add_argument("--limit", type=int, default=None, help="solo las primeras N filas")
    args = parser.parse_args(argv)

    groups = load_groups()
    if args.limit:
        groups = groups[: args.limit]
    by_ad: dict[str, list[Group]] = defaultdict(list)
    for group in groups:
        by_ad[group.ingestion_id].append(group)

    cache = load_cache()
    pending = []
    for group in groups:
        for payload in requests_for(group, by_ad).values():
            if request_key(payload) not in cache:
                pending.append(payload)
    est = sum(jev_client.tokens_cost(jev_client.request_tokens(p)) for p in pending)
    print(f"Filas: {len(groups)}; pedidos nuevos: {len(pending)}; costo estimado: {est:.4f} USD")

    if args.execute and pending:
        key = load_api_key()
        if not key:
            print("Falta la clave de Jev.", file=sys.stderr)
            return 2
        if est > args.max_cost:
            print(f"Estimado {est:.4f} > tope {args.max_cost}. Subí --max-cost.", file=sys.stderr)
            return 2
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        spent = 0.0
        failures = 0
        with httpx.Client(timeout=30.0) as client, CACHE_PATH.open("a", encoding="utf-8") as cache_file:
            for payload in pending:
                try:
                    result = jev_client.ask(client, payload, key)
                except jev_client.JevError as exc:
                    failures += 1
                    print(f"fallo: {exc}", file=sys.stderr)
                    continue
                spent += result.cost
                entry = {"key": request_key(payload), "answers": result.answers, "cost": result.cost}
                cache[entry["key"]] = entry
                cache_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                cache_file.flush()
        print(f"Costo real: {spent:.4f} USD; fallos: {failures}")
    elif not args.execute:
        print("EN SECO: no se llamó a Jev ni se escribió nada. Con --execute se mide.")

    text = report(groups, cache, by_ad)
    print()
    print(text)
    if args.execute:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
