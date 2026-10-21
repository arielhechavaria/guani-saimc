"""Tests de `scripts/vision_backtest.py` (medición: visión directa vs OCR + asistente).

Los valores esperados están calculados a mano, antes y aparte del código. Ningún
test toca la red ni la base de producción: el proveedor es un doble y la base
es la SQLite en memoria de `conftest`.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from saimc.extraction import llm_propose_ad
from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.measure import FrozenRow
from saimc.models import (
    ContentType,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReferenceSetMember,
    SourceType,
)
from scripts import vision_backtest as vb

ARROZ = Product(id=uuid.uuid4(), name="Arroz", category="granos", unit="lb", aliases=[])
POLLO = Product(id=uuid.uuid4(), name="Pollo", category="carnes", unit="lb", aliases=[])
LH = Province(id=uuid.uuid4(), code="LH", name="La Habana")
SCU = Province(id=uuid.uuid4(), code="SCU", name="Santiago de Cuba")


def _gold(product: Product, price: str, currency: str, qty: str, unit: str, prov: Province):
    return FrozenRow(
        product_id=product.id,
        province_id=prov.id,
        price=Decimal(price),
        currency=currency,
        quantity=Decimal(qty),
        unit=unit,
    )


def _prop(product, price, currency, qty, unit, prov=None) -> ExtractionProposal:
    return ExtractionProposal(
        product=product,
        price=Decimal(price) if price is not None else None,
        currency=currency,
        quantity=Decimal(qty) if qty is not None else None,
        unit=unit,
        province=prov,
    )


# --- costo ----------------------------------------------------------------------


def test_usage_cost_con_tarifa_explicita() -> None:
    # 1000 de entrada a 2 USD/Mtok = 0,002; 500 de salida a 10 = 0,005.
    assert vb.usage_cost(1000, 500, (2.0, 10.0)) == pytest.approx(0.007)


def test_estimate_suma_prompt_fijo_imagen_y_salida() -> None:
    est = vb.estimate(
        n_images=10,
        total_bytes=4_000_000,
        fixed_prompt_tokens=1000,
        tokens_per_image=1600,
        out_tokens_per_image=250,
        rates=(2.0, 10.0),
    )
    # Entrada: 10 * (1000 + 1600) = 26.000 -> 0,052. Salida: 2.500 -> 0,025.
    assert est.input_tokens == 26_000
    assert est.output_tokens == 2_500
    assert est.cost_usd == pytest.approx(0.077)
    assert est.total_bytes == 4_000_000


def test_estimate_sin_tarifa_no_inventa_costo() -> None:
    est = vb.estimate(
        n_images=3,
        total_bytes=1,
        fixed_prompt_tokens=10,
        tokens_per_image=10,
        out_tokens_per_image=10,
        rates=None,
    )
    assert est.cost_usd is None


def test_resolve_rates_prioriza_los_parametros_explicitos() -> None:
    assert vb.resolve_rates("claude-sonnet-5", None, None) == (2.0, 10.0)  # tarifa del repo
    assert vb.resolve_rates("claude-sonnet-5", 3.0, 15.0) == (3.0, 15.0)
    assert vb.resolve_rates("modelo-desconocido", None, None) is None
    assert vb.resolve_rates("modelo-desconocido", 1.0, 2.0) == (1.0, 2.0)
    # Con una sola de las dos no hay tarifa completa.
    assert vb.resolve_rates("modelo-desconocido", 1.0, None) is None


# --- puntuación -----------------------------------------------------------------


def test_score_rows_cuenta_por_campo_con_la_definicion_de_h0() -> None:
    gold = [
        _gold(ARROZ, "300", "CUP", "1", "lb", LH),
        _gold(POLLO, "5", "USD", "1", "lb", LH),
    ]
    # Arroz exacto. Pollo: producto, moneda, cantidad/unidad y provincia bien;
    # precio 6 (mal).
    candidates = [
        _prop(ARROZ, "300", "CUP", "1", "lb", LH),
        _prop(POLLO, "6", "USD", "1", "lb", LH),
    ]
    s = vb.Score()
    vb.score_rows(s, gold, candidates, LH)
    assert (s.rows, s.product, s.price, s.currency) == (2, 2, 1, 2)
    assert (s.quantity_unit, s.province, s.complete) == (2, 2, 1)


def test_score_rows_moneda_equivocada_falla_el_precio_de_h0_y_la_moneda() -> None:
    gold = [_gold(ARROZ, "300", "CUP", "1", "lb", LH)]
    s = vb.Score()
    vb.score_rows(s, gold, [_prop(ARROZ, "300", "USD", "1", "lb", LH)], LH)
    # «Precio» de H0 exige valor Y moneda; la moneda sola se cuenta aparte.
    assert (s.price, s.currency, s.complete) == (0, 0, 0)
    assert (s.product, s.quantity_unit) == (1, 1)


def test_score_rows_sin_filas_propuestas_es_fallo_salvo_la_provincia_del_canal() -> None:
    gold = [_gold(ARROZ, "300", "CUP", "1", "lb", LH)]
    s = vb.Score()
    vb.score_rows(s, gold, [], LH)
    assert s.rows == 1
    assert (s.product, s.price, s.quantity_unit, s.complete) == (0, 0, 0, 0)
    assert s.province == 1  # el canal manda, igual que en H0


def test_frac_muestra_el_n_siempre() -> None:
    assert vb.frac(1, 4) == "1/4 (25.0%)"
    assert vb.frac(0, 0) == "0/0 (n/d)"


# --- archivo de resultados y retomar --------------------------------------------


def _rec(ing: str, *, degraded: bool = False, rows: list[dict] | None = None, **kw: Any) -> dict:
    return {
        "ingestion_id": ing,
        "model": "claude-sonnet-5",
        "prompt_version": llm_propose_ad.PROMPT_VERSION,
        "degraded": degraded,
        "failure": "timeout" if degraded else "",
        "discard": False,
        "rows": rows or [],
        "channel_province_id": None,
        "input_tokens": 0 if degraded else 3000,
        "output_tokens": 0 if degraded else 200,
        "cost_usd": 0.0 if degraded else 0.008,
        **kw,
    }


def test_pending_saltea_lo_hecho_y_reintenta_lo_degradado(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    vb.append_record(_rec("a"), path)
    vb.append_record(_rec("b", degraded=True), path)
    items = [vb.GoldItem(i, f"{i}.png", []) for i in ("a", "b", "c")]
    pending = vb.pending_items(items, vb.latest_records(vb.read_records(path)))
    assert [i.ingestion_id for i in pending] == ["b", "c"]


def test_read_records_salta_lineas_rotas(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text('{"ingestion_id": "a"}\nbasura\n{"sin_id": 1}\n', encoding="utf-8")
    assert [r["ingestion_id"] for r in vb.read_records(path)] == ["a"]
    assert vb.read_records(tmp_path / "no-existe.jsonl") == []


# --- la corrida: tope de costo, límite, retomar, privacidad ---------------------


def _images(tmp_path: Path, n: int) -> tuple[list[vb.GoldItem], Path]:
    root = tmp_path / "raw"
    root.mkdir()
    items = []
    for i in range(n):
        (root / f"{i}.png").write_bytes(b"\x89PNG-falso-" + bytes([i]))
        items.append(
            vb.GoldItem(f"ing{i}", f"{i}.png", [_gold(ARROZ, "300", "CUP", "1", "lb", LH)])
        )
    return items, root


class FakeCall:
    """Doble del proveedor: cuenta llamadas y devuelve un consumo fijo."""

    def __init__(self, *, input_tokens: int = 100_000, output_tokens: int = 0, **kw: Any) -> None:
        self.calls: list[str] = []
        self.kw = kw
        self.tokens = (input_tokens, output_tokens)

    def __call__(
        self, item: vb.GoldItem, image: bytes, media_type: str
    ) -> llm_propose_ad.AdProposal:
        self.calls.append(item.ingestion_id)
        return llm_propose_ad.AdProposal(
            rows=[] if self.kw.get("degraded") else [_prop(ARROZ, "300", "CUP", "1", "lb")],
            discard=False,
            input_tokens=self.tokens[0],
            output_tokens=self.tokens[1],
            **self.kw,
        )


def _run(items, root, results, call, **kw: Any) -> dict[str, Any]:
    return vb.run_vision(
        items,
        results_path=results,
        raw_dir=root,
        call=call,
        rates=(2.0, 10.0),
        model="claude-sonnet-5",
        channel_of=lambda item: None,
        out=lambda _m: None,
        **kw,
    )


def test_el_tope_de_costo_corta_al_alcanzarlo(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 5)
    # 100.000 tokens de entrada a 2 USD/Mtok = 0,20 por imagen; tope 0,50:
    # 0,20 -> 0,40 -> 0,60 (>= tope) y corta tras la tercera.
    call = FakeCall()
    summary = _run(items, root, tmp_path / "r.jsonl", call, max_cost=0.50)
    assert call.calls == ["ing0", "ing1", "ing2"]
    assert summary["stopped_by_cost"] is True
    assert summary["cost_usd"] == pytest.approx(0.60)
    assert len(vb.read_records(tmp_path / "r.jsonl")) == 3


def test_limit_recorta_lo_pendiente(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 5)
    call = FakeCall(input_tokens=10)
    _run(items, root, tmp_path / "r.jsonl", call, max_cost=10.0, limit=2)
    assert call.calls == ["ing0", "ing1"]


def test_retomar_no_repite_imagenes_hechas(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 3)
    results = tmp_path / "r.jsonl"
    first = FakeCall(input_tokens=10)
    _run(items, root, results, first, max_cost=10.0, limit=2)
    second = FakeCall(input_tokens=10)
    _run(items, root, results, second, max_cost=10.0)
    assert first.calls == ["ing0", "ing1"]
    assert second.calls == ["ing2"]
    assert len(vb.latest_records(vb.read_records(results))) == 3


def test_el_resultado_guarda_ids_y_filas_nunca_bytes_de_imagen(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 1)
    results = tmp_path / "r.jsonl"
    _run(items, root, results, FakeCall(input_tokens=10), max_cost=10.0)
    raw = results.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["ingestion_id"] == "ing0"
    assert record["rows"][0]["price"] == "300"
    assert base64.b64encode(b"\x89PNG-falso-\x00").decode() not in raw
    assert "PNG-falso" not in raw
    assert not {"image", "data", "bytes"} & set(record)


def test_texto_libre_degradado_se_guarda_redactado(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 1)
    results = tmp_path / "r.jsonl"
    call = FakeCall(
        input_tokens=10,
        degraded=True,
        failure="other",
        raw_response="llamame al tel 5123 4567 por favor",
    )
    _run(items, root, results, call, max_cost=10.0)
    saved = vb.read_records(results)[0]
    assert saved["degraded"] is True
    assert "4567" not in json.dumps(saved)


def test_imagen_faltante_o_enorme_no_llama_al_proveedor(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 2)
    (root / "1.png").write_bytes(b"x" * (vb.MAX_IMAGE_BYTES + 1))
    items.append(vb.GoldItem("fantasma", "no-esta.png", []))
    call = FakeCall(input_tokens=10)
    _run(items, root, tmp_path / "r.jsonl", call, max_cost=10.0)
    assert call.calls == ["ing0"]
    by_id = vb.latest_records(vb.read_records(tmp_path / "r.jsonl"))
    assert by_id["ing1"]["failure"] == "too_large" and by_id["ing1"]["degraded"]
    assert by_id["fantasma"]["failure"] == "missing_file"


def test_falla_de_credito_detiene_la_corrida(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 3)
    call = FakeCall(input_tokens=0, degraded=True, failure="credit")
    summary = _run(items, root, tmp_path / "r.jsonl", call, max_cost=10.0)
    assert call.calls == ["ing0"]
    assert summary["aborted"] is True


def test_el_canal_manda_sobre_la_provincia_del_modelo(tmp_path: Path) -> None:
    items, root = _images(tmp_path, 1)
    results = tmp_path / "r.jsonl"
    vb.run_vision(
        items,
        results_path=results,
        raw_dir=root,
        call=FakeCall(input_tokens=10),
        rates=(2.0, 10.0),
        model="m",
        max_cost=10.0,
        channel_of=lambda item: SCU,
        out=lambda _m: None,
    )
    record = vb.read_records(results)[0]
    assert record["channel_province_id"] == str(SCU.id)
    assert record["rows"][0]["province_id"] == str(SCU.id)


# --- la llamada real (con cliente doble) ----------------------------------------


def test_vision_call_manda_la_imagen_con_el_mismo_prompt_y_esquema() -> None:
    captured: dict[str, Any] = {}
    body = {
        "discard": False,
        "discard_reason": None,
        "rows": [
            {"product": "Arroz", "price": 300, "currency": "CUP", "quantity": 1, "unit": "lb"}
        ],
    }

    class Messages:
        def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(body))],
                usage=SimpleNamespace(input_tokens=1500, output_tokens=60),
            )

    client = SimpleNamespace(messages=Messages())
    call = vb.make_vision_call(
        client, request_model="claude-sonnet-5", products=[ARROZ], provinces=[LH]
    )
    result = call(vb.GoldItem("ing0", "0.png", []), b"bytes-de-imagen", "image/png")

    assert captured["system"] == llm_propose_ad._SYSTEM_PROMPT
    assert captured["output_config"]["format"]["schema"] == llm_propose_ad.response_schema()
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == llm_propose_ad.MAX_TOKENS
    blocks = captured["messages"][0]["content"]
    # Catálogos idénticos a los del camino de texto: solo cambia la entrada.
    assert blocks[0] == llm_propose_ad.build_prompt_blocks("", [ARROZ], [LH])[0]
    image = next(b for b in blocks if b["type"] == "image")
    assert image["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(b"bytes-de-imagen").decode(),
    }
    assert result.degraded is False
    assert result.rows[0].product is ARROZ and result.rows[0].price == Decimal("300")
    assert (result.input_tokens, result.output_tokens) == (1500, 60)


def test_vision_call_error_del_cliente_degrada_sin_levantar() -> None:
    class Messages:
        def create(self, **kwargs: Any) -> Any:
            raise TimeoutError("timed out")

    call = vb.make_vision_call(
        SimpleNamespace(messages=Messages()),
        request_model="m",
        products=[ARROZ],
        provinces=[LH],
    )
    result = call(vb.GoldItem("x", "x.png", []), b"b", "image/png")
    assert result.degraded is True and result.failure == "timeout"


def test_media_type_por_extension() -> None:
    assert vb.media_type_of(Path("a.JPG")) == "image/jpeg"
    assert vb.media_type_of(Path("a.png")) == "image/png"
    assert vb.media_type_of(Path("a.webp")) == "image/webp"
    assert vb.media_type_of(Path("a.bmp")) is None


# --- agregación y comparación lado a lado ---------------------------------------


def _row(product, price, currency, qty, unit, prov=None) -> dict:
    return {
        "product_id": str(product.id),
        "price": price,
        "currency": currency,
        "quantity": qty,
        "unit": unit,
        "province_id": str(prov.id) if prov else None,
    }


def _setup_eval():
    products = {str(ARROZ.id): ARROZ, str(POLLO.id): POLLO}
    provinces = {str(LH.id): LH, str(SCU.id): SCU}
    gold = {
        "i1": [_gold(ARROZ, "300", "CUP", "1", "lb", LH)],
        "i2": [_gold(POLLO, "5", "USD", "1", "lb", LH)],
        "i3": [_gold(ARROZ, "100", "CUP", "2", "lb", LH)],
    }
    return products, provinces, gold


def test_aggregate_ignora_degradados_y_ajenos_al_oro() -> None:
    products, provinces, gold = _setup_eval()
    lh = str(LH.id)
    records = {
        # i1 exacto; i2 con precio mal; i3 degradado (fuera); zz no está en el oro.
        "i1": _rec("i1", rows=[_row(ARROZ, "300", "CUP", "1", "lb", LH)], channel_province_id=lh),
        "i2": _rec("i2", rows=[_row(POLLO, "6", "USD", "1", "lb", LH)], channel_province_id=lh),
        "i3": _rec("i3", degraded=True),
        "zz": _rec("zz", rows=[_row(ARROZ, "1", "CUP", "1", "lb")]),
    }
    agg = vb.aggregate(records, gold, products, provinces)
    assert agg.ads == 2 and agg.degraded == 1
    s = agg.score
    assert (s.rows, s.product, s.price, s.complete) == (2, 2, 1, 1)
    # Costo real de todo lo registrado del oro: 0,008 + 0,008 + 0 (degradado).
    assert agg.cost_usd == pytest.approx(0.016)


def test_comparison_usa_solo_las_imagenes_que_ambos_caminos_cubren() -> None:
    products, provinces, gold = _setup_eval()
    lh = str(LH.id)
    vision = {
        "i1": _rec("i1", rows=[_row(ARROZ, "300", "CUP", "1", "lb", LH)], channel_province_id=lh),
        "i2": _rec("i2", rows=[_row(POLLO, "5", "USD", "1", "lb", LH)], channel_province_id=lh),
        "i3": _rec("i3", rows=[_row(ARROZ, "100", "CUP", "2", "lb", LH)], channel_province_id=lh),
    }
    # El camino OCR cubre i1 (mal el precio) e i2 (exacto); en i3 quedó degradado.
    ocr = {
        "i1": {
            "ingestion_id": "i1",
            "degraded": False,
            "channel_province_id": lh,
            "proposal": {"rows": [_row(ARROZ, "310", "CUP", "1", "lb", LH)]},
        },
        "i2": {
            "ingestion_id": "i2",
            "degraded": False,
            "channel_province_id": lh,
            "proposal": {"rows": [_row(POLLO, "5", "USD", "1", "lb", LH)]},
        },
        "i3": {"ingestion_id": "i3", "degraded": True, "proposal": {}},
    }
    cmp_ = vb.compare(vision, ocr, gold, products, provinces)
    assert cmp_.common_ads == 2
    assert (cmp_.vision.rows, cmp_.vision.complete) == (2, 2)  # i1 e i2 exactos
    assert (cmp_.ocr.rows, cmp_.ocr.complete, cmp_.ocr.price) == (2, 1, 1)


def test_comparison_sin_interseccion_no_inventa_nada() -> None:
    products, provinces, gold = _setup_eval()
    cmp_ = vb.compare({"i1": _rec("i1")}, {}, gold, products, provinces)
    assert cmp_.common_ads == 0


def test_render_report_declara_cuando_no_se_puede_comparar() -> None:
    products, provinces, gold = _setup_eval()
    vision = {
        "i1": _rec(
            "i1",
            rows=[_row(ARROZ, "300", "CUP", "1", "lb", LH)],
            channel_province_id=str(LH.id),
        )
    }
    text = vb.render_report(vision, None, gold, products, provinces, ocr_path=Path("x.jsonl"))
    assert "No se puede comparar" in text
    assert "1/1 (100.0%)" in text  # completo correcto, con su n


def test_render_report_con_comparacion_trae_tabla_lado_a_lado() -> None:
    products, provinces, gold = _setup_eval()
    lh = str(LH.id)
    vision = {
        "i1": _rec("i1", rows=[_row(ARROZ, "300", "CUP", "1", "lb", LH)], channel_province_id=lh)
    }
    ocr = {
        "i1": {
            "ingestion_id": "i1",
            "degraded": False,
            "prompt_version": "viejo",
            "channel_province_id": lh,
            "proposal": {"rows": [_row(ARROZ, "310", "CUP", "1", "lb", LH)]},
        }
    }
    text = vb.render_report(vision, ocr, gold, products, provinces, ocr_path=Path("x.jsonl"))
    assert "| Campo | Visión | OCR + asistente |" in text
    assert "viejo" in text  # versión de prompt distinta: se avisa


# --- el oro desde la base --------------------------------------------------------


def test_load_gold_toma_solo_imagenes_del_conjunto_vigente(session: Session) -> None:
    session.add_all([ARROZ, LH])
    session.commit()

    def ing(tipo: str, name: str) -> RawIngestion:
        r = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=name,
            content_hash=name,
            content_type=tipo,
            original_text=None,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        return r

    img_v1 = ing(ContentType.IMAGE, "a.png")
    img_v2 = ing(ContentType.IMAGE, "b.png")
    txt_v2 = ing(ContentType.TEXT, "c.txt")
    fila = {
        "product_id": str(ARROZ.id),
        "province_id": str(LH.id),
        "price": "300",
        "currency": "CUP",
        "quantity": "1",
        "unit": "lb",
    }
    for version, r in ((1, img_v1), (2, img_v2), (2, txt_v2)):
        session.add(
            ReferenceSetMember(set_version=version, ingestion_id=r.id, rows=[fila], values_hash="h")
        )
    session.commit()

    gold = vb.load_gold(session)
    assert [g.ingestion_id for g in gold] == [str(img_v2.id)]  # vigente = 2, sin texto
    assert gold[0].file_path == "b.png"
    assert gold[0].rows[0].price == Decimal("300")
