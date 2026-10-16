"""La cita de una propuesta guardada se vuelve a verificar al leerla.

Las banderas `evidence_found` y `price_in_evidence` se guardan al proponer. Las
propuestas hechas antes de que la verificación entendiera emoji y «...» quedaron
con la bandera en `False` aunque el texto sea honesto. El esperado está escrito
a mano: el anuncio dice «Arroz 320 CUP la libra» y la cita lo copia sin el emoji.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    LlmProposal,
    ProcessingStatus,
    Product,
    RawIngestion,
    SourceType,
)
from saimc.validation import context


def _guardar(session: Session, texto: str, evidencia: str, **banderas: Any) -> LlmProposal:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    session.add(arroz)
    session.flush()
    ingesta = RawIngestion(
        source_type=SourceType.WHATSAPP_CHANNEL,
        file_path="raw/cita-1.txt",
        content_hash="cita-1",
        content_type=ContentType.TEXT,
        original_text=texto,
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    session.add(ingesta)
    session.flush()
    fila = {
        "product_id": str(arroz.id),
        "price": "320",
        "currency": "CUP",
        "quantity": "1",
        "unit": "lb",
        "province_id": None,
        "evidence": evidencia,
        "currency_explicit": True,
        "evidence_found": False,
        "price_in_evidence": False,
    }
    fila.update(banderas)
    propuesta = LlmProposal(
        ingestion_id=ingesta.id,
        model="modelo-de-prueba",
        prompt_version="prueba",
        status="ok",
        discard=False,
        discard_reason=None,
        rows=[fila],
        raw_response=None,
        input_tokens=0,
        output_tokens=0,
    )
    session.add(propuesta)
    session.commit()
    return propuesta


def test_una_cita_guardada_como_no_hallada_se_verifica_de_nuevo(session: Session) -> None:
    propuesta = _guardar(session, "Arroz‼️\n▪️320 CUP la libra", "Arroz ▪️320 CUP la libra")

    (meta,) = context.stored_proposal(session, propuesta).row_meta

    assert (meta.evidence_found, meta.price_in_evidence) == (True, True)


def test_una_cita_que_el_texto_no_dice_sigue_sin_hallarse(session: Session) -> None:
    propuesta = _guardar(session, "Arroz 320 CUP la libra", "Arroz 500 CUP la libra")

    (meta,) = context.stored_proposal(session, propuesta).row_meta

    assert meta.evidence_found is False


def test_verificar_de_nuevo_nunca_baja_una_bandera_ya_verdadera(session: Session) -> None:
    # Guardada como hallada: aunque hoy no coincida, no se degrada.
    propuesta = _guardar(
        session,
        "Arroz 320 CUP la libra",
        "Arroz 500 CUP la libra",
        evidence_found=True,
        price_in_evidence=True,
    )

    (meta,) = context.stored_proposal(session, propuesta).row_meta

    assert (meta.evidence_found, meta.price_in_evidence) == (True, True)
