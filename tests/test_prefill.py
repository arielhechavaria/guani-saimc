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

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.extraction import llm_assist, llm_propose_ad, prefill, prefill_status
from saimc.models import (
    ContentType,
    JevSignal,
    LlmProposal,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)
from saimc.validation import jev_signal
from tests.jev_fakes import FAKE_KEY
from tests.jev_fakes import Api as ApiJev
from tests.jev_fakes import client as jev_client_falso

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


def test_no_procesa_pendientes_fuera_de_foco(
    session: Session, catalogo, tmp_path: Path
) -> None:
    """El bug reportado: `select_candidates` pagaba también los pendientes
    fuera de foco, que después se descartan en lote sin pasar nunca por
    curaduría (`discard_out_of_focus` en `web/routers/curation.py`) — mismo
    criterio de foco que ya usa `_pending_in_focus` para la cola interactiva.
    """
    _ingesta(session, texto="arroz en foco", minutos=0)
    _ingesta(session, texto="gasolina fuera de foco", minutos=10)
    cliente = ClienteFalso()

    _correr(session, tmp_path, ["--execute"], cliente=cliente)

    assert len(cliente.llamadas) == 1
    assert "arroz en foco" in cliente.textos()[0]
    assert len(_propuestas(session)) == 1


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


# --- proveedor del modelo (fábrica, 2026-10-09) ------------------------------


def _ajustes_vercel(tmp_path: Path, **kw: Any) -> Settings:
    """Vercel AI Gateway con la clave de Jev y SIN clave de Anthropic."""
    base: dict[str, Any] = {
        "anthropic_api_key": "",
        "llm_provider": "vercel",
        "llm_jev_api_key": "vck-de-prueba",
    }
    base.update(kw)
    return _ajustes(tmp_path, **base)


def test_con_vercel_la_llamada_lleva_prefijo_y_lo_guardado_y_tarifado_no(
    session: Session, catalogo, tmp_path: Path
) -> None:
    """Con el prefijo en `llm_proposals.model`, la tabla de tarifas no lo
    encuentra: el costo daría 0 y el tope de la corrida dejaría de frenar."""
    _ingesta(session)
    cliente = ClienteFalso()

    codigo, salida = _correr(
        session, tmp_path, ["--execute"], cliente=cliente, ajustes=_ajustes_vercel(tmp_path)
    )

    assert codigo == 0
    assert cliente.llamadas[0]["model"] == "anthropic/claude-sonnet-5"
    (propuesta,) = _propuestas(session)
    assert propuesta.model == "claude-sonnet-5"
    assert _lineas_del_registro(tmp_path)[0]["model"] == "claude-sonnet-5"
    # 1000 de entrada a USD 2 y 200 de salida a USD 10, por millón.
    assert "costo real $0.0040" in salida
    assert "no tiene tarifa" not in salida


def test_con_vercel_el_model_de_la_linea_de_comandos_pisa_y_se_prefija(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session)
    cliente = ClienteFalso()

    _correr(
        session,
        tmp_path,
        ["--execute", "--model", "claude-haiku-4-5"],
        cliente=cliente,
        ajustes=_ajustes_vercel(tmp_path),
    )

    assert cliente.llamadas[0]["model"] == "anthropic/claude-haiku-4-5"
    assert _propuestas(session)[0].model == "claude-haiku-4-5"


def test_llm_model_de_la_configuracion_rige_la_estimacion_y_la_corrida(
    session: Session, catalogo, tmp_path: Path
) -> None:
    """`SAIMC_LLM_MODEL` se ignoraba en silencio: solo se leía el nombre
    anterior (`SAIMC_LLM_ASSIST_MODEL`)."""
    _ingesta(session)
    cliente = ClienteFalso()
    ajustes = _ajustes(tmp_path, llm_model="claude-haiku-4-5", llm_assist_model="claude-opus-5")

    _, seco = _correr(session, tmp_path, [], cliente=cliente, ajustes=ajustes)
    _correr(session, tmp_path, ["--execute"], cliente=cliente, ajustes=ajustes)

    assert "claude-haiku-4-5" in seco
    assert cliente.llamadas[0]["model"] == "claude-haiku-4-5"


def test_con_vercel_y_sin_ninguna_clave_se_niega_y_nombra_la_variable(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session)
    cliente = ClienteFalso()

    codigo, salida = _correr(
        session,
        tmp_path,
        ["--execute"],
        cliente=cliente,
        ajustes=_ajustes_vercel(tmp_path, llm_jev_api_key=""),
    )

    assert codigo == 2
    assert "SAIMC_VERCEL_API_KEY" in salida
    assert cliente.llamadas == []


def test_la_clave_del_gateway_no_se_imprime(session: Session, catalogo, tmp_path: Path) -> None:
    _ingesta(session)

    _, salida = _correr(
        session,
        tmp_path,
        ["--execute"],
        cliente=ClienteFalso(),
        ajustes=_ajustes_vercel(tmp_path, llm_jev_api_key="vck-secreta-123"),
    )

    assert "vck-secreta-123" not in salida


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


# --- registro de la corrida (C1: aviso en /control) ---------------------------


def test_una_corrida_real_deja_registro_con_salida_cero(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")
    ruta = tmp_path / "estado" / "prefill-status.json"
    ajustes = _ajustes(tmp_path, prefill_status_path=ruta)

    codigo, _ = _correr(
        session, tmp_path, ["--execute"], cliente=ClienteFalso(), ajustes=ajustes
    )

    assert codigo == 0
    registro = prefill_status.read_run_status(ruta)
    assert registro is not None
    assert registro.exit_code == 0
    assert registro.done == 1
    assert registro.finished_at.tzinfo is not None
    assert abs(registro.finished_at - datetime.now(UTC)) < timedelta(minutes=1)


def test_una_corrida_abortada_deja_la_falla_registrada(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")
    ruta = tmp_path / "prefill-status.json"
    ajustes = _ajustes(tmp_path, prefill_status_path=ruta)
    cliente = ClienteFalso(errores=[Exception("Your credit balance is too low")])

    codigo, _ = _correr(session, tmp_path, ["--execute"], cliente=cliente, ajustes=ajustes)

    registro = prefill_status.read_run_status(ruta)
    assert codigo == 1
    assert registro is not None
    assert registro.exit_code == 1
    assert registro.aborted_failure == "credit"


def test_sin_clave_tambien_queda_registrado_como_falla(
    session: Session, catalogo, tmp_path: Path
) -> None:
    ruta = tmp_path / "prefill-status.json"
    ajustes = _ajustes(tmp_path, anthropic_api_key="", prefill_status_path=ruta)

    codigo, _ = _correr(session, tmp_path, ["--execute"], ajustes=ajustes)

    registro = prefill_status.read_run_status(ruta)
    assert codigo == 2
    assert registro is not None and registro.exit_code == 2


def test_un_dry_run_no_deja_registro(session: Session, catalogo, tmp_path: Path) -> None:
    _ingesta(session, texto="arroz uno")
    ruta = tmp_path / "prefill-status.json"
    ajustes = _ajustes(tmp_path, prefill_status_path=ruta)

    _correr(session, tmp_path, [], ajustes=ajustes)

    assert not ruta.exists()


def test_un_registro_ilegible_se_lee_como_ausente(tmp_path: Path) -> None:
    ruta = tmp_path / "prefill-status.json"
    ruta.write_text("{esto no es json", encoding="utf-8")

    assert prefill_status.read_run_status(ruta) is None
    assert prefill_status.read_run_status(tmp_path / "no-existe.json") is None


# --- la pasada de Jev (A6b-1) --------------------------------------------------
#
# Casos 1, 5 y 6 de «Casos esperados» de `docs/spec-slice-A6b.md`, en lo que toca
# al pre-cálculo: la pasada va antes del bucle de Claude, no depende de él y no
# le cambia el código de salida. El detalle de la pasada está en
# `test_jev_signal.py`.


def _ajustes_jev(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_jev_enabled": True,
        "llm_jev_api_key": FAKE_KEY,
        "prefill_status_path": tmp_path / "prefill-status.json",
    }
    base.update(kw)
    return _ajustes(tmp_path, **base)


def _correr_con_jev(session, tmp_path, argv, api, *, cliente=None, ajustes=None):
    salida: list[str] = []
    with jev_client_falso(api) as http:
        codigo = prefill.execute(
            prefill.parse_args(argv),
            session,
            ajustes or _ajustes_jev(tmp_path),
            client=cliente,
            transcribe=lambda path: "",
            jev_http=http,
            jev_sleep=lambda _segundos: None,
            out=salida.append,
        )
    return codigo, "\n".join(salida)


def _senales(session: Session) -> list[JevSignal]:
    return list(session.execute(select(JevSignal)).scalars())


@pytest.mark.parametrize(
    "apagado",
    [{"llm_jev_enabled": False}, {"llm_jev_api_key": None}],
    ids=["interruptor-apagado", "encendido-sin-clave"],
)
def test_a6b_caso_1_inactivo_el_precalculo_no_llama_a_jev(
    session: Session, catalogo, tmp_path: Path, apagado: dict[str, Any]
) -> None:
    _ingesta(session, texto="arroz uno")
    api = ApiJev()
    cliente = ClienteFalso()
    ajustes = _ajustes_jev(tmp_path, **apagado)

    codigo, _ = _correr_con_jev(session, tmp_path, ["--execute"], api, cliente=cliente, ajustes=ajustes)

    assert codigo == 0
    assert api.requests == []
    assert _senales(session) == []
    assert len(cliente.llamadas) == 1
    registro = prefill_status.read_run_status(tmp_path / "prefill-status.json")
    assert registro is not None
    assert (registro.jev_asked, registro.jev_failed, registro.jev_cost_usd, registro.jev_stopped) == (
        0,
        0,
        0.0,
        "",
    )


def test_a6b_apagado_la_salida_del_precalculo_es_la_de_siempre(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")

    _, salida = _correr_con_jev(
        session, tmp_path, [], ApiJev(), ajustes=_ajustes_jev(tmp_path, llm_jev_enabled=False)
    )

    assert "Jev" not in salida


def test_a6b_la_pasada_de_jev_va_antes_del_bucle_de_claude(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")
    orden: list[str] = []
    api = ApiJev()
    cliente = ClienteFalso()

    def gateway(request):
        orden.append("jev")
        return api(request)

    crear = cliente.messages.create

    def claude(**kw):
        orden.append("claude")
        return crear(**kw)

    cliente.messages = SimpleNamespace(create=claude)

    codigo, salida = _correr_con_jev(session, tmp_path, ["--execute"], gateway, cliente=cliente)

    assert codigo == 0
    assert orden == ["jev", "claude"]
    assert len(_senales(session)) == 1
    assert len(_propuestas(session)) == 1
    assert "Jev" in salida
    registro = prefill_status.read_run_status(tmp_path / "prefill-status.json")
    assert registro is not None
    assert (registro.exit_code, registro.done) == (0, 1)
    assert (registro.jev_asked, registro.jev_failed, registro.jev_stopped) == (1, 0, "")
    assert registro.jev_cost_usd == pytest.approx(0.00003)


def test_a6b_jev_no_mira_el_foco(session: Session, catalogo, tmp_path: Path) -> None:
    # El triage se midió sobre todos los textos: uno que no menciona ningún
    # piloto se le pregunta a Jev aunque Claude no lo vea.
    _ingesta(session, texto="vendo zapatos talla 42")
    api = ApiJev()
    cliente = ClienteFalso()

    _correr_con_jev(session, tmp_path, ["--execute"], api, cliente=cliente)

    assert len(api.requests) == 1
    assert cliente.llamadas == []


def test_a6b_jev_corre_aunque_falte_la_clave_de_anthropic(
    session: Session, catalogo, tmp_path: Path
) -> None:
    _ingesta(session, texto="arroz uno")
    api = ApiJev()
    ajustes = _ajustes_jev(tmp_path, anthropic_api_key="")

    codigo, _ = _correr_con_jev(session, tmp_path, ["--execute"], api, ajustes=ajustes)

    # El código de salida es el de siempre para «sin clave de Anthropic».
    assert codigo == 2
    assert len(_senales(session)) == 1
    registro = prefill_status.read_run_status(tmp_path / "prefill-status.json")
    assert registro is not None and registro.jev_asked == 1


@pytest.mark.parametrize(
    ("respuestas", "corte", "llamadas"),
    [
        ([httpx.ConnectError("sin red")] * 2, "", 4),
        ([httpx.Response(401, json={"message": "clave rechazada"})], "auth", 1),
        ([httpx.ReadTimeout("tiempo agotado")] * 2, "", 4),
    ],
    ids=["a-conexion", "b-clave", "c-tiempo"],
)
def test_a6b_caso_5_un_fallo_de_jev_no_frena_a_claude_ni_cambia_la_salida(
    session: Session,
    catalogo,
    tmp_path: Path,
    respuestas: list[Any],
    corte: str,
    llamadas: int,
) -> None:
    for minutos in range(3):
        _ingesta(session, texto=f"arroz {minutos}", minutos=minutos)
    api = ApiJev(respuestas)
    cliente = ClienteFalso()

    codigo, _ = _correr_con_jev(session, tmp_path, ["--execute"], api, cliente=cliente)

    assert codigo == 0
    assert len(api.requests) == llamadas
    # El bucle de Claude corre para los tres.
    assert len(cliente.llamadas) == 3
    registro = prefill_status.read_run_status(tmp_path / "prefill-status.json")
    assert registro is not None
    assert registro.exit_code == 0
    assert registro.jev_stopped == corte
    assert registro.jev_failed == 1


def test_a6b_caso_6_el_tope_de_jev_corta_y_claude_corre(
    session: Session, catalogo, tmp_path: Path
) -> None:
    for minutos in range(3):
        _ingesta(session, texto=f"arroz {minutos}", minutos=minutos)
    api = ApiJev(cost="0.00003")
    cliente = ClienteFalso()
    ajustes = _ajustes_jev(tmp_path, llm_jev_max_cost=0.00005)

    codigo, _ = _correr_con_jev(
        session, tmp_path, ["--execute"], api, cliente=cliente, ajustes=ajustes
    )

    assert codigo == 0
    assert len(api.requests) == 1
    assert len(_senales(session)) == 1
    assert len(cliente.llamadas) == 3
    registro = prefill_status.read_run_status(tmp_path / "prefill-status.json")
    assert registro is not None
    assert registro.jev_stopped == "max_cost"
    assert registro.jev_asked == 1
    # El tope de Jev es aparte: no frena el bucle de Claude.
    assert registro.stopped_by_max_cost is False


def test_a6b_si_la_pasada_de_jev_revienta_el_precalculo_sigue(
    session: Session, catalogo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _romper(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("jev roto")

    monkeypatch.setattr(jev_signal, "prefill_pass", _romper)
    _ingesta(session, texto="arroz uno")
    cliente = ClienteFalso()

    codigo, _ = _correr_con_jev(session, tmp_path, ["--execute"], ApiJev(), cliente=cliente)

    assert codigo == 0
    assert len(cliente.llamadas) == 1


def test_a6b_en_seco_informa_lo_de_jev_sin_red(session: Session, catalogo, tmp_path: Path) -> None:
    _ingesta(session, texto="arroz uno")
    api = ApiJev()

    codigo, salida = _correr_con_jev(session, tmp_path, [], api)

    assert codigo == 0
    assert api.requests == []
    assert "Jev" in salida
    assert "1 texto" in salida


def test_a6b_un_registro_anterior_a_jev_se_sigue_leyendo(tmp_path: Path) -> None:
    ruta = tmp_path / "prefill-status.json"
    ruta.write_text(
        json.dumps(
            {
                "finished_at": "2026-10-03T12:00:00+00:00",
                "exit_code": 0,
                "done": 4,
                "degraded": 0,
                "skipped_no_text": 0,
                "cost_usd": 0.03,
                "stopped_by_max_cost": False,
                "aborted_failure": "",
            }
        ),
        encoding="utf-8",
    )

    registro = prefill_status.read_run_status(ruta)

    assert registro is not None
    assert registro.done == 4
    assert (registro.jev_asked, registro.jev_failed, registro.jev_cost_usd, registro.jev_stopped) == (
        0,
        0,
        0.0,
        "",
    )
