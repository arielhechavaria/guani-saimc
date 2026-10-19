"""Semana e historial de la fábrica (F4 de `docs/plan-fabrica-marketing.md`).

La semana es de solo lectura: lunes a domingo, lo que pide el calendario del
lote (`cards_for_day`) y el estado de lo que existe. Los casos se escriben a
mano antes del código: semana con todos los días cubiertos, con huecos y
vacía; el historial lee lo congelado, nunca la base viva.

Hoy fijo = miércoles 2026-10-21; su semana va del lunes 19 al domingo 25.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.marketing.daily import DATOS_SLOT, EDU_SLOT, cards_for_day, create_datos_draft
from saimc.marketing.lessons import lesson_for_day
from saimc.marketing.posts import (
    approve,
    create_draft,
    create_lesson_draft,
    discard,
    mark_published,
)
from saimc.marketing.week import (
    build_week,
    published_history,
    week_monday,
)
from saimc.models import MarketingPost
from saimc.models.marketing_post import PostOrigin
from tests.test_marketing_post_router import _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client, datos  # noqa: F401

LUNES = date(2026, 10, 19)
MIERCOLES = date(2026, 10, 21)
DOMINGO = date(2026, 10, 25)


def _lote(
    session: Session, day: date, slot: str, *, through: date | None = None
) -> MarketingPost:
    """Un borrador del lote para `day`, como lo deja el temporizador de las 06:00."""
    comun = {
        "planned_for": day, "product": "Arroz", "province": "La Habana",
        "data_through": through or day, "origin": PostOrigin.BATCH, "batch_slot": slot,
    }
    if slot == EDU_SLOT:
        post = create_lesson_draft(
            session, lesson=lesson_for_day(day), planned_for=day, origin=PostOrigin.BATCH,
            batch_slot=slot,
        )
    elif slot == DATOS_SLOT:
        post = create_datos_draft(
            session, day, ["Arroz"], data_through=through or day, origin=PostOrigin.BATCH,
            batch_slot=slot,
        )
    else:
        post = create_draft(session, card_slug=slot, **comun)  # type: ignore[arg-type]
    session.commit()
    return post


def _publicar(session: Session, post: MarketingPost, carpeta: Path) -> None:
    approve(session, post, images_dir=carpeta / "posts", render=_jpeg_falso)
    mark_published(post, "https://www.instagram.com/p/ABC123/")
    session.commit()


def _estados(semana, day: date) -> dict[str, str]:  # type: ignore[no-untyped-def]
    dia = next(d for d in semana.days if d.day == day)
    return {s.slot: s.state for s in dia.slots}


class TestWeekMonday:
    @pytest.mark.parametrize("dia", [LUNES, MIERCOLES, DOMINGO])
    def test_cualquier_dia_de_la_semana_cae_en_su_lunes(self, dia: date) -> None:
        assert week_monday(dia) == LUNES

    def test_el_domingo_pertenece_a_la_semana_que_cierra(self) -> None:
        assert week_monday(DOMINGO + timedelta(days=1)) == DOMINGO + timedelta(days=1)


class TestBuildWeek:
    def test_siete_dias_de_lunes_a_domingo_con_sus_vecinas(self, session: Session) -> None:
        semana = build_week(session, LUNES, today=MIERCOLES)

        assert [d.day for d in semana.days] == [LUNES + timedelta(days=i) for i in range(7)]
        assert semana.previous == LUNES - timedelta(days=7)
        assert semana.next == LUNES + timedelta(days=7)
        assert semana.is_current

    def test_cada_dia_pide_lo_que_pide_el_calendario_del_lote(self, session: Session) -> None:
        semana = build_week(session, LUNES, today=MIERCOLES)

        for dia in semana.days:
            assert tuple(s.slot for s in dia.slots) == cards_for_day(dia.day)

    def test_semana_vacia_falta_lo_pasado_y_lo_de_hoy_y_lo_futuro_esta_pendiente(
        self, session: Session
    ) -> None:
        semana = build_week(session, LUNES, today=MIERCOLES)

        assert _estados(semana, LUNES) == {
            DATOS_SLOT: "missing", EDU_SLOT: "missing", "como-se-movio": "missing"
        }
        assert _estados(semana, MIERCOLES) == {DATOS_SLOT: "missing", EDU_SLOT: "missing"}
        assert _estados(semana, DOMINGO) == {DATOS_SLOT: "pending", EDU_SLOT: "pending"}
        # Lunes (3) + martes (2) + miércoles (2).
        assert semana.missing == 7

    def test_semana_completa_no_falta_nada(self, session: Session, tmp_path: Path) -> None:
        for dia in (LUNES + timedelta(days=i) for i in range(7)):
            for slot in cards_for_day(dia):
                _lote(session, dia, slot)

        semana = build_week(session, LUNES, today=DOMINGO)

        assert semana.missing == 0
        assert all(s.state == "draft" for d in semana.days for s in d.slots)

    def test_semana_incompleta_marca_solo_el_hueco(self, session: Session) -> None:
        _lote(session, LUNES, DATOS_SLOT)
        _lote(session, LUNES, "como-se-movio")
        _lote(session, MIERCOLES - timedelta(days=1), DATOS_SLOT)
        # el carrusel de datos del miércoles falta
        for dia in (LUNES, MIERCOLES - timedelta(days=1), MIERCOLES):
            _lote(session, dia, EDU_SLOT)

        semana = build_week(session, LUNES, today=MIERCOLES)

        assert _estados(semana, MIERCOLES) == {DATOS_SLOT: "missing", EDU_SLOT: "draft"}
        assert semana.missing == 1

    def test_un_borrador_o_aprobada_de_un_dia_pasado_queda_marcado_sin_publicar(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        martes = LUNES + timedelta(days=1)
        _lote(session, LUNES, "como-se-movio")  # borrador vencido
        aprobada = _lote(session, martes, "dato-del-dia", through=date.today())
        approve(session, aprobada, images_dir=tmp_path / "posts", render=_jpeg_falso)
        session.commit()

        semana = build_week(session, LUNES, today=MIERCOLES)

        lunes = next(d for d in semana.days if d.day == LUNES)
        como = next(s for s in lunes.slots if s.slot == "como-se-movio")
        assert (como.state, como.overdue) == ("draft", True)
        dato = next(s for s in next(d for d in semana.days if d.day == martes).slots)
        assert (dato.state, dato.overdue) == ("approved", True)
        # Lo que existe sin publicar no es «falta»: el dato y el educativo del
        # lunes, el educativo del martes, y los dos de hoy.
        assert semana.missing == 5

    def test_una_publicada_no_queda_marcada_como_vencida(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        post = _lote(session, LUNES, "dato-del-dia", through=date.today())
        _publicar(session, post, tmp_path)

        semana = build_week(session, LUNES, today=MIERCOLES)

        dato = next(s for s in semana.days[0].slots if s.slot == DATOS_SLOT)
        assert (dato.state, dato.overdue) == ("published", False)

    def test_una_descartada_no_cubre_el_turno(self, session: Session) -> None:
        post = _lote(session, LUNES, "dato-del-dia")
        discard(post, images_dir=Path("/nonexistent"))
        session.commit()

        semana = build_week(session, LUNES, today=MIERCOLES)

        assert _estados(semana, LUNES)[DATOS_SLOT] == "missing"

    def test_lo_del_operador_cubre_el_turno_de_datos_o_el_educativo(
        self, session: Session
    ) -> None:
        create_datos_draft(session, MIERCOLES, ["Arroz"], data_through=MIERCOLES)
        create_lesson_draft(
            session, lesson=lesson_for_day(MIERCOLES), planned_for=MIERCOLES
        )
        session.commit()

        semana = build_week(session, LUNES, today=MIERCOLES)

        assert _estados(semana, MIERCOLES) == {DATOS_SLOT: "draft", EDU_SLOT: "draft"}

    def test_semana_futura_es_toda_pendiente_y_no_es_la_actual(self, session: Session) -> None:
        semana = build_week(session, LUNES + timedelta(days=7), today=MIERCOLES)

        assert not semana.is_current
        assert semana.missing == 0
        assert all(s.state == "pending" for d in semana.days for s in d.slots)

    def test_las_stories_son_notas_a_mano_de_la_guia(self, session: Session) -> None:
        semana = build_week(session, LUNES, today=MIERCOLES)

        notas = {d.day.weekday(): d.stories for d in semana.days}
        assert "Encuesta" in notas[2]
        assert "precalentamiento" in notas[6]
        assert notas[1] is None  # el martes no pide stories


class TestPublishedHistory:
    def test_de_la_mas_reciente_a_la_mas_vieja_con_enlace_y_datos_congelados(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        hoy = date.today()
        vieja = _lote(session, hoy - timedelta(days=2), "dato-del-dia")
        reciente = _lote(session, hoy - timedelta(days=1), "dato-del-dia")
        _publicar(session, vieja, tmp_path)
        _publicar(session, reciente, tmp_path)
        _lote(session, hoy, "dato-del-dia")  # un borrador no entra al historial

        historial = published_history(session)

        assert [h.post.id for h in historial] == [reciente.id, vieja.id]
        primero = historial[0]
        assert primero.url == "https://www.instagram.com/p/ABC123/"
        assert primero.slides[0].label == "Dato del día"
        assert primero.slides[0].product == "Arroz"
        assert primero.slides[0].data_through == hoy - timedelta(days=1)

    def test_lee_lo_congelado_aunque_cambie_la_base(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        post = _lote(session, date.today(), "dato-del-dia")
        _publicar(session, post, tmp_path)
        congelado = post.slides[0].snapshot["data_through"]  # type: ignore[index]

        # Si alguien tocara `params` (no pasa: el listener lo impide), el
        # historial igual dice lo congelado en el snapshot.
        assert published_history(session)[0].slides[0].data_through == date.fromisoformat(congelado)

    def test_el_limite_corta_el_historial(
        self, session: Session, datos: None, tmp_path: Path  # noqa: F811
    ) -> None:
        for i in range(3):
            _publicar(session, _lote(session, date.today() - timedelta(days=i), "dato-del-dia"), tmp_path)

        assert len(published_history(session, limit=2)) == 2


class TestSemanaRuta:
    def test_semana_actual_muestra_los_dias_y_marca_lo_que_falta(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        from saimc.web.app import app
        from saimc.web.routers.marketing import get_today

        app.dependency_overrides[get_today] = lambda: MIERCOLES
        _lote(session, MIERCOLES, "dato-del-dia")

        html = client.get("/marketing/semana").text

        assert "Lunes 19" in html and "Domingo 25" in html
        assert "Falta «Datos del día»" in html  # lunes y martes
        assert "Falta «Carrusel»" not in html  # ya no es un turno del lote
        assert "Encuesta" in html  # la nota de stories del miércoles

    def test_desde_mueve_de_semana_y_normaliza_a_lunes(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        html = client.get("/marketing/semana?desde=2026-10-28").text  # un miércoles

        assert "Lunes 26" in html
        assert "?desde=2026-10-19" in html  # anterior
        assert "?desde=2026-11-02" in html  # siguiente
        assert "Falta" not in html  # semana futura: nada vencido

    def test_desde_invalido_cae_en_la_semana_actual(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        from saimc.web.app import app
        from saimc.web.routers.marketing import get_today

        app.dependency_overrides[get_today] = lambda: MIERCOLES

        resp = client.get("/marketing/semana?desde=ayer")

        assert resp.status_code == 200
        assert "Lunes 19" in resp.text

    def test_historial_con_enlace_externo_y_datos_congelados(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _lote(session, date.today(), "dato-del-dia")
        _publicar(session, post, fabrica)

        html = client.get("/marketing/semana").text

        assert 'href="https://www.instagram.com/p/ABC123/"' in html
        assert 'rel="noopener' in html
        assert f"/marketing/post/{post.id}/slide/1.jpg" in html
        assert "Arroz" in html
        assert "Cifras hasta el" in html

    def test_sin_publicadas_el_historial_lo_dice(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        assert "Todavía no hay publicaciones publicadas" in client.get("/marketing/semana").text

    def test_es_solo_lectura(self, client: TestClient, fabrica: Path, session: Session) -> None:  # noqa: F811
        antes = session.query(MarketingPost).count()

        client.get("/marketing/semana")
        resp = client.post("/marketing/semana")

        assert session.query(MarketingPost).count() == antes
        assert resp.status_code == 405

    def test_privacidad_el_historial_no_muestra_ningun_dato_de_origen(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        _publicar(session, _lote(session, date.today(), "dato-del-dia"), fabrica)

        html = client.get("/marketing/semana").text.lower()

        for palabra in ("whatsapp", "telegram", "teléfono", "grupo", "canal"):
            assert palabra not in html.replace("instagram.com/p/", "")
