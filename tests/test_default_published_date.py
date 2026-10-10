"""Fecha de publicación por defecto en calendario de La Habana (T2).

Los esperados están escritos a mano, sin correr el código. Cuba va en UTC-4 en
horario de verano (desde el segundo domingo de marzo hasta el primer domingo de
noviembre) y en UTC-5 el resto del año. En 2026 el horario de verano termina
el domingo 1 de noviembre a la 01:00 hora de verano (05:00 UTC).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from saimc import curation_rows
from saimc.models import ContentType, RawIngestion, SourceType


def _ingesta(
    captured_at: datetime,
    *,
    source_type: SourceType = SourceType.WHATSAPP_CHANNEL,
    content_type: ContentType = ContentType.TEXT,
) -> RawIngestion:
    return RawIngestion(
        source_type=source_type,
        content_type=content_type,
        captured_at=captured_at,
        file_path="raw/x.txt",
        content_hash="x",
    )


@pytest.mark.parametrize(
    ("capturado_utc", "esperado"),
    [
        # Horario de verano (UTC-4), octubre de 2026.
        (datetime(2026, 10, 9, 23, 30, tzinfo=UTC), date(2026, 10, 9)),  # 19:30 del 9
        (datetime(2026, 10, 10, 0, 30, tzinfo=UTC), date(2026, 10, 9)),  # 20:30 del 9
        (datetime(2026, 10, 10, 3, 59, tzinfo=UTC), date(2026, 10, 9)),  # 23:59 del 9
        (datetime(2026, 10, 10, 4, 0, tzinfo=UTC), date(2026, 10, 10)),  # 00:00 del 10
        (datetime(2026, 10, 10, 5, 0, tzinfo=UTC), date(2026, 10, 10)),  # 01:00 del 10
        # Horario de invierno (UTC-5): el corte es a las 05:00 UTC, no a las 04:00.
        (datetime(2027, 1, 15, 4, 30, tzinfo=UTC), date(2027, 1, 14)),  # 23:30 del 14
        (datetime(2027, 1, 15, 5, 0, tzinfo=UTC), date(2027, 1, 15)),  # 00:00 del 15
        # El domingo 1 de noviembre de 2026 termina el verano (05:00 UTC):
        # 04:30 UTC todavía es 00:30 del 1 en UTC-4...
        (datetime(2026, 11, 1, 4, 30, tzinfo=UTC), date(2026, 11, 1)),
        # ...y 04:30 UTC del 2 ya es 23:30 del 1 en UTC-5.
        (datetime(2026, 11, 2, 4, 30, tzinfo=UTC), date(2026, 11, 1)),
    ],
)
def test_un_mensaje_de_whatsapp_se_fecha_en_el_dia_de_la_habana(
    capturado_utc: datetime, esperado: date
) -> None:
    ingesta = _ingesta(capturado_utc)

    assert curation_rows.default_published_date(ingesta, "Arroz a 320 la libra") == esperado


def test_la_foto_de_whatsapp_sin_fecha_en_el_ocr_tambien_cae_en_el_dia_de_la_habana() -> None:
    # A7: la foto lleva la fecha del mensaje; 02:00 UTC de octubre son las 22:00 del día anterior.
    ingesta = _ingesta(
        datetime(2026, 10, 10, 2, 0, tzinfo=UTC), content_type=ContentType.IMAGE
    )

    assert curation_rows.default_published_date(ingesta, "Arroz 320") == date(2026, 10, 9)


def test_la_fecha_del_ocr_sigue_mandando_sobre_la_del_mensaje() -> None:
    # A7: el OCR trae «ayer»; se resuelve contra el día de La Habana (9), no contra el UTC (10).
    ingesta = _ingesta(
        datetime(2026, 10, 10, 2, 0, tzinfo=UTC), content_type=ContentType.IMAGE
    )

    texto = "Juan Pérez - Ayer a las 14:35\nArroz 320"
    assert curation_rows.default_published_date(ingesta, texto) == date(2026, 10, 8)


def test_hoy_en_el_texto_se_resuelve_contra_el_dia_de_la_habana() -> None:
    ingesta = _ingesta(
        datetime(2026, 10, 10, 1, 0, tzinfo=UTC), source_type=SourceType.INBOX_TEXT
    )

    texto = "Juan Pérez - Hoy a las 20:35\nArroz 320"
    assert curation_rows.default_published_date(ingesta, texto) == date(2026, 10, 9)


def test_una_captura_sin_zona_se_lee_como_utc() -> None:
    # SQLite devuelve los datetime sin zona; lo guardado siempre es UTC.
    ingesta = _ingesta(datetime(2026, 10, 10, 2, 0))

    assert curation_rows.default_published_date(ingesta, "Arroz") == date(2026, 10, 9)


def test_una_fuente_que_no_es_whatsapp_sin_patron_de_fecha_sigue_sin_default() -> None:
    ingesta = _ingesta(
        datetime(2026, 10, 10, 2, 0, tzinfo=UTC), source_type=SourceType.INBOX_TEXT
    )

    assert curation_rows.default_published_date(ingesta, "Arroz a 320 la libra") is None
