"""Bandeja de entrada: convierte archivos sueltos en registros crudos.

El OCR **no** corre acá. La especificación (§6.1, «Rol del OCR») define el OCR y la
extracción por reglas como asistentes de la cola de curaduría, no como etapas
autónomas del pipeline: el sistema propone y el operador confirma. Correr el OCR
en la ingesta acumularía su error sobre el de la extracción sin supervisión, y
justamente eso es lo que impediría medir H0 por capas separadas.

Por eso, para las imágenes, `original_text` queda nulo hasta que la curaduría lo
complete.
"""

import hashlib
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.models.raw_ingestion import (
    ContentType,
    ProcessingStatus,
    RawIngestion,
    SourceType,
)
from saimc.privacy import redact_personal_data

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
TEXT_SUFFIXES = frozenset({".txt", ".md"})

# Subdirectorio donde van a parar las capturas repetidas. No se borra nada: el
# operador decide qué hacer con ellas.
DUPLICATES_DIRNAME = "duplicates"

# Basura que Windows deja al copiar archivos desde el Explorador hacia WSL.
_ZONE_IDENTIFIER = ":Zone.Identifier"

_HASH_CHUNK_BYTES = 1024 * 1024

_SOURCE_TYPE_BY_CONTENT = {
    ContentType.IMAGE: SourceType.INBOX_IMAGE,
    ContentType.TEXT: SourceType.INBOX_TEXT,
}


@dataclass
class IngestReport:
    """Resultado de una pasada por la bandeja."""

    ingested: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return len(self.ingested) + len(self.duplicates) + len(self.skipped)


def classify(path: Path) -> ContentType | None:
    """Tipo de contenido según la extensión, o None si no se reconoce.

    Ante una extensión desconocida no se adivina: el archivo se deja donde está y
    se reporta como salteado.
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return ContentType.IMAGE
    if suffix in TEXT_SUFFIXES:
        return ContentType.TEXT
    return None


def compute_hash(path: Path) -> str:
    """SHA-256 del contenido, leído por bloques para no cargar la imagen entera."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def discover(inbox_dir: Path) -> list[Path]:
    """Archivos candidatos en la bandeja, en orden estable.

    No entra en subdirectorios: la bandeja es plana, y `duplicates/` vive adentro.
    """
    if not inbox_dir.is_dir():
        return []

    candidates = [
        entry
        for entry in inbox_dir.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and _ZONE_IDENTIFIER not in entry.name
    ]
    return sorted(candidates, key=lambda entry: entry.name)


def _read_text(path: Path) -> str:
    # `errors="replace"` para que un archivo mal codificado no frene la ingesta.
    # No se pierde nada: el original queda preservado en el almacén.
    return path.read_text(encoding="utf-8", errors="replace")


def _captured_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _hash_exists(session: Session, content_hash: str) -> bool:
    stmt = select(RawIngestion.id).where(RawIngestion.content_hash == content_hash)
    return session.execute(stmt).first() is not None


def _move_to_duplicates(path: Path, inbox_dir: Path) -> None:
    target_dir = inbox_dir / DUPLICATES_DIRNAME
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target_dir / path.name))


class DuplicateContentError(Exception):
    """El contenido ya tenía fila cuando se intentó insertar (carrera entre
    dos ingestas del mismo archivo). No es un fallo: es un duplicado."""


def ingest_file(
    session: Session,
    path: Path,
    store_dir: Path,
    *,
    source_type: str | None = None,
    source_reference: str | None = None,
    captured_at: datetime | None = None,
    channel_id: uuid.UUID | None = None,
    sender_hash: str | None = None,
) -> RawIngestion:
    """Ingiere un archivo ya clasificado y lo mueve al almacén.

    El archivo se mueve antes de insertar la fila para que `file_path` nunca
    apunte a algo inexistente. Si la inserción falla, se devuelve a su lugar.

    Los argumentos con nombre son overrides opcionales: sin ellos, el
    comportamiento es el de siempre (`source_type` por extensión,
    `source_reference` en blanco, `captured_at` del mtime del archivo,
    `channel_id` nulo). Los usa el webhook de WhatsApp (`docs/decisions.md`,
    2026-08-15) para que un mensaje automático no quede indistinguible de un
    archivo que el operador dejó caer a mano, y para preservar el momento real
    del mensaje en vez de "ahora" — que es lo único que el mtime de un archivo
    recién escrito puede decir. `channel_id` (2026-08-19) es la identidad real
    del canal de origen, resuelta por `external_id` en el momento de la
    captura; sin ella, el origen solo se puede reconstruir por nombre.
    `sender_hash` (slice B5) es el hash con clave del emisor de un mensaje de
    grupo, ya calculado por `parse_message`: acá nunca llega el JID en claro.
    """
    content_type = classify(path)
    if content_type is None:
        raise ValueError(f"Extensión no reconocida: {path.name}")

    content_hash = compute_hash(path)
    # El hash se calcula sobre el archivo, la redacción se aplica al texto que
    # va a la base. El orden importa: si el hash saliera del texto redactado,
    # una recaptura del mismo anuncio dejaría de reconocerse como duplicada.
    # El archivo preservado tampoco cambia — sigue siendo la evidencia literal.
    original_text = (
        redact_personal_data(_read_text(path))
        if content_type is ContentType.TEXT
        else None
    )
    resolved_captured_at = captured_at if captured_at is not None else _captured_at(path)

    store_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{content_hash}{path.suffix.lower()}"
    stored_path = store_dir / stored_name

    origin = path
    shutil.move(str(path), str(stored_path))

    try:
        ingestion = RawIngestion(
            source_type=source_type or _SOURCE_TYPE_BY_CONTENT[content_type],
            source_reference=source_reference,
            file_path=stored_name,
            content_hash=content_hash,
            content_type=content_type,
            original_text=original_text,
            processing_status=ProcessingStatus.PENDING,
            captured_at=resolved_captured_at,
            channel_id=channel_id,
            sender_hash=sender_hash,
        )
        session.add(ingestion)
        session.flush()
    except IntegrityError as error:
        session.rollback()
        if _hash_exists(session, content_hash):
            # Carrera: otro request con el mismo contenido insertó primero (WAHA
            # entrega el mismo mensaje dos veces en paralelo). `stored_path` es
            # ahora la evidencia de la fila ganadora, con bytes idénticos: no se
            # toca. Devolverla a la bandeja la perdía, porque el webhook borra
            # lo que queda ahí.
            raise DuplicateContentError(content_hash) from error
        shutil.move(str(stored_path), str(origin))
        raise
    except Exception:
        # Devolver la evidencia a la bandeja para no perderla ni dejarla huérfana.
        shutil.move(str(stored_path), str(origin))
        raise

    return ingestion


def ingest_inbox(
    session: Session,
    inbox_dir: Path,
    store_dir: Path,
    paths: Iterable[Path] | None = None,
    *,
    source_type: str | None = None,
    source_reference: str | None = None,
    captured_at: datetime | None = None,
    channel_id: uuid.UUID | None = None,
    sender_hash: str | None = None,
) -> IngestReport:
    """Procesa la bandeja completa y devuelve el detalle de lo que pasó.

    Cada archivo se confirma por separado: uno que falle no arrastra a los demás.

    Los overrides se aplican a TODOS los `paths` de esta llamada — correcto
    para el webhook, que siempre pasa un único archivo temporal por llamada;
    el escaneo normal de la bandeja y el pegado no los usan.
    """
    report = IngestReport()

    for path in paths if paths is not None else discover(inbox_dir):
        if classify(path) is None:
            report.skipped.append(path.name)
            continue

        if _hash_exists(session, compute_hash(path)):
            _move_to_duplicates(path, inbox_dir)
            report.duplicates.append(path.name)
            continue

        try:
            ingest_file(
                session,
                path,
                store_dir,
                source_type=source_type,
                source_reference=source_reference,
                captured_at=captured_at,
                channel_id=channel_id,
                sender_hash=sender_hash,
            )
        except DuplicateContentError:
            report.duplicates.append(path.name)
            continue
        session.commit()
        report.ingested.append(path.name)

    return report
