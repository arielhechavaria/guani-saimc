"""Medición de H0 (spec.md §3 y §12.5) contra la cola de curaduría real.

Re-corre `propose_all()` sobre cada anuncio ya curado y compara la propuesta
contra lo que el operador confirmó en `prices_history`. La propuesta nunca
influyó lo que quedó guardado (la cola de curaduría solo la usa para prellenar
el formulario, spec.md §6.1): por eso la comparación mide extracción real y
no un eco de sí misma.

De dónde sale el texto evaluado, según el tipo de evidencia:

- **Texto:** `original_text`, escrito en la ingesta y nunca tocado después.
- **Imagen:** se re-corre el OCR sobre la evidencia original preservada. NO se
  usa `original_text`, porque en una imagen ese campo guarda la transcripción
  ya corregida por el operador (`saimc/web/routers/curation.py`): medir sobre
  ella evaluaría las reglas contra un texto que un humano limpió, y H0 (§3)
  pregunta justamente si el pipeline sostiene el volumen SIN esa intervención.
  El archivo original es inmutable, así que la transcripción es reproducible.

La propuesta se re-corre con el MISMO contexto que tiene la cola de curaduría
real, no con menos: la provincia fija del canal entra por `channel_province`
(docs/decisions.md, 2026-08-17 y 2026-08-21). Medir sin ella no era más
exigente, era medir otro extractor — uno que en producción no existe.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytesseract
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.extraction import llm_assist, ocr
from saimc.extraction.engine import ExtractionProposal, propose_all
from saimc.ingestion import origin
from saimc.models import (
    ContentType,
    DiscardReason,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
)
from saimc.units import canonical_unit


# El OCR se inyecta para que la suite no dependa del binario de Tesseract y
# para poder fijar transcripciones conocidas en los tests.
Transcriber = Callable[[Path], str]


@dataclass
class H0Report:
    """Resultado de una corrida de medición de H0 (spec.md §12.5)."""

    total_curated: int
    total_discarded: int
    total_processed: int
    # Descartes que traían dato económico pero no eran de interés para la Fase
    # 0 (no piloto, mayorista puro). `discard_rate` los saca de la fracción —
    # `spec.md` §12.5 mide «sin información económica extraíble», y esos
    # anuncios sí la tienen (2026-09-03). Cero en las corridas anteriores a
    # este cambio: la columna no existía, así que la serie de `h0-runs.jsonl`
    # sigue siendo comparable con su propia historia.
    discarded_off_focus: int
    discard_rate: float | None
    # Texto e imagen entran los dos en la medición. Se cuentan por separado
    # solo para poder leer la composición del corpus.
    text_curated_count: int
    image_curated_count: int
    # Imágenes cuyo OCR no devolvió texto. Se reporta aparte porque en el
    # porcentaje es indistinguible de una extracción fallida: un Tesseract
    # ausente hundiría las métricas sin que nada lo delate.
    image_ocr_empty_count: int
    # Un anuncio puede tener varias filas de `prices_history` (multi-producto,
    # ver `propose_all`): las cinco métricas de abajo se calculan por FILA, no
    # por ingesta.
    evaluated_rows_count: int
    # Cuántos ANUNCIOS distintos aportaron al menos una fila evaluada. Es el
    # tamaño del conjunto de referencia en la unidad que usa spec.md §3 («al
    # menos 300 anuncios reales»), y por eso es contra este número que se mide
    # si la corrida ya es oficial. Las precisiones siguen calculándose por
    # fila: la pregunta «¿cuántas extracciones salieron bien?» se responde por
    # extracción, la pregunta «¿alcanza la muestra?» se responde por evidencia.
    evaluated_ads_count: int
    # Anuncios y filas que pasaron por el asistente de LLM y por eso NO entran
    # en las cinco precisiones de abajo. Se reportan aparte, no se esconden:
    # la divergencia entre este conjunto y el medido es justamente la señal de
    # que una respuesta del modelo se está confirmando de apuro
    # (openspec/changes/llm-extraction-assist/design.md, D6).
    assisted_ads_count: int
    assisted_rows_count: int
    product_accuracy: float | None
    price_accuracy: float | None
    province_accuracy: float | None
    quantity_unit_accuracy: float | None
    complete_accuracy: float | None


def _all_products(session: Session) -> list[Product]:
    return list(session.execute(select(Product)).scalars().all())


def _all_provinces(session: Session) -> list[Province]:
    return list(session.execute(select(Province)).scalars().all())


def _curated_ingestions(session: Session) -> list[RawIngestion]:
    """Universo de las cinco métricas de extracción: todo anuncio curado, sea
    texto o imagen. Un curado sin fila confirmada en `prices_history` se
    filtra más adelante, cuando se busca su contraparte.
    """
    stmt = select(RawIngestion).where(
        RawIngestion.processing_status == ProcessingStatus.CURATED
    )
    return list(session.execute(stmt).scalars().all())


def _evaluation_text(ingestion: RawIngestion, transcribe: Transcriber) -> str:
    """Texto sobre el que se re-corre la extracción.

    Para imágenes sale de re-transcribir la evidencia original, no de
    `original_text` — ver el docstring del módulo para el motivo.
    """
    if ingestion.content_type == ContentType.IMAGE:
        return transcribe(get_settings().raw_store_dir / ingestion.file_path)
    return ingestion.original_text or ""


def _price_history_by_source(session: Session) -> dict[uuid.UUID, list[PriceHistory]]:
    """Mapa `source_id -> [PriceHistory, ...]`. Puede haber más de una fila
    por `source_id`: un anuncio multi-producto genera una fila por línea
    confirmada (`action=save_and_continue` en la cola de curaduría). Agrupar
    en lugar de indexar 1:1 evita pisar filas en silencio.
    """
    rows = session.execute(select(PriceHistory)).scalars().all()
    grouped: dict[uuid.UUID, list[PriceHistory]] = {}
    for row in rows:
        grouped.setdefault(row.source_id, []).append(row)
    return grouped


#: Peso de cada campo al elegir, entre varios candidatos del MISMO producto,
#: cuál corresponde a una fila. El precio es la señal de identidad más fuerte
#: —dos filas del mismo producto en el mismo anuncio casi siempre difieren en
#: precio antes que en nada—, después la cantidad/unidad, y la provincia casi
#: no discrimina porque la comparten todos los candidatos del anuncio.
_PRICE_MISMATCH_COST = 1.0
_QUANTITY_UNIT_MISMATCH_COST = 0.5
_PROVINCE_MISMATCH_COST = 0.25


def _pair_cost(row: PriceHistory, candidate: ExtractionProposal) -> float:
    """Cuánto «no se parecen» una fila confirmada y un candidato. 0 = idénticos
    en los tres campos que este emparejado mira."""
    cost = 0.0
    if not (candidate.price == row.price and candidate.currency == row.currency):
        cost += _PRICE_MISMATCH_COST
    if not _quantity_unit_correct(
        candidate.quantity, candidate.unit, row.quantity, row.unit
    ):
        cost += _QUANTITY_UNIT_MISMATCH_COST
    candidate_province_id = (
        candidate.province.id if candidate.province is not None else None
    )
    if candidate_province_id != row.province_id:
        cost += _PROVINCE_MISMATCH_COST
    return cost


def _assign_within_product(
    rows: list[PriceHistory], candidates: list[ExtractionProposal]
) -> list[tuple[PriceHistory, ExtractionProposal]]:
    """Empareja filas y candidatos de un MISMO producto por mejor ajuste.

    Con `permutations` cuando los dos conjuntos son chicos —el caso real: 2 o 3
    filas de Arroz por anuncio— para dar el óptimo global; greedy por si algún
    anuncio raro trae muchos de los dos lados. En los dos casos cada candidato
    se usa una sola vez.
    """
    k = min(len(rows), len(candidates))
    if len(rows) <= 6 and len(candidates) <= 6:
        best_perm = min(
            itertools.permutations(candidates, k),
            key=lambda perm: sum(_pair_cost(rows[i], perm[i]) for i in range(k)),
        )
        return [(rows[i], best_perm[i]) for i in range(k)]

    pool = list(candidates)
    pairs = []
    for row in rows[:k]:
        chosen = min(pool, key=lambda c: _pair_cost(row, c))
        pool.remove(chosen)
        pairs.append((row, chosen))
    return pairs


def _match_rows_to_candidates(
    confirmed_rows: list[PriceHistory], candidates: list[ExtractionProposal]
) -> list[tuple[PriceHistory, ExtractionProposal]]:
    """Empareja cada fila confirmada con el candidato de `propose_all` que le
    corresponde.

    Primero por `product_id` — es la señal más confiable cuando hay varios
    productos en el mismo anuncio. Cuando un producto aparece en **varias**
    filas confirmadas y `propose_all` sacó **varios** candidatos suyos —el
    price-list de canal que vende Arroz por kg al menudeo y por saco de lb al
    por mayor en el mismo mensaje—, el reparto por orden los cruzaba y contaba
    las dos filas como fallo aunque el extractor las hubiera sacado bien
    (39 de 295 filas, medido 2026-09-03). Entre candidatos del mismo producto
    se elige por **mejor ajuste** (`_assign_within_product`): no se toma nada
    prestado de otro producto, así que alinear no puede inflar la métrica —
    solo deja de perder aciertos reales por el orden.

    Lo que no matchea por `product_id` se reparte según cuántas líneas hayan
    quedado libres, porque no es lo mismo no tener elección que tener que
    adivinar:

    - **Una sola línea libre**: se empareja con ella. No hubo elección, y
      precio, cantidad y provincia igual pudieron extraerse bien aunque el
      producto no se reconociera.
    - **Varias líneas libres**: el reparto por orden sería una adivinanza. El
      orden de las filas confirmadas no tiene por qué seguir el de las líneas
      del anuncio, así que regalarle a la fila el precio de una línea ajena
      infla la métrica con coincidencias. Medido contra producción el
      2026-08-21: 15 filas caían acá y 9 se llevaban el precio por casualidad,
      unos 4.3 puntos de precisión de precio que eran ruido.
      Se conserva solo la provincia, y solo si todas las líneas libres
      coinciden en ella: no depende de qué línea se elija, porque la fija el
      canal para todo el anuncio (docs/decisions.md, 2026-08-17) y la cola de
      curaduría se la aplica a todas las filas por igual.
    - **Ninguna línea libre**: propuesta vacía, como antes.

    Ninguna línea se consume en el caso ambiguo: no se usó, así que sigue
    disponible para la fila siguiente.
    """
    remaining = list(candidates)
    matched: dict[int, ExtractionProposal] = {}
    unmatched_rows: list[PriceHistory] = []

    # Agrupado por producto, en el orden en que cada producto aparece por
    # primera vez en las filas confirmadas.
    rows_by_product: dict[object, list[PriceHistory]] = {}
    for row in confirmed_rows:
        rows_by_product.setdefault(row.product_id, []).append(row)

    for product_id, rows in rows_by_product.items():
        product_candidates = [
            c for c in remaining if c.product is not None and c.product.id == product_id
        ]
        if not product_candidates:
            unmatched_rows.extend(rows)
            continue
        for row, candidate in _assign_within_product(rows, product_candidates):
            matched[id(row)] = candidate
            remaining.remove(candidate)
        for row in rows[len(product_candidates):]:
            unmatched_rows.append(row)

    for row in unmatched_rows:
        if not remaining:
            matched[id(row)] = ExtractionProposal()
        elif len(remaining) == 1:
            matched[id(row)] = remaining.pop(0)
        else:
            matched[id(row)] = ExtractionProposal(province=_shared_province(remaining))

    # Se devuelve en el orden original de `confirmed_rows`.
    return [(row, matched[id(row)]) for row in confirmed_rows]


def _shared_province(candidates: list[ExtractionProposal]) -> Province | None:
    """La provincia en la que coinciden TODOS los candidatos, o `None`.

    Es lo único que sobrevive a un emparejamiento ambiguo: si todas las líneas
    libres proponen la misma provincia, esa es la del anuncio y acreditarla no
    depende de cuál se elija. Si difieren, no hay nada que afirmar.
    """
    provinces = {c.province.id if c.province is not None else None for c in candidates}
    if len(provinces) != 1:
        return None
    return candidates[0].province


def _quantity_unit_correct(
    proposal_quantity: Decimal | None,
    proposal_unit: str | None,
    confirmed_quantity: Decimal | None,
    confirmed_unit: str | None,
) -> bool:
    """Cantidad Y unidad, en el sentido del spec.md §12.5.

    La unidad se compara contra `prices_history.unit` — la que el operador
    confirmó para ESA fila — y no contra `Product.unit`, la unidad canónica
    del catálogo.

    Antes se comparaba contra el catálogo porque la unidad no se persistía por
    fila. Dejó de ser cierto: la columna existe (`price_history.py`) y
    `curation.py` la completa con `canonical_unit()`. Medir contra el catálogo
    hacía otra pregunta —«¿el extractor emitió la unidad de góndola del
    producto?»— en vez de la del spec —«¿emitió la unidad que este anuncio
    usa?»—, y penalizaba al extractor por acertar: un saco de arroz de 25 kg
    se anuncia y se confirma en kg aunque el catálogo lo liste en lb (95 de
    211 filas al 2026-08-21, docs/decisions.md).

    Los dos lados pasan por `canonical_unit` porque hablan vocabularios
    distintos a propósito (`saimc.units`): `engine.py` reconoce formas
    flexionadas del anuncio (`litro`, `cajas`) y la base guarda las canónicas
    (`l`, `caja`). Sin normalizar, esa diferencia de vocabulario contaría como
    fallo de extracción.

    Una unidad propuesta sobre una fila que no la tiene cuenta como fallo:
    `None` de un lado y algo del otro no coinciden, igual que en la cantidad.
    """
    quantity_matches = proposal_quantity == confirmed_quantity
    unit_matches = canonical_unit(proposal_unit) == canonical_unit(confirmed_unit)
    return quantity_matches and unit_matches


def compute_h0_report(
    session: Session,
    transcribe: Transcriber = ocr.transcribe,
    assist_log_path: Path | None = None,
) -> H0Report:
    """Calcula el reporte de H0 re-corriendo `propose_all()` sobre lo ya curado.

    Evalúa por FILA de `prices_history`, no por ingesta: un anuncio
    multi-producto (`docs/decisions.md`) tiene varias filas con el mismo
    `source_id`, cada una es un dato de H0 aparte.
    """
    total_curated = session.execute(
        select(RawIngestion).where(RawIngestion.processing_status == ProcessingStatus.CURATED)
    ).scalars().all()
    total_discarded = session.execute(
        select(RawIngestion).where(RawIngestion.processing_status == ProcessingStatus.DISCARDED)
    ).scalars().all()

    total_curated_count = len(total_curated)
    total_discarded_count = len(total_discarded)
    total_processed = total_curated_count + total_discarded_count
    discarded_off_focus = sum(
        1
        for ingestion in total_discarded
        if ingestion.discard_reason == DiscardReason.OFF_FOCUS.value
    )
    # La tasa de spec.md §12.5: descartes «sin dato» sobre lo procesado, una
    # vez sacado lo que se tiró solo por estar fuera de foco (mismo criterio
    # que `progress.IntakeHealth.discard_rate`).
    discard_base = total_processed - discarded_off_focus
    discard_rate = (
        (total_discarded_count - discarded_off_focus) / discard_base
        if discard_base > 0
        else None
    )

    image_curated_count = sum(
        1 for ingestion in total_curated if ingestion.content_type == ContentType.IMAGE
    )
    text_curated_count = total_curated_count - image_curated_count

    ingestions = _curated_ingestions(session)
    image_ocr_empty_count = 0

    products = _all_products(session)
    provinces = _all_provinces(session)
    history_by_source = _price_history_by_source(session)

    product_correct = 0
    price_correct = 0
    province_correct = 0
    quantity_unit_correct = 0
    complete_correct = 0
    evaluated_rows_count = 0
    evaluated_ads_count = 0
    assisted_ads_count = 0
    assisted_rows_count = 0

    # Qué ingestas pasaron por el asistente de LLM. Salen de la MEDICIÓN, no de
    # la cola: siguen siendo anuncios curados y siguen contando en la tasa de
    # descarte. Lo que no pueden es alimentar las precisiones, porque su verdad
    # pudo absorber un valor que escribió el modelo y entonces H0 mediría las
    # reglas contra algo que el modelo produjo (design.md del cambio, D6).
    #
    # Con el registro vacío —o ausente— este conjunto es vacío y los números
    # son exactamente los de siempre: introducir el corte no mueve nada por sí
    # solo.
    assisted_ids = (
        llm_assist.assisted_ingestion_ids(assist_log_path)
        if assist_log_path is not None
        else set()
    )

    for ingestion in ingestions:
        confirmed_rows = history_by_source.get(ingestion.id, [])
        if not confirmed_rows:
            continue
        if str(ingestion.id) in assisted_ids:
            assisted_ads_count += 1
            assisted_rows_count += len(confirmed_rows)
            continue
        evaluated_ads_count += 1
        text = _evaluation_text(ingestion, transcribe)
        if ingestion.content_type == ContentType.IMAGE and not text.strip():
            image_ocr_empty_count += 1
        # La provincia fija del canal entra a la propuesta igual que en la cola
        # de curaduría real (`web/routers/curation.py`, "el canal siempre
        # manda", docs/decisions.md 2026-08-17). Sin esto la medición evaluaba
        # un extractor más ciego que el que corre en producción: contaba como
        # fallo de provincia cada anuncio de canal cuyo mensaje no la nombra,
        # que es la forma NORMAL de un canal — el vendedor no repite dónde está
        # en cada publicación.
        candidates = propose_all(
            text,
            products,
            provinces,
            channel_province=origin.channel_province(session, ingestion),
        )

        for confirmed, proposal in _match_rows_to_candidates(confirmed_rows, candidates):
            evaluated_rows_count += 1

            is_product_ok = (
                proposal.product is not None
                and proposal.product.id == confirmed.product_id
            )
            is_price_ok = (
                proposal.price == confirmed.price and proposal.currency == confirmed.currency
            )
            proposed_province_id = (
                proposal.province.id if proposal.province is not None else None
            )
            is_province_ok = proposed_province_id == confirmed.province_id
            is_quantity_unit_ok = _quantity_unit_correct(
                proposal.quantity, proposal.unit, confirmed.quantity, confirmed.unit
            )

            if is_product_ok:
                product_correct += 1
            if is_price_ok:
                price_correct += 1
            if is_province_ok:
                province_correct += 1
            if is_quantity_unit_ok:
                quantity_unit_correct += 1
            if is_product_ok and is_price_ok and is_province_ok and is_quantity_unit_ok:
                complete_correct += 1

    if evaluated_rows_count == 0:
        return H0Report(
            total_curated=total_curated_count,
            total_discarded=total_discarded_count,
            total_processed=total_processed,
            discarded_off_focus=discarded_off_focus,
            discard_rate=discard_rate,
            text_curated_count=text_curated_count,
            image_curated_count=image_curated_count,
            image_ocr_empty_count=image_ocr_empty_count,
            evaluated_rows_count=0,
            evaluated_ads_count=0,
            assisted_ads_count=assisted_ads_count,
            assisted_rows_count=assisted_rows_count,
            product_accuracy=None,
            price_accuracy=None,
            province_accuracy=None,
            quantity_unit_accuracy=None,
            complete_accuracy=None,
        )

    return H0Report(
        total_curated=total_curated_count,
        total_discarded=total_discarded_count,
        total_processed=total_processed,
        discarded_off_focus=discarded_off_focus,
        discard_rate=discard_rate,
        text_curated_count=text_curated_count,
        image_curated_count=image_curated_count,
        image_ocr_empty_count=image_ocr_empty_count,
        evaluated_rows_count=evaluated_rows_count,
        evaluated_ads_count=evaluated_ads_count,
        assisted_ads_count=assisted_ads_count,
        assisted_rows_count=assisted_rows_count,
        product_accuracy=product_correct / evaluated_rows_count,
        price_accuracy=price_correct / evaluated_rows_count,
        province_accuracy=province_correct / evaluated_rows_count,
        quantity_unit_accuracy=quantity_unit_correct / evaluated_rows_count,
        complete_accuracy=complete_correct / evaluated_rows_count,
    )


# Umbral real de H0: la medición no es oficial por debajo de 300 ANUNCIOS
# etiquetados a mano. La unidad es la que fija spec.md §3 —«el operador
# etiqueta manualmente un conjunto de referencia de al menos 300 anuncios
# reales; las reglas de extracción se miden contra ese conjunto»— y §9 la
# repite. La spec usa dos unidades a propósito: el conjunto de referencia se
# cuenta en anuncios y la meta del producto de datos en filas («500 registros
# validados»), que es la que ya respeta `progress.PHASE0_TARGET_ROWS`.
#
# Hasta el 2026-08-21 esto se comparaba contra las FILAS mientras el
# comentario decía «300 anuncios»: código y comentario se contradecían, y la
# diferencia no era cosmética — 211 filas contra 132 anuncios en producción,
# con UN anuncio aportando 25 de esas filas (11.8% de la muestra).
#
# El motivo de fondo es de independencia estadística: las filas de un mismo
# anuncio comparten evidencia, OCR, canal y provincia, así que fallan juntas.
# Contarlas por separado infla el tamaño de muestra sin agregar información.
_H0_MINIMUM_ADS = 300


def _format_pct(value: float | None) -> str:
    return "sin datos" if value is None else f"{value:.1%}"


def _print_report(report: H0Report) -> None:
    print("=== Medición de H0 (spec.md §12.5) ===")
    print(f"Procesados totales:      {report.total_processed}")
    print(f"  Curados:               {report.total_curated}")
    print(f"  Descartados:           {report.total_discarded}")
    if report.discarded_off_focus:
        print(
            f"    fuera de foco:       {report.discarded_off_focus} "
            "(con dato, no piloto/mayorista — no cuentan en la tasa)"
        )
    print(
        f"  Tasa de descarte:      {_format_pct(report.discard_rate)}  "
        "(sin dato útil, §12.5)"
    )
    print()
    print(f"Curados de texto:        {report.text_curated_count} anuncios")
    print(f"Curados de imagen:       {report.image_curated_count} anuncios (OCR re-corrido)")
    print(f"  Sin texto del OCR:     {report.image_ocr_empty_count}")
    print(f"Anuncios evaluados:      {report.evaluated_ads_count} (el conjunto de referencia)")
    if report.assisted_ads_count:
        print(
            f"  Excluidos por asistencia: {report.assisted_ads_count} anuncios "
            f"({report.assisted_rows_count} filas)"
        )
        print(
            "    Pasaron por el asistente de LLM, así que su verdad pudo "
            "absorber un valor del modelo."
        )
        print("    Las precisiones de abajo miden SOLO lo no asistido.")
    print(f"Filas evaluadas:         {report.evaluated_rows_count} (un anuncio puede tener varias)")
    print()
    print(f"Precisión de producto:        {_format_pct(report.product_accuracy)}")
    print(f"Precisión de precio:          {_format_pct(report.price_accuracy)}")
    print(f"Precisión de provincia:       {_format_pct(report.province_accuracy)}")
    print(f"Precisión de cantidad/unidad: {_format_pct(report.quantity_unit_accuracy)}")
    print(f"Extracción completa correcta: {_format_pct(report.complete_accuracy)}")

    if report.evaluated_ads_count < _H0_MINIMUM_ADS:
        faltan = _H0_MINIMUM_ADS - report.evaluated_ads_count
        print()
        print(
            f"AVISO: el conjunto de referencia tiene {report.evaluated_ads_count} anuncios "
            f"(umbral H0: {_H0_MINIMUM_ADS}, spec.md §3). Faltan {faltan}. "
            "Este número todavía NO es la medición oficial de H0."
        )


# --- N-05: registro longitudinal de cada corrida ---------------------------
#
# `measure.py` da un número puntual; sin registro por versión no se puede
# afirmar que un cambio del extractor ayudó (docs/requisitos.md, N-05). Cada
# corrida real se appendea como una línea JSON a `docs/h0-runs.jsonl`, atada
# al commit que la produjo. El archivo vive en git a propósito: `git log -p
# docs/h0-runs.jsonl` es la interfaz de consulta —el número moviéndose al lado
# del código que lo movió— y no cuesta una tabla ni una migración para 4 filas
# al mes. Si algún día hace falta en `/control`, importar el JSONL entonces es
# trivial (Opción A de la decisión 2026-08-31).

_H0_RUNS_FILENAME = "h0-runs.jsonl"

# `measure.py` vive en `src/saimc/extraction/`, así que la raíz del repo está
# tres niveles arriba. Se resuelve desde `__file__` y no del cwd —mismo
# criterio que `web/docs_catalog.py`— para que funcione desde donde sea que se
# lance el comando o el server.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(args: list[str]) -> str | None:
    """Salida de un comando git, o `None` si git falla o no hay repo.

    Nunca revienta la corrida: el registro es telemetría, no el objetivo.
    """
    # Argumentos fijos, sin shell y sin entrada del usuario: `git` con una
    # lista literal, nunca una cadena interpolada.
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _git_revision() -> tuple[str, bool, str]:
    """`(commit_corto, arbol_sucio, rama)`.

    `commit` cae a `"unknown"` si git no responde —así el registro queda con
    la marca de que su procedencia no es fiable— y `dirty` a `False`, porque
    un checkout sin `.git` (un tarball) no está "sucio", solo no es un repo.
    """
    commit = _git(["rev-parse", "--short", "HEAD"]) or "unknown"
    dirty = bool(_git(["status", "--porcelain"]))
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
    return commit, dirty, branch


def _tesseract_version() -> str | None:
    """Versión del binario de Tesseract, o `None` si no está.

    El OCR es el techo real de H0 (docs/requisitos.md §03), así que qué
    Tesseract produjo el número es parte de su contexto. Degrada como
    `ocr.transcribe`: la ausencia del binario no rompe nada.
    """
    try:
        return str(pytesseract.get_tesseract_version())
    except (pytesseract.TesseractNotFoundError, OSError, ValueError):
        return None


def _h0_runs_path() -> Path:
    """`docs/h0-runs.jsonl` en la raíz del repo, resuelta desde `__file__`."""
    return _REPO_ROOT / "docs" / _H0_RUNS_FILENAME


def h0_run_record(
    report: H0Report,
    *,
    now: datetime,
    commit: str,
    dirty: bool,
    branch: str,
    tesseract_version: str | None,
) -> dict[str, object]:
    """Un registro de corrida, aplanado y serializable a una línea JSON.

    Las 15 métricas del `H0Report` viajan tal cual (`asdict`), más el
    contexto que las hace comparables entre versiones: cuándo, qué commit,
    si el árbol estaba limpio, qué rama y qué Tesseract. `is_official` usa el
    mismo umbral que el aviso de `_print_report` (`_H0_MINIMUM_ADS`).
    """
    return {
        "timestamp": now.astimezone(UTC).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_branch": branch,
        "tesseract_version": tesseract_version,
        "is_official": report.evaluated_ads_count >= _H0_MINIMUM_ADS,
        **asdict(report),
    }


def append_h0_run(record: dict[str, object], path: Path) -> None:
    """Appendea el registro como una sola línea JSON.

    Append puro: nunca reescribe ni reordena lo ya guardado —el historial es
    el archivo—. `sort_keys` mantiene el orden de claves estable para que el
    `git diff` de cada corrida muestre solo lo que cambió.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


@dataclass(frozen=True)
class H0RunSummary:
    """Una corrida registrada, con lo que `/control` muestra de ella.

    Subconjunto de las 21 claves del registro completo: la vista del operador
    necesita la fecha, la versión y las métricas que se comparan, no los
    conteos de composición del corpus. El archivo en disco sigue siendo la
    fuente entera.
    """

    date: str
    timestamp: str
    git_commit: str
    git_dirty: bool
    git_branch: str
    tesseract_version: str | None
    is_official: bool
    evaluated_ads_count: int
    evaluated_rows_count: int
    # Corridas anteriores al asistente no traen estas claves: se leen como 0,
    # que es lo que efectivamente eran.
    assisted_ads_count: int
    assisted_rows_count: int
    complete_accuracy: float | None
    product_accuracy: float | None
    price_accuracy: float | None
    province_accuracy: float | None
    quantity_unit_accuracy: float | None
    discard_rate: float | None
    # Diferencia de extracción completa contra la última corrida anterior que
    # haya registrado un número. `None` si es la primera con número, o si esta
    # corrida no lo tiene. Una corrida "sin datos" en el medio no corta la
    # comparación entre las que sí lo tienen.
    complete_delta: float | None


def _as_float(value: object) -> float | None:
    """`float` de un número JSON; `None` para `null`, texto o `bool`."""
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def read_h0_runs(path: Path | None = None) -> list[H0RunSummary]:
    """Lee `docs/h0-runs.jsonl` en orden cronológico (como se escribió).

    Tolera todo lo que un archivo de solo-anexado acumula: que todavía no
    exista, líneas en blanco, una línea a medio escribir, una que no es un
    objeto JSON. Nada de eso puede tumbar `/control` — la línea ilegible se
    saltea y el resto se lee igual.
    """
    target = path if path is not None else _h0_runs_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return []

    summaries: list[H0RunSummary] = []
    previous_complete: float | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        complete = _as_float(record.get("complete_accuracy"))
        delta = (
            complete - previous_complete
            if complete is not None and previous_complete is not None
            else None
        )
        timestamp = str(record.get("timestamp", ""))
        tesseract = record.get("tesseract_version")
        summaries.append(
            H0RunSummary(
                date=timestamp[:10],
                timestamp=timestamp,
                git_commit=str(record.get("git_commit", "unknown")),
                git_dirty=bool(record.get("git_dirty", False)),
                git_branch=str(record.get("git_branch", "unknown")),
                tesseract_version=str(tesseract) if tesseract is not None else None,
                is_official=bool(record.get("is_official", False)),
                evaluated_ads_count=_as_int(record.get("evaluated_ads_count")),
                evaluated_rows_count=_as_int(record.get("evaluated_rows_count")),
                assisted_ads_count=_as_int(record.get("assisted_ads_count")),
                assisted_rows_count=_as_int(record.get("assisted_rows_count")),
                complete_accuracy=complete,
                product_accuracy=_as_float(record.get("product_accuracy")),
                price_accuracy=_as_float(record.get("price_accuracy")),
                province_accuracy=_as_float(record.get("province_accuracy")),
                quantity_unit_accuracy=_as_float(record.get("quantity_unit_accuracy")),
                discard_rate=_as_float(record.get("discard_rate")),
                complete_delta=delta,
            )
        )
        if complete is not None:
            previous_complete = complete

    return summaries


def main() -> None:
    """Punto de entrada: `python -m saimc.extraction.measure`.

    Registra la corrida en `docs/h0-runs.jsonl` salvo `--no-record`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m saimc.extraction.measure",
        description="Mide H0 (spec.md §12.5) contra la cola de curaduría real.",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help=f"No persistir la corrida en docs/{_H0_RUNS_FILENAME} (N-05).",
    )
    args = parser.parse_args()

    with session_scope() as session:
        report = compute_h0_report(
            session, assist_log_path=get_settings().llm_assist_log_path
        )
    _print_report(report)

    if args.no_record:
        return

    commit, dirty, branch = _git_revision()
    record = h0_run_record(
        report,
        now=datetime.now(UTC),
        commit=commit,
        dirty=dirty,
        branch=branch,
        tesseract_version=_tesseract_version(),
    )
    path = _h0_runs_path()
    append_h0_run(record, path)

    print()
    if dirty:
        print(
            "AVISO: árbol de trabajo sucio — este número no es atribuible a un "
            "commit limpio. Quedó registrado igual, con git_dirty=true."
        )
    print(f"Corrida registrada en {path} (commit {commit}).")


if __name__ == "__main__":
    main()
