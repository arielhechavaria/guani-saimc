"""K4 — aviso de hueco de ingesta por canal.

La regla la aprobó el operador el 2026-10-03: un canal activo avisa cuando su
silencio supera `max(24 h, 2 × p95 de sus huecos en 30 días)`. Los valores
esperados salen de la medición de ese día y están escritos a mano, no
derivados del código.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion.channel_gaps import (
    ChannelRef,
    channel_gap_alerts,
    load_channel_gap_report,
    p95_gap_hours,
    threshold_hours,
)
from saimc.models import Channel, Platform, RawIngestion
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

AHORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

# Textos que marcan el aviso en cada página: su ausencia prueba «no se
# renderiza nada» cuando todo está bien.
MARCA_LISTA = "Canales sin mensajes"
MARCA_TODO = "No entra nada desde hace"


def _serie(silencio_h: float, p95_h: float) -> list[datetime]:
    """Marcas de tiempo con p95 de huecos exactamente `p95_h`.

    21 huecos: 19 de 1 h y 2 de `p95_h`. Con interpolación lineal el índice
    del p95 es (21-1)*0.95 = 19, o sea el hueco 20.º en orden: `p95_h`.
    """
    huecos = [1.0] * 19 + [p95_h, p95_h]
    ultimo = AHORA - timedelta(hours=silencio_h)
    marcas = [ultimo]
    for h in reversed(huecos):
        marcas.append(marcas[-1] - timedelta(hours=h))
    return sorted(marcas)


def _canal(nombre: str = "Canal", activo: bool = True) -> ChannelRef:
    return ChannelRef(id=uuid.uuid4(), name=nombre, is_active=activo)


class TestUmbral:
    @pytest.mark.parametrize(
        ("p95", "esperado"),
        [
            (9.3, 24.0),
            (15.7, 31.4),
            (16.2, 32.4),
            (18.3, 36.6),
            (18.5, 37.0),
            (26.6, 53.2),
            (57.6, 115.2),
            (67.4, 134.8),
        ],
    )
    def test_valores_medidos_el_2026_10_03(self, p95: float, esperado: float) -> None:
        assert threshold_hours(p95) == pytest.approx(esperado)

    def test_sin_huecos_vale_el_piso(self) -> None:
        assert threshold_hours(None) == 24.0

    def test_p95_con_interpolacion_lineal(self) -> None:
        # 21 huecos 1..20 y 100 h: el índice 19 cae justo en el valor 20.
        huecos = [*range(1, 21), 100]
        marcas = [AHORA - timedelta(days=20)]
        for h in huecos:
            marcas.append(marcas[-1] + timedelta(hours=h))
        assert marcas[-1] <= AHORA
        assert p95_gap_hours(marcas, now=AHORA) == pytest.approx(20.0)

    def test_p95_interpola_entre_dos_huecos(self) -> None:
        # 3 huecos 10, 20, 30: índice 1.9 → 20 + 0.9*(30-20) = 29.
        marcas = [AHORA - timedelta(hours=70)]
        for h in (10, 20, 30):
            marcas.append(marcas[-1] + timedelta(hours=h))
        assert p95_gap_hours(marcas, now=AHORA) == pytest.approx(29.0)

    def test_un_solo_mensaje_no_tiene_huecos(self) -> None:
        assert p95_gap_hours([AHORA - timedelta(hours=5)], now=AHORA) is None

    def test_huecos_fuera_de_la_ventana_de_30_dias_no_cuentan(self) -> None:
        # El hueco de 250 h es de hace casi 50 días: no entra.
        viejo = AHORA - timedelta(days=60)
        marcas = [
            viejo,
            viejo + timedelta(hours=250),
            AHORA - timedelta(hours=10),
            AHORA - timedelta(hours=5),
        ]
        assert p95_gap_hours(marcas, now=AHORA) == pytest.approx(5.0)


class TestAlertas:
    def test_25_h_con_p95_de_9_3_avisa(self) -> None:
        canal = _canal("Rápido")
        informe = channel_gap_alerts(AHORA, [canal], {canal.id: _serie(25, 9.3)})
        assert [s.name for s in informe.silent] == ["Rápido"]
        assert informe.silent[0].silence_hours == pytest.approx(25.0)
        assert informe.silent[0].threshold_hours == pytest.approx(24.0)

    def test_23_h_con_p95_de_9_3_no_avisa(self) -> None:
        canal = _canal()
        informe = channel_gap_alerts(AHORA, [canal], {canal.id: _serie(23, 9.3)})
        assert informe.silent == ()

    def test_canal_lento_a_42_8_h_no_avisa(self) -> None:
        canal = _canal("Lento")
        informe = channel_gap_alerts(AHORA, [canal], {canal.id: _serie(42.8, 67.4)})
        assert informe.silent == ()

    def test_canal_lento_pasado_su_umbral_avisa(self) -> None:
        canal = _canal("Lento")
        otro = _canal("Otro")
        informe = channel_gap_alerts(
            AHORA,
            [canal, otro],
            {canal.id: _serie(140, 67.4), otro.id: _serie(1, 9.3)},
        )
        assert [s.name for s in informe.silent] == ["Lento"]
        assert informe.silent[0].threshold_hours == pytest.approx(134.8)

    def test_canal_inactivo_nunca_avisa(self) -> None:
        canal = _canal(activo=False)
        informe = channel_gap_alerts(AHORA, [canal], {canal.id: _serie(300, 9.3)})
        assert informe.silent == ()
        assert not informe.all_silent

    def test_canal_activo_sin_ningun_mensaje_no_avisa(self) -> None:
        # Recién dado de alta: no hay línea de base de la que estar callado.
        canal = _canal()
        informe = channel_gap_alerts(AHORA, [canal], {})
        assert informe.silent == ()
        assert not informe.all_silent

    def test_un_solo_mensaje_viejo_avisa_con_el_piso(self) -> None:
        canal = _canal("Uno")
        informe = channel_gap_alerts(
            AHORA, [canal], {canal.id: [AHORA - timedelta(hours=30)]}
        )
        assert informe.silent[0].threshold_hours == 24.0

    def test_ultimo_mensaje_fuera_de_la_ventana_avisa_con_el_piso(self) -> None:
        canal = _canal("Viejo")
        informe = channel_gap_alerts(
            AHORA, [canal], {canal.id: [AHORA - timedelta(days=45)]}
        )
        assert informe.silent[0].threshold_hours == 24.0
        assert informe.silent[0].silence_hours == pytest.approx(45 * 24)

    def test_todos_callados_da_un_solo_mensaje_con_el_silencio_mas_corto(self) -> None:
        a, b = _canal("A"), _canal("B")
        informe = channel_gap_alerts(
            AHORA, [a, b], {a.id: _serie(30, 9.3), b.id: _serie(50, 9.3)}
        )
        assert informe.all_silent
        assert informe.hours_without_intake == pytest.approx(30.0)
        assert informe.headline == "No entra nada desde hace 30 h"
        assert "WAHA" in informe.hint

    def test_algunos_callados_lista_nombre_horas_y_umbral(self) -> None:
        a, b = _canal("Callado"), _canal("Vivo")
        informe = channel_gap_alerts(
            AHORA, [a, b], {a.id: _serie(40, 9.3), b.id: _serie(2, 9.3)}
        )
        assert not informe.all_silent
        assert informe.headline == ""
        assert [(s.name, s.silence_hours, s.threshold_hours) for s in informe.silent] == [
            ("Callado", pytest.approx(40.0), pytest.approx(24.0))
        ]

    def test_el_inactivo_no_cuenta_para_decidir_que_todos_callan(self) -> None:
        a, muerto = _canal("A"), _canal("Muerto", activo=False)
        informe = channel_gap_alerts(
            AHORA, [a, muerto], {a.id: _serie(30, 9.3), muerto.id: _serie(2, 9.3)}
        )
        assert informe.all_silent

    def test_nada_callado_no_hay_informe(self) -> None:
        a = _canal()
        informe = channel_gap_alerts(AHORA, [a], {a.id: _serie(3, 9.3)})
        assert informe.silent == ()
        assert not informe.all_silent
        assert informe.headline == ""

    def test_orden_por_silencio_relativo_al_umbral(self) -> None:
        a, b = _canal("Poco"), _canal("Mucho")
        informe = channel_gap_alerts(
            AHORA, [a, b], {a.id: _serie(30, 9.3), b.id: _serie(100, 9.3)}
        )
        assert [s.name for s in informe.silent] == ["Mucho", "Poco"]


def _agregar(
    session: Session, canal: Channel, hace_horas: float, hash_: str
) -> None:
    session.add(
        RawIngestion(
            source_type=SourceType.WHATSAPP_CHANNEL,
            file_path=f"raw/{hash_}.dat",
            content_hash=hash_,
            content_type=ContentType.TEXT,
            original_text="x",
            processing_status=ProcessingStatus.PENDING,
            captured_at=AHORA - timedelta(hours=hace_horas),
            channel_id=canal.id,
        )
    )


class TestCargador:
    def test_lee_canales_y_marcas_de_la_base(self, session: Session) -> None:
        canal = Channel(name="Real", platform=Platform.WHATSAPP_CHANNEL)
        session.add(canal)
        session.flush()
        _agregar(session, canal, 30, "g1")
        _agregar(session, canal, 40, "g2")
        _agregar(session, canal, 24 * 45, "g3")  # fuera de ventana: no es el último
        session.commit()

        informe = load_channel_gap_report(session, now=AHORA)

        assert [s.name for s in informe.silent] == ["Real"]
        assert informe.silent[0].silence_hours == pytest.approx(30.0)

    def test_usa_dos_consultas_a_lo_sumo(self, session: Session) -> None:
        from sqlalchemy import event

        canales = [
            Channel(name=f"C{i}", platform=Platform.WHATSAPP_CHANNEL) for i in range(5)
        ]
        session.add_all(canales)
        session.flush()
        for i, c in enumerate(canales):
            _agregar(session, c, 2 + i, f"q{i}")
        session.commit()

        consultas: list[str] = []
        engine = session.get_bind()
        event.listen(
            engine, "before_cursor_execute", lambda *a: consultas.append(a[2])
        )
        load_channel_gap_report(session, now=AHORA)
        assert len(consultas) <= 2


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        yield session

    # `GET /curation` ingesta la bandeja antes de mostrar la cola: se aísla
    # para no tocar `inbox/` ni `data/raw/` reales.
    (tmp_path / "inbox").mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _sembrar(session: Session, *, silencios: dict[str, float]) -> None:
    """Un canal por nombre, con 3 mensajes y el último hace `silencio` horas."""
    ahora = datetime.now(UTC)
    for nombre, silencio in silencios.items():
        canal = Channel(name=nombre, platform=Platform.WHATSAPP_CHANNEL)
        session.add(canal)
        session.flush()
        for i, extra in enumerate((0, 5, 10)):
            session.add(
                RawIngestion(
                    source_type=SourceType.WHATSAPP_CHANNEL,
                    file_path=f"raw/{nombre}{i}.dat",
                    content_hash=f"{nombre}-{i}",
                    content_type=ContentType.TEXT,
                    original_text="x",
                    processing_status=ProcessingStatus.PENDING,
                    captured_at=ahora - timedelta(hours=silencio + extra),
                    channel_id=canal.id,
                )
            )
    session.commit()


class TestPaginas:
    def test_curation_sin_aviso_cuando_todo_entra(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"Vivo": 1})
        texto = client.get("/curation").text
        assert MARCA_LISTA not in texto
        assert MARCA_TODO not in texto

    def test_curation_lista_los_canales_callados(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"Vivo": 1, "Callado": 60})
        texto = client.get("/curation").text
        assert MARCA_LISTA in texto
        assert "Callado" in texto
        assert "60 h" in texto
        assert "aviso a las 24 h" in texto
        assert MARCA_TODO not in texto

    def test_curation_un_solo_mensaje_si_todos_callan(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"A": 40, "B": 70})
        texto = client.get("/curation").text
        assert f"{MARCA_TODO} 40 h" in texto
        assert "WAHA" in texto
        assert MARCA_LISTA not in texto

    def test_control_indicador_ok_cuando_todo_entra(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"Vivo": 1})
        texto = client.get("/control").text
        assert MARCA_LISTA not in texto
        assert MARCA_TODO not in texto

    def test_control_lista_canales_callados(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"Vivo": 1, "Callado": 60})
        texto = client.get("/control").text
        assert "Callado" in texto
        assert "60 h" in texto

    def test_control_todos_callados_es_down(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"A": 40, "B": 70})
        texto = client.get("/control").text
        assert f"{MARCA_TODO} 40 h" in texto
        # La tarjeta de «Última captura» es la que pasa a rojo.
        assert re.search(
            r'is-down">\s*<div class="m3-health-top">\s*<span class="m3-health-dot"></span>'
            r'\s*<span class="m3-health-label">Última captura',
            texto,
        )

    def test_control_un_canal_callado_es_warn(
        self, client: TestClient, session: Session
    ) -> None:
        _sembrar(session, silencios={"Vivo": 1, "Callado": 60})
        texto = client.get("/control").text
        assert re.search(
            r'is-warn">\s*<div class="m3-health-top">\s*<span class="m3-health-dot"></span>'
            r'\s*<span class="m3-health-label">Última captura',
            texto,
        )
