"""La sección «Instagram» de `/marketing` (`saimc.instagram.panel`).

Lo que se fija: cada publicación muestra su ÚLTIMA lectura (la serie entera
queda en la base), un carrusel sale sin cifras, y los seguidores se ven por
día con la última lectura de cada día y el cambio contra el día anterior. La
puerta del operador tiene su propio test (`test_control_marketing_auth.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.instagram.panel import load_panel
from saimc.models import InstagramAccountSnapshot, InstagramMedia, InstagramMediaMetrics
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

AHORA = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)


@pytest.fixture
def datos(session: Session) -> None:
    reel = InstagramMedia(
        ig_media_id="18000000000000002",
        media_type="REELS",
        permalink="https://www.instagram.com/reel/BBB222/",
        caption="De los grupos a la cifra: así se arma cada precio que publica Guani cada semana",
        posted_at=datetime(2026, 10, 11, 20, 30, tzinfo=UTC),
        first_seen_at=AHORA - timedelta(days=2),
        last_synced_at=AHORA,
    )
    carrusel = InstagramMedia(
        ig_media_id="18000000000000003",
        media_type="FEED",
        permalink="https://www.instagram.com/p/CCC333/",
        caption="Cómo leer los precios",
        posted_at=datetime(2026, 10, 12, 9, 0, tzinfo=UTC),
        first_seen_at=AHORA - timedelta(days=1),
        last_synced_at=AHORA,
    )
    session.add_all([reel, carrusel])
    session.flush()
    session.add_all(
        [
            InstagramMediaMetrics(
                media_id=reel.id, captured_at=AHORA - timedelta(hours=3), views=400, reach=300,
                likes=20, comments=2, saved=4, shares=1, total_interactions=27, avg_watch_time_ms=7000,
            ),
            InstagramMediaMetrics(
                media_id=reel.id, captured_at=AHORA, views=1520, reach=980,
                likes=64, comments=7, saved=12, shares=9, total_interactions=92, avg_watch_time_ms=8450,
            ),
            InstagramAccountSnapshot(
                captured_at=datetime(2026, 10, 12, 9, 0, tzinfo=UTC), followers_count=300, media_count=25
            ),
            InstagramAccountSnapshot(
                captured_at=datetime(2026, 10, 12, 21, 0, tzinfo=UTC), followers_count=305, media_count=26
            ),
            InstagramAccountSnapshot(captured_at=AHORA, followers_count=312, media_count=27),
        ]
    )
    session.commit()


class TestLoadPanel:
    def test_sin_lecturas_esta_vacio(self, session: Session) -> None:
        panel = load_panel(session, now=AHORA)

        assert panel.posts == []
        assert panel.followers == []
        assert panel.current_followers is None
        assert panel.last_sync is None

    def test_cada_publicacion_muestra_su_ultima_lectura(self, session: Session, datos: None) -> None:
        panel = load_panel(session, now=AHORA)

        reel = next(p for p in panel.posts if p.ig_media_id == "18000000000000002")
        assert reel.metrics is not None
        assert (reel.metrics.views, reel.metrics.total_interactions) == (1520, 92)
        assert reel.avg_watch_seconds == pytest.approx(8.45)

    def test_lo_mas_nuevo_primero_y_el_carrusel_sin_cifras(self, session: Session, datos: None) -> None:
        panel = load_panel(session, now=AHORA)

        assert [p.ig_media_id for p in panel.posts] == ["18000000000000003", "18000000000000002"]
        assert panel.posts[0].metrics is None

    def test_el_texto_largo_se_recorta(self, session: Session, datos: None) -> None:
        panel = load_panel(session, now=AHORA)

        reel = panel.posts[1]
        assert len(reel.caption_short) <= 61
        assert reel.caption_short.endswith("…")

    def test_seguidores_por_dia_con_el_cambio(self, session: Session, datos: None) -> None:
        panel = load_panel(session, now=AHORA)

        assert [(d.day, d.followers, d.change) for d in panel.followers] == [
            (date(2026, 10, 13), 312, 7),
            (date(2026, 10, 12), 305, None),
        ]
        assert panel.current_followers == 312
        assert panel.last_sync == AHORA

    def test_lo_de_hace_mas_de_30_dias_no_entra(self, session: Session) -> None:
        session.add(
            InstagramAccountSnapshot(captured_at=AHORA - timedelta(days=40), followers_count=1, media_count=1)
        )
        session.commit()

        panel = load_panel(session, now=AHORA)

        assert panel.followers == []
        # La cifra actual sí: es la última lectura, sea de cuando sea.
        assert panel.current_followers == 1


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestSeccionEnMarketing:
    def test_sin_lecturas_explica_como_empezar(self, client: TestClient) -> None:
        respuesta = client.get("/marketing")

        assert respuesta.status_code == 200
        assert 'id="instagram"' in respuesta.text
        assert "Todavía no hay lecturas de Instagram" in respuesta.text

    def test_con_lecturas_muestra_publicaciones_y_seguidores(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing")

        assert respuesta.status_code == 200
        assert "https://www.instagram.com/reel/BBB222/" in respuesta.text
        assert "1520" in respuesta.text
        assert "Reel" in respuesta.text
        assert "312" in respuesta.text
        assert "+7" in respuesta.text
        assert "Todavía no hay lecturas de Instagram" not in respuesta.text
