"""`saimc.extraction.ocr_store` — la transcripción del OCR guardada (slice K3).

Los casos salen de `docs/spec-slice-K3.md` («Tests primero»); los valores
esperados los fija la especificación, no el comportamiento del código.
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.extraction import ocr, ocr_store
from saimc.extraction.ocr import OcrResult
from saimc.models import ContentType, OcrTranscription, ProcessingStatus, RawIngestion, SourceType
from saimc.validation import context


def _imagen(session: Session, nombre: str = "a") -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_IMAGE,
        file_path=f"raw/{nombre}.png",
        content_hash=f"ocr-{nombre}-{uuid.uuid4()}",
        content_type=ContentType.IMAGE,
        original_text=None,
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(ingestion)
    session.commit()
    return ingestion


class _Transcriptor:
    """Cuenta sus llamadas; devuelve lo que se le diga."""

    def __init__(self, resultado: OcrResult | str) -> None:
        self.resultado = resultado
        self.llamadas = 0

    def __call__(self, path: Path) -> OcrResult | str:
        self.llamadas += 1
        return self.resultado


def _rapid(*renglones: str) -> OcrResult:
    return OcrResult(
        text="\n".join(renglones),
        engine="rapidocr",
        boxes=[
            {
                "text": t,
                "box": [[0.0, 10.0 * i], [90.0, 10.0 * i], [90.0, 10.0 * i + 8], [0.0, 10.0 * i + 8]],
                "score": 0.97,
            }
            for i, t in enumerate(renglones)
        ],
    )


def _filas(session: Session) -> list[OcrTranscription]:
    return list(session.execute(select(OcrTranscription)).scalars())


class TestGuardaYReusa:
    def test_la_primera_lectura_guarda_una_fila(self, session: Session, tmp_path: Path) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(_rapid("pollo a 500"))

        texto = ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert texto == "pollo a 500"
        (fila,) = _filas(session)
        assert fila.ingestion_id == ing.id
        assert fila.engine == "rapidocr"
        assert fila.ocr_version == ocr.OCR_VERSION
        assert fila.text == "pollo a 500"

    def test_la_segunda_lectura_no_corre_el_ocr_ni_agrega_fila(
        self, session: Session, tmp_path: Path
    ) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(_rapid("pollo a 500"))

        ocr_store.stored_transcription(session, ing, tmp_path, tr)
        texto = ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert texto == "pollo a 500"
        assert tr.llamadas == 1
        assert len(_filas(session)) == 1

    def test_el_transcriptor_recibe_la_ruta_de_la_evidencia(self, session: Session, tmp_path: Path) -> None:
        ing = _imagen(session, "ruta")
        vistos: list[Path] = []

        def tr(path: Path) -> str:
            vistos.append(path)
            return "x"

        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert vistos == [tmp_path / "raw/ruta.png"]


class TestPrivacidad:
    def test_un_telefono_no_se_guarda_ni_en_text_ni_en_boxes(self, session: Session, tmp_path: Path) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(_rapid("Pollo a 500", "Llame al +53 5 5550101"))

        texto = ocr_store.stored_transcription(session, ing, tmp_path, tr)

        (fila,) = _filas(session)
        assert "5550101" not in texto
        assert "5550101" not in fila.text
        assert fila.boxes is not None
        assert "5550101" not in repr(fila.boxes)
        assert [b["redacted"] for b in fila.boxes] == [False, True]
        assert fila.boxes[0]["text"] == "Pollo a 500"

    def test_un_telefono_partido_en_dos_renglones_tampoco_queda_en_boxes(
        self, session: Session, tmp_path: Path
    ) -> None:
        """Si el número se parte entre renglones, la redacción por renglón no lo
        ve: se tapan los renglones con dígitos antes que arriesgar el número."""
        ing = _imagen(session)
        tr = _Transcriptor(_rapid("Pollo a 500", "+53 5 234", "5678 ok"))

        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        (fila,) = _filas(session)
        assert fila.boxes is not None
        assert "5 234" not in repr(fila.boxes)
        assert "5678" not in repr(fila.boxes)


class TestMotoresYCajas:
    def test_rapidocr_guarda_una_caja_por_renglon_en_el_orden_del_texto(
        self, session: Session, tmp_path: Path
    ) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(_rapid("uno", "dos", "tres"))

        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        (fila,) = _filas(session)
        assert fila.boxes is not None
        assert [b["text"] for b in fila.boxes] == fila.text.split("\n") == ["uno", "dos", "tres"]
        assert all(len(b["box"]) == 4 and b["score"] == 0.97 for b in fila.boxes)

    def test_tesseract_deja_boxes_nulo(self, session: Session, tmp_path: Path) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(OcrResult(text="pollo", engine="tesseract", boxes=None))

        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        (fila,) = _filas(session)
        assert fila.engine == "tesseract"
        assert fila.boxes is None

    def test_un_transcriptor_que_devuelve_str_se_guarda_como_tesseract_sin_cajas(
        self, session: Session, tmp_path: Path
    ) -> None:
        """El seam de los tests devuelve `str` y no declara motor: se guarda
        como el respaldo, sin cajas, y no se inventa un tercer valor."""
        ing = _imagen(session)

        texto = ocr_store.stored_transcription(session, ing, tmp_path, lambda p: "pollo")

        (fila,) = _filas(session)
        assert texto == "pollo"
        assert (fila.engine, fila.boxes) == ("tesseract", None)


class TestVacias:
    @pytest.mark.parametrize("vacio", ["", "  \n "])
    def test_una_transcripcion_vacia_no_guarda_y_se_reintenta(
        self, session: Session, tmp_path: Path, vacio: str
    ) -> None:
        ing = _imagen(session)
        tr = _Transcriptor(vacio)

        ocr_store.stored_transcription(session, ing, tmp_path, tr)
        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert _filas(session) == []
        assert tr.llamadas == 2


class TestVersion:
    def test_subir_la_version_transcribe_de_nuevo_y_conserva_la_vieja(
        self, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ing = _imagen(session)
        tr = _Transcriptor("pollo")
        monkeypatch.setattr(ocr, "OCR_VERSION", "v-vieja")
        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        monkeypatch.setattr(ocr, "OCR_VERSION", "v-nueva")
        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert tr.llamadas == 2
        assert sorted(f.ocr_version for f in _filas(session)) == ["v-nueva", "v-vieja"]


class TestCarrera:
    def test_la_unicidad_por_imagen_y_version_la_garantiza_la_base(self, session: Session) -> None:
        ing = _imagen(session)
        for _ in range(2):
            session.add(OcrTranscription(ingestion_id=ing.id, engine="tesseract", ocr_version="v", text="x"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_si_otro_proceso_ya_guardo_se_devuelve_su_fila_y_la_sesion_sigue_viva(
        self, session: Session, tmp_path: Path
    ) -> None:
        ing = _imagen(session)
        otra = _imagen(session, "otra")
        otra.processing_status = ProcessingStatus.DISCARDED  # trabajo externo sin commit

        def tr(path: Path) -> str:
            # El «otro proceso» guarda justo mientras este transcribe.
            session.add(
                OcrTranscription(
                    ingestion_id=ing.id,
                    engine="rapidocr",
                    ocr_version=ocr.OCR_VERSION,
                    text="del otro proceso",
                )
            )
            session.flush()
            return "propio"

        # `_find` no ve la fila rival la primera vez: se simula la carrera.
        texto = ocr_store.stored_transcription(session, ing, tmp_path, tr)

        assert texto == "del otro proceso"
        assert len(_filas(session)) == 1
        assert otra.processing_status == ProcessingStatus.DISCARDED
        session.commit()
        session.refresh(otra)
        assert otra.processing_status == ProcessingStatus.DISCARDED


class _MotorConCajas:
    def __call__(self, ruta: str) -> object:
        return type(
            "R",
            (),
            {
                "txts": ("uno", "dos"),
                "boxes": np.array(
                    [[[1, 2], [3, 2], [3, 4], [1, 4]], [[1, 6], [3, 6], [3, 8], [1, 8]]],
                    dtype=np.float32,
                ),
                "scores": (0.5, 0.75),
            },
        )()


class TestTranscribeDetailed:
    def test_transcribe_devuelve_el_texto_del_resultado_detallado(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: _MotorConCajas())
        imagen = tmp_path / "f.png"
        imagen.write_bytes(b"x")

        res = ocr.transcribe_detailed(imagen)

        assert res.text == ocr.transcribe(imagen) == "uno\ndos"
        assert res.engine == "rapidocr"
        assert res.boxes == [
            {"text": "uno", "box": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]], "score": 0.5},
            {"text": "dos", "box": [[1.0, 6.0], [3.0, 6.0], [3.0, 8.0], [1.0, 8.0]], "score": 0.75},
        ]

    def test_las_cajas_son_tipos_planos_de_python(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: _MotorConCajas())
        imagen = tmp_path / "f.png"
        imagen.write_bytes(b"x")

        (caja, _) = ocr.transcribe_detailed(imagen).boxes or []

        assert type(caja["score"]) is float
        assert type(caja["box"][0][0]) is float

    def test_sin_cajas_utilizables_boxes_queda_nulo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un motor que no devuelve cajas del largo de los renglones no deja
        cajas desalineadas: queda nulo antes que mentir."""
        sin_cajas = type("R", (), {"txts": ("uno", "dos")})()
        monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: lambda ruta: sin_cajas)
        imagen = tmp_path / "f.png"
        imagen.write_bytes(b"x")

        res = ocr.transcribe_detailed(imagen)

        assert (res.text, res.engine, res.boxes) == ("uno\ndos", "rapidocr", None)


def _cargar_migracion(nombre: str) -> ModuleType:
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestMigracion0028:
    def test_sube_y_baja(self) -> None:
        modulo = _cargar_migracion("0028_ocr_transcriptions.py")
        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table("raw_ingestions", metadata, sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True))
        metadata.create_all(engine)

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.upgrade()
            conn.commit()
            insp = inspect(conn)
            assert "ocr_transcriptions" in insp.get_table_names()
            columnas = {c["name"] for c in insp.get_columns("ocr_transcriptions")}
            assert columnas == {"id", "ingestion_id", "engine", "ocr_version", "text", "boxes", "created_at"}
            unicas = {tuple(u["column_names"]) for u in insp.get_unique_constraints("ocr_transcriptions")}
            assert ("ingestion_id", "ocr_version") in unicas

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.downgrade()
            conn.commit()
            assert "ocr_transcriptions" not in inspect(conn).get_table_names()
        engine.dispose()


class TestContextoDelMotor:
    """El motor opina sobre el mismo texto que vio el modelo (K3)."""

    def test_usa_la_transcripcion_guardada_sin_volver_a_correr_el_ocr(
        self, session: Session, tmp_path: Path
    ) -> None:
        ing = _imagen(session)
        tr = _Transcriptor("pollo a 500")
        ocr_store.stored_transcription(session, ing, tmp_path, tr)

        texto = context.ad_text(session, ing, raw_store_dir=tmp_path, transcribe=tr)

        assert texto == "pollo a 500"
        assert tr.llamadas == 1

    def test_si_el_ocr_no_da_nada_queda_lo_que_confirmo_el_operador(
        self, session: Session, tmp_path: Path
    ) -> None:
        ing = _imagen(session)
        ing.original_text = "texto confirmado"

        texto = context.ad_text(session, ing, raw_store_dir=tmp_path, transcribe=lambda p: "")

        assert texto == "texto confirmado"
