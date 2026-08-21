"""Resolución del canal de origen de una ingesta.

Esta lógica vivía dentro de `web/routers/curation.py` como dos funciones
privadas. Se movió acá sin cambiarle el comportamiento porque la medición de
H0 (`extraction/measure.py`) necesita exactamente la misma regla, y la capa de
extracción no puede importar de un router web sin invertir la dependencia.

Los casos son los mismos que ya cubría `test_curation.py` a través de la cola,
más los del contrato de `channel_province`, que antes no existía como función
propia: la cola devolvía el id como string para meterlo en un formulario.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from saimc.ingestion.origin import channel_province, resolve_origin
from saimc.models import (
    Channel,
    ContentType,
    Platform,
    ProcessingStatus,
    Province,
    RawIngestion,
    SourceType,
)


@pytest.fixture
def habana(session: Session) -> Province:
    provincia = Province(code="LH", name="La Habana")
    session.add(provincia)
    session.commit()
    return provincia


def _canal(session: Session, *, name: str, province_id=None) -> Channel:
    canal = Channel(
        name=name,
        platform=Platform.WHATSAPP_CHANNEL,
        province_id=province_id,
        is_active=True,
    )
    session.add(canal)
    session.commit()
    return canal


def _ingesta(
    session: Session,
    *,
    hash_: str,
    source_reference: str | None = None,
    channel_id=None,
    source_type: str = SourceType.WHATSAPP_CHANNEL,
) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=source_type,
        source_reference=source_reference,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        original_text="pollo congelado 40lb a 500",
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        channel_id=channel_id,
    )
    session.add(ingestion)
    session.commit()
    return ingestion


class TestResolveOrigin:
    def test_channel_id_es_la_identidad_real(self, session: Session) -> None:
        canal = _canal(session, name="Groupej Morales")
        ingestion = _ingesta(
            session, hash_="a1", source_reference="otro nombre", channel_id=canal.id
        )

        assert resolve_origin(session, ingestion) is canal

    def test_sin_channel_id_cae_al_nombre(self, session: Session) -> None:
        canal = _canal(session, name="Groupej Morales")
        ingestion = _ingesta(session, hash_="a2", source_reference="Groupej Morales")

        assert resolve_origin(session, ingestion) is canal

    def test_nombre_ambiguo_resuelve_en_none(self, session: Session) -> None:
        """Migración 0014: dos filas pueden compartir nombre (canal y grupo a
        la vez). Desempatar en silencio está descartado (design.md, D4)."""
        _canal(session, name="Ventas Mayoristas")
        _canal(session, name="Ventas Mayoristas")
        ingestion = _ingesta(session, hash_="a3", source_reference="Ventas Mayoristas")

        assert resolve_origin(session, ingestion) is None

    def test_nombre_sin_coincidencia_resuelve_en_none(self, session: Session) -> None:
        ingestion = _ingesta(session, hash_="a4", source_reference="Canal Fantasma")

        assert resolve_origin(session, ingestion) is None

    def test_sin_referencia_ni_channel_id_resuelve_en_none(self, session: Session) -> None:
        ingestion = _ingesta(session, hash_="a5", source_reference=None)

        assert resolve_origin(session, ingestion) is None


class TestChannelProvince:
    """docs/decisions.md, 2026-08-17 — "el canal siempre manda"."""

    def test_devuelve_la_provincia_fija_del_canal(
        self, session: Session, habana: Province
    ) -> None:
        canal = _canal(session, name="Groupej Morales", province_id=habana.id)
        ingestion = _ingesta(
            session, hash_="b1", source_reference="Groupej Morales", channel_id=canal.id
        )

        assert channel_province(session, ingestion) is habana

    def test_canal_sin_provincia_cargada_devuelve_none(self, session: Session) -> None:
        canal = _canal(session, name="Groupej Morales")
        ingestion = _ingesta(
            session, hash_="b2", source_reference="Groupej Morales", channel_id=canal.id
        )

        assert channel_province(session, ingestion) is None

    def test_fuente_que_no_es_canal_de_whatsapp_devuelve_none(
        self, session: Session, habana: Province
    ) -> None:
        """Para la bandeja y el pegado, `source_reference` es texto libre del
        operador: no hay canal que resolver con certeza."""
        canal = _canal(session, name="Groupej Morales", province_id=habana.id)
        ingestion = _ingesta(
            session,
            hash_="b3",
            source_reference="Groupej Morales",
            channel_id=canal.id,
            source_type=SourceType.INBOX_TEXT,
        )

        assert channel_province(session, ingestion) is None
