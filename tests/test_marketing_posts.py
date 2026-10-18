"""Publicaciones con estado (`saimc.marketing.posts`, F2 de
`docs/plan-fabrica-marketing.md`; casos de `docs/spec-slice-fabrica-publicaciones.md` §1.7).

Los casos esperados están escritos a mano antes que el código: qué
transiciones existen, que lo aprobado no cambia aunque se corrija un precio,
cuántos días se guarda cada estado. El render a JPEG es una función falsa que
anota el HTML que le pidieron: acá no se levanta Chromium.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.marketing.cards import DatoDelDiaCard, ProvinceRow
from saimc.marketing.context import BY_SLUG, build_context
from saimc.marketing.options import parse_options
from saimc.marketing.posts import (
    InvalidTransition,
    InvalidUrl,
    NoData,
    approve,
    create_draft,
    discard,
    duplicate,
    effective_caption,
    mark_published,
    purge_expired,
    save_draft,
    slide_html,
)
from saimc.marketing.render import RendererUnavailable
from saimc.marketing.snapshot import freeze_context, thaw_context
from saimc.models import (
    ContentType,
    Currency,
    MarketingPost,
    MarketingPostSlide,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.marketing_post import FrozenSlideError, PostOrigin, PostStatus
from saimc.models.raw_ingestion import ProcessingStatus

LUNES = date(2026, 10, 19)
DOMINGO = LUNES - timedelta(days=1)


class JpegFalso:
    """Reemplaza a `render_jpeg`: anota el HTML y devuelve bytes reconocibles."""

    def __init__(self) -> None:
        self.htmls: list[str] = []

    def __call__(self, html: str, *, width: int, height: int) -> bytes:
        self.htmls.append(html)
        return f"JPEG {width}x{height}".encode()


def _sin_chromium(html: str, *, width: int, height: int) -> bytes:
    raise RendererUnavailable("Chromium no arrancó.")


def _sin_estilos(html: str) -> str:
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


@pytest.fixture
def datos(session: Session) -> dict[str, PriceHistory]:
    """Arroz piloto en La Habana a 300, 320 y 350 la semana que termina el
    domingo, y Santiago a 400. Devuelve la fila de 350 para poder corregirla."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, habana, santiago])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/p.dat",
        content_hash="hash-posts",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    filas: dict[str, PriceHistory] = {}

    def precio(provincia: Province, hace: int, valor: str) -> None:
        fila = PriceHistory(
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
        session.add(fila)
        filas[valor] = fila

    precio(habana, 3, "300")
    precio(habana, 2, "320")
    precio(habana, 1, "350")
    precio(santiago, 2, "400")
    session.commit()
    return filas


def _borrador(session: Session, **extra: object) -> MarketingPost:
    post = create_draft(
        session,
        card_slug="dato-del-dia",
        planned_for=LUNES,
        product="Arroz",
        province=None,
        data_through=DOMINGO,
        **extra,  # type: ignore[arg-type]
    )
    session.commit()
    return post


def _aprobada(session: Session, tmp_path: Path) -> MarketingPost:
    post = _borrador(session)
    approve(session, post, images_dir=tmp_path, render=JpegFalso())
    session.commit()
    return post


# --- Transiciones -----------------------------------------------------------


class TestTransicionesPermitidas:
    def test_un_borrador_nuevo_tiene_una_diapositiva_y_texto_automatico(
        self, session: Session, datos: dict
    ) -> None:
        post = _borrador(session)

        assert post.status == PostStatus.DRAFT
        assert post.origin == PostOrigin.OPERATOR
        assert post.format == "single"
        assert post.caption is None
        assert [s.position for s in post.slides] == [1]
        assert post.slides[0].params == {
            "product": "Arroz", "province": None, "data_through": "2026-10-18"
        }
        assert post.slides[0].snapshot is None

    def test_aprobar_congela_escribe_el_jpeg_y_marca_la_hora(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)

        slide = post.slides[0]
        assert post.status == PostStatus.APPROVED
        assert post.approved_at is not None
        assert slide.snapshot is not None and slide.snapshot["version"] == 1
        assert slide.image_path == f"{post.id}/1.jpg"
        assert (tmp_path / slide.image_path).read_bytes() == b"JPEG 1080x1350"
        assert post.caption and "Arroz" in post.caption

    def test_marcar_publicada_guarda_el_enlace(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)

        mark_published(post, "https://www.instagram.com/p/abc123/")

        assert post.status == PostStatus.PUBLISHED
        assert post.published_url == "https://www.instagram.com/p/abc123/"
        assert post.published_at is not None

    def test_descartar_un_borrador(self, session: Session, datos: dict, tmp_path: Path) -> None:
        post = _borrador(session)

        discard(post, images_dir=tmp_path)

        assert post.status == PostStatus.DISCARDED
        assert post.discarded_at is not None

    def test_descartar_una_aprobada_borra_su_imagen(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        archivo = tmp_path / post.slides[0].image_path

        discard(post, images_dir=tmp_path)
        session.commit()

        assert post.status == PostStatus.DISCARDED
        assert not archivo.exists()
        assert post.slides[0].image_path is None


class TestTransicionesProhibidas:
    def test_un_borrador_no_se_publica_sin_aprobar(self, session: Session, datos: dict) -> None:
        post = _borrador(session)

        with pytest.raises(InvalidTransition):
            mark_published(post, "https://www.instagram.com/p/abc/")
        assert post.status == PostStatus.DRAFT

    def test_una_publicada_no_se_descarta_ni_se_reaprueba(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        mark_published(post, "https://www.instagram.com/p/abc/")

        with pytest.raises(InvalidTransition):
            discard(post, images_dir=tmp_path)
        with pytest.raises(InvalidTransition):
            approve(session, post, images_dir=tmp_path, render=JpegFalso())
        with pytest.raises(InvalidTransition):
            mark_published(post, "https://www.instagram.com/p/otro/")
        assert post.status == PostStatus.PUBLISHED
        assert post.published_url == "https://www.instagram.com/p/abc/"

    def test_una_descartada_no_vuelve(self, session: Session, datos: dict, tmp_path: Path) -> None:
        post = _borrador(session)
        discard(post, images_dir=tmp_path)

        with pytest.raises(InvalidTransition):
            approve(session, post, images_dir=tmp_path, render=JpegFalso())
        with pytest.raises(InvalidTransition):
            mark_published(post, "https://www.instagram.com/p/abc/")
        with pytest.raises(InvalidTransition):
            discard(post, images_dir=tmp_path)
        assert post.status == PostStatus.DISCARDED

    def test_una_aprobada_no_se_aprueba_dos_veces(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)

        with pytest.raises(InvalidTransition):
            approve(session, post, images_dir=tmp_path, render=JpegFalso())

    def test_una_aprobada_no_se_edita(self, session: Session, datos: dict, tmp_path: Path) -> None:
        post = _aprobada(session, tmp_path)
        kind = BY_SLUG["dato-del-dia"]

        with pytest.raises(InvalidTransition):
            save_draft(
                session, post, product="Arroz", province=None, data_through=DOMINGO,
                options=parse_options(kind, {"b_barra": "0"}), caption="otro texto",
            )

    @pytest.mark.parametrize(
        "enlace", ["", "   ", "javascript:alert(1)", "instagram.com/p/abc", "ftp://x.com/a"]
    )
    def test_publicar_exige_un_enlace_http(
        self, session: Session, datos: dict, tmp_path: Path, enlace: str
    ) -> None:
        post = _aprobada(session, tmp_path)

        with pytest.raises(InvalidUrl):
            mark_published(post, enlace)
        assert post.status == PostStatus.APPROVED

    def test_la_base_rechaza_una_publicada_sin_enlace(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        post.status = PostStatus.PUBLISHED

        with pytest.raises(IntegrityError):
            session.commit()


# --- Aprobar: todo o nada ---------------------------------------------------


class TestAprobar:
    def test_sin_chromium_el_borrador_queda_intacto(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _borrador(session)

        with pytest.raises(RendererUnavailable):
            approve(session, post, images_dir=tmp_path, render=_sin_chromium)

        assert post.status == PostStatus.DRAFT
        assert post.slides[0].snapshot is None
        assert post.slides[0].image_path is None
        assert not any(tmp_path.iterdir())

    def test_sin_datos_no_se_aprueba(self, session: Session, datos: dict, tmp_path: Path) -> None:
        post = create_draft(
            session, card_slug="dato-del-dia", planned_for=LUNES, product="Arroz",
            province=None, data_through=LUNES - timedelta(days=30),
        )
        session.commit()

        with pytest.raises(NoData):
            approve(session, post, images_dir=tmp_path, render=JpegFalso())
        assert post.status == PostStatus.DRAFT

    def test_el_jpeg_se_rasteriza_desde_el_snapshot(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _borrador(session)
        render = JpegFalso()

        approve(session, post, images_dir=tmp_path, render=render)

        assert render.htmls == [slide_html(session, post.slides[0])]


# --- Snapshot inmutable -----------------------------------------------------


class TestSnapshot:
    def test_corregir_un_precio_no_cambia_lo_aprobado(
        self, session: Session, datos: dict[str, PriceHistory], tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        texto = post.caption

        datos["350"].price = Decimal("999")
        session.commit()

        html = _sin_estilos(slide_html(session, post.slides[0]))
        assert "350" in html and "999" not in html
        assert effective_caption(session, post) == texto
        # La base viva sí cambió: el mismo pedido en vivo ya trae el 999.
        vivo = build_context(
            BY_SLUG["dato-del-dia"], session, product="Arroz", province=None, hoy=DOMINGO
        )
        assert vivo is not None and "999" in vivo["card"].rows[0].range_text

    def test_un_borrador_sigue_a_la_base(
        self, session: Session, datos: dict[str, PriceHistory]
    ) -> None:
        post = _borrador(session)

        datos["350"].price = Decimal("999")
        session.commit()

        assert "999" in _sin_estilos(slide_html(session, post.slides[0]))

    def test_el_snapshot_no_se_reescribe(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        slide = post.slides[0]

        slide.snapshot = {**slide.snapshot, "context": {}}
        with pytest.raises(FrozenSlideError):
            session.flush()
        session.rollback()

        slide.options = {"show": {}, "subtitle": "x", "cta": ""}
        with pytest.raises(FrozenSlideError):
            session.flush()
        session.rollback()

        slide.params = {**slide.params, "product": "Frijol"}
        with pytest.raises(FrozenSlideError):
            session.flush()

    def test_el_snapshot_no_se_reescribe_aunque_este_vencido(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        """En la app, `commit` vence los atributos: el listener igual tiene
        que ver el valor anterior (`active_history`)."""
        post = _aprobada(session, tmp_path)
        slide = post.slides[0]
        session.expire_all()

        slide.snapshot = {"version": 1, "context": {}}
        with pytest.raises(FrozenSlideError):
            session.flush()

    def test_el_texto_de_una_aprobada_no_se_reescribe(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)

        post.caption = "otro"
        with pytest.raises(FrozenSlideError):
            session.flush()

    def test_ida_y_vuelta_da_las_mismas_dataclasses(self) -> None:
        contexto = {
            "card": DatoDelDiaCard(
                product_name="Arroz", unit="lb", unit_noun="libra", currency="CUP",
                week_from="12 oct", week_to="18 oct",
                rows=[
                    ProvinceRow(name="La Habana", color="#123", has_data=True,
                                is_cheapest=True, range_text="300 – 350", count=3,
                                bar_left_pct=10.5, bar_width_pct=40.0, median_pct=20.0),
                    ProvinceRow(name="Santiago de Cuba", color="#456", has_data=False),
                ],
            )
        }

        congelado = freeze_context(contexto)

        assert thaw_context(congelado) == contexto
        assert thaw_context({"products": ["Arroz"]}) == {"products": ["Arroz"]}

    def test_un_tipo_desconocido_en_el_snapshot_es_un_error(self) -> None:
        with pytest.raises(ValueError):
            thaw_context({"card": {"__type__": "Inventada", "x": 1}})


# --- Guardar el borrador ----------------------------------------------------


class TestGuardar:
    def test_guarda_opciones_y_un_texto_editado(self, session: Session, datos: dict) -> None:
        post = _borrador(session)
        kind = BY_SLUG["dato-del-dia"]

        save_draft(
            session, post, product="Arroz", province=None, data_through=DOMINGO,
            options=parse_options(kind, {"b_comparacion": "0", "subtitle": "Hola"}),
            caption="Mi texto",
        )
        session.commit()

        slide = post.slides[0]
        assert slide.options["show"]["comparacion"] is False
        assert slide.options["subtitle"] == "Hola"
        assert post.caption == "Mi texto"

    def test_un_bloque_sin_interruptor_no_se_apaga_ni_guardado(
        self, session: Session, datos: dict
    ) -> None:
        post = _borrador(session)
        post.slides[0].options = {"show": {"titulo": False, "barra": False}, "subtitle": "", "cta": ""}
        session.commit()

        html = slide_html(session, post.slides[0])

        assert 'data-block="titulo"' in html
        assert 'data-block="barra"' not in html

    def test_reenviar_el_texto_generado_lo_deja_automatico(
        self, session: Session, datos: dict
    ) -> None:
        post = _borrador(session)
        kind = BY_SLUG["dato-del-dia"]
        generado = effective_caption(session, post)

        save_draft(
            session, post, product="Arroz", province=None, data_through=DOMINGO,
            options=parse_options(kind, {}), caption=generado,
        )

        assert post.caption is None


# --- Duplicar ---------------------------------------------------------------


class TestDuplicar:
    def test_duplicar_una_aprobada_da_un_borrador_nuevo_para_hoy(
        self, session: Session, datos: dict, tmp_path: Path
    ) -> None:
        post = _aprobada(session, tmp_path)
        hoy = LUNES + timedelta(days=2)

        copia = duplicate(session, post, today=hoy)
        session.commit()

        assert copia.id != post.id
        assert copia.status == PostStatus.DRAFT
        assert copia.origin == PostOrigin.OPERATOR and copia.batch_slot is None
        assert copia.planned_for == hoy
        assert copia.slides[0].params == post.slides[0].params
        assert copia.slides[0].snapshot is None
        # El texto era el automático: la copia lo vuelve a generar.
        assert copia.caption is None
        assert post.status == PostStatus.APPROVED

    def test_un_texto_editado_se_copia(self, session: Session, datos: dict, tmp_path: Path) -> None:
        post = _borrador(session, caption="Escrito a mano")
        approve(session, post, images_dir=tmp_path, render=JpegFalso())

        copia = duplicate(session, post, today=LUNES)

        assert copia.caption == "Escrito a mano"

    def test_un_borrador_no_se_duplica(self, session: Session, datos: dict) -> None:
        with pytest.raises(InvalidTransition):
            duplicate(session, _borrador(session), today=LUNES)


# --- Retención --------------------------------------------------------------


def _con_fechas(
    session: Session, tmp_path: Path, estado: PostStatus, *, creada_hace: int,
    descartada_hace: int | None = None,
) -> MarketingPost:
    hoy = datetime(2026, 11, 30, 12, tzinfo=UTC)
    post = create_draft(
        session, card_slug="carrusel-portada", planned_for=hoy.date(), product=None,
        province=None, data_through=hoy.date(), now=hoy - timedelta(days=creada_hace),
    )
    session.flush()
    if estado in (PostStatus.APPROVED, PostStatus.PUBLISHED):
        approve(session, post, images_dir=tmp_path, render=JpegFalso())
    if estado == PostStatus.PUBLISHED:
        mark_published(post, "https://www.instagram.com/p/x/")
    if estado == PostStatus.DISCARDED:
        assert descartada_hace is not None
        discard(post, images_dir=tmp_path, now=hoy - timedelta(days=descartada_hace))
    session.commit()
    return post


class TestRetencion:
    HOY = date(2026, 11, 30)

    def _ids(self, session: Session) -> set:
        return set(session.scalars(select(MarketingPost.id)))

    def test_borradores_de_30_dias_se_quedan_y_de_31_se_van(
        self, session: Session, tmp_path: Path
    ) -> None:
        queda = _con_fechas(session, tmp_path, PostStatus.DRAFT, creada_hace=30)
        se_va = _con_fechas(session, tmp_path, PostStatus.DRAFT, creada_hace=31)
        (tmp_path / str(se_va.id)).mkdir()

        borrados = purge_expired(session, tmp_path, self.HOY)
        session.commit()

        assert borrados == [se_va.id]
        assert self._ids(session) == {queda.id}
        assert not (tmp_path / str(se_va.id)).exists()
        assert session.scalars(select(MarketingPostSlide)).all() == queda.slides

    def test_una_descartada_se_mide_desde_que_se_descarto(
        self, session: Session, tmp_path: Path
    ) -> None:
        reciente = _con_fechas(
            session, tmp_path, PostStatus.DISCARDED, creada_hace=90, descartada_hace=5
        )
        vieja = _con_fechas(
            session, tmp_path, PostStatus.DISCARDED, creada_hace=90, descartada_hace=31
        )

        purge_expired(session, tmp_path, self.HOY)
        session.commit()

        assert self._ids(session) == {reciente.id}
        assert vieja.id not in self._ids(session)

    def test_publicadas_y_aprobadas_se_quedan_aunque_sean_viejas(
        self, session: Session, tmp_path: Path
    ) -> None:
        publicada = _con_fechas(session, tmp_path, PostStatus.PUBLISHED, creada_hace=400)
        aprobada = _con_fechas(session, tmp_path, PostStatus.APPROVED, creada_hace=60)

        assert purge_expired(session, tmp_path, self.HOY) == []
        assert self._ids(session) == {publicada.id, aprobada.id}
        assert (tmp_path / publicada.slides[0].image_path).exists()
