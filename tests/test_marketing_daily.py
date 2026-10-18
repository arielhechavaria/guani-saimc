"""Las tarjetas diarias de marketing (`saimc.marketing.daily`).

Los casos esperados están escritos a mano, antes que el código: qué toca cada
día de la semana, que se usen los datos del día anterior y no los del día que
corre, cuántos días se conservan, que dos corridas del mismo día no dupliquen
(`docs/spec-slice-fabrica-publicaciones.md` §1.7). El lote ya no rasteriza.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import saimc.marketing.daily as daily_module
from saimc.marketing.context import CARD_TYPES, build_context
from saimc.marketing.daily import (
    RETENTION_DAYS,
    candidate_order,
    cards_for_day,
    havana_today,
    main,
    purge_old,
    run_daily,
    to_havana,
)
from saimc.marketing.posts import create_draft, discard
from saimc.models import (
    ContentType,
    Currency,
    MarketingPost,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.marketing_post import PostOrigin, PostStatus
from saimc.models.raw_ingestion import ProcessingStatus

#: Un lunes. El martes siguiente es el 20, el jueves el 22.
LUNES = date(2026, 10, 19)
JUEVES = date(2026, 10, 22)


def _kind(slug: str):
    return next(t for t in CARD_TYPES if t.slug == slug)


# --- Qué toca cada día --------------------------------------------------------


@pytest.mark.parametrize(
    ("dia", "esperado"),
    [
        (date(2026, 10, 19), ("dato-del-dia", "como-se-movio")),  # lunes
        (date(2026, 10, 20), ("dato-del-dia",)),  # martes
        (date(2026, 10, 21), ("dato-del-dia",)),  # miércoles
        (date(2026, 10, 22), ("dato-del-dia", "de-donde-sale")),  # jueves
        (date(2026, 10, 23), ("dato-del-dia",)),  # viernes
        (date(2026, 10, 24), ("dato-del-dia",)),  # sábado
        (date(2026, 10, 25), ("dato-del-dia",)),  # domingo
    ],
)
def test_cada_dia_de_la_semana_toca_lo_que_dice_el_plan(dia: date, esperado: tuple) -> None:
    assert cards_for_day(dia) == esperado


# --- Rotación de producto y provincia ----------------------------------------


def test_la_rotacion_es_estable_dentro_del_dia_y_recorre_todos_los_candidatos() -> None:
    productos = ("Arroz", "Frijol", "Aceite")

    assert candidate_order(productos, LUNES) == candidate_order(productos, LUNES)
    primeros = {
        candidate_order(productos, LUNES + timedelta(days=i))[0] for i in range(3)
    }
    assert primeros == set(productos)
    # Siempre devuelve los mismos tres, solo cambia por dónde empieza.
    assert sorted(candidate_order(productos, JUEVES)) == sorted(productos)


def test_la_rotacion_sin_candidatos_es_vacia() -> None:
    assert candidate_order((), LUNES) == []


# --- Retención ----------------------------------------------------------------


def test_purga_los_dias_de_mas_de_30_dias_y_no_toca_el_resto(tmp_path: Path) -> None:
    hoy = date(2026, 10, 19)
    for nombre in ("2026-09-18", "2026-09-19", "2026-10-18", "2026-10-19"):
        (tmp_path / nombre).mkdir()
        (tmp_path / nombre / "tarjeta.png").write_bytes(b"x")
    # Cosas que no son un día: ni se miran ni se borran.
    (tmp_path / "notas").mkdir()
    (tmp_path / "LEEME.txt").write_text("hola")

    borrados = purge_old(tmp_path, hoy)

    # 2026-09-18 tiene 31 días: se va. 2026-09-19 tiene exactamente 30: se queda.
    assert RETENTION_DAYS == 30
    assert [p.name for p in borrados] == ["2026-09-18"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-09-19", "2026-10-18", "2026-10-19", "LEEME.txt", "notas",
    ]


def test_purgar_un_directorio_inexistente_no_falla(tmp_path: Path) -> None:
    assert purge_old(tmp_path / "no-existe", LUNES) == []


# --- Datos -------------------------------------------------------------------


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz piloto: La Habana con 3 anuncios la semana pasada, 2 la anterior
    y uno **del propio lunes** a 9000 que no puede entrar (la corrida usa los
    datos del día anterior); Santiago con uno. Frijol es piloto pero no tiene
    ni un anuncio. Pollo no es piloto."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    frijol = Product(name="Frijol", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=False)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, frijol, pollo, habana, santiago])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/d.dat",
        content_hash="hash-daily",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()

    def precio(provincia: Province, hace: int, valor: str) -> None:
        session.add(
            PriceHistory(
                provenance="human",
                product_id=arroz.id,
                province_id=provincia.id,
                price=Decimal(valor),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=LUNES - timedelta(days=hace),
            )
        )

    precio(habana, 3, "300")
    precio(habana, 2, "320")
    precio(habana, 1, "350")
    precio(santiago, 2, "400")
    precio(habana, 0, "9000")  # el día de la corrida: no entra
    precio(habana, 11, "250")  # semana anterior, para «Cómo se movió»
    precio(habana, 10, "260")
    session.commit()


def test_las_cifras_son_de_hasta_el_dia_anterior(session: Session, datos: None) -> None:
    contexto = build_context(
        _kind("dato-del-dia"), session, product="Arroz", province=None,
        hoy=LUNES - timedelta(days=1),
    )

    assert contexto is not None
    filas = {fila.name: fila for fila in contexto["card"].rows}
    # Los 3 anuncios de la semana; el de 9000 del propio lunes quedó afuera.
    assert filas["La Habana"].count == 3
    assert "350" in filas["La Habana"].range_text
    assert "9" not in filas["La Habana"].range_text


def test_sin_hoy_el_contexto_sigue_siendo_el_de_hoy(session: Session, datos: None) -> None:
    """El router no pasa `hoy`: su comportamiento no cambia con la mudanza."""
    contexto = build_context(_kind("carrusel-cierre"), session, product=None, province=None)

    assert contexto == {}


# --- La corrida: crea los borradores del día ----------------------------------


def _posts(session: Session) -> list[MarketingPost]:
    return list(session.scalars(select(MarketingPost).order_by(MarketingPost.batch_slot)))


def test_un_lunes_crea_los_borradores_de_dato_del_dia_y_como_se_movio(
    session: Session, datos: None, tmp_path: Path
) -> None:
    # Frijol no tiene anuncios: cualquiera sea el día en que la rotación
    # arranque por él, el borrador sale de Arroz, que sí tiene datos.
    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    posts = _posts(session)
    assert [(p.batch_slot, p.status, p.origin, p.planned_for) for p in posts] == [
        ("como-se-movio", PostStatus.DRAFT, PostOrigin.BATCH, LUNES),
        ("dato-del-dia", PostStatus.DRAFT, PostOrigin.BATCH, LUNES),
    ]
    assert all(p.slides[0].card_slug == p.batch_slot for p in posts)
    # Las cifras con tope en el día anterior, como antes.
    assert {p.slides[0].params["data_through"] for p in posts} == {"2026-10-18"}
    assert {p.slides[0].params["product"] for p in posts} == {"Arroz"}
    assert [c.estado for c in reporte.cards] == ["creada", "creada"]
    # Ya no rasteriza: ni PNG ni JPEG en disco.
    assert not any(tmp_path.rglob("*.png")) and not any(tmp_path.rglob("*.jpg"))


def test_un_jueves_crea_de_donde_sale_con_una_provincia_con_anuncios(
    session: Session, datos: None, tmp_path: Path
) -> None:
    run_daily(session, JUEVES, tmp_path, execute=True)

    donde = next(p for p in _posts(session) if p.batch_slot == "de-donde-sale")
    assert donde.slides[0].params["province"] in ("La Habana", "Santiago de Cuba")


def test_correr_dos_veces_el_mismo_dia_no_duplica(
    session: Session, datos: None, tmp_path: Path
) -> None:
    run_daily(session, LUNES, tmp_path, execute=True)
    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    assert len(_posts(session)) == 2
    assert [c.estado for c in reporte.cards] == ["ya existía", "ya existía"]
    assert reporte.exit_code == 0


def test_un_borrador_descartado_no_se_vuelve_a_crear(
    session: Session, datos: None, tmp_path: Path
) -> None:
    run_daily(session, LUNES, tmp_path, execute=True)
    dato = next(p for p in _posts(session) if p.batch_slot == "dato-del-dia")
    discard(dato, images_dir=tmp_path)
    session.commit()

    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    assert len(_posts(session)) == 2
    assert reporte.cards[0].estado == "ya existía"


def test_la_base_no_admite_dos_borradores_del_lote_del_mismo_dia_y_tipo(
    session: Session, datos: None
) -> None:
    for _ in range(2):
        create_draft(
            session, card_slug="dato-del-dia", planned_for=LUNES, product="Arroz",
            province=None, data_through=LUNES - timedelta(days=1),
            origin=PostOrigin.BATCH, batch_slot="dato-del-dia",
        )

    with pytest.raises(IntegrityError):
        session.commit()


def test_si_otra_corrida_lo_crea_en_el_medio_dice_ya_existia(
    session: Session, datos: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La carrera: la consulta previa no lo ve, pero la base sí lo tiene."""
    create_draft(
        session, card_slug="dato-del-dia", planned_for=LUNES, product="Arroz", province=None,
        data_through=LUNES - timedelta(days=1), origin=PostOrigin.BATCH, batch_slot="dato-del-dia",
    )
    session.commit()
    real = daily_module._exists
    llamadas: list[str] = []

    def _ciega_la_primera_vez(db: Session, day: date, slot: str) -> bool:
        llamadas.append(slot)
        if slot == "dato-del-dia" and llamadas.count(slot) == 1:
            return False
        return real(db, day, slot)

    monkeypatch.setattr(daily_module, "_exists", _ciega_la_primera_vez)

    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    assert reporte.cards[0].estado == "ya existía"
    # La segunda consulta es la del `except`: la carrera sí pasó por el savepoint.
    assert llamadas.count("dato-del-dia") == 2
    assert len([p for p in _posts(session) if p.batch_slot == "dato-del-dia"]) == 1
    # El otro tipo del lunes se creó igual: el savepoint deshizo solo el repetido.
    assert reporte.cards[1].estado == "creada"


def test_otra_restriccion_que_salta_no_se_tapa_como_ya_existia(
    session: Session, datos: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _rota(db: Session, **kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("ck_marketing_posts_status"))

    monkeypatch.setattr(daily_module, "create_draft", _rota)

    with pytest.raises(IntegrityError):
        run_daily(session, LUNES, tmp_path, execute=True)


def test_otro_dia_si_crea_los_suyos(session: Session, datos: None, tmp_path: Path) -> None:
    run_daily(session, LUNES, tmp_path, execute=True)
    run_daily(session, LUNES + timedelta(days=1), tmp_path, execute=True)

    assert len(_posts(session)) == 3


def test_en_seco_no_crea_nada_ni_purga(
    session: Session, datos: None, tmp_path: Path
) -> None:
    viejo = tmp_path / "2026-01-01"
    viejo.mkdir()

    reporte = run_daily(session, LUNES, tmp_path, execute=False)

    assert [c.estado for c in reporte.cards] == ["prevista", "prevista"]
    assert _posts(session) == []
    assert viejo.exists()


def test_con_execute_purga_las_carpetas_viejas_y_las_publicaciones_vencidas(
    session: Session, datos: None, tmp_path: Path
) -> None:
    (tmp_path / "2026-01-01").mkdir()
    vencido = create_draft(
        session, card_slug="carrusel-cierre", planned_for=date(2026, 9, 1), product=None,
        province=None, data_through=date(2026, 9, 1),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    session.commit()

    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    assert not (tmp_path / "2026-01-01").exists()
    assert [p.name for p in reporte.purgados] == ["2026-01-01"]
    assert reporte.posts_purgados == [vencido.id]
    assert vencido.id not in {p.id for p in _posts(session)}


def test_sin_ningun_anuncio_no_crea_borrador_y_dice_por_que(
    session: Session, tmp_path: Path
) -> None:
    """Producto piloto sin una sola fila: no se crea un borrador vacío."""
    session.add(Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True))
    session.commit()

    reporte = run_daily(session, LUNES, tmp_path, execute=True)

    assert [(c.slug, c.estado) for c in reporte.cards] == [
        ("dato-del-dia", "sin datos"),
        ("como-se-movio", "sin datos"),
    ]
    assert _posts(session) == []
    assert reporte.exit_code == 1


# --- CLI ---------------------------------------------------------------------


def test_el_codigo_de_salida_es_1_si_no_salio_ninguna_tarjeta(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    session.add(Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True))
    session.commit()

    from contextlib import contextmanager

    @contextmanager
    def _scope():
        yield session

    monkeypatch.setattr("saimc.marketing.daily.session_scope", _scope)
    monkeypatch.setattr("saimc.marketing.daily.default_marketing_dir", lambda: tmp_path)

    codigo = main(["--day", "2026-10-19", "--execute"])

    assert codigo == 1
    assert "sin datos" in capsys.readouterr().out


def test_la_hora_que_ve_el_operador_es_la_de_cuba() -> None:
    # El 19 de octubre Cuba está en horario de verano: UTC − 4.
    assert to_havana(datetime(2026, 10, 19, 15, 0, tzinfo=UTC)).hour == 11
    # Lo que viene sin huso (SQLite) se toma como UTC, que es lo que se guarda.
    assert to_havana(datetime(2026, 10, 19, 15, 0)).hour == 11
    assert havana_today(datetime(2026, 10, 20, 2, 0, tzinfo=UTC)) == date(2026, 10, 19)


def test_ya_no_acepta_out_dir() -> None:
    with pytest.raises(SystemExit):
        main(["--out-dir", "var/marketing"])
