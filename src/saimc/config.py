"""Configuración leída del entorno.

`get_settings` está cacheada porque `Settings()` lee el archivo `.env` en cada
instanciación. La caché la convierte en un singleton perezoso, que es el patrón
habitual en FastAPI: el objeto se arma la primera vez que alguien lo pide y las
llamadas siguientes reciben el mismo. En los tests se limpia con
`get_settings.cache_clear()`.
"""

import logging
import math
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
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
    # Sin clave del proveedor elegido (ver `llm_provider`, más abajo) el
    # asistente degrada a "sin propuesta" y la cola funciona exactamente como
    # hoy — mismo criterio que `waha_api_key`, y por eso la ausencia es un
    # estado soportado y no un error de configuración. `anthropic_api_key` es
    # la clave del proveedor por defecto.
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

    # Por dónde se llama al modelo del asistente (`saimc.llm.provider`,
    # docs/decisions.md 2026-10-09). Este objeto es la ÚNICA fuente: la fábrica
    # no lee `os.environ`, porque pydantic-settings carga el `.env` acá y no lo
    # exporta al entorno del proceso.
    #
    # - `anthropic` (por defecto): la API de Anthropic, directo.
    # - `vercel`: Vercel AI Gateway, que expone la misma API de Messages en
    #   otra URL y con otra clave.
    #
    # Cualquier otro valor deja al asistente sin configurar, igual que si
    # faltara la clave. `python -m saimc.llm.provider` dice cómo quedó.
    llm_provider: str = "anthropic"
    #
    # Un interruptor y, según el proveedor, su clave: `anthropic_api_key` para
    # `anthropic`, la del gateway para `vercel` (`gateway_api_key`, más
    # abajo). La de uno nunca sirve para el otro. No hay clave unificada ni
    # URL configurable: `SAIMC_LLM_API_KEY` y `SAIMC_LLM_BASE_URL` existieron
    # un día y se retiraron porque permitían mezclas inválidas
    # (docs/decisions.md, 2026-10-09). Si siguen en el `.env` se ignoran
    # (`extra="ignore"`), y `python -m saimc.llm.provider` lo avisa.
    #
    # La clave de Vercel AI Gateway, para todo lo que pase por el gateway: el
    # asistente con `llm_provider=vercel` y Jev. Antes se llamaba
    # `llm_jev_api_key` (nació con Jev); ese nombre sigue valiendo y se lee
    # solo por `gateway_api_key` (docs/decisions.md, 2026-10-09).
    vercel_api_key: str | None = None
    # Nombre canónico del modelo, SIN prefijo de proveedor: es el que se
    # registra y con el que se tarifa. El prefijo `anthropic/` que pide el
    # gateway lo agrega la fábrica solo en la llamada. Si está, gana sobre
    # `llm_assist_model`, que queda como nombre anterior y sigue valiendo.
    llm_model: str | None = None
    # Segundos por llamada desde la cola. Si está, gana sobre
    # `llm_assist_timeout` (nombre anterior). El pre-cálculo y el backtest no
    # lo usan: tienen su `--timeout`, más holgado porque nadie espera.
    llm_timeout: float | None = None
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

    # La fábrica de `/marketing` (F2, `docs/spec-slice-fabrica-publicaciones.md`):
    # los JPEG aprobados van a `<marketing_dir>/posts/<id>/`. Bajo `var/`, que
    # está en `.gitignore`, igual que las carpetas diarias del lote anterior.
    marketing_dir: Path = Path("var/marketing")

    # Candado entre corridas reales del pre-cálculo (`extraction/prefill_lock.py`):
    # con otra `--execute` viva, la nueva sale sin gastar. El archivo queda
    # siempre en disco, vacío; lo que cuenta es quién lo tiene abierto.
    prefill_lock_path: Path = Path("data/prefill.lock")

    # Carril 0 encendido (slice B2a, `docs/spec-slice-B2a.md`): el pre-cálculo
    # descarta solo lo que el motor manda al carril 0, y la auditoría semanal
    # (`/curation/auditoria`) lo controla. Uno por tipo porque G1a fijó una
    # meta de falsos descartes para cada uno (10% texto, 20% imagen) y cada
    # tipo se tiene que poder apagar solo. Apagados, el pre-cálculo es el de
    # siempre. Nada los apaga automáticamente: lo hace el operador.
    lane0_text_enabled: bool = False
    lane0_image_enabled: bool = False
    # Jev como segundo juez del carril 0 (slice jev-decisor,
    # `docs/spec-slice-jev-decisor.md`): con el carril 0 de texto encendido,
    # un descarte del modelo pide además que Jev dé P(sí) < 0,2, y Jev puede
    # descartar solo cuando el asistente no propuso. Apagado, Jev sigue en
    # sombra. Se apaga cambiando el `.env`, sin desplegar.
    lane0_jev_enabled: bool = False
    # Jev como árbitro del desacuerdo (slice jev-desacuerdo,
    # `docs/spec-slice-jev-desacuerdo.md`): un texto que el modelo descarta y
    # en el que las reglas ven un piloto se descarta igual si Jev da P(sí) <
    # 0,2. Solo cuenta con `lane0_jev_enabled` también encendido. Nace apagado
    # porque no hay muestra humana del desacuerdo; tiene su propia cota.
    lane0_jev_arbiter_enabled: bool = False

    # Carril 1 encendido (slice B2b, `docs/spec-slice-B2b.md`): el pre-cálculo
    # guarda solo, con `provenance = auto`, las filas de los textos que el
    # motor manda al carril 1, y salen en el tablero sin esperar a nadie.
    # Apagado, el pre-cálculo es el de siempre. Lo aceptado se audita en
    # `/curation/auditoria` y el freno se pone solo (`validation.lane1`).
    lane1_enabled: bool = False
    # Tope de filas por anuncio del carril 1 (`validation.lane1.build_config`).
    # 0 = sin tope, el valor por defecto desde el 2026-10-19 (listas largas de
    # los almacenes, `docs/spec-slice-lane1-sin-tope.md`). Un N positivo
    # reinstala el tope en N sin tocar código; un negativo no arranca.
    lane1_max_rows: int = Field(default=0, ge=0)
    # Avisos al celular (`saimc.alerting`). Sin las dos primeras el comando
    # avisa por consola y sale: no es una falla, es una capacidad sin usar.
    # Un bot solo SALE hacia Telegram; no recibe ni captura nada (decisión
    # cerrada 2). El estado guarda qué ya se avisó para no repetirlo.
    # Días que un anuncio puede esperar en la cola antes de vencer solo
    # (`saimc.queue_expiry`). 0 = sin tope, que es el valor por defecto: un anuncio
    # viejo es histórico y no se descarta solo (2026-10-13, ver `docs/decisions.md`).
    queue_max_age_days: int = 0
    # Acceso al sitio público (`web/access.py`, 2026-10-13). Apagado por defecto.
    # Entra con las credenciales del operador; `site_password` fija otra
    # contraseña que vale solo para el sitio, sin abrir la herramienta.
    site_gate_enabled: bool = False
    site_password: str | None = None
    site_access_days: int = 30
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alerts_state_path: Path = Path("data/alerts-state.json")

    # Seguimiento de la cuenta de Instagram de Guani (`saimc.instagram.sync`,
    # `docs/spec-slice-instagram.md`). Solo lee la cuenta propia.
    #
    # `instagram_access_token` es el token de larga duración del alta: solo
    # siembra `instagram_credentials` la primera vez. Desde ahí el lote lo
    # renueva y guarda el nuevo en la base, porque un `.env` no se reescribe
    # solo; cambiarlo acá después no tiene efecto. Sin token, el lote avisa y
    # sale sin fallar: es una capacidad sin usar, no un error.
    #
    # El id y el secreto de la app son los del alta manual (cambiar el token
    # corto por el largo); el lote no los usa. Viven acá para que el `.env`
    # sea el único lugar donde se anotan.
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    instagram_access_token: str | None = None
    # Segundos por llamada a la API. Nadie espera esta respuesta (corre en el
    # temporizador), pero una llamada colgada no puede frenar la corrida.
    instagram_timeout: float = 15.0
    # Desde qué día cuenta lo auditado para la cota del carril 1 (B2b-3). Es
    # la forma de volver a encenderlo después de que el freno lo apagó por
    # error auditado: el operador fija la fecha y la cota arranca de nuevo.
    # Sin fecha, cuenta todo.
    lane1_audit_since: date | None = None

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
    # Nombre anterior de `vercel_api_key`. Sigue valiendo: rige si la nueva
    # está vacía. No se lee directo, sino por `gateway_api_key`.
    llm_jev_api_key: str | None = None
    # Segundos por llamada. Nadie espera esta respuesta (corre en el
    # pre-cálculo, no en una request), pero una llamada colgada no puede
    # frenar la corrida.
    llm_jev_timeout: float = _JEV_DEFAULTS["llm_jev_timeout"]
    # Tope en USD por corrida del pre-cálculo, aparte del de Claude: ninguno
    # se come el presupuesto del otro. En 0 no se llama (y se sigue anotando
    # lo ya guardado). Una llamada cuesta del orden de 0,00002 a 0,00007 USD.
    llm_jev_max_cost: float = _JEV_DEFAULTS["llm_jev_max_cost"]

    # Origen público del sitio, sin barra final (`https://guani.app`).
    # Lo usan `canonical`, `og:*` y el sitemap, que necesitan URL absolutas
    # (`web/seo.py`). Sin él se usa el host de la petición, que detrás de un
    # proxy es el interno: en producción hay que ponerlo.
    site_url: str | None = None

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
    def gateway_api_key(self) -> tuple[str | None, str]:
        """La clave efectiva de Vercel AI Gateway y la variable de la que salió.

        Único lugar que decide entre los dos nombres: `SAIMC_VERCEL_API_KEY`
        gana; vacía o ausente (vacío es ausente, igual que en
        `saimc.llm.provider`), rige `SAIMC_LLM_JEV_API_KEY`, el nombre
        anterior. Sin ninguna devuelve `None` y el nombre de la nueva, que es
        la que hay que definir. La usan la fábrica del asistente y Jev.

        Devuelve una tupla, que quien llama desarma en dos variables:
        `clave, variable = settings.gateway_api_key()`.
        """
        for field_name in ("vercel_api_key", "llm_jev_api_key"):
            value = (getattr(self, field_name) or "").strip()
            if value:
                return value, f"SAIMC_{field_name.upper()}"
        return None, "SAIMC_VERCEL_API_KEY"

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

    # Mismo motivo que el validador de arriba, con otra salida: acá no hay un
    # valor por defecto que reponer sino un nombre anterior al que caer
    # (`llm_assist_timeout`), así que lo inválido se vuelve `None`. Vacío no es
    # un error —`SAIMC_LLM_TIMEOUT=` es «no lo definí»— y no se avisa; un texto
    # que no es un número positivo sí.
    @field_validator("llm_timeout", mode="before")
    @classmethod
    def _llm_timeout_or_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            number = math.nan
        if math.isfinite(number) and number > 0:
            return number
        # Tampoco acá se registra el valor recibido.
        logger.warning(
            "config.valor_invalido SAIMC_LLM_TIMEOUT no es un número positivo; "
            "se usa SAIMC_LLM_ASSIST_TIMEOUT o su valor por defecto"
        )
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
