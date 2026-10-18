"""Vista de flujo de `/control` (`/control/flujo`): el módulo puro y el endpoint.

Los casos de las funciones puras se escribieron a mano, antes que el código:
la respuesta correcta (cuántas barras, de qué alto, qué salud) está definida
acá y no sale de correr `control_flow`.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.extraction.prefill_status import PrefillRun
from saimc.models import (
    CurationAction,
    CurationEvent,
    CurationMode,
    ProcessingStatus,
    RawIngestion,
)
from saimc.progress import CoverageCell, Last24h
from saimc.validation.lane0 import IMAGE, TEXT, Bound
from saimc.web import control_flow
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.control_flow import FlowInputs, FlowNode, FlowView
from saimc.web.deps import get_db
from saimc.web.health_strip import Indicator

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _node(view: FlowView, key: str) -> FlowNode:
    return next(n for n in view.nodes if n.key == key)


def _indicator(key: str, level: str, value: str = "x") -> Indicator:
    return Indicator(key=key, label=key, value=value, level=level)


def _rects(chart: control_flow.Chart) -> list[control_flow.Shape]:
    return [s for s in chart.shapes if s.tag == "rect"]


# --- ingesta: barras por hora ---------------------------------------------------


class TestHourlyCounts:
    def test_cada_marca_cae_en_la_hora_que_le_toca(self) -> None:
        marcas = [
            NOW - timedelta(minutes=30),  # hace 0,5 h -> última barra
            NOW - timedelta(minutes=5),  # idem
            NOW - timedelta(hours=1, minutes=1),  # hace 1,02 h -> la anterior
            NOW - timedelta(hours=23, minutes=30),  # primera barra
        ]

        conteos = control_flow.hourly_counts(marcas, NOW)

        assert len(conteos) == 24
        assert conteos[23] == 2
        assert conteos[22] == 1
        assert conteos[0] == 1
        assert sum(conteos) == 4

    def test_lo_de_hace_24_horas_o_mas_y_lo_futuro_quedan_fuera(self) -> None:
        marcas = [NOW - timedelta(hours=24), NOW - timedelta(hours=30), NOW + timedelta(minutes=1)]

        assert sum(control_flow.hourly_counts(marcas, NOW)) == 0

    def test_la_barra_mas_alta_llena_el_grafico_y_la_vacia_es_una_linea(self) -> None:
        conteos = [0] * 24
        conteos[5] = 10
        conteos[23] = 5

        grafico = control_flow.hourly_chart(conteos)
        barras = _rects(grafico)

        assert len(barras) == 24
        assert barras[5].attrs["height"] == 40
        assert barras[5].attrs["y"] == 0
        assert barras[23].attrs["height"] == 20
        assert barras[0].attrs["height"] == 1
        assert "is-now" in barras[23].cls
        assert "is-now" not in barras[5].cls

    def test_sin_ninguna_marca_no_divide_por_cero(self) -> None:
        barras = _rects(control_flow.hourly_chart([0] * 24))

        assert {b.attrs["height"] for b in barras} == {1}


# --- cola: histograma de edades -------------------------------------------------


class TestAgeBuckets:
    def test_los_bordes_pertenecen_al_tramo_de_arriba(self) -> None:
        # <6 h | 6–24 h | 1–2 d | 2–7 d | >7 d
        edades = [0, 5.9, 6, 23.9, 24, 47.9, 48, 167.9, 168, 1000]

        assert control_flow.age_buckets(edades) == [2, 2, 2, 2, 2]

    def test_sin_pendientes_todo_en_cero(self) -> None:
        assert control_flow.age_buckets([]) == [0, 0, 0, 0, 0]

    def test_alturas_proporcionales_a_la_barra_mas_alta(self) -> None:
        barras = _rects(control_flow.age_chart([14, 12, 7, 4, 0], max_age_days=0))

        assert [b.attrs["height"] for b in barras] == [28, 24, 14, 8, 1]

    def test_sin_tope_de_edad_ninguna_barra_se_pinta_de_vencida(self) -> None:
        barras = _rects(control_flow.age_chart([1, 1, 1, 1, 1], max_age_days=0))

        assert not any("err" in b.cls for b in barras)

    def test_con_tope_se_pintan_los_tramos_que_empiezan_en_o_despues_del_tope(self) -> None:
        barras = _rects(control_flow.age_chart([1, 1, 1, 1, 1], max_age_days=2))

        # El tramo 2–7 d empieza a las 48 h = 2 días: ya está en el tope.
        assert [("err" in b.cls) for b in barras] == [False, False, False, True, True]


# --- pre-cálculo: reloj ----------------------------------------------------------


class TestClockRing:
    def test_mitad_del_periodo_es_media_vuelta(self) -> None:
        grafico = control_flow.clock_chart(elapsed_hours=1.5, period_hours=3, health="ok")
        arco = next(s for s in grafico.shapes if "ring" in s.cls)

        assert arco.attrs["stroke-dasharray"] == "69.1 138.2"
        assert next(s for s in grafico.shapes if s.tag == "text").text == "1 h"

    def test_pasado_el_periodo_el_arco_se_queda_en_vuelta_completa(self) -> None:
        grafico = control_flow.clock_chart(elapsed_hours=9, period_hours=3, health="warn")
        arco = next(s for s in grafico.shapes if "ring" in s.cls)

        assert arco.attrs["stroke-dasharray"] == "138.2 138.2"
        assert "warn" in arco.cls

    def test_sin_registro_solo_hay_pista_y_un_guion(self) -> None:
        grafico = control_flow.clock_chart(elapsed_hours=None, period_hours=3, health="off")

        assert not any("ring" in s.cls for s in grafico.shapes)
        assert next(s for s in grafico.shapes if s.tag == "text").text == "—"


# --- carril 0: error auditado contra la meta -------------------------------------


class TestBulletChart:
    def test_la_barra_y_la_meta_se_escalan_con_la_meta(self) -> None:
        # Escala: el ancho completo (148) vale 4 veces la meta.
        filas = [control_flow.BulletRow("Aa", rate=0.06, target=0.10, exceeded=False)]

        grafico = control_flow.bullet_chart(filas, label="x")
        relleno = next(s for s in grafico.shapes if s.tag == "rect" and "bar" in s.cls)
        meta = next(s for s in grafico.shapes if s.tag == "line")

        assert relleno.attrs["width"] == 22.2
        assert meta.attrs["x1"] == 59.0
        assert "ok" in relleno.cls

    def test_pasada_la_meta_se_pinta_de_error_y_no_se_sale_de_la_pista(self) -> None:
        filas = [control_flow.BulletRow("Aa", rate=0.9, target=0.10, exceeded=True)]

        relleno = next(s for s in control_flow.bullet_chart(filas, label="x").shapes if "bar" in s.cls)

        assert relleno.attrs["width"] == 148
        assert "err" in relleno.cls

    def test_sin_auditar_dibuja_la_meta_pero_ningun_relleno(self) -> None:
        filas = [control_flow.BulletRow("Aa", rate=None, target=0.10, exceeded=False)]

        grafico = control_flow.bullet_chart(filas, label="x")

        assert not any("bar" in s.cls for s in grafico.shapes)
        assert any(s.tag == "line" for s in grafico.shapes)


# --- tablero: cuadrícula producto × provincia -------------------------------------


class TestCoverageGrid:
    def test_una_celda_por_producto_y_provincia_encendida_si_tiene_filas(self) -> None:
        celdas = [
            CoverageCell("Arroz", "La Habana", 4),
            CoverageCell("Arroz", "Santiago de Cuba", 0),
            CoverageCell("Pollo", "La Habana", 1),
            CoverageCell("Pollo", "Santiago de Cuba", 2),
        ]

        grafico, con_dato = control_flow.coverage_chart(celdas)
        assert grafico is not None
        cuadros = _rects(grafico)

        assert con_dato == 3
        assert len(cuadros) == 4
        assert sum("is-on" in c.cls for c in cuadros) == 3
        assert "3 de 4" in grafico.label

    def test_sin_celdas_no_hay_grafico(self) -> None:
        grafico, con_dato = control_flow.coverage_chart([])

        assert grafico is None
        assert con_dato == 0


# --- formatos --------------------------------------------------------------------


class TestFormatAge:
    @pytest.mark.parametrize(
        ("horas", "esperado"),
        [
            (0.01, "menos de 1 min"),
            (0.2, "12 min"),
            (13.4, "13 h"),
            (23.9, "23 h"),
            (28.0, "1 d 4 h"),
            (52.5, "2 d 4 h"),
            (96.0, "4 d"),
        ],
    )
    def test_edad_corta(self, horas: float, esperado: str) -> None:
        assert control_flow.format_age(horas) == esperado

    @pytest.mark.parametrize(
        ("valor", "esperado"),
        [(0.088, "8,8 %"), (0.0, "0,0 %"), (1.0, "100,0 %")],
    )
    def test_porcentaje_con_coma(self, valor: float, esperado: str) -> None:
        assert control_flow.format_percent(valor) == esperado


# --- el flujo completo ------------------------------------------------------------


class TestBuildViewVacio:
    def test_sin_ningun_dato_arma_los_siete_nodos_sin_romper(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))

        assert [n.key for n in view.nodes] == ["in", "pre", "c0", "c1", "c2", "pub", "mkt"]
        assert _node(view, "c1").health == "off"
        assert _node(view, "c0").health == "off"
        # Sin franja no hay regla que derive salud: «apagado», no «al día».
        assert _node(view, "pre").health == "off"
        assert _node(view, "mkt").health == "off"

    def test_sin_dato_se_dice_sin_dato_y_no_cero(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))

        assert _node(view, "pre").chart is not None  # el reloj vacío
        assert _node(view, "c1").big == "—"
        assert _node(view, "pub").chart is None
        assert _node(view, "pub").big == "0"

    def test_cada_nodo_tiene_icono_y_nombre(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))

        assert [n.name for n in view.nodes] == [
            "Ingesta",
            "Pre-cálculo",
            "Carril 0",
            "Carril 1",
            "Cola",
            "Tablero",
            "Marketing",
        ]
        assert all(n.icon for n in view.nodes)


class TestIngesta:
    def test_cifra_barras_y_origen_salen_de_las_marcas(self) -> None:
        marcas = [
            (NOW - timedelta(minutes=4), "whatsapp_channel"),
            (NOW - timedelta(hours=2), "whatsapp_channel"),
            (NOW - timedelta(hours=3), "inbox_text"),
        ]
        view = control_flow.build_view(
            FlowInputs(now=NOW, intake=marcas, last_ingest_at=NOW - timedelta(minutes=4))
        )
        nodo = _node(view, "in")

        assert nodo.big == "3"
        assert [c.text for c in nodo.chips] == ["2", "1"]
        assert nodo.foot_text == "hace 4 min"
        assert nodo.chart is not None
        assert sum(1 for s in _rects(nodo.chart) if s.attrs["height"] > 1) == 3

    def test_la_salud_es_la_peor_de_whatsapp_y_ultima_captura(self) -> None:
        franja = [_indicator("waha", "ok"), _indicator("intake", "warn")]

        view = control_flow.build_view(FlowInputs(now=NOW, strip=franja))

        assert _node(view, "in").health == "warn"

    def test_down_de_la_franja_es_atrasado(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW, strip=[_indicator("waha", "down")]))

        assert _node(view, "in").health == "err"


class TestPreCalculo:
    def test_reloj_desde_el_ultimo_disparo_y_salud_de_la_franja(self) -> None:
        corrida = PrefillRun(
            finished_at=NOW - timedelta(hours=2, minutes=10),
            exit_code=0,
            done=5,
            degraded=0,
            skipped_no_text=0,
            cost_usd=0.1,
            stopped_by_max_cost=False,
            aborted_failure="",
        )
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                prefill_run=corrida,
                strip=[_indicator("prefill", "ok", "ok, hace 2 h")],
                last_24h=Last24h(entered=20, curated=5, discarded=2, pending=13, oldest_pending_hours=23),
            )
        )
        nodo = _node(view, "pre")

        assert nodo.health == "ok"
        assert nodo.big == "13"
        assert nodo.unit == "sin procesar"
        assert nodo.chart is not None
        assert next(s for s in nodo.chart.shapes if s.tag == "text").text == "2 h"
        assert "más viejo 23 h" in [c.text for c in nodo.chips]

    def test_rojo_de_la_franja_es_atrasado(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW, strip=[_indicator("prefill", "down", "falló")]))

        assert _node(view, "pre").health == "err"
        assert _node(view, "pre").foot_text == "falló"


class TestCarril0:
    def test_apagado_en_ambos_tipos_es_off(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))

        assert _node(view, "c0").health == "off"

    def test_dentro_de_la_meta_es_al_dia_y_cuenta_los_descartes_de_24_h(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                lane0_enabled_types=frozenset({TEXT, IMAGE}),
                lane0_bounds={TEXT: Bound(TEXT, k=0, n=30), IMAGE: Bound(IMAGE, k=0, n=15)},
                cohort_lane0=98,
            )
        )
        nodo = _node(view, "c0")

        assert nodo.health == "ok"
        assert nodo.big == "98"
        assert nodo.foot_text == "auditados 30 · 15"

    def test_la_cota_que_pasa_la_meta_es_atrasado(self) -> None:
        # 5 rescatados en 10 textos: la cota superior pasa por mucho el 10 %.
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                lane0_enabled_types=frozenset({TEXT}),
                lane0_bounds={TEXT: Bound(TEXT, k=5, n=10), IMAGE: Bound(IMAGE, k=0, n=0)},
            )
        )

        assert _node(view, "c0").health == "err"

    def test_encendido_pero_sin_auditar_no_inventa_barras(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                lane0_enabled_types=frozenset({TEXT}),
                lane0_bounds={TEXT: Bound(TEXT, k=0, n=0), IMAGE: Bound(IMAGE, k=0, n=0)},
            )
        )
        nodo = _node(view, "c0")

        assert nodo.health == "ok"
        assert nodo.chart is not None
        assert not any("bar" in s.cls for s in nodo.chart.shapes)
        assert nodo.foot_text == "auditados 0 · 0"


class TestCarril1:
    def test_apagado_es_off_con_su_n_contra_el_arranque(self) -> None:
        view = control_flow.build_view(
            FlowInputs(now=NOW, lane1_enabled=False, lane1_audited=30, lane1_errors=3)
        )
        nodo = _node(view, "c1")

        assert nodo.health == "off"
        assert nodo.big == "10,0"
        assert nodo.unit == "% error"
        assert nodo.foot_text == "apagado · n 30/60"
        assert nodo.chart is not None
        anillo = next(s for s in nodo.chart.shapes if "ring" in s.cls)
        assert anillo.attrs["stroke-dasharray"] == "69.1 138.2"

    def test_encendido_y_sin_freno_es_al_dia(self) -> None:
        view = control_flow.build_view(
            FlowInputs(now=NOW, lane1_enabled=True, lane1_audited=80, lane1_errors=2)
        )
        nodo = _node(view, "c1")

        assert nodo.health == "ok"
        assert nodo.foot_text == "encendido · n 80/60"

    def test_el_freno_puesto_es_atrasado(self) -> None:
        view = control_flow.build_view(
            FlowInputs(now=NOW, lane1_enabled=True, lane1_audited=80, lane1_brake_on=True)
        )

        assert _node(view, "c1").health == "err"

    def test_la_auditoria_atrasada_de_la_franja_pide_mirar(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                lane1_enabled=True,
                strip=[_indicator("lane1_audit", "warn")],
            )
        )

        assert _node(view, "c1").health == "warn"

    def test_sin_auditados_no_hay_tasa_de_error(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW, lane1_enabled=True))

        assert _node(view, "c1").big == "—"
        assert _node(view, "c1").unit == "sin auditar"


class TestCola:
    def test_histograma_y_el_mas_viejo_salen_de_las_edades(self) -> None:
        creadas = [
            NOW - timedelta(hours=1),
            NOW - timedelta(hours=2),
            NOW - timedelta(hours=10),
            NOW - timedelta(hours=30),
            NOW - timedelta(hours=52, minutes=30),
        ]
        view = control_flow.build_view(FlowInputs(now=NOW, queue_created=creadas))
        nodo = _node(view, "c2")

        assert nodo.big == "5"
        assert nodo.chart is not None
        assert [b.attrs["height"] for b in _rects(nodo.chart)] == [28, 14, 14, 14, 1]
        assert nodo.foot_text == "más viejo 2 d 4 h"

    def test_cola_vacia_es_cero_y_no_tiene_el_mas_viejo(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))
        nodo = _node(view, "c2")

        assert nodo.big == "0"
        assert nodo.foot_text == "sin pendientes"
        assert nodo.health == "off"  # sin franja, sin regla

    def test_la_salud_sale_de_la_tarjeta_de_24_h_de_la_franja(self) -> None:
        view = control_flow.build_view(
            FlowInputs(now=NOW, strip=[_indicator("last_24h", "warn")], queue_created=[NOW])
        )

        assert _node(view, "c2").health == "warn"

    def test_con_tope_de_edad_un_pendiente_mas_viejo_que_el_tope_es_atrasado(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                strip=[_indicator("last_24h", "ok")],
                queue_created=[NOW - timedelta(days=3)],
                queue_max_age_days=2,
            )
        )

        assert _node(view, "c2").health == "err"

    def test_sin_tope_de_edad_la_antiguedad_sola_no_marca_atrasado(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                strip=[_indicator("last_24h", "ok")],
                queue_created=[NOW - timedelta(days=30)],
                queue_max_age_days=0,
            )
        )

        assert _node(view, "c2").health == "ok"


class TestTablero:
    def test_cifra_celdas_y_salud_de_la_tasa(self) -> None:
        celdas = [CoverageCell("Arroz", "La Habana", 3), CoverageCell("Arroz", "Santiago de Cuba", 0)]
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                price_rows_total=226,
                last_price_row_at=NOW - timedelta(hours=3),
                coverage=celdas,
                strip=[_indicator("rate", "warn")],
            )
        )
        nodo = _node(view, "pub")

        assert nodo.big == "226"
        assert nodo.health == "warn"
        assert nodo.foot_text == "hace 3 h"
        assert nodo.chart is not None
        assert "1 de 2" in nodo.chart.label


class TestMarketing:
    def test_pasos_borrador_aprobada_publicada(self) -> None:
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                marketing={"draft": 3, "approved": 1, "published": 0},
                last_batch_at=NOW - timedelta(hours=6),
            )
        )
        nodo = _node(view, "mkt")

        assert [(s.count, s.on) for s in nodo.steps] == [(3, True), (1, True), (0, False)]
        assert nodo.health == "ok"
        assert nodo.foot_text == "lote hace 6 h"

    def test_sin_publicaciones_es_off(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW, marketing={}))

        assert _node(view, "mkt").health == "off"
        assert [s.count for s in _node(view, "mkt").steps] == [0, 0, 0]


class TestCables:
    def test_los_numeros_de_los_cables_salen_de_la_misma_cohorte(self) -> None:
        marcas = [(NOW - timedelta(hours=h), "whatsapp_channel") for h in range(1, 11)]
        view = control_flow.build_view(
            FlowInputs(
                now=NOW,
                intake=marcas,
                cohort_lane0=4,
                cohort_lane1=1,
                price_rows_human_24h=22,
                lane0_enabled_types=frozenset({TEXT}),
                lane1_enabled=True,
            )
        )
        pastillas = {p.key: p for p in view.pills}

        assert pastillas["in-pre"].label == "10"
        assert pastillas["pre-c0"].label == "4"
        assert pastillas["pre-c1"].label == "1"
        # Lo que no resolvieron solos los carriles 0 y 1 llega a la cola.
        assert pastillas["pre-c2"].label == "5"
        assert pastillas["c2-pub"].label == "22"
        assert len(view.wires) == 8

    def test_la_cola_nunca_baja_de_cero(self) -> None:
        view = control_flow.build_view(
            FlowInputs(now=NOW, intake=[(NOW, "inbox_text")], cohort_lane0=3, cohort_lane1=2)
        )

        assert {p.key: p for p in view.pills}["pre-c2"].label == "0"

    def test_un_cable_sin_caudal_o_de_un_carril_apagado_va_punteado(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))
        estilos = {w.key: w.flow for w in view.wires}

        assert estilos["pre-c1"] is False
        assert estilos["in-pre"] is False

    def test_la_geometria_es_la_del_prototipo(self) -> None:
        view = control_flow.build_view(FlowInputs(now=NOW))
        cables = {w.key: w.d for w in view.wires}

        assert cables["in-pre"] == "M170 280 C190 280 190 280 210 280"
        assert cables["pre-c0"] == "M370 280 C400 280 400 104 430 104"
        assert cables["c2-pub"] == "M590 456 C615 456 615 368 640 368"
        assert cables["pub-mkt"] == "M800 368 H830"


# --- endpoint ---------------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestEndpoint:
    def test_responde_con_los_siete_nodos_y_el_menu(self, client: TestClient) -> None:
        respuesta = client.get("/control/flujo")

        assert respuesta.status_code == 200
        texto = respuesta.text
        for clave in ("in", "pre", "c0", "c1", "c2", "pub", "mkt"):
            assert f'data-n="{clave}"' in texto
        assert 'href="/control/flujo"' in texto
        assert 'class="m3-ctl-link is-active" href="/control/flujo"' in texto

    def test_no_trae_javascript_ni_colores_sueltos(self, client: TestClient) -> None:
        texto = client.get("/control/flujo").text
        lienzo = texto.split('class="m3-flow-canvas"')[1].split("m3-flow-legend")[0]

        assert "<script" not in lienzo
        assert "#" not in lienzo.replace("&#", "")  # ni hex, ni fallbacks

    def test_con_datos_reales_pinta_cifras_de_la_base(self, client: TestClient, session: Session) -> None:
        ahora = datetime.now(UTC)
        ids = []
        for horas in (1, 2, 30):
            ingestion = RawIngestion(
                source_type="whatsapp_channel",
                file_path=f"x/{uuid4()}.txt",
                content_hash=uuid4().hex + uuid4().hex,
                content_type="text",
                original_text="Arroz 100 CUP",
                processing_status=ProcessingStatus.PENDING,
                captured_at=ahora - timedelta(hours=horas),
                created_at=ahora - timedelta(hours=horas),
            )
            session.add(ingestion)
            ids.append(ingestion)
        session.flush()
        session.add(
            CurationEvent(
                ingestion_id=ids[0].id,
                action=CurationAction.DISCARDED,
                mode=CurationMode.AUTO,
            )
        )
        session.commit()

        texto = client.get("/control/flujo").text
        cola = texto.split('data-n="c2"')[1].split("</article>")[0]
        ingesta = texto.split('data-n="in"')[1].split("</article>")[0]

        assert '<div class="m3-flow-big">3</div>' in cola
        # Dos de las tres entraron en las últimas 24 h.
        assert '<div class="m3-flow-big">2</div>' in ingesta
