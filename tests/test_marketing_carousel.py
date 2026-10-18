"""Carruseles de la fábrica (F3 de `docs/plan-fabrica-marketing.md`).

Los casos esperados están escritos a mano antes que el código: el orden de
las diapositivas después de subir, bajar, agregar y quitar; el tope de 10; que
todas tengan el mismo tamaño; que aprobar congele y rasterice cada una en su
orden; y que solo un borrador se reordene. Reusa los datos de
`test_marketing_posts.py` (arroz en La Habana y Santiago).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from saimc.marketing.context import BY_SLUG, CardType
from saimc.marketing.options import parse_options
from saimc.marketing.posts import (
    MAX_SLIDES,
    InvalidSlide,
    InvalidTransition,
    add_slide,
    approve,
    create_carousel_draft,
    create_draft,
    duplicate,
    move_slide,
    remove_slide,
    save_draft,
)
from saimc.models import MarketingPost
from saimc.models.marketing_post import PostFormat, PostStatus
from tests.test_marketing_posts import DOMINGO, LUNES, JpegFalso, datos  # noqa: F401

CUATRO = ("carrusel-portada", "carrusel-consejo", "dato-del-dia", "carrusel-cierre")


def _carrusel(session: Session) -> MarketingPost:
    post = create_carousel_draft(
        session,
        card_slugs=CUATRO,
        planned_for=LUNES,
        product="Arroz",
        province=None,
        data_through=DOMINGO,
    )
    session.commit()
    return post


def _orden(post: MarketingPost) -> list[tuple[int, str]]:
    return [(s.position, s.card_slug) for s in post.slides]


class TestAlta:
    def test_un_carrusel_nace_con_sus_diapositivas_en_orden(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        assert post.format == PostFormat.CAROUSEL
        assert _orden(post) == [
            (1, "carrusel-portada"),
            (2, "carrusel-consejo"),
            (3, "dato-del-dia"),
            (4, "carrusel-cierre"),
        ]
        # Las de branding no llevan producto; la de datos sí, con su corte.
        assert post.slides[0].params == {"product": None, "province": None, "data_through": "2026-10-18"}
        assert post.slides[2].params == {"product": "Arroz", "province": None, "data_through": "2026-10-18"}

    def test_un_carrusel_vacio_o_de_mas_de_10_no_se_crea(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        for slugs in ((), ("carrusel-portada",) * (MAX_SLIDES + 1)):
            with pytest.raises(InvalidSlide):
                create_carousel_draft(
                    session,
                    card_slugs=slugs,
                    planned_for=LUNES,
                    product="Arroz",
                    province=None,
                    data_through=DOMINGO,
                )


class TestAgregar:
    def test_agrega_al_final_la_plantilla_elegida(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        nueva = add_slide(session, post, "dato-del-dia")
        session.commit()

        assert nueva.position == 5
        assert [s for _, s in _orden(post)] == [*CUATRO, "dato-del-dia"]
        # Hereda producto y fecha de corte de las que ya tienen datos.
        assert nueva.params == {"product": "Arroz", "province": None, "data_through": "2026-10-18"}

    def test_una_suelta_con_una_diapositiva_mas_pasa_a_carrusel(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = create_draft(
            session,
            card_slug="dato-del-dia",
            planned_for=LUNES,
            product="Arroz",
            province=None,
            data_through=DOMINGO,
        )
        session.commit()

        add_slide(session, post, "carrusel-cierre")
        session.commit()

        assert post.format == PostFormat.CAROUSEL
        assert _orden(post) == [(1, "dato-del-dia"), (2, "carrusel-cierre")]

    def test_el_tope_es_10(self, session: Session, datos: dict) -> None:  # noqa: F811
        post = _carrusel(session)
        for _ in range(MAX_SLIDES - len(CUATRO)):
            add_slide(session, post, "carrusel-cierre")
        session.commit()
        assert len(post.slides) == 10

        with pytest.raises(InvalidSlide, match="10"):
            add_slide(session, post, "carrusel-cierre")
        session.rollback()

        assert len(post.slides) == 10

    def test_no_se_mezclan_tamanos(
        self,
        session: Session,
        datos: dict,
        monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    ) -> None:
        """Hoy todas las publicables miden 1080×1350; una de 1080×1920 que se
        sume mañana no entra en un carrusel de 1080×1350."""
        alta = CardType("alta", "Alta", "marketing_story.html", 1080, 1920)
        monkeypatch.setitem(BY_SLUG, "alta", alta)
        post = _carrusel(session)

        with pytest.raises(InvalidSlide, match="tamaño"):
            add_slide(session, post, "alta")
        with pytest.raises(InvalidSlide, match="tamaño"):
            create_carousel_draft(
                session,
                card_slugs=("carrusel-portada", "alta"),
                planned_for=LUNES,
                product=None,
                province=None,
                data_through=DOMINGO,
            )

    def test_una_pieza_fija_o_desconocida_no_se_agrega(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        for slug in ("como-funciona", "og-compartir", "story-precalentamiento", "no-existe"):
            with pytest.raises(InvalidSlide):
                add_slide(session, post, slug)

    def test_una_story_no_se_convierte_en_carrusel(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        story = create_draft(
            session,
            card_slug="story-precalentamiento",
            planned_for=LUNES,
            product=None,
            province=None,
            data_through=DOMINGO,
        )
        session.commit()

        with pytest.raises(InvalidSlide):
            add_slide(session, story, "story-precalentamiento")


class TestOrden:
    def test_subir_la_tercera_la_cambia_con_la_segunda(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        nueva = move_slide(session, post, 3, -1)
        session.commit()

        assert nueva == 2
        assert [s for _, s in _orden(post)] == [
            "carrusel-portada",
            "dato-del-dia",
            "carrusel-consejo",
            "carrusel-cierre",
        ]

    def test_bajar_la_primera_la_cambia_con_la_segunda(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        assert move_slide(session, post, 1, +1) == 2
        session.commit()

        assert [s for _, s in _orden(post)][:2] == ["carrusel-consejo", "carrusel-portada"]

    def test_en_los_bordes_no_se_mueve(self, session: Session, datos: dict) -> None:  # noqa: F811
        post = _carrusel(session)

        assert move_slide(session, post, 1, -1) == 1
        assert move_slide(session, post, 4, +1) == 4
        session.commit()

        assert [s for _, s in _orden(post)] == list(CUATRO)

    def test_lo_movido_sobrevive_a_releer_de_la_base(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        move_slide(session, post, 4, -1)
        session.commit()
        session.expire_all()

        releida = session.get(MarketingPost, post.id)
        assert releida is not None
        assert _orden(releida) == [
            (1, "carrusel-portada"),
            (2, "carrusel-consejo"),
            (3, "carrusel-cierre"),
            (4, "dato-del-dia"),
        ]

    def test_quitar_renumera_sin_huecos(self, session: Session, datos: dict) -> None:  # noqa: F811
        post = _carrusel(session)

        remove_slide(session, post, 2)
        session.commit()
        session.expire_all()

        assert _orden(post) == [(1, "carrusel-portada"), (2, "dato-del-dia"), (3, "carrusel-cierre")]

    def test_con_una_sola_vuelve_a_ser_suelta_y_la_ultima_no_se_quita(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        for _ in range(3):
            remove_slide(session, post, 1)
        session.commit()

        assert post.format == PostFormat.SINGLE
        assert _orden(post) == [(1, "carrusel-cierre")]
        with pytest.raises(InvalidSlide):
            remove_slide(session, post, 1)

    def test_una_posicion_que_no_existe(self, session: Session, datos: dict) -> None:  # noqa: F811
        post = _carrusel(session)

        with pytest.raises(InvalidSlide):
            move_slide(session, post, 9, -1)
        with pytest.raises(InvalidSlide):
            remove_slide(session, post, 9)

    def test_una_aprobada_no_se_reordena_ni_cambia_de_diapositivas(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        approve(session, post, images_dir=tmp_path, render=JpegFalso())
        session.commit()

        with pytest.raises(InvalidTransition):
            add_slide(session, post, "carrusel-cierre")
        with pytest.raises(InvalidTransition):
            move_slide(session, post, 2, -1)
        with pytest.raises(InvalidTransition):
            remove_slide(session, post, 2)


class TestAprobarYDuplicar:
    def test_aprobar_congela_y_rasteriza_cada_una_en_su_orden(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        move_slide(session, post, 3, -1)
        render = JpegFalso()

        approve(session, post, images_dir=tmp_path, render=render)
        session.commit()

        assert post.status == PostStatus.APPROVED
        assert len(render.htmls) == 4
        # La segunda que se rasteriza es la de datos: el orden que dejó el operador.
        assert "Arroz" in render.htmls[1] and "Arroz" not in render.htmls[0]
        assert [s.image_path for s in post.slides] == [f"{post.id}/{n}.jpg" for n in (1, 2, 3, 4)]
        assert sorted(p.name for p in (tmp_path / str(post.id)).iterdir()) == [
            "1.jpg",
            "2.jpg",
            "3.jpg",
            "4.jpg",
        ]
        assert all(s.snapshot is not None for s in post.slides)

    def test_el_texto_del_carrusel_sale_de_la_diapositiva_con_datos(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        approve(session, post, images_dir=tmp_path, render=JpegFalso())

        assert post.caption is not None and post.caption.startswith("Arroz")

    def test_duplicar_un_carrusel_copia_todas_las_diapositivas(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        move_slide(session, post, 4, -1)
        approve(session, post, images_dir=tmp_path, render=JpegFalso())
        session.commit()

        copia = duplicate(session, post, today=LUNES)
        session.commit()

        assert copia.format == PostFormat.CAROUSEL
        assert copia.status == PostStatus.DRAFT
        assert [s for _, s in _orden(copia)] == [s for _, s in _orden(post)]
        assert all(s.snapshot is None and s.image_path is None for s in copia.slides)


class TestEditarUnaDiapositiva:
    def test_guardar_toca_solo_la_diapositiva_elegida(
        self,
        session: Session,
        datos: dict,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        antes = post.slides[0].options

        save_draft(
            session,
            post,
            position=3,
            product="Arroz",
            province=None,
            data_through=LUNES,
            options=parse_options(BY_SLUG["dato-del-dia"], {"b_comparacion": "0"}),
            caption=None,
        )
        session.commit()

        assert post.slides[2].params["data_through"] == "2026-10-19"
        assert post.slides[2].options["show"]["comparacion"] is False
        assert post.slides[0].options == antes
        assert post.slides[0].params["data_through"] == "2026-10-18"


class TestContador:
    """Las plantillas traían «1 / 4», «2 / 4», «3 / 4» y «4 / 4» escritos a
    mano: reordenar o sumar una diapositiva los hacía mentir."""

    def test_cada_diapositiva_dice_su_lugar_real_en_el_carrusel(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        move_slide(session, post, 2, -1)  # «cómo leer» pasa a ser la primera
        add_slide(session, post, "carrusel-cierre")
        render = JpegFalso()

        approve(session, post, images_dir=tmp_path, render=render)

        # Orden: cómo leer, portada, dato del día, cierre, cierre. «Dato del día»
        # no tiene contador en su diseño; las otras cuatro dicen su lugar real.
        assert len(render.htmls) == 5
        for n in (1, 2, 4, 5):
            assert f">{n} / 5<" in render.htmls[n - 1]
        assert " / 5<" not in render.htmls[2]
        assert not any("/ 4<" in html for html in render.htmls)
        assert "EJEMPLO ILUSTRATIVO" in render.htmls[0]

    def test_una_suelta_no_muestra_contador(
        self,
        session: Session,
        datos: dict,
        tmp_path: Path,  # noqa: F811
    ) -> None:
        post = create_draft(
            session,
            card_slug="carrusel-portada",
            planned_for=LUNES,
            product=None,
            province=None,
            data_through=DOMINGO,
        )
        session.commit()
        render = JpegFalso()

        approve(session, post, images_dir=tmp_path, render=render)

        assert " / 4<" not in render.htmls[0] and " / 1<" not in render.htmls[0]
