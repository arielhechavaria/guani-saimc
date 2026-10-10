"""Medición de H0 (spec.md §3 y §12.5) contra el conjunto de referencia congelado.

Re-corre `propose_all()` sobre cada miembro de la versión vigente del conjunto
(`reference_set_members`, `docs/spec-slice-B3.md` sección 3) y compara la
propuesta contra las filas que se congelaron con él. No lee `prices_history`:
editar o borrar un anuncio congelado cambia lo publicado, no la referencia. Sin
conjunto congelado no se mide (`NoReferenceSetError`): no hay camino de vuelta
al oro vivo, que se rearmaba en cada corrida y se movía con cada edición.

Lo que sí sale de la base viva es la composición del corpus: curados,
descartados y la tasa de descarte describen lo que entró, no la referencia.

De dónde sale el texto evaluado, según el tipo de evidencia:

- **Texto:** `original_text`, escrito en la ingesta y nunca tocado después.
- **Imagen:** se re-corre el OCR sobre la evidencia original preservada. NO se
  usa `original_text`, porque en una imagen ese campo guarda la transcripción
  ya corregida por el operador (`saimc/web/routers/curation.py`): medir sobre
  ella evaluaría las reglas contra un texto que un humano limpió, y H0 (§3)
  pregunta justamente si el pipeline sostiene el volumen SIN esa intervención.
  El archivo original es inmutable, así que la transcripción es reproducible.

La propuesta se re-corre con el MISMO contexto que tiene la cola de curaduría
real, no con menos. Medir con menos no era más exigente: era medir otro
extractor, uno que en producción no existe. Dos insumos entran por eso:

- **La provincia fija del canal**, por `channel_province` (docs/decisions.md,
  2026-08-17 y 2026-08-21).
- **El catálogo del foco**, por `_focus_products` (docs/decisions.md,
  2026-09-09). La cola le pasa a `propose_all` solo los productos con
  `is_pilot` (`_pilot_products` en `web/routers/curation.py`: «único punto por
  donde el catálogo entra a la curaduría»); acá se consultaba `select(Product)`
  sin filtro. Al 2026-09-09 eran 60 productos contra los 5 de producción, y los
  55 de más no son inertes: `_match_product` devuelve el PRIMER producto de la
  lista que matchea, así que un artefacto de parseo del catálogo —`Precio :`,
  que aparece en casi todos los anuncios— le robaba el match al piloto y la
  fila se contaba como fallo.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Protocol

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
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReferenceSetMember,
)
from saimc.progress import pilot_products
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
    # Eran los anuncios y filas que la corrida dejaba fuera por haber pasado
    # por el asistente de LLM. Desde B3-2 la pertenencia la fija el congelado,
    # que ya los deja afuera: van siempre en 0, y se siguen escribiendo para no
    # romper a quien lee `docs/h0-runs.jsonl`.
    assisted_ads_count: int
    assisted_rows_count: int
    product_accuracy: float | None
    price_accuracy: float | None
    province_accuracy: float | None
    quantity_unit_accuracy: float | None
    complete_accuracy: float | None
    # Contra qué se midió. Dos corridas solo son comparables si coinciden la
    # versión y la huella (`reference_set.version_hash`).
    reference_set_version: int
    reference_set_hash: str
    # Miembros cuyas filas vivas ya no son las congeladas el día de la corrida.
    # No salen del conjunto ni cortan la corrida: es un aviso.
    reference_set_drifted: int


class NoReferenceSetError(RuntimeError):
    """No hay ningún conjunto de referencia congelado contra el que medir."""


class ConfirmedValues(Protocol):
    """Los seis campos de una fila confirmada contra los que se compara.

    `PriceHistory` lo cumple (lo usa el backtest del modelo) y `FrozenRow`
    también. Un `Protocol` es tipado estructural: alcanza con tener estos
    atributos, sin heredar de nada.
    """

    @property
    def product_id(self) -> uuid.UUID: ...
    @property
    def province_id(self) -> uuid.UUID | None: ...
    @property
    def price(self) -> Decimal: ...
    @property
    def currency(self) -> str: ...
    @property
    def quantity(self) -> Decimal | None: ...
    @property
    def unit(self) -> str | None: ...


@dataclass(frozen=True)
class FrozenRow:
    """Una fila congelada, en los tipos que espera `row_correctness`.

    `reference_set_members.rows` guarda todo como texto o nulo para que el hash
    no dependa del motor de base. Acá vuelve a `UUID` y `Decimal`: un `"340"`
    de texto no es igual al `Decimal("340")` que propone el extractor.
    """

    product_id: uuid.UUID
    province_id: uuid.UUID | None
    price: Decimal
    currency: str
    quantity: Decimal | None
    unit: str | None


def frozen_rows(member: ReferenceSetMember) -> list[FrozenRow]:
    """Las filas confirmadas de un miembro, en el orden en que se congelaron.

    Ese orden es por `price_history_id`, un UUID al azar: no es el orden en que
    el operador cargó las filas. No cambia el número: el emparejado
    (`_match_rows_to_candidates`) reparte los candidatos por los valores de las
    filas y no por su posición (`docs/decisions.md`, 2026-10-10, «El emparejado
    de H0 deja de depender del orden…»).
    """
    return [
        FrozenRow(
            product_id=uuid.UUID(row["product_id"]),
            province_id=uuid.UUID(row["province_id"]) if row["province_id"] else None,
            price=Decimal(row["price"]),
            currency=row["currency"],
            quantity=Decimal(row["quantity"]) if row["quantity"] is not None else None,
            unit=row["unit"],
        )
        for row in member.rows
    ]


def _focus_products(session: Session) -> list[Product]:
    """El catálogo que ve el motor: el foco, igual que la cola de curaduría.

    Delegado a `saimc.progress.pilot_products` para que la definición del foco
    no vuelva a existir por duplicado — ver el docstring de módulo.
    """
    return pilot_products(session)


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


def _pair_cost(row: ConfirmedValues, candidate: ExtractionProposal) -> float:
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


def _row_order_key(row: ConfirmedValues) -> tuple[tuple[bool, object], ...]:
    """Orden de las filas confirmadas por sus VALORES, no por cómo llegaron.

    Precio, moneda, cantidad, unidad, provincia y, al final, producto, de menor
    a mayor. Es el desempate de todo el emparejado: dos filas con la misma
    clave tienen los mismos seis valores, así que da igual cuál queda primera.

    Cada campo va como `(está vacío, valor)`: un vacío queda al final y nunca
    se compara contra un número. `PriceHistory` y `FrozenRow` siempre traen
    producto y precio, pero `llm_checks` usa este emparejado con las filas del
    modelo como «confirmadas», y ahí pueden faltar.
    """
    values: tuple[object, ...] = (
        row.price,
        row.currency,
        row.quantity,
        row.unit,
        str(row.province_id) if row.province_id is not None else None,
        str(row.product_id) if row.product_id is not None else None,
    )
    return tuple((value is None, value if value is not None else 0) for value in values)


def _assign_within_product[RowT: ConfirmedValues](
    rows: list[RowT], candidates: list[ExtractionProposal]
) -> list[tuple[RowT, ExtractionProposal]]:
    """Empareja filas y candidatos de un MISMO producto por mejor ajuste.

    Se forman `min(filas, candidatos)` pares; cada candidato y cada fila se usan
    una sola vez. Cuando hay más filas que candidatos también se elige **qué
    filas** reciben candidato, con el mismo `_pair_cost`: las que no aparecen
    en el resultado son las que quedaron sin candidato, estén donde estén.

    El resultado no depende del orden en que llegan las filas: se ordenan
    primero por sus valores (`_row_order_key`). Si dos repartos cuestan lo
    mismo, el desempate sale de esa clave y del orden en que el extractor
    propuso los candidatos, que tampoco depende de las filas: con más filas
    que candidatos gana el reparto que le da el primer candidato a la fila de
    menor clave (y si sigue el empate, el segundo, y así); si no, el que le da
    a la fila de menor clave el candidato propuesto antes.

    Con `permutations` cuando los dos conjuntos son chicos —el caso real: 2 o 3
    filas de Arroz por anuncio— para dar el óptimo global; greedy por si algún
    anuncio raro trae muchos de los dos lados: toma de a un par, siempre el de
    menor costo entre los que quedan, con el mismo desempate.
    """
    ordered = sorted(rows, key=_row_order_key)
    k = min(len(ordered), len(candidates))

    if len(ordered) <= 6 and len(candidates) <= 6:
        # `permutations` recorre en orden lexicográfico y `min` se queda con el
        # primer mínimo: ese es el desempate, sin código aparte.
        if len(ordered) <= len(candidates):
            best_candidates = min(
                itertools.permutations(candidates, k),
                key=lambda perm: sum(_pair_cost(ordered[i], perm[i]) for i in range(k)),
            )
            return list(zip(ordered, best_candidates, strict=True))
        best_rows = min(
            itertools.permutations(ordered, k),
            key=lambda perm: sum(_pair_cost(perm[i], candidates[i]) for i in range(k)),
        )
        return list(zip(best_rows, candidates, strict=True))

    by_cost = sorted(
        (_pair_cost(row, candidate), candidate_index, row_index)
        for row_index, row in enumerate(ordered)
        for candidate_index, candidate in enumerate(candidates)
    )
    used_rows: set[int] = set()
    used_candidates: set[int] = set()
    pairs = []
    for _, candidate_index, row_index in by_cost:
        if row_index in used_rows or candidate_index in used_candidates:
            continue
        used_rows.add(row_index)
        used_candidates.add(candidate_index)
        pairs.append((ordered[row_index], candidates[candidate_index]))
    return pairs


def _match_rows_to_candidates[RowT: ConfirmedValues](
    confirmed_rows: list[RowT],
    candidates: list[ExtractionProposal],
    channel_province: Province | None = None,
) -> list[tuple[RowT, ExtractionProposal]]:
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

    Lo mismo cuando un producto tiene **más filas confirmadas que candidatos**
    —el operador cargó menudeo y saco, el extractor sacó una sola línea—: qué
    filas reciben candidato se elige por mejor ajuste, no por posición. Antes
    lo recibían «las primeras», y desde que las filas congeladas vienen
    ordenadas por un UUID al azar (B3-2) eso era un sorteo: la fila que el
    extractor había leído bien podía quedar sin candidato y contar como fallo
    mientras su candidato se comparaba contra la otra. Tampoco acá se toma
    nada prestado: los candidatos que se reparten son solo los de ese
    producto, y son menos que las filas, así que cada fila que queda sin
    candidato sigue fallando producto y precio como antes. Elegir bien no
    agrega candidatos, solo deja de gastar uno correcto en la fila equivocada
    (`docs/decisions.md`, 2026-10-10).

    **El resultado no depende del orden en que llegan las filas confirmadas.**
    Todo desempate sale de los valores de las filas (`_row_order_key`), nunca
    de su posición; dos filas con los seis valores iguales son intercambiables.

    Lo que no matchea por `product_id` se reparte según cuántas líneas hayan
    quedado libres, porque no es lo mismo no tener elección que tener que
    adivinar:

    - **Una sola línea libre**: se empareja con ella. No hubo elección, y
      precio, cantidad y provincia igual pudieron extraerse bien aunque el
      producto no se reconociera. Si son varias las filas sin candidato, se la
      lleva la primera por valores (`_row_order_key`) y no la que mejor
      ajusta: la línea no es de su producto, y elegirle fila por parecido sería
      regalar un precio por coincidencia.
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
    - **Ninguna línea libre**: propuesta sin producto ni precio, pero **con la
      provincia del canal** si el anuncio viene de uno. La cola le pone esa
      provincia a todas las filas del formulario sin mirar si el motor propuso
      algo para ellas (`curation.py`: `row["province_id"] = channel_province_id`
      sobre `rows` entero, y también sobre la fila en blanco que el operador
      agrega a mano). Devolver `ExtractionProposal()` pelada contaba como fallo
      una provincia que en producción siempre está puesta — el 12.9% de las
      filas de texto al 2026-09-09.

    Ninguna línea se consume en el caso ambiguo: no se usó, así que sigue
    disponible para la fila siguiente.

    Lo usan también el backtest del modelo (`llm_backtest.py`) y
    `llm_checks.match_rule_rows`.
    """
    remaining = list(candidates)
    matched: dict[int, ExtractionProposal] = {}
    unmatched_rows: list[RowT] = []

    # Agrupado por producto, en el orden en que cada producto aparece por
    # primera vez en las filas confirmadas.
    rows_by_product: dict[object, list[RowT]] = {}
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
        # Sin candidato quedan las filas no elegidas, no las últimas.
        unmatched_rows.extend(row for row in rows if id(row) not in matched)

    # Por valores y no por llegada: con una sola línea libre para varias filas,
    # quién se la lleva no puede depender del orden.
    for row in sorted(unmatched_rows, key=_row_order_key):
        if not remaining:
            matched[id(row)] = ExtractionProposal(province=channel_province)
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


@dataclass(frozen=True)
class RowCorrectness:
    """Qué campos de una fila confirmada acertó una propuesta.

    Es LA definición de «correcto» de H0: la usan `compute_h0_report` y el
    backtest del LLM (`llm_backtest`) para que sus números sean comparables.
    """

    product: bool
    price: bool
    province: bool
    quantity_unit: bool

    @property
    def complete(self) -> bool:
        return self.product and self.price and self.province and self.quantity_unit


def row_correctness(
    confirmed: ConfirmedValues, proposal: ExtractionProposal
) -> RowCorrectness:
    """Compara una propuesta contra la fila que el operador confirmó."""
    proposed_province_id = proposal.province.id if proposal.province is not None else None
    return RowCorrectness(
        product=(
            proposal.product is not None and proposal.product.id == confirmed.product_id
        ),
        # Valor Y moneda.
        price=proposal.price == confirmed.price and proposal.currency == confirmed.currency,
        province=proposed_province_id == confirmed.province_id,
        quantity_unit=_quantity_unit_correct(
            proposal.quantity, proposal.unit, confirmed.quantity, confirmed.unit
        ),
    )


def assisted_ingestion_ids(session: Session, assist_log_path: Path | None) -> set[str]:
    """Las ingestas que no son oro: las del registro del operador o las que
    tienen una revisión capturada en `llm_reviews` (slice 3a).

    La usan el congelado del conjunto de referencia
    (`reference_set.select_members`) y el backtest (`llm_backtest.assisted_ids`)
    para que los dos excluyan exactamente lo mismo.
    """
    del_registro = (
        llm_assist.assisted_ingestion_ids(assist_log_path)
        if assist_log_path is not None
        else set()
    )
    revisados = {
        str(ingestion_id)
        for ingestion_id in session.execute(select(LlmReview.ingestion_id)).scalars()
    }
    return del_registro | revisados


def _reference_set() -> ModuleType:
    """`saimc.extraction.reference_set`, importado recién al usarlo.

    Ese módulo importa `assisted_ingestion_ids` de acá: importarlo arriba
    cerraría el ciclo y rompería el arranque de los dos.
    """
    from saimc.extraction import reference_set

    return reference_set


def _ratio(correct: int, total: int) -> float | None:
    return correct / total if total else None


def compute_h0_report(
    session: Session,
    transcribe: Transcriber = ocr.transcribe,
) -> H0Report:
    """Calcula el reporte de H0 re-corriendo `propose_all()` sobre los miembros
    de la versión vigente del conjunto de referencia.

    Evalúa por FILA congelada, no por anuncio: un anuncio multi-producto
    (`docs/decisions.md`) tiene varias filas, cada una es un dato de H0 aparte.

    Levanta `NoReferenceSetError` si nunca se congeló un conjunto.
    """
    reference_set = _reference_set()
    version = reference_set.current_version(session)
    if version is None:
        raise NoReferenceSetError("No hay ningún conjunto de referencia congelado.")
    members: list[ReferenceSetMember] = reference_set.members_of(session, version)

    # Composición del corpus: sale de la base viva, no del conjunto.
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

    # El anuncio de cada miembro existe siempre (FK con RESTRICT), cualquiera
    # sea su estado de hoy: uno descartado después del congelado se sigue
    # evaluando.
    ingestions = {
        ingestion.id: ingestion
        for ingestion in session.execute(
            select(RawIngestion).where(
                RawIngestion.id.in_([member.ingestion_id for member in members])
            )
        ).scalars()
    }
    image_ocr_empty_count = 0

    products = _focus_products(session)
    provinces = _all_provinces(session)

    product_correct = 0
    price_correct = 0
    province_correct = 0
    quantity_unit_correct = 0
    complete_correct = 0
    evaluated_rows_count = 0
    evaluated_ads_count = 0

    for member in members:
        confirmed_rows = frozen_rows(member)
        if not confirmed_rows:
            continue
        ingestion = ingestions[member.ingestion_id]
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
        channel_province = origin.channel_province(session, ingestion)
        candidates = propose_all(
            text,
            products,
            provinces,
            channel_province=channel_province,
        )

        # La misma provincia va al emparejado: una fila que se queda sin línea
        # libre la conserva igual, porque el formulario también se la pone.
        for confirmed, proposal in _match_rows_to_candidates(
            confirmed_rows, candidates, channel_province
        ):
            evaluated_rows_count += 1

            ok = row_correctness(confirmed, proposal)
            if ok.product:
                product_correct += 1
            if ok.price:
                price_correct += 1
            if ok.province:
                province_correct += 1
            if ok.quantity_unit:
                quantity_unit_correct += 1
            if ok.complete:
                complete_correct += 1

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
        assisted_ads_count=0,
        assisted_rows_count=0,
        product_accuracy=_ratio(product_correct, evaluated_rows_count),
        price_accuracy=_ratio(price_correct, evaluated_rows_count),
        province_accuracy=_ratio(province_correct, evaluated_rows_count),
        quantity_unit_accuracy=_ratio(quantity_unit_correct, evaluated_rows_count),
        complete_accuracy=_ratio(complete_correct, evaluated_rows_count),
        reference_set_version=version,
        reference_set_hash=reference_set.version_hash(session, version),
        reference_set_drifted=len(reference_set.drift_report(session, version)),
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


def _print_report(report: H0Report, out: Callable[[str], None] = print) -> None:
    out(
        "=== H0 — extractor sobre el conjunto congelado, "
        f"versión {report.reference_set_version} (spec.md §12.5) ==="
    )
    out(f"Hash del conjunto:       {report.reference_set_hash}")
    out(f"Miembros con deriva:     {report.reference_set_drifted}")
    if report.reference_set_drifted:
        out("  Sus filas vivas ya no son las congeladas. H0 mide contra las congeladas.")
    out("")
    out("Corpus (de la base viva, no del conjunto):")
    out(f"Procesados totales:      {report.total_processed}")
    out(f"  Curados:               {report.total_curated}")
    out(f"    de texto:            {report.text_curated_count}")
    out(f"    de imagen:           {report.image_curated_count}")
    out(f"  Descartados:           {report.total_discarded}")
    if report.discarded_off_focus:
        out(
            f"    fuera de foco:       {report.discarded_off_focus} "
            "(con dato, no piloto/mayorista — no cuentan en la tasa)"
        )
    out(
        f"  Tasa de descarte:      {_format_pct(report.discard_rate)}  "
        "(sin dato útil, §12.5)"
    )
    out("")
    out(f"Anuncios evaluados:      {report.evaluated_ads_count} (el conjunto de referencia)")
    out(f"  Sin texto del OCR:     {report.image_ocr_empty_count} (imágenes, OCR re-corrido)")
    out(f"Filas evaluadas:         {report.evaluated_rows_count} (un anuncio puede tener varias)")
    out("")
    out(f"Precisión de producto:        {_format_pct(report.product_accuracy)}")
    out(f"Precisión de precio:          {_format_pct(report.price_accuracy)}")
    out(f"Precisión de provincia:       {_format_pct(report.province_accuracy)}")
    out(f"Precisión de cantidad/unidad: {_format_pct(report.quantity_unit_accuracy)}")
    out(f"Extracción completa correcta: {_format_pct(report.complete_accuracy)}")

    if report.evaluated_ads_count < _H0_MINIMUM_ADS:
        faltan = _H0_MINIMUM_ADS - report.evaluated_ads_count
        out("")
        out(
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
        completed = subprocess.run(  # noqa: S603 — sin shell, args fijos
            ["git", *args],  # noqa: S607 — "git" del PATH, no una ruta externa
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

    Los campos del `H0Report` viajan tal cual (`asdict`): las métricas y
    contra qué conjunto se midieron (`reference_set_version`,
    `reference_set_hash`, `reference_set_drifted`). Más el contexto que las
    hace comparables entre versiones del código: cuándo, qué commit, si el
    árbol estaba limpio, qué rama y qué Tesseract. `is_official` usa el
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

    Subconjunto de las claves del registro completo: la vista del operador
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
    # haya registrado un número. `None` si es la primera con número, si esta
    # corrida no lo tiene, o si las dos no midieron contra el mismo conjunto de
    # referencia: esa diferencia no es atribuible al extractor. Una corrida
    # "sin datos" en el medio no corta la comparación entre las que sí lo
    # tienen.
    complete_delta: float | None
    # Versión del conjunto congelado contra el que se midió. `None` en las
    # corridas anteriores a B3-2, que midieron contra el oro vivo de su día.
    reference_set_version: int | None


def _as_float(value: object) -> float | None:
    """`float` de un número JSON; `None` para `null`, texto o `bool`."""
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _reference_set_key(record: dict[str, object]) -> tuple[int, str] | None:
    """Contra qué conjunto midió una corrida: versión y huella.

    `None` para una línea vieja, sin versión. Dos líneas viejas se siguen
    comparando entre sí, como hasta ahora; una vieja y una nueva, nunca. La
    huella entra porque otra base (la de desarrollo) también tiene versión 1.
    """
    version = record.get("reference_set_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    return version, str(record.get("reference_set_hash", ""))


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
    previous_set: tuple[int, str] | None = None
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
        reference_set_key = _reference_set_key(record)
        delta = (
            complete - previous_complete
            if complete is not None
            and previous_complete is not None
            and reference_set_key == previous_set
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
                reference_set_version=reference_set_key[0] if reference_set_key else None,
            )
        )
        if complete is not None:
            previous_complete = complete
            previous_set = reference_set_key

    return summaries


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.extraction.measure",
        description=(
            "Mide H0 (spec.md §12.5): el extractor por reglas contra el conjunto "
            "de referencia congelado."
        ),
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help=f"No persistir la corrida en docs/{_H0_RUNS_FILENAME} (N-05).",
    )
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    session: Session,
    *,
    runs_path: Path,
    transcribe: Transcriber = ocr.transcribe,
    out: Callable[[str], None] = print,
) -> int:
    """Mide y, salvo `--no-record`, registra. Devuelve el código de salida.

    Sin conjunto congelado no mide ni escribe: devuelve 1.
    """
    try:
        report = compute_h0_report(session, transcribe=transcribe)
    except NoReferenceSetError as error:
        out(
            f"{error} H0 se mide solo contra un conjunto congelado. "
            f"Se congela con: {_reference_set().COMMAND} --execute "
            "(docs/runbook-medir-h0.md, «Congelar el conjunto»)."
        )
        out(f"No se midió nada y no se escribió en {runs_path}.")
        return 1
    _print_report(report, out)

    if args.no_record:
        return 0

    commit, dirty, branch = _git_revision()
    record = h0_run_record(
        report,
        now=datetime.now(UTC),
        commit=commit,
        dirty=dirty,
        branch=branch,
        tesseract_version=_tesseract_version(),
    )
    append_h0_run(record, runs_path)

    out("")
    if dirty:
        out(
            "AVISO: árbol de trabajo sucio — este número no es atribuible a un "
            "commit limpio. Quedó registrado igual, con git_dirty=true."
        )
    out(f"Corrida registrada en {runs_path} (commit {commit}).")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    """Punto de entrada: `python -m saimc.extraction.measure`.

    Registra la corrida en `docs/h0-runs.jsonl` salvo `--no-record`.
    """
    args = parse_args(argv)
    with session_scope() as session:
        code = run(args, session, runs_path=_h0_runs_path())
    sys.exit(code)


if __name__ == "__main__":
    main()
