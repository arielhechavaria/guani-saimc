"""H0 contra el conjunto de referencia congelado (slice B3-2).

Contrato: `docs/spec-slice-B3.md`, sección 3, casos 15 a 20. Los esperados
están escritos A MANO, antes que el código, y salen de la especificación: sin
conjunto congelado no se mide ni se registra; las filas confirmadas son las
congeladas y no las vivas; quién se evalúa lo fija la versión vigente; la línea
del registro lleva la versión, la huella y la deriva; el delta no cruza de un
conjunto a otro; y lo ya escrito del archivo no se toca. Ningún esperado sale
de correr el módulo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.extraction import measure, reference_set
from saimc.models import (
    Currency,
    LlmProposal,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReferenceSetMember,
)
from tests.test_h0_measurement import _curar_imagen, _curar_texto, _descartar

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pollo(session: Session) -> Product:
    producto = Product(
        name="Pollo", category="carnes", unit="lb", aliases=["pollo congelado"], is_pilot=True
    )
    session.add(producto)
    session.commit()
    return producto


@pytest.fixture
def habana(session: Session) -> Province:
    provincia = Province(code="LH", name="La Habana")
    session.add(provincia)
    session.commit()
    return provincia


def _sin_ocr(path: Path) -> str:
    return ""


def _anuncio(session: Session, hash_: str) -> RawIngestion:
    return session.execute(
        select(RawIngestion).where(RawIngestion.content_hash == hash_)
    ).scalar_one()


def _fila_viva(session: Session, hash_: str) -> PriceHistory:
    return session.execute(
        select(PriceHistory).where(PriceHistory.source_id == _anuncio(session, hash_).id)
    ).scalar_one()


def _pollo_a(session: Session, pollo: Product, hash_: str, precio: str) -> None:
    """Un curado a mano de una fila: «pollo a <precio>», sin provincia ni cantidad."""
    _curar_texto(
        session,
        hash_=hash_,
        texto=f"pollo a {precio}",
        product_id=pollo.id,
        price=precio,
        currency=Currency.CUP.value,
        province_id=None,
        quantity=None,
        unit=None,
    )


def _congelar(session: Session) -> int:
    version = reference_set.freeze(
        session, reference_set.select_members(session, assist_log_path=None)
    )
    session.commit()
    return version


def _correr(
    session: Session, runs_path: Path, *, no_record: bool = False
) -> tuple[int, str]:
    lineas: list[str] = []
    codigo = measure.run(
        argparse.Namespace(no_record=no_record),
        session,
        runs_path=runs_path,
        transcribe=_sin_ocr,
        out=lineas.append,
    )
    return codigo, "\n".join(lineas)


# --- caso 15 ----------------------------------------------------------------------


def test_caso_15_sin_conjunto_congelado_termina_con_error_y_no_registra(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    # Hay oro vivo de sobra: un curado a mano con su fila. No alcanza.
    _pollo_a(session, pollo, "vivo", "100")
    runs = tmp_path / "h0-runs.jsonl"

    codigo, salida = _correr(session, runs)

    assert codigo != 0
    assert "python -m saimc.extraction.reference_set" in salida
    assert not runs.exists()


def test_caso_15_el_error_tampoco_toca_un_archivo_que_ya_tiene_corridas(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    _pollo_a(session, pollo, "vivo", "100")
    runs = tmp_path / "h0-runs.jsonl"
    runs.write_bytes(b'{"complete_accuracy": 0.4}\n')

    codigo, _ = _correr(session, runs)

    assert codigo != 0
    assert runs.read_bytes() == b'{"complete_accuracy": 0.4}\n'


def test_sin_conjunto_congelado_no_hay_camino_de_vuelta_al_oro_vivo(
    session: Session, pollo: Product
) -> None:
    """La función tampoco mide: no es solo el comando el que se niega."""
    _pollo_a(session, pollo, "vivo", "100")

    with pytest.raises(measure.NoReferenceSetError):
        measure.compute_h0_report(session, transcribe=_sin_ocr)


# --- caso 16 ----------------------------------------------------------------------


def test_caso_16_el_precio_que_cuenta_es_el_congelado_no_el_vivo(
    session: Session, pollo: Product
) -> None:
    _pollo_a(session, pollo, "editado", "100")
    _congelar(session)
    _fila_viva(session, "editado").price = Decimal("120")
    session.commit()

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    # El extractor lee «pollo a 100». Contra el 100 congelado acierta; contra
    # el 120 vivo fallaría.
    assert report.evaluated_rows_count == 1
    assert report.price_accuracy == 1.0
    assert report.complete_accuracy == 1.0


def test_una_fila_borrada_despues_del_congelado_se_sigue_evaluando(
    session: Session, pollo: Product
) -> None:
    _pollo_a(session, pollo, "borrada", "100")
    _congelar(session)
    session.delete(_fila_viva(session, "borrada"))
    session.commit()

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.evaluated_ads_count == 1
    assert report.evaluated_rows_count == 1
    assert report.complete_accuracy == 1.0


def test_un_miembro_descartado_despues_sigue_en_la_medicion(
    session: Session, pollo: Product
) -> None:
    """`/delete` borra las filas y descarta el anuncio: el miembro queda."""
    _pollo_a(session, pollo, "eliminado", "100")
    _congelar(session)
    session.delete(_fila_viva(session, "eliminado"))
    _anuncio(session, "eliminado").processing_status = ProcessingStatus.DISCARDED
    session.commit()

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.evaluated_ads_count == 1
    assert report.complete_accuracy == 1.0
    # La composición sí es la de hoy: ya no hay ningún curado.
    assert report.total_curated == 0
    assert report.total_discarded == 1


def test_las_filas_congeladas_vuelven_a_los_tipos_que_compara_row_correctness(
    session: Session, pollo: Product, habana: Province
) -> None:
    """`rows` guarda texto o nulo. Un `"500"` de texto no es igual al `500`
    que propone el extractor, ni un id de texto al id de la provincia: si la
    conversión falta, este caso da 0 en vez de 1."""
    _curar_texto(
        session,
        hash_="completo",
        texto="pollo congelado 40lb a 500 habana",
        product_id=pollo.id,
        price="500.00",
        currency=Currency.CUP.value,
        province_id=habana.id,
        quantity="40.0",
        unit="lb",
    )
    _congelar(session)

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.product_accuracy == 1.0
    assert report.price_accuracy == 1.0
    assert report.province_accuracy == 1.0
    assert report.quantity_unit_accuracy == 1.0
    assert report.complete_accuracy == 1.0


# --- caso 17 ----------------------------------------------------------------------


def test_caso_17_un_curado_a_mano_posterior_al_congelado_no_se_evalua(
    session: Session, pollo: Product
) -> None:
    _pollo_a(session, pollo, "miembro", "100")
    _congelar(session)
    # Curado después, a mano y sin asistencia: con el oro vivo habría entrado.
    # Su texto no nombra el producto, así que bajaría la precisión a 1/2.
    _curar_texto(
        session,
        hash_="posterior",
        texto="comida rica a 300",
        product_id=pollo.id,
        price="300",
        currency=Currency.CUP.value,
        province_id=None,
        quantity=None,
        unit=None,
    )

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.evaluated_ads_count == 1
    assert report.evaluated_rows_count == 1
    assert report.product_accuracy == 1.0
    # La composición del corpus sí lo cuenta.
    assert report.total_curated == 2


def test_se_mide_contra_la_version_mayor(session: Session, pollo: Product) -> None:
    _pollo_a(session, pollo, "uno", "100")
    _congelar(session)
    _pollo_a(session, pollo, "dos", "200")
    version = _congelar(session)

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert version == 2
    assert report.reference_set_version == 2
    assert report.evaluated_ads_count == 2


def test_la_composicion_y_la_tasa_de_descarte_salen_de_la_base_viva(
    session: Session, pollo: Product
) -> None:
    _pollo_a(session, pollo, "miembro", "100")
    _congelar(session)
    _pollo_a(session, pollo, "posterior", "200")
    _curar_imagen(
        session,
        hash_="imagen-posterior",
        product_id=pollo.id,
        price="300",
        province_id=None,
        quantity=None,
        unit=None,
    )
    _descartar(session, hash_="ruido")

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.total_curated == 3
    assert report.text_curated_count == 2
    assert report.image_curated_count == 1
    assert report.total_discarded == 1
    assert report.total_processed == 4
    assert report.discarded_off_focus == 0
    assert report.discard_rate == pytest.approx(1 / 4)
    assert report.evaluated_ads_count == 1


def test_los_conteos_de_asistencia_se_escriben_en_cero(
    session: Session, pollo: Product
) -> None:
    """La pertenencia ya está fijada: una revisión del modelo posterior al
    congelado no saca al miembro ni se cuenta como «excluido por asistencia»."""
    _pollo_a(session, pollo, "miembro", "100")
    _congelar(session)
    anuncio = _anuncio(session, "miembro")
    propuesta = LlmProposal(
        ingestion_id=anuncio.id,
        model="m",
        prompt_version="v",
        status="ok",
        rows=[],
        input_tokens=0,
        output_tokens=0,
    )
    session.add(propuesta)
    session.flush()
    session.add(
        LlmReview(ingestion_id=anuncio.id, proposal_id=propuesta.id, outcome="curated", rows=[])
    )
    session.commit()

    report = measure.compute_h0_report(session, transcribe=_sin_ocr)

    assert report.evaluated_ads_count == 1
    assert report.assisted_ads_count == 0
    assert report.assisted_rows_count == 0


# --- caso 18 ----------------------------------------------------------------------


def _ultima_linea(runs: Path) -> dict[str, object]:
    registro = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1])
    assert isinstance(registro, dict)
    return registro


def test_caso_18_la_linea_trae_la_version_la_huella_y_la_deriva(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    _pollo_a(session, pollo, "a", "100")
    _pollo_a(session, pollo, "b", "200")
    _congelar(session)
    # La huella del conjunto, por su definición (especificación, sección 1):
    # SHA-256 de las líneas `ingestion_id:values_hash` ordenadas.
    miembros = session.execute(select(ReferenceSetMember)).scalars().all()
    lineas = sorted(f"{m.ingestion_id}:{m.values_hash}" for m in miembros)
    huella = hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()
    runs = tmp_path / "h0-runs.jsonl"

    codigo, salida = _correr(session, runs)

    assert codigo == 0
    registro = _ultima_linea(runs)
    assert registro["reference_set_version"] == 1
    assert registro["reference_set_hash"] == huella
    assert registro["reference_set_drifted"] == 0
    assert registro["assisted_ads_count"] == 0
    assert registro["assisted_rows_count"] == 0
    assert registro["evaluated_ads_count"] == 2
    # 2 anuncios, no 300: no se baja el número.
    assert registro["is_official"] is False
    # La salida dice contra qué se midió.
    assert "versión 1" in salida
    assert "Miembros con deriva" in salida


def test_caso_18_un_miembro_editado_cuenta_como_deriva_en_la_linea(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    _pollo_a(session, pollo, "a", "100")
    _pollo_a(session, pollo, "b", "200")
    _congelar(session)
    _fila_viva(session, "a").price = Decimal("120")
    session.commit()
    runs = tmp_path / "h0-runs.jsonl"

    _correr(session, runs)

    registro = _ultima_linea(runs)
    assert registro["reference_set_drifted"] == 1
    # La deriva no saca al miembro ni corta la corrida.
    assert registro["evaluated_ads_count"] == 2
    assert registro["complete_accuracy"] == 1.0


def test_no_record_mide_y_no_escribe(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    _pollo_a(session, pollo, "a", "100")
    _congelar(session)
    runs = tmp_path / "h0-runs.jsonl"

    codigo, salida = _correr(session, runs, no_record=True)

    assert codigo == 0
    assert "Extracción completa correcta" in salida
    assert not runs.exists()


# --- caso 19 ----------------------------------------------------------------------


def _linea(complete: float, **claves: object) -> str:
    return json.dumps(
        {"timestamp": "2026-10-10T00:00:00+00:00", "complete_accuracy": complete, **claves}
    )


def _deltas(tmp_path: Path, *lineas: str) -> list[float | None]:
    runs = tmp_path / "h0-runs.jsonl"
    runs.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return [run.complete_delta for run in measure.read_h0_runs(runs)]


def test_caso_19_una_linea_nueva_despues_de_una_vieja_no_tiene_delta(
    tmp_path: Path,
) -> None:
    deltas = _deltas(
        tmp_path,
        _linea(0.40),
        _linea(0.45, reference_set_version=1, reference_set_hash="h1"),
    )

    assert deltas == [None, None]


def test_dos_corridas_sobre_el_mismo_conjunto_si_tienen_delta(tmp_path: Path) -> None:
    deltas = _deltas(
        tmp_path,
        _linea(0.40),
        _linea(0.45, reference_set_version=1, reference_set_hash="h1"),
        _linea(0.50, reference_set_version=1, reference_set_hash="h1"),
    )

    assert deltas[:2] == [None, None]
    assert deltas[2] == pytest.approx(0.05)


def test_una_version_nueva_del_conjunto_corta_el_delta(tmp_path: Path) -> None:
    deltas = _deltas(
        tmp_path,
        _linea(0.45, reference_set_version=1, reference_set_hash="h1"),
        _linea(0.50, reference_set_version=2, reference_set_hash="h2"),
        _linea(0.52, reference_set_version=2, reference_set_hash="h2"),
    )

    assert deltas[:2] == [None, None]
    assert deltas[2] == pytest.approx(0.02)


def test_la_misma_version_con_otra_huella_no_comparte_conjunto(tmp_path: Path) -> None:
    """Otra base (la de desarrollo) también tiene una versión 1."""
    deltas = _deltas(
        tmp_path,
        _linea(0.45, reference_set_version=1, reference_set_hash="h1"),
        _linea(0.50, reference_set_version=1, reference_set_hash="otra"),
    )

    assert deltas == [None, None]


def test_el_resumen_trae_la_version_y_una_linea_vieja_no_la_tiene(tmp_path: Path) -> None:
    runs = tmp_path / "h0-runs.jsonl"
    runs.write_text(
        _linea(0.40) + "\n" + _linea(0.45, reference_set_version=3, reference_set_hash="h") + "\n",
        encoding="utf-8",
    )

    vieja, nueva = measure.read_h0_runs(runs)

    assert vieja.reference_set_version is None
    assert nueva.reference_set_version == 3


def test_las_corridas_registradas_del_repo_conservan_sus_deltas() -> None:
    """Entre líneas viejas el delta sigue como estaba: la serie ya publicada en
    `/control` no cambia por este slice."""
    runs = measure.read_h0_runs(_REPO_ROOT / "docs" / "h0-runs.jsonl")
    viejas = [run for run in runs if run.reference_set_version is None]

    assert len(viejas) >= 10
    # La última corrida contra el oro vivo (2026-09-09) dio 39,67 % y la
    # anterior (2026-09-04) 41,6 %: los dos números leídos del archivo.
    assert viejas[9].complete_accuracy == pytest.approx(0.39672131147540984)
    assert viejas[9].complete_delta == pytest.approx(0.39672131147540984 - 0.416)


# --- caso 20 ----------------------------------------------------------------------


def test_caso_20_las_lineas_ya_escritas_quedan_byte_por_byte_iguales(
    session: Session, pollo: Product, tmp_path: Path
) -> None:
    runs = tmp_path / "h0-runs.jsonl"
    shutil.copyfile(_REPO_ROOT / "docs" / "h0-runs.jsonl", runs)
    antes = runs.read_bytes()
    assert antes.count(b"\n") >= 10
    _pollo_a(session, pollo, "a", "100")
    _congelar(session)

    codigo, _ = _correr(session, runs)

    despues = runs.read_bytes()
    assert codigo == 0
    assert despues[: len(antes)] == antes
    # Y lo agregado es exactamente una línea.
    agregado = despues[len(antes) :]
    assert agregado.endswith(b"\n")
    assert agregado.count(b"\n") == 1
    assert json.loads(agregado)["reference_set_version"] == 1
