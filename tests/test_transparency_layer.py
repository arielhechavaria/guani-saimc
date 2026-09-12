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


class TestDeudaEsteticaDelShellPublico:
    """`_public_shell.html` es el layout que carga esta vista, y hasta el
    2026-09-12 sus seis clases —`.m3-public`, `.m3-public-top`, `.m3-kicker`,
    `.m3-title`, `.m3-public-main`, `.m3-public-foot`— no tenían NINGUNA regla
    en `saimc-public.css`. `public_home.html` lo tapaba con su propio
    `<style>` (y ocultando `.m3-public-top`); esta vista es la única que
    renderiza el shell tal cual, así que salía a sangre contra el borde de la
    ventana, con el `<h1>` en el default del navegador.

    docs/decisions.md, entrada del 2026-09-12 («deudas estéticas de UI»)."""

    def test_el_titular_usa_la_tipografia_editorial(self, client: TestClient, session: Session) -> None:
        ingestion = _crear_ingestion(session)

        response = client.get(f"/anuncio/{ingestion.id}")
        hoja = client.get("/static/saimc-public.css").text

        assert response.status_code == 200
        assert '<h1 class="m3-title">' in response.text
        assert ".m3-title {" in hoja
        assert "var(--ph-font-heading)" in hoja[hoja.index(".m3-title {") :]

    def test_la_tarjeta_de_evidencia_tiene_superficie_propia(
        self, client: TestClient, session: Session
    ) -> None:
        """El `<pre>` con el texto del anuncio vivía en `.ph-table-card`, una
        clase que solo `public_home.html` define en su `<style>` — acá
        renderizaba sin fondo, sin sombra y sin padding propio (el
        `style="padding:1.5rem"` inline era lo único que sobrevivía)."""
        ingestion = _crear_ingestion(session)

        response = client.get(f"/anuncio/{ingestion.id}")
        hoja = client.get("/static/saimc-public.css").text

        assert 'class="ph-evidence-card"' in response.text
        assert 'class="ph-evidence-text"' in response.text
        assert ".ph-evidence-card {" in hoja
        assert ".ph-evidence-text {" in hoja

    def test_los_enlaces_publicos_usan_el_acento_azul_no_el_teal_del_operador(
        self, client: TestClient
    ) -> None:
        """El 2026-09-04 se decidió que el acento de la capa pública es
        `--ph-accent` (azul) y el del operador `--m3-primary` (teal) — dos
        colores de marca deliberados. `saimc-public.css` pintaba `a { color:
        var(--m3-primary) }`: el teal del operador colándose en la única
        capa que el visitante final ve."""
        hoja = client.get("/static/saimc-public.css").text
        regla_link = hoja[hoja.index("\na {") :]
        regla_link = regla_link[: regla_link.index("}")]

        assert "var(--ph-accent)" in regla_link
        assert "var(--m3-primary)" not in regla_link
