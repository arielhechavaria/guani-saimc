"""Configuración leída del entorno.

`get_settings` está cacheada porque `Settings()` lee el archivo `.env` en cada
instanciación. La caché la convierte en un singleton perezoso, que es el patrón
habitual en FastAPI: el objeto se arma la primera vez que alguien lo pide y las
llamadas siguientes reciben el mismo. En los tests se limpia con
`get_settings.cache_clear()`.
"""

import logging
import math
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Valores por defecto de los dos números de Jev. Viven afuera de la clase
# porque también los usa el validador que los repone cuando el `.env` trae
# algo que no es un número.
_JEV_DEFAULTS: dict[str, float] = {"llm_jev_timeout": 10.0, "llm_jev_max_cost": 0.01}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAIMC_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://saimc:saimc@localhost:5432/saimc"

    # Directorio observado donde el operador deposita capturas y textos.
    inbox_dir: Path = Path("inbox")

    # Evidencia preservada, nombrada por hash de contenido.
    raw_store_dir: Path = Path("data/raw")

    # Token bearer de la API TRMI de elTOQUE (spec.md §6.3 E). Ya provisto en
    # `.env` como `SAIMC_EL_TOQUE_API`; sin él, la resolución de tasa
    # degrada a "sin tasa" en vez de fallar (no hay feature flag separado).
    el_toque_api: str | None = None
    # Timeout corto (design.md, D4): un fetch lento nunca debe demorar el
    # guardado de un anuncio, degrada a "sin tasa" en cuanto expira.
    el_toque_timeout: float = 3.0

    # HTTP Basic para `/curation` (decisions.md, 2026-08-13): la decisión de
    # "sin auth en Fase 0" asumía despliegue local; el despliegue real expone
    # uvicorn directo a internet, sin proxy, así que `/evidence/{id}` servía
    # capturas con datos personales a cualquiera con la IP. Sin password
    # configurado, `require_operator` niega todo (cierra en falso, no abre).
    curation_username: str = "operador"
    curation_password: str | None = None

    # Clave HMAC para verificar que un webhook entrante viene realmente de la
    # instancia de WAHA del operador (docs/decisions.md, 2026-08-15 — canales
    # de WhatsApp automatizados). Se configura como `config.hmac.key` del lado
    # de WAHA. Sin clave configurada, `/webhooks/whatsapp` rechaza todo: mismo
    # criterio de "cierra en falso" que `curation_password`.
    whatsapp_webhook_hmac_key: str | None = None

    # Clave del hash del emisor de los mensajes de grupo (slice B5,
    # `docs/spec-slice-B5.md`): `raw_ingestions.sender_hash` es
    # HMAC-SHA256(esta clave, JID del emisor). Al menos 32 caracteres; más
    # corta se trata como ausente y se avisa al arrancar. Sin clave el hash
    # queda nulo y la ingesta sigue igual: es un estado soportado. Rotarla o
    # perderla corta el historial por vendedor, así que va en el respaldo de
    # `.env`. No tiene nada que ver con `whatsapp_webhook_hmac_key`, que
    # autentica a WAHA.
    sender_hash_key: str | None = None

    # Datos para que SAIMC llame DE VUELTA a la API de WAHA (backfill de
    # histórico desde /catalog, docs/decisions.md, 2026-08-17) — dirección
    # opuesta al webhook, que es WAHA llamando a SAIMC. Sin `waha_base_url` o
    # `waha_api_key`, el botón de /catalog degrada a un mensaje de error, no
    # a una excepción. `waha_session` es el nombre de sesión de WAHA, "default"
    # salvo que el operador haya creado otra.
    waha_base_url: str | None = None
    waha_api_key: str | None = None
    waha_session: str = "default"
    # Mismo criterio que `el_toque_timeout`: un fetch lento no debe colgar la
    # request del operador — degrada a error en vez de esperar indefinido.
    waha_timeout: float = 30.0

    # Cuánto tiempo alrededor de un mensaje sigue siendo el MISMO anuncio
    # (docs/decisions.md, 2026-08-21). En los grupos y canales reales el
    # vendedor parte el anuncio en varios mensajes seguidos: la foto o el
    # nombre del producto en uno, el precio en el siguiente. Los dos pares
    # medidos en producción están a 22 segundos y a 4m33s; diez minutos los
    # cubre con margen sin fundir publicaciones de la mañana con las de la
    # tarde. Configurable porque el ritmo de publicación varía por fuente y
    # el número correcto solo se conoce con más curaduría encima.
    burst_window_minutes: int = 10

    # Asistente de extracción por LLM (`saimc.extraction.llm_assist`,
    # openspec/changes/llm-extraction-assist). Solo llena los campos que las
    # reglas dejaron sin resolver, y solo cuando el operador lo pide.
    #
    # Sin `anthropic_api_key` el asistente degrada a "sin propuesta" y la cola
    # funciona exactamente como hoy — mismo criterio que `waha_api_key`, y por
    # eso la ausencia es un estado soportado y no un error de configuración.
    #
    # **El costo es adicional al plan Pro del operador**: la API se factura
    # aparte, por token (docs/costos.md).
    anthropic_api_key: str | None = None
    # Configurable para que cambiar de modelo nunca sea un cambio de código.
    # Sonnet por defecto: lo que llega a esta capa es el residuo ambiguo que
    # las reglas no supieron, y la diferencia con el tier más barato es menos
    # de un dólar al mes a este volumen (design.md, D7).
    llm_assist_model: str = "claude-sonnet-5"
    # Mismo criterio que `el_toque_timeout` y `waha_timeout`: la llamada vive
    # dentro de una request del operador, así que expira en vez de colgarla.
    llm_assist_timeout: float = 30.0
    # Registro de solo-anexado de cada invocación del asistente. Vive bajo
    # `data/` —que `.gitignore` excluye— y no en `docs/` como
    # `h0-runs.jsonl`: son ~600 líneas al mes atadas a ids de la base de
    # producción, que no significan nada en otro checkout. Es lo que permite
    # medir H0 sobre el conjunto no asistido (design.md, D6).
    llm_assist_log_path: Path = Path("data/llm-assist-log.jsonl")

    # Cómo terminó la última corrida real del pre-cálculo (slice C1): lo escribe
    # `extraction/prefill.py` y lo lee la franja de `/control`. Bajo `data/`
    # por el mismo motivo que `llm_assist_log_path`.
    prefill_status_path: Path = Path("data/prefill-status.json")

    # Carril 0 encendido (slice B2a, `docs/spec-slice-B2a.md`): el pre-cálculo
    # descarta solo lo que el motor manda al carril 0, y la auditoría semanal
    # (`/curation/auditoria`) lo controla. Uno por tipo porque G1a fijó una
    # meta de falsos descartes para cada uno (10% texto, 20% imagen) y cada
    # tipo se tiene que poder apagar solo. Apagados, el pre-cálculo es el de
    # siempre. Nada los apaga automáticamente: lo hace el operador.
    lane0_text_enabled: bool = False
    lane0_image_enabled: bool = False

    # Jev como señal en sombra (slice A6b, `docs/spec-slice-A6b.md`): el
    # pre-cálculo le hace tres preguntas cerradas a cada texto pendiente y la
    # respuesta se anota en `validation_detail` al curar. Solo anota: no cambia
    # el estado, el carril ni la procedencia de ninguna fila.
    #
    # «Activo» es interruptor encendido Y clave no vacía
    # (`validation.jev_signal.is_active`). Apagado, o encendido sin clave, el
    # sistema se comporta exactamente como antes de A6b: es un estado
    # soportado, mismo criterio que `anthropic_api_key`.
    #
    # La URL y el modelo no están acá a propósito: son constantes de
    # `validation/jev_client.py`, porque cambiarlos cambia la huella de las
    # preguntas y pide medir de nuevo.
    llm_jev_enabled: bool = False
    llm_jev_api_key: str | None = None
    # Segundos por llamada. Nadie espera esta respuesta (corre en el
    # pre-cálculo, no en una request), pero una llamada colgada no puede
    # frenar la corrida.
    llm_jev_timeout: float = _JEV_DEFAULTS["llm_jev_timeout"]
    # Tope en USD por corrida del pre-cálculo, aparte del de Claude: ninguno
    # se come el presupuesto del otro. En 0 no se llama (y se sigue anotando
    # lo ya guardado). Una llamada cuesta del orden de 0,00002 a 0,00007 USD.
    llm_jev_max_cost: float = _JEV_DEFAULTS["llm_jev_max_cost"]

    # Contacto y redes del pie de la landing (`GET /`, 2026-10-05). Los cuatro
    # son opcionales y se dibuja solo el que esté puesto; sin ninguno, el pie
    # no muestra esa columna. Los tres enlaces tienen que empezar con
    # `http://` o `https://` (`web/landing_view.build_footer_links` ignora lo
    # demás). Son del proyecto, nunca de un anunciante.
    link_facebook: str | None = None
    link_whatsapp: str | None = None
    link_telegram: str | None = None
    contact_email: str | None = None

    # Procedencia del `price_basis` retro-completado por inferencia en las
    # filas anteriores al campo (docs/decisions.md, 2026-09-02). Mismo lugar y
    # mismo motivo que `llm_assist_log_path`: ata ids de la base de producción,
    # así que vive bajo `data/` —que `.gitignore` excluye— y no en `docs/`. Es
    # lo que mantiene distinguible el basis inferido del que curó el operador
    # sin necesidad de una columna nueva.
    basis_backfill_log_path: Path = Path("data/basis-backfill.jsonl")

    # `mode="before"` corre antes de que pydantic convierta el texto a número:
    # es el único lugar donde un «abc» todavía se puede atajar. Sin esto, un
    # `.env` mal escrito levanta al instanciar `Settings` y tumba la aplicación
    # entera por una señal que solo anota.
    @field_validator("llm_jev_timeout", "llm_jev_max_cost", mode="before")
    @classmethod
    def _jev_number_or_default(cls, value: object, info: ValidationInfo) -> object:
        name = info.field_name or ""
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            number = math.nan
        # El tiempo límite tiene que ser positivo; el tope admite 0 («no llamar»).
        valid = math.isfinite(number) and (number > 0 or (number == 0 and name == "llm_jev_max_cost"))
        if valid:
            return number
        default = _JEV_DEFAULTS[name]
        # No se registra el valor recibido: es un `.env`, y un renglón corrido
        # podría dejar ahí una clave.
        logger.warning(
            "config.valor_invalido SAIMC_%s no es un número válido; se usa %s", name.upper(), default
        )
        return default


@lru_cache
def get_settings() -> Settings:
    return Settings()
