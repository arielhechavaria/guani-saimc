"""Tipos de entrada y de salida del motor de validación (`validation.engine`).

Todo es dato ya leído: quien llama al motor (el informe de sombra en A5c, el
guardado en B1, el pre-cálculo en B2) arma el `AdContext` consultando la base, y
el motor no vuelve a consultar nada. Contrato: `docs/spec-motor-validacion.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import Product


# `StrEnum`: cada miembro ES un string. `Outcome.PASS == "pass"` da `True` y se
# serializa a JSON sin conversión; es lo más parecido a un enum con respaldo de
# string de PHP (`enum Outcome: string`).
class Outcome(StrEnum):
    """Resultado de una regla. `UNKNOWN` = no hay con qué evaluarla; nunca aprueba."""

    PASS = "pass"  # noqa: S105 — ruff lo toma por una contraseña; es «aprobó»
    FAIL = "fail"
    UNKNOWN = "unknown"


class ValidationStatus(StrEnum):
    """`validation_status` de una fila. Solo `approved` viene de `spec.md` §6.2."""

    APPROVED = "approved"
    INCOMPLETE = "incomplete"
    ANOMALY = "anomaly"
    INCONSISTENT = "inconsistent"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    UNTRUSTED_SOURCE = "untrusted_source"
    UNVERIFIED = "unverified"


#: Los tres carriles. Enteros a secas: el número es el nombre que usan la
#: especificación y el plan.
LANE_DISCARD = 0
LANE_ACCEPT = 1
LANE_REVIEW = 2


@dataclass(frozen=True)
class ValidationConfig:
    """Los parámetros del motor. NINGUNO tiene valor por defecto, a propósito.

    Los fija A5c midiendo (§12.6: ningún umbral se inventa). Quien llama los
    pasa explícitos; si no hay configuración se llama al motor con `None` y todo
    anuncio va al carril 2.
    """

    #: Referencias mínimas en la celda para que las reglas 2 y 6 opinen.
    min_cell_n: int
    #: Decisiones individuales mínimas en el canal para que la regla 4 opine.
    channel_min_history: int
    #: Tope de la fracción de filas propuestas que el operador editó o borró
    #: para que el canal cuente como «limpio» (0..1).
    channel_max_edit_rate: float
    #: Desde qué probabilidad un desacuerdo de una señal externa saca del
    #: carril 1. Lo fija A6b; `None` = sin fijar, y entonces cualquier
    #: desacuerdo saca (la lectura conservadora).
    signal_high_probability: float | None


@dataclass(frozen=True)
class ChannelContext:
    """El canal del anuncio y su historial (regla 3 y regla 4)."""

    #: Provincia fija del canal, si tiene una cargada.
    province_id: uuid.UUID | None
    #: Decisiones INDIVIDUALES del operador sobre anuncios del canal. Los
    #: descartes aceptados en lote no cuentan (pregunta 5 de la especificación).
    decisions: int
    #: Filas que se le propusieron al operador en esas decisiones.
    proposed_rows: int
    #: De esas, las que editó o borró.
    changed_rows: int


@dataclass(frozen=True)
class CellReference:
    """Precios de referencia de la celda de una fila (reglas 2 y 6).

    Ya normalizados como `cells.compute_cells`: en CUP y por unidad base del
    producto. **Solo los anteriores a la fecha del anuncio**: filtrarlos es de
    quien arma el contexto, porque acá llegan sin fecha.
    """

    #: Celda producto × provincia de la fila × unidad base.
    own_province: Sequence[Decimal]
    #: Mismo producto y unidad en todas las provincias: el repliegue.
    all_provinces: Sequence[Decimal]


@dataclass(frozen=True)
class RowContext:
    """Lo que el motor necesita de afuera para UNA fila de la propuesta."""

    cell: CellReference
    #: El precio de la fila llevado a CUP con la tasa de la fecha del anuncio,
    #: cuando la fila viene en divisa. Sin esto una fila en divisa no se puede
    #: comparar con su celda. No es un parámetro: es un dato de la fila.
    price_cup: Decimal | None = None


@dataclass(frozen=True)
class RecentRow:
    """Una fila ya guardada del mismo canal (regla 5)."""

    product_id: uuid.UUID
    province_id: uuid.UUID | None
    price: Decimal
    published_at: datetime


#: Las únicas preguntas que una señal externa puede contestar: las que tienen
#: sus opciones en un catálogo del sistema. Nada sobre el precio.
SIGNAL_FIELDS = ("product", "currency", "province", "sale_format")


@dataclass(frozen=True)
class ExternalSignal:
    """Respuesta de un proveedor externo a UNA pregunta cerrada sobre una fila.

    Es solo el contrato de entrada: A5b no trae ningún proveedor (Jev entra por
    A6b si pasa A6). El motor compara `value` con lo extraído y lo lee como
    acuerdo o desacuerdo. Una señal mal formada equivale a «sin señal».
    """

    #: Índice de la fila en `AdProposal.rows`.
    row_index: int
    #: Uno de `SIGNAL_FIELDS`.
    field: str
    #: La opción elegida: el id del producto o de la provincia, o el código de
    #: la moneda o del formato de venta.
    value: object
    #: Probabilidad que el proveedor le da a esa opción (0..1).
    probability: float


@dataclass(frozen=True)
class AdContext:
    """Todo lo que el motor necesita de un anuncio, ya leído."""

    #: `"text"` o `"image"` (`ContentType`).
    content_type: str
    #: El texto del anuncio, o la transcripción del OCR si es una imagen.
    text: str
    #: Fecha del anuncio. Con zona horaria, igual que las de `recent_channel_rows`.
    published_at: datetime
    #: `None` si el anuncio no tiene canal: las reglas 3 y 4 quedan en `unknown`.
    channel: ChannelContext | None
    #: La propuesta del modelo, o `None` si no hubo (sin Claude).
    model: AdProposal | None
    #: La propuesta de las reglas (`engine.propose_all`).
    rule_rows: Sequence[ExtractionProposal]
    #: Los productos piloto del catálogo.
    pilot_products: Sequence[Product]
    #: Los ids del catálogo de provincias.
    province_ids: frozenset[uuid.UUID]
    #: Un contexto por fila de `model.rows`, mismo índice y misma longitud.
    row_contexts: Sequence[RowContext] = ()
    #: Filas ya guardadas del mismo canal. El motor aplica la ventana de 24 h.
    recent_channel_rows: Sequence[RecentRow] = ()
    signals: Sequence[ExternalSignal] = ()


@dataclass(frozen=True)
class RowValidation:
    """El resultado del motor para una fila."""

    structure: Outcome
    price: Outcome
    location: Outcome
    trust: Outcome
    duplicate: Outcome
    quantity_price: Outcome
    status: ValidationStatus
    #: Reglas en `pass` sobre seis, por cien. Descriptivo: ningún carril lo usa.
    confidence_score: int
    #: Por qué esta fila, por sí sola, no deja entrar al carril 1. Vacío si
    #: cumple las cuatro condiciones por fila.
    reasons: tuple[str, ...] = ()

    def detail(self) -> dict[str, object]:
        """El JSON de `spec.md` §6.2, con `pass|fail|unknown` y `quantity_price`."""
        return {
            "validation_status": self.status.value,
            "confidence_score": self.confidence_score,
            "rules": {
                "structure": self.structure.value,
                "price": self.price.value,
                "location": self.location.value,
                "trust": self.trust.value,
                "duplicate": self.duplicate.value,
                "quantity_price": self.quantity_price.value,
            },
        }


@dataclass(frozen=True)
class AdValidation:
    """El resultado del motor para un anuncio."""

    #: 0 (descarte automático), 1 (aceptación automática) o 2 (revisión humana).
    lane: int
    #: Motivos legibles que explican el carril.
    reasons: tuple[str, ...]
    #: Una por fila de la propuesta; vacío si no hay propuesta utilizable o si
    #: el modelo descartó el anuncio.
    rows: tuple[RowValidation, ...] = ()
