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
from saimc.models import (
    ContentType,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
)


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
    productos en el mismo anuncio. Lo que no matchea así (el motor no
    reconoció el producto en ninguna línea, o no hay más de un producto)
    cae a emparejamiento posicional con lo que sobre de candidatos: así una
    fila sin match de producto sigue comparándose contra la línea real que
    la originó — precio, cantidad y provincia igual pudieron extraerse bien
    aunque el producto no —, en vez de contra una propuesta vacía.
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
        candidate = remaining.pop(0) if remaining else ExtractionProposal()
        pairs.append((row, candidate))

    return pairs


def _quantity_unit_correct(
    proposal_quantity: Decimal | None,
    proposal_unit: str | None,
    confirmed_quantity: Decimal | None,
    confirmed_product: Product | None,
) -> bool:
    """Cantidad Y unidad, en el sentido del spec.md §12.5.

    La unidad no se persiste por fila en `prices_history` (decisión ya
    tomada, ver `curation.py`): se compara la unidad propuesta contra la
    unidad canónica del producto CONFIRMADO, no del propuesto, porque es
    contra esa unidad que el precio confirmado realmente se interpreta.

    `propose()` extrae cantidad y unidad como un solo par (`_extract_quantity`
    en `engine.py`): nunca hay unidad sin cantidad ni cantidad sin unidad. Por
    eso, cuando no se propuso unidad, no hay nada que comparar contra el
    producto confirmado — la única forma de que ese caso cuente como correcto
    es que el operador tampoco haya confirmado cantidad (None == None, el
    mismo criterio que usa la comparación de cantidad).
    """
    quantity_matches = proposal_quantity == confirmed_quantity
    if proposal_unit is None:
        unit_matches = confirmed_quantity is None
    else:
        confirmed_unit = confirmed_product.unit if confirmed_product is not None else None
        unit_matches = proposal_unit == confirmed_unit
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
    products_by_id = {product.id: product for product in products}

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
        candidates = propose_all(text, products, provinces)

        for confirmed, proposal in _match_rows_to_candidates(confirmed_rows, candidates):
            evaluated_rows_count += 1
            confirmed_product = products_by_id.get(confirmed.product_id)

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
                proposal.quantity, proposal.unit, confirmed.quantity, confirmed_product
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
