"""Calidad del texto del OCR frente al texto nativo, sobre el conjunto de referencia.

Solo lectura: no escribe en la base ni en `docs/h0-runs.jsonl`. Parte las filas
congeladas del conjunto de referencia por tipo de contenido y mide, sobre el
texto que ve el flujo de texto (OCR guardado para imágenes, texto nativo para
textos):

- el precio confirmado por la persona aparece como número en el texto;
- el extractor por reglas acierta producto / precio / completo (misma
  definición que H0, `measure.row_correctness`).

No es H0: H0 sigue corriendo `python -m saimc.extraction.measure`. Esto solo
parte ese mismo conjunto por tipo. Uso:

    python scripts/ocr_vs_native.py
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from saimc.db import session_scope
from saimc.extraction import measure, ocr, ocr_store
from saimc.extraction.engine import propose_all
from saimc.models import ContentType, OcrTranscription, RawIngestion
from saimc.progress import pilot_products
from saimc.ingestion import origin

_NUM = re.compile(r"\d[\d.,]*")


def price_in_text(price: Decimal, text: str) -> bool:
    """¿Aparece el precio entero como número en el texto (con o sin separadores)?"""
    target = str(int(price))
    for token in _NUM.findall(text):
        if re.sub(r"[.,]", "", token) == target:
            return True
    return False


def main() -> None:
    reference_set = measure._reference_set()
    with session_scope() as session:
        version = reference_set.current_version(session)
        members = reference_set.members_of(session, version)
        ingestions = {
            i.id: i
            for i in session.execute(
                select(RawIngestion).where(
                    RawIngestion.id.in_([m.ingestion_id for m in members])
                )
            ).scalars()
        }
        stored = {
            t.ingestion_id: t.text
            for t in session.execute(
                select(OcrTranscription).where(OcrTranscription.ocr_version == ocr.OCR_VERSION)
            ).scalars()
        }
        products = pilot_products(session)
        provinces = measure._all_provinces(session)
        acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for member in members:
            rows = measure.frozen_rows(member)
            if not rows:
                continue
            ing = ingestions[member.ingestion_id]
            tipo = str(ing.content_type)
            if ing.content_type == ContentType.IMAGE:
                text = stored.get(ing.id)
                if text is None:
                    text = ocr_store.stored_transcription(session, ing, measure.get_settings().raw_store_dir)
                    acc[tipo]["ocr_calculado_al_vuelo"] += 1
            else:
                text = ing.original_text or ""
            acc[tipo]["anuncios"] += 1
            if not text.strip():
                acc[tipo]["sin_texto"] += 1
            channel_province = origin.channel_province(session, ing)
            cands = propose_all(text, products, provinces, channel_province=channel_province)
            for confirmed, proposal in measure._match_rows_to_candidates(
                rows, cands, channel_province
            ):
                acc[tipo]["filas"] += 1
                acc[tipo]["precio_en_texto"] += price_in_text(confirmed.price, text)
                ok = measure.row_correctness(confirmed, proposal)
                acc[tipo]["producto_ok"] += ok.product
                acc[tipo]["precio_ok"] += ok.price
                acc[tipo]["completo_ok"] += ok.complete
        session.rollback()
    print(f"conjunto de referencia v{version}, OCR {ocr.OCR_VERSION}")
    for tipo, c in sorted(acc.items()):
        n = c["filas"] or 1
        print(
            f"{tipo}: {c['anuncios']} anuncios, {c['filas']} filas, sin texto {c['sin_texto']}, "
            f"OCR al vuelo {c['ocr_calculado_al_vuelo']}\n"
            f"  precio aparece en el texto {c['precio_en_texto'] / n:.1%} | "
            f"producto {c['producto_ok'] / n:.1%} | precio {c['precio_ok'] / n:.1%} | "
            f"completo {c['completo_ok'] / n:.1%}"
        )


if __name__ == "__main__":
    main()
