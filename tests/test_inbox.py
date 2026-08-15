"""Comportamiento esperado de la bandeja de entrada."""

import hashlib
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

    def test_el_telefono_no_llega_a_la_base(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        """`CLAUDE.md`: no se almacenan teléfonos. La ingesta es la primera vez
        que texto capturado toca la base, así que la redacción tiene que correr
        acá y no solo en la curaduría (docs/decisions.md 2026-08-11).
        """
        (inbox_dir / "mayorista.txt").write_text(
            "#llame_escriba_55550303\nArroz 25Kg a $17,500 Cup\nwatsap 55550202",
            encoding="utf-8",
        )

        ingest_inbox(session, inbox_dir, store_dir)

        (row,) = _rows(session)
        assert "55550303" not in (row.original_text or "")
        assert "55550202" not in (row.original_text or "")
        # El precio sobrevive intacto: es el dato que el sistema mide.
        assert "$17,500 Cup" in (row.original_text or "")

    def test_el_hash_sigue_siendo_el_del_archivo_original(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        """La redacción toca `original_text`, nunca el archivo ni su hash.

        `content_hash` es el índice único de deduplicación: si se calculara
        sobre el texto redactado, una recaptura del mismo anuncio dejaría de
        reconocerse como duplicada.
        """
        contenido = "watsap 55550202\nArroz a 730 cup"
        (inbox_dir / "anuncio-tel.txt").write_text(contenido, encoding="utf-8")

        ingest_inbox(session, inbox_dir, store_dir)

        (row,) = _rows(session)
        assert row.content_hash == hashlib.sha256(contenido.encode()).hexdigest()
        # El archivo preservado tampoco se toca.
        assert (store_dir / row.file_path).read_text(encoding="utf-8") == contenido

    def test_el_ocr_no_corre_en_la_ingesta(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        """Whitepaper §6.1 «Rol del OCR»: asiste a la curaduría, no al pipeline.

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


class TestOverridesDeProcedencia:
    """`source_type`/`source_reference`/`captured_at` explícitos: lo que usa
    el webhook de WhatsApp (2026-08-15) para no quedar indistinguible de un
    archivo que cayó a mano en la bandeja, y para preservar el momento real
    del mensaje en vez de "ahora"."""

    def test_source_type_explicito_reemplaza_al_de_la_extension(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "webhook.txt")

        ingest_inbox(session, inbox_dir, store_dir, source_type=SourceType.WHATSAPP_CHANNEL)

        (row,) = _rows(session)
        assert row.source_type == SourceType.WHATSAPP_CHANNEL

    def test_source_reference_explicito_se_guarda(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        _write_text(inbox_dir, "webhook.txt")

        ingest_inbox(session, inbox_dir, store_dir, source_reference="Tienda: The Boss")

        (row,) = _rows(session)
        assert row.source_reference == "Tienda: The Boss"

    def test_captured_at_explicito_reemplaza_al_mtime_del_archivo(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        from datetime import UTC, datetime

        _write_text(inbox_dir, "webhook.txt")
        momento_real = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)

        ingest_inbox(session, inbox_dir, store_dir, captured_at=momento_real)

        (row,) = _rows(session)
        # SQLite (backend de este test) no conserva tzinfo al hacer round-trip;
        # se compara en UTC explícito para no depender de esa mecánica.
        assert row.captured_at.replace(tzinfo=UTC) == momento_real

    def test_sin_override_se_comporta_como_antes(
        self, session: Session, inbox_dir: Path, store_dir: Path
    ) -> None:
        """Guardarraíl de no-regresión: el camino de la bandeja y el pegado no
        pasan estos argumentos, y no deben cambiar de comportamiento."""
        _write_text(inbox_dir, "manual.txt")

        ingest_inbox(session, inbox_dir, store_dir)

        (row,) = _rows(session)
        assert row.source_type == SourceType.INBOX_TEXT
        assert row.source_reference is None
