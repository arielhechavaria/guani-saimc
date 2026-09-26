"""`saimc.extraction.prefill` — el pre-cálculo por lotes de la propuesta (slice 3b).

Los casos se escribieron antes que el comando. El cliente es un doble: ningún
test toca la red ni necesita clave real. El costo esperado se calcula a mano con
las tarifas de Sonnet 5 ($2 / $10 por millón): 1000 tokens de entrada y 200 de
salida son $0,002 + $0,002 = $0,004 por anuncio.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.extraction import llm_assist, llm_propose_ad, prefill
from saimc.models import (
    ContentType,
    LlmProposal,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)

_RESPUESTA_OK = {
    "discard": False,
    "discard_reason": None,
    "rows": [
        {
            "product": "Arroz",
            "price": 100,
            "currency": "CUP",
            "quantity": 1,
            "unit": "lb",
            "province": None,
            "evidence": "arroz 100",
            "currency_explicit": False,
        }
    ],
}


class ClienteFalso:
    """Doble de `anthropic.Anthropic`: solo `messages.create`.

    `errores` es una cola: cada llamada saca el primero; `None` = responde bien.
    """

    def __init__(self, cuerpo: object = None, errores: list | None = None) -> None:
        self._cuerpo = _RESPUESTA_OK if cuerpo is None else cuerpo
        self._errores = list(errores or [])
        self.llamadas: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.llamadas.append(kw)
        error = self._errores.pop(0) if self._errores else None
        if error is not None:
            raise error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._cuerpo))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )

    def textos(self) -> list[str]:
        return ["".join(b["text"] for b in kw["messages"][0]["content"]) for kw in self.llamadas]


@pytest.fixture
def catalogo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    session.add_all([arroz, lh])
    session.commit()
    return SimpleNamespace(arroz=arroz, lh=lh)


_contador = 0


def _ingesta(
    session: Session,
    *,
    texto: str | None = "arroz 100",
    tipo: str = ContentType.TEXT,
    estado: str = ProcessingStatus.PENDING,
    minutos: int = 0,
) -> RawIngestion:
    global _contador
    _contador += 1
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{_contador}.png" if tipo == ContentType.IMAGE else f"raw/{_contador}.txt",
        content_hash=f"pf{_contador}",
        content_type=tipo,
        original_text=texto,
        processing_status=estado,
        captured_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=minutos),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _ajustes(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "clave-de-prueba",
        "llm_assist_log_path": tmp_path / "assist.jsonl",
        "raw_store_dir": tmp_path / "raw",
    }
    base.update(kw)
    # `_env_file=None`: que el `.env` del operador (y su clave) no se cuele.
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _correr(session, tmp_path, argv, *, cliente=None, ajustes=None, transcribe=None):
    salida: list[str] = []
    codigo = prefill.execute(
        prefill.parse_args(argv),
        session,
        ajustes or _ajustes(tmp_path),
        client=cliente,
        transcribe=transcribe or (lambda path: ""),
        out=salida.append,
    )
    return codigo, "\n".join(salida)


def _propuestas(session: Session) -> list[LlmProposal]:
    return list(session.execute(select(LlmProposal)).scalars())


def _lineas_del_registro(tmp_path: Path) -> list[dict]:
    path = tmp_path / "assist.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


# --- dry-run ----------------------------------------------------------------


def test_dry_run_no_llama_y_reporta_conteo_y_costo_documentado(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session)
    _ingesta(session, minutos=1)
    _ingesta(session, texto=None, tipo=ContentType.IMAGE, minutos=2)
    cliente = ClienteFalso()

    codigo, salida = _correr(session, tmp_path, [], cliente=cliente)

    assert codigo == 0
    assert cliente.llamadas == []
    assert _propuestas(session) == []
    assert "dry-run" in salida.lower()
    assert "texto: 2" in salida
    assert "imagen: 1" in salida
    assert "total: 3" in salida
    # Sin propuestas guardadas: el promedio documentado (2407/245 tokens con
    # Sonnet 5 = $0,007264 por anuncio), por 3 anuncios = $0,0218.
    assert "documentado" in salida
    assert "$0.0218" in salida
    assert not (tmp_path / "assist.jsonl").exists()


def test_dry_run_estima_con_lo_medido_si_ya_hay_propuestas(
    session: Session, catalogo, tmp_path: Path
) -> None:
    hecho = _ingesta(session, estado=ProcessingStatus.CURATED)
    # Dos llamadas guardadas: promedio 3000 de entrada y 500 de salida,
    # $0,006 + $0,005 = $0,011 por anuncio.
    for entrada, salida_ in ((2000, 400), (4000, 600)):
        session.add(
            LlmProposal(
                ingestion_id=hecho.id,
                model="claude-sonnet-5",
                prompt_version="x",
                status="ok",
                rows=[],
                input_tokens=entrada,
                output_tokens=salida_,
            )
        )
    session.commit()
    _ingesta(session)
    _ingesta(session, minutos=1)

    codigo, salida = _correr(session, tmp_path, [])

    assert codigo == 0
    assert "medido" in salida
    assert "2 propuestas guardadas" in salida
    assert "$0.0110" in salida  # por anuncio
    assert "$0.0220" in salida  # total, 2 candidatos


# --- ejecución --------------------------------------------------------------


def test_execute_guarda_propuestas_ok_como_completar_con_ia(
    session: Session, catalogo, tmp_path: Path
) -> None:
    ingesta = _ingesta(session, texto="arroz 100")
    cliente = ClienteFalso()

    codigo, salida = _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert codigo == 0
    (propuesta,) = _propuestas(session)
    assert propuesta.ingestion_id == ingesta.id
    assert propuesta.status == "ok"
    assert propuesta.model == "claude-sonnet-5"
    assert propuesta.prompt_version == llm_propose_ad.PROMPT_VERSION
    assert propuesta.input_tokens == 1000
    assert propuesta.output_tokens == 200
    assert propuesta.rows[0]["product_id"] == str(catalogo.arroz.id)
    assert propuesta.rows[0]["price"] == "100"
    assert propuesta.raw_response is not None
    # Línea de avance: id corto, filas, tokens y costo acumulado.
    assert str(ingesta.id)[:8] in salida
    assert "$0.0040" in salida


def test_execute_saltea_los_que_ya_tienen_propuesta_ok(
    session: Session, catalogo, tmp_path: Path
) -> None:
    con_propuesta = _ingesta(session, texto="arroz viejo")
    session.add(
        LlmProposal(
            ingestion_id=con_propuesta.id,
            model="claude-sonnet-5",
            prompt_version="una-version-vieja",
            status="ok",
            rows=[],
            input_tokens=0,
            output_tokens=0,
        )
    )
    session.commit()
    _ingesta(session, texto="arroz nuevo", minutos=1)
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert len(cliente.llamadas) == 1
    assert "arroz nuevo" in cliente.textos()[0]


def test_una_propuesta_degradada_previa_no_cuenta_como_hecha(
    session: Session, catalogo, tmp_path: Path
) -> None:
    ingesta = _ingesta(session)
    session.add(
        LlmProposal(
            ingestion_id=ingesta.id,
            model="claude-sonnet-5",
            prompt_version="x",
            status="degraded",
            failure="timeout",
            rows=[],
            input_tokens=0,
            output_tokens=0,
        )
    )
    session.commit()
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert len(cliente.llamadas) == 1


def test_reanudable_la_segunda_corrida_no_procesa_nada(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session)
    _ingesta(session, minutos=1)
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente)
    _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert len(cliente.llamadas) == 2
    assert len(_propuestas(session)) == 2


def test_solo_procesa_pendientes(session: Session, catalogo, tmp_path: Path) -> None:
    _ingesta(session, texto="arroz curado", estado=ProcessingStatus.CURATED)
    _ingesta(session, texto="arroz descartado", estado=ProcessingStatus.DISCARDED)
    _ingesta(session, texto="arroz pendiente")
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert len(cliente.llamadas) == 1
    assert "arroz pendiente" in cliente.textos()[0]


def test_limit_toma_los_mas_viejos_primero(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz tercero", minutos=30)
    _ingesta(session, texto="arroz primero", minutos=0)
    _ingesta(session, texto="arroz segundo", minutos=10)
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute", "--limit", "2"], cliente=cliente)

    textos = cliente.textos()
    assert len(textos) == 2
    assert "arroz primero" in textos[0]
    assert "arroz segundo" in textos[1]


def test_degradado_se_guarda_y_la_corrida_sigue(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")
    _ingesta(session, texto="arroz dos", minutos=1)
    cliente = ClienteFalso(errores=[TimeoutError("timed out")])

    codigo, salida = _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert codigo == 0
    assert len(cliente.llamadas) == 2
    estados = sorted(p.status for p in _propuestas(session))
    assert estados == ["degraded", "ok"]
    assert "timeout" in salida


@pytest.mark.parametrize(
    ("error", "falla"),
    [
        (Exception("Your credit balance is too low"), "credit"),
        (Exception("authentication_error: invalid x-api-key"), "auth"),
    ],
)
def test_falla_de_saldo_o_clave_detiene_la_corrida(
    session: Session, catalogo, tmp_path: Path, error, falla
) -> None:
    _ingesta(session, texto="arroz uno")
    _ingesta(session, texto="arroz dos", minutos=1)
    cliente = ClienteFalso(errores=[error])

    codigo, salida = _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert codigo != 0
    assert len(cliente.llamadas) == 1
    (propuesta,) = _propuestas(session)
    assert propuesta.status == "degraded"
    assert propuesta.failure == falla
    assert "detenida" in salida.lower()


def test_sin_clave_error_claro_y_ninguna_llamada(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session)
    cliente = ClienteFalso()

    codigo, salida = _correr(
        session,
        tmp_path,
        ["--execute"],
        cliente=cliente,
        ajustes=_ajustes(tmp_path, anthropic_api_key=None),
    )

    assert codigo != 0
    assert "clave" in salida.lower()
    assert cliente.llamadas == []
    assert _propuestas(session) == []


def test_la_clave_no_se_imprime(session: Session, catalogo, tmp_path: Path) -> None:
    _ingesta(session)

    _, salida = _correr(
        session,
        tmp_path,
        ["--execute"],
        cliente=ClienteFalso(),
        ajustes=_ajustes(tmp_path, anthropic_api_key="sk-secreta-123"),
    )

    assert "sk-secreta-123" not in salida


def test_max_cost_frena_antes_de_pasarse(
    session: Session, catalogo, tmp_path: Path
) -> None:
    """Cada anuncio cuesta $0,004. Con tope de $0,010: el tercero llevaría el
    acumulado a $0,012, así que se frena después del segundo."""
    for n in range(5):
        _ingesta(session, texto=f"arroz {n}", minutos=n)
    cliente = ClienteFalso()

    codigo, salida = _correr(
        session, tmp_path, ["--execute", "--max-cost", "0.010"], cliente=cliente
    )

    assert codigo == 0
    assert len(cliente.llamadas) == 2
    assert "tope" in salida.lower()


def test_imagen_usa_el_texto_del_ocr(session: Session, catalogo, tmp_path: Path) -> None:
    imagen = _ingesta(session, texto=None, tipo=ContentType.IMAGE)
    vistos: list[Path] = []

    def ocr_falso(path: Path) -> str:
        vistos.append(path)
        return "arroz de la imagen a 100"

    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente, transcribe=ocr_falso)

    assert vistos == [tmp_path / "raw" / imagen.file_path]
    assert "arroz de la imagen a 100" in cliente.textos()[0]
    assert len(_propuestas(session)) == 1


def test_imagen_sin_texto_reconocido_no_llama(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto=None, tipo=ContentType.IMAGE)
    cliente = ClienteFalso()

    codigo, salida = _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert codigo == 0
    assert cliente.llamadas == []
    assert _propuestas(session) == []
    assert "sin texto" in salida.lower()


# --- consumo y H0 -----------------------------------------------------------


def test_el_registro_del_prefill_suma_consumo_pero_no_asiste(
    session: Session, catalogo, tmp_path: Path
) -> None:
    ingesta = _ingesta(session)

    _correr(session, tmp_path, ["--execute"], cliente=ClienteFalso())

    (linea,) = _lineas_del_registro(tmp_path)
    assert linea["source"] == "prefill"
    assert linea["ingestion_id"] == str(ingesta.id)
    log = tmp_path / "assist.jsonl"
    consumo = llm_assist.consumption_summary(log)
    assert consumo.calls == 1
    assert consumo.input_tokens == 1000
    assert consumo.output_tokens == 200
    assert llm_assist.assisted_ingestion_ids(log) == set()


def test_las_lineas_viejas_sin_source_siguen_contando_como_asistidas(
    tmp_path: Path,
) -> None:
    log = tmp_path / "assist.jsonl"
    llm_assist.append_assist_run(
        {"ingestion_id": "viejo", "model": "claude-sonnet-5", "input_tokens": 10}, log
    )
    llm_assist.append_assist_run(
        {"ingestion_id": "boton", "model": "claude-sonnet-5", "source": "curation"}, log
    )
    llm_assist.append_assist_run(
        {"ingestion_id": "pre", "model": "claude-sonnet-5", "source": "prefill"}, log
    )

    assert llm_assist.assisted_ingestion_ids(log) == {"viejo", "boton"}
    assert llm_assist.consumption_summary(log).calls == 3
