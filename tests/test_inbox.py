"""Comportamiento esperado de la bandeja de entrada."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.ingestion.inbox import (
    DUPLICATES_DIRNAME,
    classify,
    compute_hash,
    discover,
    ingest_inbox,
)
from saimc.models import ContentType, ProcessingStatus, RawIngestion, SourceType

ANUNCIO = "Pollo congelado 950 cup la bandeja, Santiago. Entrega a domicilio."


def _write_text(inbox_dir: Path, name: str, body: str = ANUNCIO) -> Path:
    path = inbox_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _write_image(inbox_dir: Path, name: str, body: bytes = b"\x89PNG fake") -> Path:
    path = inbox_dir / name
    path.write_bytes(body)
    return path


def _rows(session: Session) -> list[RawIngestion]:
    return list(session.execute(select(RawIngestion)).scalars())


class TestClassify:
    def test_reconoce_imagenes(self, tmp_path: Path) -> None:
        assert classify(tmp_path / "captura.png") is ContentType.IMAGE
        assert classify(tmp_path / "captura.JPG") is ContentType.IMAGE

    def test_reconoce_texto(self, tmp_path: Path) -> None:
        assert classify(tmp_path / "anuncio.txt") is ContentType.TEXT

    def test_extension_desconocida_no_se_adivina(self, tmp_path: Path) -> None:
        assert classify(tmp_path / "anuncio.pdf") is None
        assert classify(tmp_path / "sin_extension") is None


class TestComputeHash:
    def test_mismo_contenido_mismo_hash(self, inbox_dir: Path) -> None:
        uno = _write_text(inbox_dir, "a.txt")
        otro = _write_text(inbox_dir, "b.txt")
        assert compute_hash(uno) == compute_hash(otro)

    def test_contenido_distinto_hash_distinto(self, inbox_dir: Path) -> None:
        uno = _write_text(inbox_dir, "a.txt", "Arroz 300 cup")
        otro = _write_text(inbox_dir, "b.txt", "Aceite 1200 cup")
        assert compute_hash(uno) != compute_hash(otro)


class TestDiscover:
    def test_ignora_ocultos_y_metadatos_de_windows(self, inbox_dir: Path) -> None:
        _write_text(inbox_dir, "anuncio.txt")
        _write_text(inbox_dir, ".oculto.txt")
        _write_text(inbox_dir, "anuncio.txt:Zone.Identifier")

        assert [p.name for p in discover(inbox_dir)] == ["anuncio.txt"]

    def test_ignora_subdirectorios(self, inbox_dir: Path) -> None:
        (inbox_dir / DUPLICATES_DIRNAME).mkdir()
        _write_text(inbox_dir, "anuncio.txt")

        assert [p.name for p in discover(inbox_dir)] == ["anuncio.txt"]

    def test_bandeja_inexistente_no_rompe(self, tmp_path: Path) -> None:
        assert discover(tmp_path / "no_existe") == []


class TestIngestInbox:
    def test_texto_queda_registrado_con_su_contenido(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "anuncio.txt")

        report = ingest_inbox(session, inbox_dir, store_dir)

        assert report.ingested == ["anuncio.txt"]
        (row,) = _rows(session)
        assert row.original_text == ANUNCIO
        assert row.content_type == ContentType.TEXT
        assert row.source_type == SourceType.INBOX_TEXT
        assert row.processing_status == ProcessingStatus.PENDING

    def test_el_ocr_no_corre_en_la_ingesta(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        """Whitepaper líneas 452-456: el OCR asiste a la curaduría, no al pipeline.

        Una imagen recién ingerida no tiene texto. Lo pone el operador cuando
        confirma o corrige la propuesta del OCR en la cola.
        """
        _write_image(inbox_dir, "captura.png")

        ingest_inbox(session, inbox_dir, store_dir)

        (row,) = _rows(session)
        assert row.original_text is None
        assert row.content_type == ContentType.IMAGE
        assert row.source_type == SourceType.INBOX_IMAGE

    def test_la_evidencia_se_preserva_nombrada_por_hash(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        origen = _write_image(inbox_dir, "captura.png")
        esperado = compute_hash(origen)

        ingest_inbox(session, inbox_dir, store_dir)

        (row,) = _rows(session)
        assert row.content_hash == esperado
        assert row.file_path == f"{esperado}.png"

        preservado = store_dir / row.file_path
        assert preservado.is_file()
        assert compute_hash(preservado) == esperado

    def test_la_bandeja_queda_vacia(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "anuncio.txt")
        _write_image(inbox_dir, "captura.png")

        ingest_inbox(session, inbox_dir, store_dir)

        assert discover(inbox_dir) == []

    def test_capturas_repetidas_no_duplican_registros(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "anuncio.txt")
        ingest_inbox(session, inbox_dir, store_dir)

        # El mismo anuncio, capturado otra vez con otro nombre de archivo.
        _write_text(inbox_dir, "anuncio-copia.txt")
        report = ingest_inbox(session, inbox_dir, store_dir)

        assert report.duplicates == ["anuncio-copia.txt"]
        assert len(_rows(session)) == 1

    def test_los_duplicados_no_se_borran(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "anuncio.txt")
        ingest_inbox(session, inbox_dir, store_dir)
        _write_text(inbox_dir, "anuncio-copia.txt")

        ingest_inbox(session, inbox_dir, store_dir)

        assert (inbox_dir / DUPLICATES_DIRNAME / "anuncio-copia.txt").is_file()

    def test_extension_desconocida_se_deja_donde_esta(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        (inbox_dir / "anuncio.pdf").write_bytes(b"%PDF-1.4")

        report = ingest_inbox(session, inbox_dir, store_dir)

        assert report.skipped == ["anuncio.pdf"]
        assert _rows(session) == []
        assert (inbox_dir / "anuncio.pdf").is_file()

    def test_bandeja_vacia_no_hace_nada(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        report = ingest_inbox(session, inbox_dir, store_dir)

        assert report.total_seen == 0
        assert _rows(session) == []
