"""`saimc.extraction.llm_backtest` — el backtest del asistente LLM.

Los números esperados del reporte están calculados A MANO sobre un fixture
chico (ver `_armar_corpus`), antes y con independencia del código de métricas.
El cliente es un doble: ningún test toca la red ni necesita clave.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.extraction import llm_backtest, llm_propose_ad
from saimc.models import (
    ContentType,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)


@pytest.fixture
def catalogo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    scu = Province(code="SCU", name="Santiago de Cuba")
    session.add_all([arroz, pollo, lh, scu])
    session.commit()
    return SimpleNamespace(arroz=arroz, pollo=pollo, lh=lh, scu=scu)


_contador = 0


def _ingesta(
    session: Session,
    *,
    tipo: str = ContentType.TEXT,
    estado: str = ProcessingStatus.CURATED,
    texto: str | None = "anuncio",
    filas: list[dict] | None = None,
) -> RawIngestion:
    """Crea una ingesta y, si es curada, sus filas confirmadas."""
    global _contador
    _contador += 1
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{_contador}.png" if tipo == ContentType.IMAGE else f"raw/{_contador}.txt",
        content_hash=f"h{_contador}",
        content_type=tipo,
        original_text=texto,
        processing_status=estado,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(ingesta)
    session.flush()
    for fila in filas or []:
        session.add(PriceHistory(source_id=ingesta.id, **fila))
    session.commit()
    return ingesta


def _confirmada(producto, precio, moneda, cantidad, unidad, provincia) -> dict:
    return {
        "product_id": producto.id,
        "price": Decimal(precio),
        "currency": moneda,
        "quantity": Decimal(cantidad),
        "unit": unidad,
        "province_id": provincia.id,
    }


def _fila(producto, precio, moneda, cantidad, unidad, provincia) -> dict:
    """Una fila propuesta, en el formato serializado del archivo de resultados."""
    return {
        "product_id": str(producto.id) if producto else None,
        "price": precio,
        "currency": moneda,
        "quantity": cantidad,
        "unit": unidad,
        "province_id": str(provincia.id) if provincia else None,
    }


def _registro(
    ingesta,
    esperado,
    *,
    llm_rows,
    reglas,
    discard=False,
    degraded=False,
    tokens=(1000, 100),
    canal=None,
) -> dict:
    return {
        "ingestion_id": str(ingesta.id),
        "content_type": ingesta.content_type,
        "expected": esperado,
        "model": "claude-sonnet-5",
        "proposal": {"discard": discard, "discard_reason": None, "rows": llm_rows},
        "rules_proposal": reglas,
        "channel_province_id": str(canal.id) if canal else None,
        "input_tokens": 0 if degraded else tokens[0],
        "output_tokens": 0 if degraded else tokens[1],
        "degraded": degraded,
        "failure": "timeout" if degraded else "",
        "timestamp": "2026-09-25T00:00:00+00:00",
    }


def _armar_corpus(session: Session, c: SimpleNamespace) -> list[dict]:
    """Corpus a mano. Verdad (lo que confirmó el operador):

    A1 texto  Arroz 100 CUP 1 lb LH     LLM exacto          reglas: precio 90
    A2 texto  Pollo 200 CUP 1 lb LH     LLM: precio 210     reglas exactas
    A3 texto  Arroz 50 CUP 1 lb SCU     ambos exactos
    A5 texto  (LLM degradado: se excluye de todo salvo el conteo de degradados)
    A4 imagen Pollo 300 USD 2 lb LH     LLM sin filas y discard=True (canal LH); reglas exactas
    D1 texto  descartado, LLM discard=True
    D2 texto  descartado, LLM discard=False sin filas
    D3 imagen descartado, LLM discard=True
    """
    a1 = _ingesta(session, filas=[_confirmada(c.arroz, "100", "CUP", "1", "lb", c.lh)])
    a2 = _ingesta(session, filas=[_confirmada(c.pollo, "200", "CUP", "1", "lb", c.lh)])
    a3 = _ingesta(session, filas=[_confirmada(c.arroz, "50", "CUP", "1", "lb", c.scu)])
    a5 = _ingesta(session, filas=[_confirmada(c.arroz, "70", "CUP", "1", "lb", c.lh)])
    a4 = _ingesta(
        session,
        tipo=ContentType.IMAGE,
        filas=[_confirmada(c.pollo, "300", "USD", "2", "lb", c.lh)],
    )
    d1 = _ingesta(session, estado=ProcessingStatus.DISCARDED)
    d2 = _ingesta(session, estado=ProcessingStatus.DISCARDED)
    d3 = _ingesta(session, tipo=ContentType.IMAGE, estado=ProcessingStatus.DISCARDED)

    K, D = "kept", "discarded"
    return [
        _registro(
            a1, K,
            llm_rows=[_fila(c.arroz, 100, "CUP", 1, "lb", c.lh)],
            reglas=[_fila(c.arroz, 90, "CUP", 1, "lb", c.lh)],
        ),
        _registro(
            a2, K,
            llm_rows=[_fila(c.pollo, 210, "CUP", 1, "lb", c.lh)],
            reglas=[_fila(c.pollo, 200, "CUP", 1, "lb", c.lh)],
        ),
        _registro(
            a3, K,
            llm_rows=[_fila(c.arroz, 50, "CUP", 1, "lb", c.scu)],
            reglas=[_fila(c.arroz, 50, "CUP", 1, "lb", c.scu)],
        ),
        _registro(a5, K, llm_rows=[], reglas=[], degraded=True),
        _registro(
            a4, K,
            llm_rows=[],
            discard=True,
            reglas=[_fila(c.pollo, 300, "USD", 2, "lb", c.lh)],
            canal=c.lh,
        ),
        _registro(d1, D, llm_rows=[], reglas=[], discard=True),
        _registro(d2, D, llm_rows=[], reglas=[], discard=False),
        _registro(d3, D, llm_rows=[], reglas=[], discard=True),
    ]


def test_metricas_del_corpus_armado_a_mano(session: Session, catalogo) -> None:
    registros = _armar_corpus(session, catalogo)

    m = llm_backtest.compute_metrics(session, registros)

    texto, imagen, total = m["texto"], m["imagen"], m["total"]

    # --- texto: gold A1, A2, A3 (A5 degradado fuera) = 3 filas ---------------
    assert (texto.gold_ads, texto.gold_rows, texto.degraded) == (3, 3, 1)
    assert texto.llm.rows == 3 and texto.rules.rows == 3
    assert (texto.llm.product, texto.llm.price, texto.llm.province) == (3, 2, 3)
    assert (texto.llm.quantity_unit, texto.llm.complete) == (3, 2)
    assert (texto.rules.product, texto.rules.price, texto.rules.province) == (3, 2, 3)
    assert (texto.rules.quantity_unit, texto.rules.complete) == (3, 2)
    # A3 ambos; A1 solo LLM; A2 solo reglas.
    assert (texto.both, texto.only_llm, texto.only_rules, texto.neither) == (1, 1, 1, 0)
    # Veredicto: gold A1 A2 A3 predichos "conservar" (TN 3); D1 TP, D2 FN.
    assert (texto.tp, texto.fp, texto.fn, texto.tn) == (1, 0, 1, 3)

    # --- imagen: gold A4 = 1 fila; el LLM no propuso filas ------------------
    assert (imagen.gold_ads, imagen.gold_rows, imagen.degraded) == (1, 1, 0)
    # Sin candidatos la fila recibe solo la provincia del canal (LH): acierta.
    assert (imagen.llm.product, imagen.llm.price, imagen.llm.province) == (0, 0, 1)
    assert (imagen.llm.quantity_unit, imagen.llm.complete) == (0, 0)
    assert imagen.rules.complete == 1 and imagen.rules.rows == 1
    assert (imagen.both, imagen.only_llm, imagen.only_rules, imagen.neither) == (0, 0, 1, 0)
    assert imagen.zero_row_ads == 1
    # A4 (conservar) predicho descartar = FP; D3 = TP.
    assert (imagen.tp, imagen.fp, imagen.fn, imagen.tn) == (1, 1, 0, 0)

    # --- total --------------------------------------------------------------
    assert (total.gold_ads, total.gold_rows, total.degraded) == (4, 4, 1)
    assert (total.llm.product, total.llm.price, total.llm.province) == (3, 2, 4)
    assert (total.llm.quantity_unit, total.llm.complete) == (3, 2)
    assert (total.rules.product, total.rules.price, total.rules.complete) == (4, 3, 3)
    assert (total.both, total.only_llm, total.only_rules, total.neither) == (1, 1, 2, 0)
    assert (total.tp, total.fp, total.fn, total.tn) == (2, 1, 1, 3)
    assert total.discard_precision == (2, 3)
    # El operador descartó 3 (D1, D2, D3) y el modelo acertó 2.
    assert total.discard_recall == (2, 3)

    # --- consumo: 1000 in + 100 out a (2, 10) USD/Mtok = 0.003 por llamada ---
    # Texto: A1 A2 A3 D1 D2 (A5 degradado sin tokens) = 5; imagen: A4 D3 = 2.
    assert texto.input_tokens == 5000 and texto.output_tokens == 500
    assert imagen.input_tokens == 2000
    assert texto.cost_usd == pytest.approx(0.015)
    assert imagen.cost_usd == pytest.approx(0.006)
    assert total.cost_usd == pytest.approx(0.021)


def test_el_reporte_muestra_n_junto_a_cada_porcentaje_y_no_da_veredicto(
    session: Session, catalogo
) -> None:
    m = llm_backtest.compute_metrics(session, _armar_corpus(session, catalogo))

    texto = llm_backtest.render_report(m)

    assert "2/3 (66.7%)" in texto  # precio LLM en texto
    assert "1/2 (50.0%)" in texto  # precisión de descarte en imagen
    # El reporte no fija umbrales ni dice si conviene confiar (spec §12.6).
    for prohibida in ("aprobado", "reprobado", "umbral superado", "recomendamos"):
        assert prohibida not in texto.lower()


def test_metricas_sin_datos_no_dividen_por_cero(session: Session, catalogo) -> None:
    m = llm_backtest.compute_metrics(session, [])

    assert m["total"].gold_rows == 0
    assert m["total"].discard_precision == (0, 0)
    assert "n/d" in llm_backtest.render_report(m)


def test_un_registro_degradado_se_reemplaza_por_el_reintento(
    session: Session, catalogo
) -> None:
    corpus = _armar_corpus(session, catalogo)
    a5 = corpus[3]
    reintento = dict(a5)
    reintento.update(
        degraded=False,
        failure="",
        proposal={
            "discard": False,
            "discard_reason": None,
            "rows": [_fila(catalogo.arroz, 70, "CUP", 1, "lb", catalogo.lh)],
        },
        rules_proposal=[_fila(catalogo.arroz, 70, "CUP", 1, "lb", catalogo.lh)],
    )

    m = llm_backtest.compute_metrics(session, [*corpus, reintento])

    assert m["texto"].degraded == 0
    assert m["texto"].gold_ads == 4


# --- selección del corpus ---------------------------------------------------


def test_el_oro_excluye_los_asistidos_y_lo_no_curado(
    session: Session, catalogo, tmp_path: Path
) -> None:
    fila = [_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)]
    limpio = _ingesta(session, filas=fila)
    asistido = _ingesta(session, filas=fila)
    _ingesta(session, estado=ProcessingStatus.DISCARDED)
    _ingesta(session, estado=ProcessingStatus.PENDING)
    log = tmp_path / "assist.jsonl"
    log.write_text(json.dumps({"ingestion_id": str(asistido.id)}) + "\n")

    oro = llm_backtest.select_gold(session, llm_backtest.assisted_ids(log))

    assert [i.id for i in oro] == [limpio.id]


def test_el_muestreo_de_descartes_es_estratificado_y_determinista(
    session: Session, catalogo
) -> None:
    texto = {_ingesta(session, estado=ProcessingStatus.DISCARDED).id for _ in range(6)}
    imagen = {
        _ingesta(session, tipo=ContentType.IMAGE, estado=ProcessingStatus.DISCARDED).id
        for _ in range(4)
    }
    _ingesta(session, filas=[_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)])

    a = llm_backtest.select_discard_sample(
        session, {"text": 3, "image": 10}, seed=7, excluded=set()
    )
    b = llm_backtest.select_discard_sample(
        session, {"text": 3, "image": 10}, seed=7, excluded=set()
    )

    assert [i.id for i in a] == [i.id for i in b]  # determinista
    por_tipo = {t: [i.id for i in a if i.content_type == t] for t in ("text", "image")}
    assert len(por_tipo["text"]) == 3 and set(por_tipo["text"]) <= texto
    # Pidió 10 y hay 4: toma todos, sin repetir.
    assert set(por_tipo["image"]) == imagen and len(por_tipo["image"]) == 4
    assert all(i.processing_status == ProcessingStatus.DISCARDED for i in a)


def test_por_defecto_el_muestreo_iguala_el_oro_por_tipo(
    session: Session, catalogo
) -> None:
    fila = [_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)]
    for _ in range(2):
        _ingesta(session, filas=fila)
    _ingesta(session, tipo=ContentType.IMAGE, filas=fila)
    for _ in range(5):
        _ingesta(session, estado=ProcessingStatus.DISCARDED)
        _ingesta(session, tipo=ContentType.IMAGE, estado=ProcessingStatus.DISCARDED)

    items = llm_backtest.build_workset(session, set(), sample_n=None, seed=1)

    def contar(tipo, esperado):
        return sum(1 for i in items if i.content_type == tipo and i.expected == esperado)

    assert (contar("text", "kept"), contar("text", "discarded")) == (2, 2)
    assert (contar("image", "kept"), contar("image", "discarded")) == (1, 1)


# --- dry-run y ejecución ----------------------------------------------------


def test_estimacion_de_tokens_y_costo(session: Session, catalogo) -> None:
    # 350 caracteres / 3.5 = 100 tokens de anuncio, sin redondeo de por medio.
    texto = _ingesta(
        session,
        texto="x" * 350,
        filas=[
            _confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh),
            _confirmada(catalogo.pollo, "2", "CUP", "1", "lb", catalogo.lh),
        ],
    )
    # Imagen sin transcripción conocida: 1000 caracteres supuestos = 286 tokens.
    imagen = _ingesta(
        session,
        tipo=ContentType.IMAGE,
        texto=None,
        estado=ProcessingStatus.DISCARDED,
    )
    items = [
        llm_backtest.WorkItem(str(texto.id), "text", "kept"),
        llm_backtest.WorkItem(str(imagen.id), "image", "discarded"),
    ]

    est = llm_backtest.estimate(session, items, ocr_cache={})

    assert est.ads == {"text": 1, "image": 1}
    assert est.prompt_tokens > 0
    assert est.input_tokens == 2 * est.prompt_tokens + 100 + 286
    # Salida: 30 + 45 por fila esperada (2) = 120, más 60 del descartado.
    assert est.output_tokens == 120 + 60
    e_in, e_out = est.input_tokens, est.output_tokens
    assert est.cost_usd["claude-sonnet-5"] == pytest.approx(
        e_in * 2.0 / 1e6 + e_out * 10.0 / 1e6
    )
    assert est.cost_usd["claude-haiku-4-5"] == pytest.approx(
        e_in * 1.0 / 1e6 + e_out * 5.0 / 1e6
    )
    assert est.cost_usd["claude-opus-5"] == pytest.approx(
        e_in * 5.0 / 1e6 + e_out * 25.0 / 1e6
    )


class ClienteFalso:
    def __init__(self, cuerpo=None, error=None):
        self.llamadas: list[dict] = []
        self._cuerpo = cuerpo
        self._error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.llamadas.append(kw)
        if self._error:
            raise self._error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._cuerpo))],
            usage=SimpleNamespace(input_tokens=500, output_tokens=50),
        )


def _ajustes(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_dry_run_por_defecto_no_llama_ni_pide_clave(
    session: Session, catalogo, tmp_path: Path, monkeypatch, capsys
) -> None:
    _ingesta(session, filas=[_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)])
    _ingesta(session, estado=ProcessingStatus.DISCARDED)

    def _prohibido(*a, **k):
        raise AssertionError("el dry-run no puede llamar al modelo")

    monkeypatch.setattr(llm_propose_ad, "propose_ad", _prohibido)
    args = llm_backtest.parse_args(["--results", str(tmp_path / "r.jsonl")])

    codigo = llm_backtest.execute(args, session, _ajustes())

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "dry-run" in salida.lower()
    for modelo in ("claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5"):
        assert modelo in salida
    assert "3.5" in salida  # la heurística declarada
    assert not (tmp_path / "r.jsonl").exists()


def test_execute_sin_clave_se_niega(
    session: Session, catalogo, tmp_path: Path, capsys
) -> None:
    args = llm_backtest.parse_args(["--execute", "--results", str(tmp_path / "r.jsonl")])

    codigo = llm_backtest.execute(args, session, _ajustes(anthropic_api_key=None))

    assert codigo != 0
    assert "clave" in capsys.readouterr().out.lower()
    assert not (tmp_path / "r.jsonl").exists()


def test_la_clave_no_se_imprime(
    session: Session, catalogo, tmp_path: Path, capsys
) -> None:
    _ingesta(session, filas=[_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)])
    args = llm_backtest.parse_args(
        ["--execute", "--results", str(tmp_path / "r.jsonl"),
         "--calls-log", str(tmp_path / "c.jsonl")]
    )
    cliente = ClienteFalso({"discard": True, "discard_reason": "no_data", "rows": []})

    llm_backtest.execute(
        args, session, _ajustes(anthropic_api_key="sk-secreta-123"), client=cliente
    )

    assert "sk-secreta-123" not in capsys.readouterr().out


def test_run_escribe_un_registro_por_anuncio_con_ocr_redactado(
    session: Session, catalogo, tmp_path: Path
) -> None:
    txt = _ingesta(
        session,
        texto="Arroz 100 cup La Habana",
        filas=[_confirmada(catalogo.arroz, "100", "CUP", "1", "lb", catalogo.lh)],
    )
    img = _ingesta(session, tipo=ContentType.IMAGE, estado=ProcessingStatus.DISCARDED, texto=None)
    items = [
        llm_backtest.WorkItem(str(txt.id), "text", "kept"),
        llm_backtest.WorkItem(str(img.id), "image", "discarded"),
    ]
    cuerpo = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            {"product": "Arroz", "price": 100, "currency": "CUP", "quantity": 1,
             "unit": "lb", "province": "La Habana"}
        ],
    }
    cliente = ClienteFalso(cuerpo)
    resultados = tmp_path / "r.jsonl"

    llm_backtest.run_backtest(
        session, items,
        results_path=resultados, calls_log_path=tmp_path / "c.jsonl",
        api_key="k", client=cliente, model="claude-sonnet-5", timeout=5.0,
        transcribe=lambda p: "oferta llamar 52345678",
        out=lambda *_: None,
    )

    lineas = [json.loads(x) for x in resultados.read_text().splitlines()]
    assert [x["ingestion_id"] for x in lineas] == [str(txt.id), str(img.id)]
    t, i = lineas
    assert t["expected"] == "kept" and i["expected"] == "discarded"
    assert t["proposal"]["rows"][0]["product_id"] == str(catalogo.arroz.id)
    assert t["proposal"]["rows"][0]["price"] == "100"
    assert t["rules_proposal"], "las reglas también corren sobre el mismo texto"
    assert (t["input_tokens"], t["output_tokens"]) == (500, 50)
    assert t["degraded"] is False
    assert "ocr_text" not in t
    # La imagen guarda el OCR que vio el modelo, ya sin teléfono.
    assert "52345678" not in i["ocr_text"]
    assert "52345678" not in json.dumps(cliente.llamadas[1])


def test_reanudar_salta_lo_hecho_reintenta_lo_degradado_y_no_repite_ocr(
    session: Session, catalogo, tmp_path: Path
) -> None:
    hecho = _ingesta(session, estado=ProcessingStatus.DISCARDED)
    roto = _ingesta(session, tipo=ContentType.IMAGE, estado=ProcessingStatus.DISCARDED, texto=None)
    nuevo = _ingesta(session, estado=ProcessingStatus.DISCARDED)
    resultados = tmp_path / "r.jsonl"
    previos = [
        {"ingestion_id": str(hecho.id), "content_type": "text", "expected": "discarded",
         "degraded": False, "proposal": {"rows": []}, "rules_proposal": []},
        {"ingestion_id": str(roto.id), "content_type": "image", "expected": "discarded",
         "degraded": True, "failure": "timeout", "ocr_text": "texto ya leido",
         "proposal": {"rows": []}, "rules_proposal": []},
    ]
    resultados.write_text("".join(json.dumps(p) + "\n" for p in previos))
    items = [
        llm_backtest.WorkItem(str(hecho.id), "text", "discarded"),
        llm_backtest.WorkItem(str(roto.id), "image", "discarded"),
        llm_backtest.WorkItem(str(nuevo.id), "text", "discarded"),
    ]
    cliente = ClienteFalso({"discard": True, "discard_reason": "no_data", "rows": []})

    def _sin_ocr(_):
        raise AssertionError("no se re-hace el OCR de lo ya transcripto")

    llm_backtest.run_backtest(
        session, items,
        results_path=resultados, calls_log_path=tmp_path / "c.jsonl",
        api_key="k", client=cliente, model="claude-sonnet-5", timeout=5.0,
        transcribe=_sin_ocr, out=lambda *_: None,
    )

    # Llamó por el roto y el nuevo, no por el hecho.
    assert len(cliente.llamadas) == 2
    assert "texto ya leido" in json.dumps(cliente.llamadas[0])
    lineas = [json.loads(x) for x in resultados.read_text().splitlines()]
    assert len(lineas) == 4  # 2 previas + roto reintentado + nuevo
    assert llm_backtest.latest_records(lineas)[str(roto.id)]["degraded"] is False


def test_una_falla_de_credito_o_clave_aborta_la_corrida(
    session: Session, catalogo, tmp_path: Path
) -> None:
    ids = [_ingesta(session, estado=ProcessingStatus.DISCARDED) for _ in range(3)]
    items = [llm_backtest.WorkItem(str(i.id), "text", "discarded") for i in ids]
    cliente = ClienteFalso(error=RuntimeError("Your credit balance is too low"))

    llm_backtest.run_backtest(
        session, items,
        results_path=tmp_path / "r.jsonl", calls_log_path=tmp_path / "c.jsonl",
        api_key="k", client=cliente, model="claude-sonnet-5", timeout=5.0,
        transcribe=lambda p: "", out=lambda *_: None,
    )

    assert len(cliente.llamadas) == 1  # no sigue gastando llamadas condenadas


def test_limit_acota_los_anuncios_y_mezcla_tipos(session: Session, catalogo) -> None:
    fila = [_confirmada(catalogo.arroz, "1", "CUP", "1", "lb", catalogo.lh)]
    for _ in range(5):
        _ingesta(session, filas=fila)
        _ingesta(session, estado=ProcessingStatus.DISCARDED)

    items = llm_backtest.build_workset(session, set(), sample_n=None, seed=3, limit=4)

    assert len(items) == 4
    assert items == llm_backtest.build_workset(session, set(), sample_n=None, seed=3, limit=4)


# --- prompt_version y expected_rows en cada línea nueva ----------------------


def test_cada_linea_nueva_lleva_prompt_version_y_las_filas_del_operador(
    session: Session, catalogo, tmp_path: Path
) -> None:
    txt = _ingesta(
        session,
        texto="Arroz 100 cup La Habana",
        filas=[_confirmada(catalogo.arroz, "100", "CUP", "1", "lb", catalogo.lh)],
    )
    desc = _ingesta(session, estado=ProcessingStatus.DISCARDED)
    items = [
        llm_backtest.WorkItem(str(txt.id), "text", "kept"),
        llm_backtest.WorkItem(str(desc.id), "text", "discarded"),
    ]
    resultados = tmp_path / "r.jsonl"

    llm_backtest.run_backtest(
        session, items,
        results_path=resultados, calls_log_path=tmp_path / "c.jsonl",
        api_key="k",
        client=ClienteFalso({"discard": True, "discard_reason": "no_data", "rows": []}),
        model="claude-sonnet-5", timeout=5.0, out=lambda *_: None,
    )

    kept, descartado = [json.loads(x) for x in resultados.read_text().splitlines()]
    assert kept["prompt_version"] == "2026-09-25.2"
    assert descartado["prompt_version"] == "2026-09-25.2"
    (fila,) = kept["expected_rows"]
    assert fila["product"] == "Arroz" and fila["currency"] == "CUP" and fila["unit"] == "lb"
    assert Decimal(fila["price"]) == 100 and Decimal(fila["quantity"]) == 1
    assert descartado["expected_rows"] == []


# --- filas de más -----------------------------------------------------------


def _armar_corpus_extras(session: Session, c: SimpleNamespace) -> list[dict]:
    """Corpus a mano para «filas de más». Verdad y propuesta del modelo:

    E1 texto  operador: Arroz 100 CUP 1 lb.
              modelo:   Arroz 100 CUP 1 lb + Arroz 0.14 USD 1 lb  -> 1 extra, moneda duplicada
    E2 texto  operador: Pollo 200 CUP 1 lb.
              modelo:   Pollo 200 CUP 1 lb + Arroz 50 CUP 1 lb    -> 1 extra, otra
    E3 imagen operador: Arroz 50 CUP 1 lb.
              modelo:   exacto                                    -> 0 extras
    E4 texto  degradado: fuera de todo.
    """
    e1 = _ingesta(session, filas=[_confirmada(c.arroz, "100", "CUP", "1", "lb", c.lh)])
    e2 = _ingesta(session, filas=[_confirmada(c.pollo, "200", "CUP", "1", "lb", c.lh)])
    e3 = _ingesta(
        session, tipo=ContentType.IMAGE,
        filas=[_confirmada(c.arroz, "50", "CUP", "1", "lb", c.lh)],
    )
    e4 = _ingesta(session, filas=[_confirmada(c.arroz, "9", "CUP", "1", "lb", c.lh)])
    K = "kept"
    return [
        _registro(
            e1, K,
            llm_rows=[
                _fila(c.arroz, 100, "CUP", 1, "lb", c.lh),
                _fila(c.arroz, "0.14", "USD", 1, "lb", c.lh),
            ],
            reglas=[],
        ),
        _registro(
            e2, K,
            llm_rows=[
                _fila(c.pollo, 200, "CUP", 1, "lb", c.lh),
                _fila(c.arroz, 50, "CUP", 1, "lb", c.lh),
            ],
            reglas=[],
        ),
        _registro(e3, K, llm_rows=[_fila(c.arroz, 50, "CUP", 1, "lb", c.lh)], reglas=[]),
        _registro(
            e4, K, llm_rows=[_fila(c.arroz, 9, "USD", 1, "lb", c.lh)], reglas=[], degraded=True
        ),
    ]


def test_filas_de_mas_por_tipo_y_total_con_su_reparto(session: Session, catalogo) -> None:
    m = llm_backtest.compute_metrics(session, _armar_corpus_extras(session, catalogo))

    texto, imagen, total = m["texto"], m["imagen"], m["total"]
    # Texto: 2 anuncios del oro, 2 filas de más (1 moneda duplicada, 1 otra),
    # y los dos anuncios tienen al menos una.
    assert (texto.gold_ads, texto.extra_rows) == (2, 2)
    assert (texto.extra_dup_currency, texto.extra_other, texto.ads_with_extra) == (1, 1, 2)
    # Imagen: 1 anuncio, exacto.
    assert (imagen.gold_ads, imagen.extra_rows, imagen.ads_with_extra) == (1, 0, 0)
    # Total: 3 anuncios, 2 extras, 2 anuncios con extras.
    assert (total.gold_ads, total.extra_rows, total.ads_with_extra) == (3, 2, 2)
    assert (total.extra_dup_currency, total.extra_other) == (1, 1)


def test_el_reporte_muestra_las_filas_de_mas_y_aclara_que_no_son_error_de_h0(
    session: Session, catalogo
) -> None:
    m = llm_backtest.compute_metrics(session, _armar_corpus_extras(session, catalogo))

    reporte = llm_backtest.render_report(m)

    assert "Filas de más" in reporte
    assert "2/2 (100.0%)" in reporte  # texto: anuncios con al menos una
    assert "2/3 (66.7%)" in reporte  # total: anuncios con al menos una
    assert "promedio 1.00" in reporte  # texto: 2 extras / 2 anuncios
    assert "promedio 0.67" in reporte  # total: 2 / 3
    assert "no son errores" in reporte  # aclaración explícita
    assert "incompleto" in reporte  # el oro puede estar incompleto


def test_un_registro_viejo_sin_las_filas_del_operador_igual_se_mide(
    session: Session, catalogo
) -> None:
    registros = _armar_corpus_extras(session, catalogo)
    assert all("expected_rows" not in r for r in registros)  # formato v1

    assert llm_backtest.compute_metrics(session, registros)["total"].extra_rows == 2
