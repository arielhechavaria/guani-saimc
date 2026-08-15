"""Pegado directo de anuncios — la vía rápida de entrada.

El 98% del material capturado eran imágenes, y la medición del 2026-08-15
mostró por qué: desde el teléfono una captura de pantalla se sincroniza sola,
mientras que meter texto exige abrir un editor, crear un `.txt` y guardarlo en
la carpeta sincronizada. No había dónde pegar.

Estos casos fijan que pegar texto produce exactamente lo mismo que dejar caer
un `.txt` en la bandeja: mismo `source_type`, mismo hash, misma deduplicación
y la misma redacción de datos personales. La vía rápida no puede ser una vía
con menos garantías.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.models import ContentType, ProcessingStatus, RawIngestion, SourceType
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ingestas(session: Session) -> list[RawIngestion]:
    return list(session.execute(select(RawIngestion)).scalars())


class TestPegarAnuncios:
    def test_un_anuncio_pegado_entra_como_pendiente(
        self, client: TestClient, session: Session
    ) -> None:
        response = client.post(
            "/curation/paste",
            data={"pasted": "aceite 1L a 950 santiago"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        ingestas = _ingestas(session)
        assert len(ingestas) == 1
        assert ingestas[0].content_type == ContentType.TEXT
        assert ingestas[0].source_type == SourceType.INBOX_TEXT
        assert ingestas[0].processing_status == ProcessingStatus.PENDING
        assert ingestas[0].original_text is not None
        assert "aceite 1L a 950 santiago" in ingestas[0].original_text

    def test_varios_anuncios_separados_crean_uno_por_anuncio(
        self, client: TestClient, session: Session
    ) -> None:
        """El separador es lo que convierte «pegar» en «meter unos cuantos de
        una»: sin él, tres anuncios entran como un solo registro crudo y la
        cola pierde la unidad de curaduría."""
        pegado = "\n".join(
            [
                "aceite 1L a 950 santiago",
                "---",
                "pollo lb a 620 habana",
                "---",
                "arroz lb a 240 santiago",
            ]
        )

        response = client.post("/curation/paste", data={"pasted": pegado}, follow_redirects=False)

        assert response.status_code == 303
        assert len(_ingestas(session)) == 3

    def test_el_separador_tolera_espacios_y_guiones_de_mas(
        self, client: TestClient, session: Session
    ) -> None:
        """Se pega desde el teléfono: exigir exactamente tres guiones sin
        espacios convertiría la vía rápida en una fuente de errores."""
        pegado = "aceite 1L a 950 santiago\n  ----  \npollo lb a 620 habana"

        client.post("/curation/paste", data={"pasted": pegado}, follow_redirects=False)

        assert len(_ingestas(session)) == 2

    def test_un_bloque_vacio_no_crea_registro(
        self, client: TestClient, session: Session
    ) -> None:
        pegado = "aceite 1L a 950 santiago\n---\n   \n---\npollo lb a 620 habana"

        client.post("/curation/paste", data={"pasted": pegado}, follow_redirects=False)

        assert len(_ingestas(session)) == 2

    def test_pegar_vacio_no_crea_nada_ni_rompe(
        self, client: TestClient, session: Session
    ) -> None:
        response = client.post("/curation/paste", data={"pasted": "   "}, follow_redirects=False)

        assert response.status_code == 303
        assert _ingestas(session) == []

    def test_el_mismo_anuncio_pegado_dos_veces_no_duplica(
        self, client: TestClient, session: Session
    ) -> None:
        """Misma garantía que la bandeja: el hash del contenido decide, así que
        volver a pegar algo ya cargado no ensucia la cola."""
        texto = "aceite 1L a 950 santiago"

        client.post("/curation/paste", data={"pasted": texto}, follow_redirects=False)
        client.post("/curation/paste", data={"pasted": texto}, follow_redirects=False)

        assert len(_ingestas(session)) == 1

    def test_el_telefono_se_redacta_igual_que_en_la_bandeja(
        self, client: TestClient, session: Session
    ) -> None:
        """spec.md §10.6: no se almacenan datos de contacto. La vía rápida no
        puede ser el agujero por donde entran."""
        client.post(
            "/curation/paste",
            data={"pasted": "aceite 1L a 950 santiago, llamar al 53439609"},
            follow_redirects=False,
        )

        ingesta = _ingestas(session)[0]
        assert ingesta.original_text is not None
        assert "53439609" not in ingesta.original_text

    def test_el_archivo_de_evidencia_queda_en_el_almacen(
        self, client: TestClient, session: Session, tmp_path: Path
    ) -> None:
        """spec.md §6.1: el registro crudo referencia evidencia que existe. Un
        pegado tiene que dejar archivo igual que una captura, o la Transparency
        Layer queda apuntando a la nada."""
        client.post(
            "/curation/paste",
            data={"pasted": "aceite 1L a 950 santiago"},
            follow_redirects=False,
        )

        ingesta = _ingestas(session)[0]
        assert (tmp_path / "data" / "raw" / ingesta.file_path).is_file()

    def test_la_bandeja_no_queda_con_basura(
        self, client: TestClient, session: Session, tmp_path: Path
    ) -> None:
        """El pegado usa un archivo temporal para reusar el camino de la
        bandeja; ese temporal no puede quedarse ahí."""
        client.post(
            "/curation/paste",
            data={"pasted": "aceite 1L a 950 santiago"},
            follow_redirects=False,
        )

        assert list((tmp_path / "inbox").glob("*.txt")) == []
