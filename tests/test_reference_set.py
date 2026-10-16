"""Conjunto de referencia congelado (slice B3-1).

Contrato: `docs/spec-slice-B3.md`, secciones 1 y 2, casos 1 a 14. Los
esperados están escritos A MANO, antes que el código, y salen de la
especificación: quién entra (curado, con filas, sin asistencia, no excluido),
qué lleva cada fila congelada (el id de la fila y los seis campos de
`row_correctness`), la forma canónica (`340.00` y `340` dan el mismo hash, el
orden de lectura no importa) y qué es deriva (el hash de las filas vivas ya no
coincide con el congelado). Ningún esperado sale de correr el módulo.

Los casos 10 a 14 pasan por `POST /curation/{id}/edit` y `/delete` reales. El
caso 3 usa el pre-cálculo con el carril 1 encendido y `lane1.mark_ok`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.extraction import llm_assist, reference_set
from saimc.models import (
    ContentType,
    LlmProposal,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReferenceSetMember,
    SourceType,
)
from saimc.validation import lane1
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher

# `mundo` es una fixture: importarla la registra en este módulo. La usa solo el
# caso 3, que necesita el canal y el producto con los que acepta el carril 1.
from tests.test_lane1 import _aceptar, mundo  # noqa: F401

#: Los siete datos de una fila congelada: el id de la fila viva y los seis
#: campos contra los que compara `row_correctness` (especificación, sección 1).
_ROW_KEYS = {
    "price_history_id",
    "product_id",
    "province_id",
    "price",
    "currency",
    "quantity",
    "unit",
}

# --- datos ----------------------------------------------------------------------


@pytest.fixture
def catalogo(session: Session) -> SimpleNamespace:
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True)
    stgo = Province(code="SC", name="Santiago de Cuba")
    session.add_all([pollo, aceite, stgo])
    session.commit()
    return SimpleNamespace(pollo=pollo, aceite=aceite, stgo=stgo)


_n = 0


def _curado(
    session: Session,
    *,
    content_type: str = ContentType.TEXT,
    status: str = ProcessingStatus.CURATED,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=(
            SourceType.INBOX_TEXT if content_type == ContentType.TEXT else SourceType.INBOX_IMAGE
        ),
        file_path=f"raw/rs-{_n}.dat",
        content_hash=f"rs-{_n}",
        content_type=content_type,
        original_text="pollo a 340 la libra" if content_type == ContentType.TEXT else None,
        processing_status=status,
        # Mediodía UTC: el mismo día en La Habana, sin borde de medianoche.
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _fila(
    session: Session,
    ingesta: RawIngestion,
    producto: Product,
    provincia: Province | None,
    *,
    price: str = "340",
    quantity: str | None = "1",
    unit: str | None = "lb",
    currency: str = "CUP",
) -> PriceHistory:
    fila = PriceHistory(
        provenance="human",
        product_id=producto.id,
        province_id=provincia.id if provincia else None,
        price=Decimal(price),
        currency=currency,
        quantity=Decimal(quantity) if quantity is not None else None,
        unit=unit,
        source_id=ingesta.id,
    )
    session.add(fila)
    session.commit()
    return fila


def _revision_del_modelo(session: Session, ingesta: RawIngestion) -> None:
    """Deja al anuncio como curado con la propuesta a la vista."""
    propuesta = LlmProposal(
        ingestion_id=ingesta.id,
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
        LlmReview(ingestion_id=ingesta.id, proposal_id=propuesta.id, outcome="curated", rows=[])
    )
    session.commit()


def _ajustes(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_assist_log_path=tmp_path / "assist.jsonl",
        raw_store_dir=tmp_path / "raw",
    )


def _comando(session: Session, tmp_path: Path, *argv: str) -> tuple[int, str]:
    salida: list[str] = []
    codigo = reference_set.run(
        reference_set.parse_args(list(argv)), session, _ajustes(tmp_path), out=salida.append
    )
    session.commit()
    return codigo, "\n".join(salida)


def _congelar(session: Session, tmp_path: Path, *argv: str) -> int:
    codigo, _ = _comando(session, tmp_path, "--execute", *argv)
    assert codigo == 0
    version = reference_set.current_version(session)
    assert version is not None
    return version


def _miembros(session: Session, version: int | None = None) -> list[ReferenceSetMember]:
    stmt = select(ReferenceSetMember)
    if version is not None:
        stmt = stmt.where(ReferenceSetMember.set_version == version)
    return list(session.execute(stmt).scalars())


def _cuantos(session: Session) -> int:
    return session.execute(select(func.count()).select_from(ReferenceSetMember)).scalar_one()


def _seleccion(session: Session, tmp_path: Path, *excluidos: uuid.UUID) -> reference_set.Selection:
    return reference_set.select_members(
        session, assist_log_path=tmp_path / "assist.jsonl", excluded=frozenset(excluidos)
    )


# --- congelar: casos 1 a 9 ---------------------------------------------------------


def test_caso_1_un_curado_a_mano_con_dos_filas_entra_con_sus_dos_filas(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _curado(session)
    pollo = _fila(session, ingesta, catalogo.pollo, catalogo.stgo, price="340.00")
    aceite = _fila(
        session, ingesta, catalogo.aceite, None, price="2.50", currency="USD",
        quantity=None, unit=None,
    )

    version = _congelar(session, tmp_path)

    assert version == 1
    (miembro,) = _miembros(session)
    assert miembro.set_version == 1
    assert miembro.ingestion_id == ingesta.id
    assert miembro.frozen_at is not None
    assert len(miembro.rows) == 2
    por_id = {fila["price_history_id"]: fila for fila in miembro.rows}
    assert set(por_id) == {str(pollo.id), str(aceite.id)}
    assert all(set(fila) == _ROW_KEYS for fila in miembro.rows)
    assert por_id[str(pollo.id)] == {
        "price_history_id": str(pollo.id),
        "product_id": str(catalogo.pollo.id),
        "province_id": str(catalogo.stgo.id),
        "price": "340",
        "currency": "CUP",
        "quantity": "1",
        "unit": "lb",
    }
    assert por_id[str(aceite.id)] == {
        "price_history_id": str(aceite.id),
        "product_id": str(catalogo.aceite.id),
        "province_id": None,
        "price": "2.5",
        "currency": "USD",
        "quantity": None,
        "unit": None,
    }
    # El hash guardado es el de las filas guardadas, y es un SHA-256 en hexa.
    assert miembro.values_hash == reference_set.values_hash(miembro.rows)
    assert len(miembro.values_hash) == 64
    int(miembro.values_hash, 16)


def test_caso_2_un_curado_con_revision_del_modelo_no_entra_y_cuenta_como_asistido(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    a_mano = _curado(session)
    _fila(session, a_mano, catalogo.pollo, catalogo.stgo)
    asistido = _curado(session)
    _fila(session, asistido, catalogo.pollo, catalogo.stgo)
    _revision_del_modelo(session, asistido)

    seleccion = _seleccion(session, tmp_path)
    codigo, salida = _comando(session, tmp_path)

    assert [m.ingestion.id for m in seleccion.members] == [a_mano.id]
    assert (seleccion.assisted, seleccion.without_rows, seleccion.excluded) == (1, 0, 0)
    assert codigo == 0
    assert "asistidos: 1" in salida


def test_un_anuncio_del_registro_del_operador_tambien_cuenta_como_asistido(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    """El registro de `llm_assist` (anterior a `llm_reviews`) sigue contando:
    es el mismo `measure.assisted_ingestion_ids` de hoy."""
    ingesta = _curado(session)
    _fila(session, ingesta, catalogo.pollo, catalogo.stgo)
    (tmp_path / "assist.jsonl").write_text(
        json.dumps({"ingestion_id": str(ingesta.id), "model": "m"}) + "\n", encoding="utf-8"
    )
    # Guarda del propio test: si el registro no se leyera, no probaría nada.
    assert llm_assist.assisted_ingestion_ids(tmp_path / "assist.jsonl") == {str(ingesta.id)}

    seleccion = _seleccion(session, tmp_path)

    assert seleccion.members == []
    assert seleccion.assisted == 1


def test_caso_3_un_aceptado_por_el_carril_1_y_auditado_esta_bien_no_entra(
    session: Session, mundo: SimpleNamespace, tmp_path: Path  # noqa: F811
) -> None:
    (ingesta,) = _aceptar(session, tmp_path, mundo.canal)
    assert ingesta.processing_status == ProcessingStatus.CURATED
    assert lane1.mark_ok(session, ingesta.id) is True
    session.commit()

    seleccion = _seleccion(session, tmp_path)
    codigo, _ = _comando(session, tmp_path, "--execute")

    assert seleccion.members == []
    assert seleccion.assisted == 1
    # Sin nadie que entre, no se escribe una versión vacía.
    assert codigo != 0
    assert _cuantos(session) == 0


def test_caso_4_un_curado_sin_filas_no_entra(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    con_filas = _curado(session)
    _fila(session, con_filas, catalogo.pollo, catalogo.stgo)
    _curado(session)

    seleccion = _seleccion(session, tmp_path)
    _, salida = _comando(session, tmp_path)

    assert [m.ingestion.id for m in seleccion.members] == [con_filas.id]
    assert (seleccion.assisted, seleccion.without_rows, seleccion.excluded) == (0, 1, 0)
    assert "sin filas: 1" in salida


def test_un_pendiente_y_un_descartado_no_entran_ni_cuentan_como_motivo(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    _curado(session, status=ProcessingStatus.PENDING)
    _curado(session, status=ProcessingStatus.DISCARDED)

    seleccion = _seleccion(session, tmp_path)

    assert seleccion.members == []
    assert (seleccion.assisted, seleccion.without_rows, seleccion.excluded) == (0, 0, 0)


def test_caso_5_un_id_excluido_no_entra_y_se_cuenta_aparte(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    queda = _curado(session)
    _fila(session, queda, catalogo.pollo, catalogo.stgo)
    fuera = _curado(session)
    _fila(session, fuera, catalogo.pollo, catalogo.stgo)

    codigo, salida = _comando(session, tmp_path, "--exclude", str(fuera.id))
    _congelar(session, tmp_path, "--exclude", str(fuera.id))

    assert codigo == 0
    assert "excluidos a mano: 1" in salida
    assert "asistidos: 0" in salida
    assert [m.ingestion_id for m in _miembros(session)] == [queda.id]


def test_exclude_acepta_el_prefijo_de_ocho_caracteres_y_se_repite(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    queda = _curado(session)
    _fila(session, queda, catalogo.pollo, catalogo.stgo)
    fuera = [_curado(session), _curado(session)]
    for ingesta in fuera:
        _fila(session, ingesta, catalogo.pollo, catalogo.stgo)

    _congelar(
        session, tmp_path,
        "--exclude", str(fuera[0].id)[:8],
        "--exclude", str(fuera[1].id)[:8],
    )

    assert [m.ingestion_id for m in _miembros(session)] == [queda.id]


def test_un_prefijo_ambiguo_o_que_no_es_de_ningun_anuncio_corta_sin_escribir(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    # Dos anuncios con el mismo prefijo de 8: el id se fija a mano.
    gemelos = [
        uuid.UUID("abcdef01-0000-4000-8000-000000000001"),
        uuid.UUID("abcdef01-0000-4000-8000-000000000002"),
    ]
    for gemelo in gemelos:
        nueva = RawIngestion(
            id=gemelo,
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/gemelo-{gemelo}.dat",
            content_hash=f"gemelo-{gemelo}",
            content_type=ContentType.TEXT,
            original_text="pollo a 340",
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        )
        session.add(nueva)
        session.commit()
        _fila(session, nueva, catalogo.pollo, catalogo.stgo)

    ambiguo, salida_ambiguo = _comando(session, tmp_path, "--execute", "--exclude", "abcdef01")
    ajeno, salida_ajeno = _comando(session, tmp_path, "--execute", "--exclude", "00000000")
    corto, _ = _comando(session, tmp_path, "--execute", "--exclude", "abc")

    assert ambiguo != 0
    assert "abcdef01" in salida_ambiguo
    assert ajeno != 0
    assert "00000000" in salida_ajeno
    assert corto != 0
    assert _cuantos(session) == 0
    # El id completo sí desambigua.
    _congelar(session, tmp_path, "--exclude", str(gemelos[0]))
    assert [m.ingestion_id for m in _miembros(session)] == [gemelos[1]]


def test_caso_6_el_modo_en_seco_no_escribe_ninguna_fila(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = _curado(session)
    _fila(session, texto, catalogo.pollo, catalogo.stgo)
    _fila(session, texto, catalogo.aceite, catalogo.stgo)
    imagen = _curado(session, content_type=ContentType.IMAGE)
    _fila(session, imagen, catalogo.pollo, catalogo.stgo)

    codigo, salida = _comando(session, tmp_path)

    assert codigo == 0
    assert _cuantos(session) == 0
    assert reference_set.current_version(session) is None
    assert "EN SECO" in salida
    # Cuántos anuncios y filas entrarían, en total y por tipo.
    assert "2 anuncios, 3 filas" in salida
    assert "texto: 1 anuncios, 2 filas" in salida
    assert "imagen: 1 anuncios, 1 filas" in salida


def test_caso_7_340_00_y_340_dan_el_mismo_hash(
    session: Session, catalogo: SimpleNamespace
) -> None:
    def fila(price: str, quantity: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            product_id=catalogo.pollo.id,
            province_id=catalogo.stgo.id,
            price=Decimal(price),
            currency="CUP",
            quantity=Decimal(quantity),
            unit="lb",
        )

    con_ceros = reference_set.canonical_rows([fila("340.00", "1.000")])
    sin_ceros = reference_set.canonical_rows([fila("340", "1")])
    # `Decimal.normalize()` escribe 340 como 3.4E+2: tampoco puede colarse.
    exponente = reference_set.canonical_rows([fila("3.4E+2", "1E+0")])
    otro_precio = reference_set.canonical_rows([fila("341", "1")])

    assert con_ceros[0]["price"] == sin_ceros[0]["price"] == exponente[0]["price"] == "340"
    assert con_ceros[0]["quantity"] == "1"
    assert reference_set.values_hash(con_ceros) == reference_set.values_hash(sin_ceros)
    assert reference_set.values_hash(exponente) == reference_set.values_hash(sin_ceros)
    assert reference_set.values_hash(otro_precio) != reference_set.values_hash(sin_ceros)


def test_la_unidad_se_congela_canonica(catalogo: SimpleNamespace) -> None:
    def fila(unit: str | None) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            product_id=catalogo.aceite.id,
            province_id=None,
            price=Decimal("900"),
            currency="CUP",
            quantity=Decimal("0.500"),
            unit=unit,
        )

    (litros,) = reference_set.canonical_rows([fila("Litros")])
    (corta,) = reference_set.canonical_rows([fila("l")])

    # «litros» → «l» es la tabla de `canonical_unit`, no algo de este módulo.
    assert litros["unit"] == corta["unit"] == "l"
    assert litros["quantity"] == "0.5"
    assert litros["province_id"] is None
    assert reference_set.values_hash([litros]) == reference_set.values_hash([corta])


def test_caso_8_las_mismas_filas_en_otro_orden_dan_el_mismo_hash(
    catalogo: SimpleNamespace,
) -> None:
    def fila(fila_id: str, price: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.UUID(fila_id),
            product_id=catalogo.pollo.id,
            province_id=catalogo.stgo.id,
            price=Decimal(price),
            currency="CUP",
            quantity=Decimal("1"),
            unit="lb",
        )

    a = fila("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "340")
    b = fila("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "360")

    ida = reference_set.canonical_rows([a, b])
    vuelta = reference_set.canonical_rows([b, a])

    assert [f["price_history_id"] for f in ida] == [str(a.id), str(b.id)]
    assert ida == vuelta
    assert reference_set.values_hash(ida) == reference_set.values_hash(vuelta)
    # Y el hash tampoco depende de cómo llegue ordenada la lista ya canónica
    # (p. ej. releída del JSON de la base).
    assert reference_set.values_hash(list(reversed(ida))) == reference_set.values_hash(ida)


def test_el_hash_del_conjunto_son_las_lineas_id_hash_ordenadas() -> None:
    """Especificación, sección 1: SHA-256 sobre las líneas
    `ingestion_id:values_hash` ordenadas. Acá se arma a mano."""
    pares = [("b-id", "h2"), ("a-id", "h1")]
    esperado = hashlib.sha256(b"a-id:h1\nb-id:h2").hexdigest()

    assert reference_set.set_hash(pares) == esperado
    assert reference_set.set_hash(list(reversed(pares))) == esperado


def test_caso_9_congelar_dos_veces_da_dos_versiones_con_el_mismo_hash(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    for _ in range(2):
        ingesta = _curado(session)
        _fila(session, ingesta, catalogo.pollo, catalogo.stgo)

    primera = _congelar(session, tmp_path)
    foto = {
        m.id: (m.set_version, m.ingestion_id, m.rows, m.values_hash, m.frozen_at)
        for m in _miembros(session, 1)
    }
    hash_1 = reference_set.version_hash(session, 1)
    segunda = _congelar(session, tmp_path)

    assert (primera, segunda) == (1, 2)
    assert len(_miembros(session, 1)) == 2
    assert len(_miembros(session, 2)) == 2
    assert reference_set.version_hash(session, 2) == hash_1
    session.expire_all()
    assert {
        m.id: (m.set_version, m.ingestion_id, m.rows, m.values_hash, m.frozen_at)
        for m in _miembros(session, 1)
    } == foto
    assert reference_set.current_version(session) == 2


def test_execute_imprime_la_version_los_conteos_y_el_hash_del_conjunto(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _curado(session)
    _fila(session, ingesta, catalogo.pollo, catalogo.stgo)

    codigo, salida = _comando(session, tmp_path, "--execute")

    assert codigo == 0
    assert "versión 1" in salida
    assert "1 anuncios, 1 filas" in salida
    assert reference_set.version_hash(session, 1) in salida


def test_un_anuncio_del_conjunto_no_se_puede_borrar_de_raw_ingestions(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    """La clave foránea es RESTRICT, como toda referencia al registro crudo."""
    ingesta = _curado(session)
    fila = _fila(session, ingesta, catalogo.pollo, catalogo.stgo)
    _congelar(session, tmp_path)
    session.delete(fila)
    session.commit()

    session.delete(ingesta)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_el_mismo_anuncio_no_entra_dos_veces_a_una_version(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _curado(session)
    _fila(session, ingesta, catalogo.pollo, catalogo.stgo)
    _congelar(session, tmp_path)
    (miembro,) = _miembros(session)

    session.add(
        ReferenceSetMember(
            set_version=1,
            ingestion_id=ingesta.id,
            rows=miembro.rows,
            values_hash=miembro.values_hash,
            frozen_at=miembro.frozen_at,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- editar un congelado: casos 10 a 14 ---------------------------------------------


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
    app.dependency_overrides[get_rate_fetcher] = lambda: (lambda key, day: None)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _campos(fila: PriceHistory, i: int, *, borrar: bool = False, **cambios: str) -> dict[str, str]:
    campos = {
        "price_id": str(fila.id),
        "product_id": str(fila.product_id),
        "province_id": str(fila.province_id) if fila.province_id else "",
        "price": str(fila.price),
        "currency": fila.currency,
        "quantity": str(fila.quantity) if fila.quantity is not None else "",
        "unit": fila.unit or "",
        "sale_format": "",
        "packaging": "",
        "min_purchase": "",
        "price_basis": "",
    }
    campos.update(cambios)
    datos = {f"{campo}_{i}": valor for campo, valor in campos.items()}
    if borrar:
        datos[f"delete_{i}"] = "on"
    return datos


def _editar(client: TestClient, ingesta: RawIngestion, *filas: dict[str, str]) -> None:
    datos = {
        "source_reference": "",
        "published_date": "2026-09-01",
        "row_count": str(len(filas)),
    }
    for fila in filas:
        datos.update(fila)
    respuesta = client.post(f"/curation/{ingesta.id}/edit", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303, respuesta.text


def _miembro_congelado(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> tuple[RawIngestion, PriceHistory, PriceHistory]:
    """Un anuncio de dos filas (pollo a 340, aceite a 900) congelado en la
    versión 1, junto a otro que nadie toca."""
    ingesta = _curado(session)
    pollo = _fila(session, ingesta, catalogo.pollo, catalogo.stgo, price="340")
    aceite = _fila(session, ingesta, catalogo.aceite, catalogo.stgo, price="900", unit="l")
    quieto = _curado(session)
    _fila(session, quieto, catalogo.pollo, catalogo.stgo)
    assert _congelar(session, tmp_path) == 1
    return ingesta, pollo, aceite


def _precios_congelados(session: Session, ingesta: RawIngestion, version: int = 1) -> list[str]:
    session.expire_all()
    (miembro,) = [m for m in _miembros(session, version) if m.ingestion_id == ingesta.id]
    return sorted(fila["price"] for fila in miembro.rows)


def _con_deriva(session: Session, version: int = 1) -> list[uuid.UUID]:
    return [m.ingestion_id for m in reference_set.drifted(session, version)]


def test_caso_10_editar_un_precio_cambia_lo_vivo_no_lo_congelado_y_hay_deriva(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    ingesta, pollo, aceite = _miembro_congelado(session, catalogo, tmp_path)
    assert _con_deriva(session) == []

    _editar(client, ingesta, _campos(pollo, 0, price="360"), _campos(aceite, 1))

    session.expire_all()
    viva = session.get(PriceHistory, pollo.id)
    assert viva is not None
    assert viva.price == Decimal("360")
    assert _precios_congelados(session, ingesta) == ["340", "900"]
    assert _con_deriva(session) == [ingesta.id]
    codigo, salida = _comando(session, tmp_path, "status")
    assert codigo == 0
    assert "Miembros con deriva: 1" in salida
    assert str(ingesta.id) in salida


def test_caso_11_borrar_una_fila_desde_edit_es_deriva_y_la_fila_congelada_queda(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    ingesta, pollo, aceite = _miembro_congelado(session, catalogo, tmp_path)

    _editar(client, ingesta, _campos(pollo, 0, borrar=True), _campos(aceite, 1))

    session.expire_all()
    assert session.get(PriceHistory, pollo.id) is None
    assert _precios_congelados(session, ingesta) == ["340", "900"]
    assert _con_deriva(session) == [ingesta.id]


def test_caso_12_eliminar_el_anuncio_lo_deja_en_el_conjunto_con_deriva(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    ingesta, _, _ = _miembro_congelado(session, catalogo, tmp_path)

    respuesta = client.post(f"/curation/{ingesta.id}/delete", follow_redirects=False)

    assert respuesta.status_code == 303
    session.expire_all()
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert len(_miembros(session, 1)) == 2
    assert _precios_congelados(session, ingesta) == ["340", "900"]
    assert _con_deriva(session) == [ingesta.id]


def test_caso_13_una_edicion_sin_cambios_no_produce_deriva(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    ingesta, pollo, aceite = _miembro_congelado(session, catalogo, tmp_path)

    _editar(client, ingesta, _campos(pollo, 0), _campos(aceite, 1))

    assert _con_deriva(session) == []
    _, salida = _comando(session, tmp_path, "status")
    assert "Miembros con deriva: 0" in salida


def test_caso_14_la_version_nueva_toma_el_precio_corregido_y_la_1_sigue_con_el_viejo(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path, client: TestClient
) -> None:
    ingesta, pollo, aceite = _miembro_congelado(session, catalogo, tmp_path)
    hash_1 = reference_set.version_hash(session, 1)
    _editar(client, ingesta, _campos(pollo, 0, price="360"), _campos(aceite, 1))

    assert _congelar(session, tmp_path) == 2

    assert _precios_congelados(session, ingesta, 2) == ["360", "900"]
    assert _precios_congelados(session, ingesta, 1) == ["340", "900"]
    assert reference_set.version_hash(session, 1) == hash_1
    assert reference_set.version_hash(session, 2) != hash_1
    # La deriva se mira contra la versión vigente: la 2 nació al día.
    assert _con_deriva(session, 2) == []
    assert _con_deriva(session, 1) == [ingesta.id]


# --- status -------------------------------------------------------------------------


def test_status_sin_conjunto_congelado_lo_dice_y_no_escribe(
    session: Session, tmp_path: Path
) -> None:
    codigo, salida = _comando(session, tmp_path, "status")

    assert codigo != 0
    assert "python -m saimc.extraction.reference_set --execute" in salida
    assert _cuantos(session) == 0


def test_status_imprime_la_version_vigente_su_n_y_su_hash(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = _curado(session)
    _fila(session, texto, catalogo.pollo, catalogo.stgo)
    imagen = _curado(session, content_type=ContentType.IMAGE)
    _fila(session, imagen, catalogo.pollo, catalogo.stgo)
    _fila(session, imagen, catalogo.aceite, catalogo.stgo)
    _congelar(session, tmp_path)
    antes = _cuantos(session)

    codigo, salida = _comando(session, tmp_path, "status")

    assert codigo == 0
    assert "versión 1" in salida
    assert "2 anuncios, 3 filas" in salida
    assert "texto: 1 anuncios, 1 filas" in salida
    assert "imagen: 1 anuncios, 2 filas" in salida
    assert reference_set.version_hash(session, 1) in salida
    assert "Miembros con deriva: 0" in salida
    assert _cuantos(session) == antes
