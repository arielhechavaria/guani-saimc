"""Configuración leída del entorno.

`get_settings` está cacheada porque `Settings()` lee el archivo `.env` en cada
instanciación. La caché la convierte en un singleton perezoso, que es el patrón
habitual en FastAPI: el objeto se arma la primera vez que alguien lo pide y las
llamadas siguientes reciben el mismo. En los tests se limpia con
`get_settings.cache_clear()`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Procedencia del `price_basis` retro-completado por inferencia en las
    # filas anteriores al campo (docs/decisions.md, 2026-09-02). Mismo lugar y
    # mismo motivo que `llm_assist_log_path`: ata ids de la base de producción,
    # así que vive bajo `data/` —que `.gitignore` excluye— y no en `docs/`. Es
    # lo que mantiene distinguible el basis inferido del que curó el operador
    # sin necesidad de una columna nueva.
    basis_backfill_log_path: Path = Path("data/basis-backfill.jsonl")


@lru_cache
def get_settings() -> Settings:
    return Settings()
