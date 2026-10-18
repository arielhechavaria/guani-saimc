"""Las tarjetas diarias de marketing (`saimc.marketing.daily`).

Los casos esperados están escritos a mano, antes que el código: qué toca cada
día de la semana, que se usen los datos del día anterior y no los del día que
corre, cuántos días se conservan. El render a PNG se reemplaza por una función
falsa que anota lo que le pidieron: acá no se levanta Chromium.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from saimc.marketing.context import CARD_TYPES, build_context
from saimc.marketing.daily import (
    RETENTION_DAYS,
    candidate_order,
    cards_for_day,
    main,
    purge_old,
    run_daily,
)
from saimc.marketing.render import RendererUnavailable
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.raw_ingestion import ProcessingStatus

#: Un lunes. El martes siguiente es el 20, el jueves el 22.
LUNES = date(2026, 10, 19)
JUEVES = date(2026, 10, 22)


class RenderFalso:
    """Reemplaza a `render_png`: anota (ancho, alto, ¿trae el producto?) y
    devuelve bytes reconocibles en vez de un PNG."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[int, int, str]] = []

    def __call__(self, html: str, *, width: int, height: int) -> bytes:
        self.llamadas.append((width, height, html))
        return f"PNG {width}x{height}".encode()


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


# --- La corrida --------------------------------------------------------------


def test_un_lunes_genera_dato_del_dia_y_como_se_movio_de_arroz(
    session: Session, datos: None, tmp_path: Path
) -> None:
    render = RenderFalso()

    # Frijol no tiene anuncios: cualquiera sea el día en que la rotación
    # arranque por él, la tarjeta sale de Arroz, que sí tiene datos.
    reporte = run_daily(session, LUNES, tmp_path, render=render, execute=True)

    carpeta = tmp_path / "2026-10-19"
    assert sorted(p.name for p in carpeta.iterdir()) == [
        "como-se-movio-arroz.png",
        "dato-del-dia-arroz.png",
    ]
    assert (carpeta / "dato-del-dia-arroz.png").read_bytes() == b"PNG 1080x1350"
    assert [c.estado for c in reporte.cards] == ["escrita", "escrita"]
    # El HTML que llegó al render trae el producto elegido.
    assert all("Arroz" in html for _, _, html in render.llamadas)


def test_un_jueves_genera_de_donde_sale_con_una_provincia_con_anuncios(
    session: Session, datos: None, tmp_path: Path
) -> None:
    reporte = run_daily(session, JUEVES, tmp_path, render=RenderFalso(), execute=True)

    nombres = sorted(p.name for p in (tmp_path / "2026-10-22").iterdir())
    assert nombres[0] == "dato-del-dia-arroz.png"
    assert len(nombres) == 2
    assert nombres[1].startswith("de-donde-sale-arroz-")
    # Las dos provincias del foco tienen anuncios en los últimos 30 días; la
    # que toque ese día es una de ellas.
    assert nombres[1] in (
        "de-donde-sale-arroz-la-habana.png",
        "de-donde-sale-arroz-santiago-de-cuba.png",
    )
    assert [c.estado for c in reporte.cards] == ["escrita", "escrita"]


def test_en_seco_no_escribe_nada_ni_purga_ni_levanta_chromium(
    session: Session, datos: None, tmp_path: Path
) -> None:
    viejo = tmp_path / "2026-01-01"
    viejo.mkdir()
    render = RenderFalso()

    reporte = run_daily(session, LUNES, tmp_path, render=render, execute=False)

    assert render.llamadas == []
    assert [c.estado for c in reporte.cards] == ["prevista", "prevista"]
    assert viejo.exists()
    assert not (tmp_path / "2026-10-19").exists()


def test_con_execute_purga_lo_viejo_despues_de_escribir(
    session: Session, datos: None, tmp_path: Path
) -> None:
    (tmp_path / "2026-01-01").mkdir()

    reporte = run_daily(session, LUNES, tmp_path, render=RenderFalso(), execute=True)

    assert not (tmp_path / "2026-01-01").exists()
    assert [p.name for p in reporte.purgados] == ["2026-01-01"]


def test_sin_ningun_anuncio_la_tarjeta_se_salta_y_dice_por_que(
    session: Session, tmp_path: Path
) -> None:
    """Producto piloto sin una sola fila: no se publica una tarjeta vacía."""
    session.add(Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True))
    session.commit()

    reporte = run_daily(session, LUNES, tmp_path, render=RenderFalso(), execute=True)

    assert [(c.slug, c.estado) for c in reporte.cards] == [
        ("dato-del-dia", "sin datos"),
        ("como-se-movio", "sin datos"),
    ]
    assert not tmp_path.joinpath("2026-10-19").exists() or not list(
        tmp_path.joinpath("2026-10-19").iterdir()
    )


def test_sin_chromium_avisa_y_no_deja_archivos_a_medias(
    session: Session, datos: None, tmp_path: Path
) -> None:
    def sin_chromium(html: str, *, width: int, height: int) -> bytes:
        raise RendererUnavailable("Chromium no arrancó.")

    reporte = run_daily(session, LUNES, tmp_path, render=sin_chromium, execute=True)

    assert reporte.renderer_error == "Chromium no arrancó."
    assert not any(tmp_path.rglob("*.png"))


def test_volver_a_correr_el_mismo_dia_pisa_los_archivos(
    session: Session, datos: None, tmp_path: Path
) -> None:
    run_daily(session, LUNES, tmp_path, render=RenderFalso(), execute=True)
    run_daily(session, LUNES, tmp_path, render=RenderFalso(), execute=True)

    assert len(list((tmp_path / "2026-10-19").iterdir())) == 2


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

    codigo = main(["--day", "2026-10-19", "--out-dir", str(tmp_path), "--execute"])

    assert codigo == 1
    assert "sin datos" in capsys.readouterr().out
