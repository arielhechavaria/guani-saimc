"""Transparency Layer pública: `GET /anuncio/{id}` (§10.6, mitigación 2).

Lo que estos casos protegen: que la vista muestre el anuncio y su contenido
económico —nunca la identidad del anunciante— y que solo llegue a ser pública
la evidencia de texto ya curada. Una imagen, algo pendiente o descartado, o un
id que no existe dan el mismo 404: distinguir un caso de otro filtraría
información por el camino que la mitigación 2 quiere cerrado.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import Channel, ContentType, Platform, ProcessingStatus, RawIngestion, SourceType
from saimc.web.app import app
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _crear_ingestion(
    session: Session,
    *,
    content_type: str = ContentType.TEXT,
    processing_status: str = ProcessingStatus.CURATED,
    original_text: str | None = "Arroz Santiago 25kg a 8.500 CUP, entrega en Vista Alegre",
    channel_id: uuid.UUID | None = None,
) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT if content_type == ContentType.TEXT else SourceType.INBOX_IMAGE,
        file_path="raw/evidencia.dat",
        content_hash=uuid.uuid4().hex,
        content_type=content_type,
        original_text=original_text,
        processing_status=processing_status,
        captured_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        channel_id=channel_id,
    )
    session.add(ingestion)
    session.commit()
    return ingestion


class TestEvidenciaPublica:
    def test_texto_curado_se_muestra(self, client: TestClient, session: Session) -> None:
        ingestion = _crear_ingestion(session)

        response = client.get(f"/anuncio/{ingestion.id}")

        assert response.status_code == 200
        assert "Arroz Santiago 25kg a 8.500 CUP" in response.text
        assert "20/08/2026" in response.text

    def test_imagen_da_404(self, client: TestClient, session: Session) -> None:
        """Los píxeles de una captura traen nombre del remitente y a veces
        teléfono (docs/spec-map.md, contradicción 1): no hay forma de
        redactarlos sin OCR/ML, fuera de alcance en Fase 0."""
        ingestion = _crear_ingestion(session, content_type=ContentType.IMAGE, original_text=None)

        response = client.get(f"/anuncio/{ingestion.id}")

        assert response.status_code == 404

    def test_pendiente_da_404(self, client: TestClient, session: Session) -> None:
        ingestion = _crear_ingestion(session, processing_status=ProcessingStatus.PENDING)

        response = client.get(f"/anuncio/{ingestion.id}")

        assert response.status_code == 404

    def test_descartado_da_404(self, client: TestClient, session: Session) -> None:
        ingestion = _crear_ingestion(session, processing_status=ProcessingStatus.DISCARDED)

        response = client.get(f"/anuncio/{ingestion.id}")

        assert response.status_code == 404

    def test_id_inexistente_da_404(self, client: TestClient) -> None:
        response = client.get(f"/anuncio/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_no_expone_el_canal_de_origen(self, client: TestClient, session: Session) -> None:
        """CLAUDE.md: el identificador de usuario/canal de origen nunca se
        expone en la capa pública. El nombre del canal no debe llegar al HTML
        aunque la ingesta tenga uno cargado."""
        canal = Channel(name="Groupej Morales Surl", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add(canal)
        session.commit()
        ingestion = _crear_ingestion(session, channel_id=canal.id)

        response = client.get(f"/anuncio/{ingestion.id}")

        assert response.status_code == 200
        assert "Groupej Morales Surl" not in response.text
