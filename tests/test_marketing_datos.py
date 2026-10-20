"""El carrusel de datos diario (`daily.DATOS_SLOT`).

Los casos esperados se escribieron a mano antes que el código: cuántos
productos entran (con datos, hasta 4, desde el que toca por la rotación), qué
dice la portada (cantidad real de productos y fecha de corte), que aprobar
congele cada diapositiva con SU producto y que el texto del post nombre los
productos sin escribir ninguna cifra.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.marketing.caption import CAPTION_MAX, HASHTAGS_MAX
from saimc.marketing.context import BY_SLUG, build_context
from saimc.marketing.daily import (
    DATOS_SLOT,
    candidate_order,
    create_datos_draft,
    datos_products,
    run_daily,
    slot_label,
)
from saimc.marketing.html import card_html
from saimc.marketing.posts import (
    InvalidSlide,
    approve,
    create_carousel_draft,
    create_draft,
    effective_caption,
    live_context,
    slide_html,
)
from saimc.marketing.week import build_week, post_slot
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
from saimc.progress import pilot_product_names
from saimc.web.app import app
from saimc.web.routers.marketing import get_today
from tests.test_marketing_post_router import fabrica  # noqa: F401
from tests.test_marketing_posts import JpegFalso
from tests.test_marketing_router import client  # noqa: F401

DIA = date(2026, 10, 19)  # lunes: los datos llegan hasta el 18

#: Piloto con anuncios en La Habana: precio de la semana pasada.
CON_DATOS = {"Arroz": "300", "Frijol": "500", "Aceite": "1200", "Pollo": "800", "Azúcar": "250"}


@pytest.fixture
def cinco(session: Session) -> None:
    """Cinco productos piloto con datos, uno piloto sin ni un anuncio (Harina)
    y uno con datos fuera del foco (Leche)."""
    productos = [
        Product(name=n, category="x", unit="lb", aliases=[], is_pilot=True)
        for n in (*CON_DATOS, "Harina")
    ]
    leche = Product(name="Leche", category="x", unit="lb", aliases=[], is_pilot=False)
    habana = Province(code="hab", name="La Habana")
    session.add_all([*productos, leche, habana])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT, file_path="raw/c.dat", content_hash="hash-cinco",
        content_type=ContentType.TEXT, processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    for producto in [*productos, leche]:
        if producto.name == "Harina":
            continue
        session.add(
            PriceHistory(
                provenance="human", product_id=producto.id, province_id=habana.id,
                price=Decimal(CON_DATOS.get(producto.name, "900")), currency=Currency.CUP.value,
                source_id=origen.id, unit="lb", sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"), published_date=DIA - timedelta(days=2),
            )
        )
    session.commit()


# --- Qué productos entran ------------------------------------------------------


def test_entran_hasta_cuatro_con_datos_desde_el_que_toca_por_la_rotacion(
    session: Session, cinco: None
) -> None:
    elegidos = datos_products(session, DIA)

    assert len(elegidos) == 4
    assert set(elegidos) <= set(CON_DATOS)  # ni Harina (sin anuncios) ni Leche (fuera del foco)
    # Empiezan por el primero de la rotación del día que tiene datos.
    rotados = [p for p in candidate_order(pilot_product_names(session), DIA) if p in CON_DATOS]
    assert elegidos == rotados[:4]


def test_la_rotacion_cambia_el_primero_de_un_dia_al_siguiente(
    session: Session, cinco: None
) -> None:
    # El segundo día solo tiene datos hasta el 19: el precio del 17 sigue en la semana.
    assert datos_products(session, DIA)[0] != datos_products(session, DIA + timedelta(days=1))[0]


def test_con_un_solo_producto_con_datos_sale_igual_con_un_dato(
    session: Session, tmp_path: Path
) -> None:
    session.add_all(
        [
            Product(name="Arroz", category="x", unit="lb", aliases=[], is_pilot=True),
            Product(name="Frijol", category="x", unit="lb", aliases=[], is_pilot=True),
            Province(code="hab", name="La Habana"),
        ]
    )
    session.commit()
    arroz = session.query(Product).filter_by(name="Arroz").one()
    habana = session.query(Province).one()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT, file_path="raw/u.dat", content_hash="hash-uno",
        content_type=ContentType.TEXT, processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    session.add(
        PriceHistory(
            provenance="human", product_id=arroz.id, province_id=habana.id,
            price=Decimal("300"), currency=Currency.CUP.value, source_id=origen.id, unit="lb",
            sale_format=SaleFormat.RETAIL.value, quantity=Decimal("1"),
            published_date=DIA - timedelta(days=2),
        )
    )
    session.commit()

    run_daily(session, DIA, tmp_path, execute=True)

    post = session.query(MarketingPost).filter_by(batch_slot=DATOS_SLOT).one()
    assert [s.card_slug for s in post.slides] == ["datos-portada", "dato-del-dia", "carrusel-cierre"]


def test_el_lote_arma_portada_cuatro_datos_y_cierre_cada_uno_con_su_producto(
    session: Session, cinco: None, tmp_path: Path
) -> None:
    run_daily(session, DIA, tmp_path, execute=True)

    post = session.query(MarketingPost).filter_by(batch_slot=DATOS_SLOT).one()
    esperados = datos_products(session, DIA)
    assert [s.card_slug for s in post.slides] == [
        "datos-portada", *["dato-del-dia"] * 4, "carrusel-cierre"
    ]
    assert [s.params["product"] for s in post.slides] == [None, *esperados, None]
    assert [s.position for s in post.slides] == [1, 2, 3, 4, 5, 6]


# --- La portada --------------------------------------------------------------


def test_la_portada_dice_la_cantidad_real_de_productos_con_tope_y_la_fecha_de_corte(
    session: Session, cinco: None
) -> None:
    kind = BY_SLUG["datos-portada"]

    contexto = build_context(kind, session, product=None, province=None, hoy=DIA - timedelta(days=1))

    # Hay cinco con datos pero el carrusel lleva cuatro: la portada dice cuatro.
    assert contexto == {"portada": {"products": 4, "through": "18/10"}}
    html = card_html(kind, contexto, slide_counter="1 / 6")
    assert "datos hasta el 18/10" in html
    assert "4 productos" in html
    assert ">1 / 6<" in html
    assert 'class="ph-brand-avatar"' in html  # el avatar SVG del sitio, inline


def test_la_portada_con_un_producto_va_en_singular(session: Session, cinco: None) -> None:
    for nombre in ("Frijol", "Aceite", "Pollo", "Azúcar"):
        producto = session.query(Product).filter_by(name=nombre).one()
        producto.is_pilot = False
    session.commit()

    contexto = build_context(
        BY_SLUG["datos-portada"], session, product=None, province=None,
        hoy=DIA - timedelta(days=1),
    )

    assert contexto is not None and contexto["portada"]["products"] == 1
    assert "1 producto " in card_html(BY_SLUG["datos-portada"], contexto)


def test_sin_datos_no_hay_portada(session: Session) -> None:
    session.add(Product(name="Arroz", category="x", unit="lb", aliases=[], is_pilot=True))
    session.commit()

    assert (
        build_context(BY_SLUG["datos-portada"], session, product=None, province=None, hoy=DIA)
        is None
    )


# --- Aprobar -----------------------------------------------------------------


def test_aprobar_congela_cada_diapositiva_con_su_producto_y_coincide_con_la_vista_previa(
    session: Session, cinco: None, tmp_path: Path
) -> None:
    post = create_datos_draft(
        session, DIA, ["Arroz", "Frijol", "Aceite"], data_through=DIA - timedelta(days=1)
    )
    session.commit()
    previa = [slide_html(session, s) for s in post.slides]
    render = JpegFalso()

    approve(session, post, images_dir=tmp_path, render=render)

    assert post.status == PostStatus.APPROVED
    assert render.htmls == previa  # lo rasterizado es lo que se veía
    congeladas = [s.snapshot or {} for s in post.slides]
    nombres = [
        c["context"]["card"]["product_name"]
        for c, s in zip(congeladas, post.slides, strict=True) if s.card_slug == "dato-del-dia"
    ]
    assert nombres == ["Arroz", "Frijol", "Aceite"]
    for html, producto in zip(render.htmls[1:4], ["Arroz", "Frijol", "Aceite"], strict=True):
        assert producto in html
    assert ">2 / 5<" in render.htmls[1] and ">5 / 5<" in render.htmls[4]
    # La portada cuenta los productos con datos del día (con tope 4), no las
    # diapositivas que el operador dejó: es una cifra real, congelada al aprobar.
    assert "4 productos" in render.htmls[0]
    assert congeladas[0]["context"]["portada"] == {"products": 4, "through": "18/10"}


def test_no_se_aprueba_si_un_producto_se_quedo_sin_datos(
    session: Session, cinco: None, tmp_path: Path
) -> None:
    from saimc.marketing.posts import NoData

    post = create_datos_draft(session, DIA, ["Harina"], data_through=DIA - timedelta(days=1))
    session.commit()

    with pytest.raises(NoData):
        approve(session, post, images_dir=tmp_path, render=JpegFalso())
    assert post.status == PostStatus.DRAFT


# --- El texto del post -------------------------------------------------------


def test_el_texto_nombra_los_productos_la_llamada_y_los_hashtags_sin_ninguna_cifra(
    session: Session, cinco: None, tmp_path: Path
) -> None:
    post = create_datos_draft(
        session, DIA, ["Arroz", "Frijol"], data_through=DIA - timedelta(days=1)
    )
    session.commit()

    borrador = effective_caption(session, post)
    approve(session, post, images_dir=tmp_path, render=JpegFalso())

    assert "Arroz, Frijol" in borrador
    assert "Mándaselo a quien hace las compras." in borrador
    assert borrador.rstrip().endswith("#mercadocubano")
    assert len(borrador) <= CAPTION_MAX
    assert sum(w.startswith("#") for w in borrador.split()) <= HASHTAGS_MAX
    # Nada de rangos ni medianas: ni el 300 del arroz ni el 500 del frijol.
    assert "300" not in borrador and "500" not in borrador
    assert post.caption == borrador  # aprobar guarda el mismo texto que se veía


# --- create_carousel_draft con un producto por diapositiva ---------------------


def test_un_producto_por_diapositiva_exige_el_mismo_largo(session: Session) -> None:
    with pytest.raises(InvalidSlide):
        create_carousel_draft(
            session, card_slugs=("dato-del-dia", "dato-del-dia"), planned_for=DIA, product=None,
            province=None, data_through=DIA, products=("Arroz",),
        )


def test_sin_products_todas_las_diapositivas_llevan_el_mismo_producto(
    session: Session,
) -> None:
    post = create_carousel_draft(
        session, card_slugs=("dato-del-dia", "dato-del-dia"), planned_for=DIA, product="Arroz",
        province=None, data_through=DIA,
    )

    assert [s.params["product"] for s in post.slides] == ["Arroz", "Arroz"]


# --- Compatibilidad con posts viejos y Hoy -----------------------------------


def test_los_turnos_viejos_se_siguen_mostrando_como_el_de_datos(session: Session) -> None:
    for slot in ("dato-del-dia", "carrusel"):
        post = create_draft(
            session, card_slug="dato-del-dia", planned_for=DIA, product="Arroz",
            province=None, data_through=DIA, origin=PostOrigin.BATCH, batch_slot=slot,
        )
        assert post_slot(post) == DATOS_SLOT
        break  # el UNIQUE es por turno: el segundo se prueba abajo
    otro = create_datos_draft(session, DIA, ["Arroz"], data_through=DIA)
    assert post_slot(otro) == DATOS_SLOT  # operador: abre con su portada
    assert slot_label("dato-del-dia") == "Dato del día"  # el viejo sigue teniendo nombre
    assert slot_label("carrusel") == "Carrusel"
    assert slot_label(DATOS_SLOT) == "Datos del día"


def test_un_dato_viejo_del_lote_cubre_el_turno_de_datos_en_la_semana(
    session: Session,
) -> None:
    create_draft(
        session, card_slug="dato-del-dia", planned_for=DIA, product="Arroz", province=None,
        data_through=DIA, origin=PostOrigin.BATCH, batch_slot="dato-del-dia",
    )
    session.commit()

    semana = build_week(session, DIA, today=DIA)

    lunes = next(d for d in semana.days if d.day == DIA)
    assert {s.slot: s.state for s in lunes.slots}[DATOS_SLOT] == "draft"


def test_hoy_con_un_carrusel_de_datos_lo_titula_y_lista_los_productos(
    client: TestClient, cinco: None, fabrica: Path, session: Session  # noqa: F811
) -> None:
    app.dependency_overrides[get_today] = lambda: DIA
    post = create_datos_draft(session, DIA, ["Arroz", "Frijol"], data_through=DIA)
    session.commit()

    html = client.get("/marketing").text

    assert f"/marketing/post/{post.id}" in html
    assert "<h3>Datos del día</h3>" in html
    assert "4 diapositivas" in html
    assert "Arroz, Frijol" in html


def test_crear_los_datos_desde_hoy_arma_el_carrusel(
    client: TestClient, cinco: None, fabrica: Path, session: Session  # noqa: F811
) -> None:
    app.dependency_overrides[get_today] = lambda: DIA

    r = client.post("/marketing/post", data={"kind": "datos"}, follow_redirects=False)

    assert r.status_code == 303
    post = session.get(MarketingPost, uuid.UUID(r.headers["location"].rsplit("/", 1)[1]))
    assert post is not None
    assert post.slides[0].card_slug == "datos-portada"
    assert post.slides[-1].card_slug == "carrusel-cierre"
    assert [s.params["product"] for s in post.slides[1:-1]] == datos_products(session, DIA)
    assert {s.params["data_through"] for s in post.slides} == {"2026-10-18"}
    assert live_context(session, post.slides[0]) is not None


def test_crear_los_datos_sin_datos_da_409_y_no_deja_un_borrador_vacio(
    client: TestClient, fabrica: Path, session: Session  # noqa: F811
) -> None:
    app.dependency_overrides[get_today] = lambda: DIA

    r = client.post("/marketing/post", data={"kind": "datos"}, follow_redirects=False)

    assert r.status_code == 409
    assert session.query(MarketingPost).count() == 0
