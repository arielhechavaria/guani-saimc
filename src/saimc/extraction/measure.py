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

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.extraction import ocr
from saimc.extraction.engine import ExtractionProposal, propose_all
from saimc.ingestion import origin
from saimc.models import (
    ContentType,
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


def _match_rows_to_candidates(
    confirmed_rows: list[PriceHistory], candidates: list[ExtractionProposal]
) -> list[tuple[PriceHistory, ExtractionProposal]]:
    """Empareja cada fila confirmada con el candidato de `propose_all` que le
    corresponde.

    Primero por `product_id` — es la señal más confiable cuando hay varios
    productos en el mismo anuncio.

    Lo que no matchea así se reparte según cuántas líneas hayan quedado
    libres, porque no es lo mismo no tener elección que tener que adivinar:

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
    pairs: list[tuple[PriceHistory, ExtractionProposal]] = []
    unmatched_rows: list[PriceHistory] = []

    for row in confirmed_rows:
        match = next(
            (c for c in remaining if c.product is not None and c.product.id == row.product_id),
            None,
        )
        if match is not None:
            remaining.remove(match)
            pairs.append((row, match))
        else:
            unmatched_rows.append(row)

    for row in unmatched_rows:
        if not remaining:
            pairs.append((row, ExtractionProposal()))
        elif len(remaining) == 1:
            pairs.append((row, remaining.pop(0)))
        else:
            pairs.append((row, ExtractionProposal(province=_shared_province(remaining))))

    return pairs


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
    session: Session, transcribe: Transcriber = ocr.transcribe
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
    discard_rate = (
        total_discarded_count / total_processed if total_processed > 0 else None
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

    for ingestion in ingestions:
        confirmed_rows = history_by_source.get(ingestion.id, [])
        if not confirmed_rows:
            continue
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
            discard_rate=discard_rate,
            text_curated_count=text_curated_count,
            image_curated_count=image_curated_count,
            image_ocr_empty_count=image_ocr_empty_count,
            evaluated_rows_count=0,
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
        discard_rate=discard_rate,
        text_curated_count=text_curated_count,
        image_curated_count=image_curated_count,
        image_ocr_empty_count=image_ocr_empty_count,
        evaluated_rows_count=evaluated_rows_count,
        product_accuracy=product_correct / evaluated_rows_count,
        price_accuracy=price_correct / evaluated_rows_count,
        province_accuracy=province_correct / evaluated_rows_count,
        quantity_unit_accuracy=quantity_unit_correct / evaluated_rows_count,
        complete_accuracy=complete_correct / evaluated_rows_count,
    )


# Umbral real de H0 (spec.md §3): la medición no es oficial por debajo de
# 300 anuncios etiquetados a mano.
_H0_MINIMUM_SAMPLE = 300


def _format_pct(value: float | None) -> str:
    return "sin datos" if value is None else f"{value:.1%}"


def _print_report(report: H0Report) -> None:
    print("=== Medición de H0 (spec.md §12.5) ===")
    print(f"Procesados totales:      {report.total_processed}")
    print(f"  Curados:               {report.total_curated}")
    print(f"  Descartados:           {report.total_discarded}")
    print(f"  Tasa de descarte:      {_format_pct(report.discard_rate)}")
    print()
    print(f"Curados de texto:        {report.text_curated_count} anuncios")
    print(f"Curados de imagen:       {report.image_curated_count} anuncios (OCR re-corrido)")
    print(f"  Sin texto del OCR:     {report.image_ocr_empty_count}")
    print(f"Filas evaluadas:         {report.evaluated_rows_count} (un anuncio puede tener varias)")
    print()
    print(f"Precisión de producto:        {_format_pct(report.product_accuracy)}")
    print(f"Precisión de precio:          {_format_pct(report.price_accuracy)}")
    print(f"Precisión de provincia:       {_format_pct(report.province_accuracy)}")
    print(f"Precisión de cantidad/unidad: {_format_pct(report.quantity_unit_accuracy)}")
    print(f"Extracción completa correcta: {_format_pct(report.complete_accuracy)}")

    if report.evaluated_rows_count < _H0_MINIMUM_SAMPLE:
        print()
        print(
            f"AVISO: solo hay {report.evaluated_rows_count} filas curadas "
            f"(umbral H0: {_H0_MINIMUM_SAMPLE}). Este número todavía NO es la medición "
            "oficial de H0."
        )


def main() -> None:
    """Punto de entrada: `python -m saimc.extraction.measure`."""
    with session_scope() as session:
        report = compute_h0_report(session)
    _print_report(report)


if __name__ == "__main__":
    main()
