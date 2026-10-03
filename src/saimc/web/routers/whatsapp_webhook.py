"""Recibe eventos de mensaje de la instancia de WAHA del operador.

Automatización de canales de WhatsApp (`docs/decisions.md`, 2026-08-15):
apartamiento consciente de la decisión cerrada 2 (nada de scraping/bots en
Fase 0) y de la decisión cerrada 4 (nada de Docker en Fase 0) — WAHA corre en
Docker en el VPS del operador, y este router es el único punto de contacto
entre esa instancia y SAIMC.

No es un camino de ingesta paralelo: escribe el texto del mensaje como archivo
temporal y llama al mismo `ingest_inbox` que usan la bandeja y el pegado
(`saimc.web.routers.curation.paste_ads`), con los overrides de procedencia que
`ingest_file` acepta desde este mismo slice. Así hereda el hash de contenido,
la deduplicación y la redacción de datos personales sin duplicar esa lógica.

No va detrás de `require_operator`: WAHA no puede resolver un diálogo de HTTP
Basic. La autenticación es la firma HMAC que WAHA calcula sobre el body crudo
(`config.hmac.key` del lado de WAHA, `whatsapp_webhook_hmac_key` acá) — mismo
criterio de "cierra en falso" que `require_operator`: sin clave configurada,
se rechaza todo.
"""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion.inbox import ingest_inbox
from saimc.ingestion.whatsapp_media import fetch_media, is_trusted_media_url
from saimc.ingestion.whatsapp_webhook import ParsedMessage, parse_message, verify_hmac
from saimc.models import Channel
from saimc.models.raw_ingestion import SourceType
from saimc.web.deps import get_db

router = APIRouter()

logger = logging.getLogger(__name__)


def _contexto(*, jid: str | None = None, mensaje: ParsedMessage | None = None) -> str:
    """Metadatos del evento para el log. NUNCA el cuerpo del mensaje.

    El log no es la capa pública, pero el texto de un anuncio trae teléfonos y
    el proyecto no almacena datos de contacto (spec.md §10.6). Con el JID y la
    marca de tiempo alcanza para cruzar contra WAHA y encontrar el mensaje
    exacto, que es para lo que sirve este rastro.

    El JID es el del CHAT (canal o grupo). El emisor de un mensaje de grupo no
    entra acá, ni en claro ni como hash (`docs/spec-slice-B5.md`).
    """
    partes = [f"jid={jid or (mensaje.chat_id if mensaje else '?')}"]
    if mensaje is not None:
        partes.append(f"ts={mensaje.timestamp.isoformat()}")
        partes.append(f"media={'si' if mensaje.has_media else 'no'}")
        if mensaje.media_suffix:
            partes.append(f"tipo={mensaje.media_suffix}")
    return " ".join(partes)


def _write_media_to_inbox(
    mensaje: ParsedMessage, inbox_dir: Path
) -> tuple[Path | None, str | None]:
    """Baja la foto de WAHA y la deja en la bandeja.

    Devuelve `(ruta, None)` si pudo, o `(None, motivo)` si no. El motivo se
    devuelve en vez de perderse porque los cuatro caminos de falla son
    indistinguibles desde afuera y llevan a arreglos distintos: que WAHA no
    mande la URL no es lo mismo que que la URL apunte a otro host, ni que la
    descarga falle. `whatsapp_media` ya avisaba de este modo de falla — «no
    rompe nada y no captura nada».

    La descarga es sincrónica dentro del webhook a propósito: WAHA corre en el
    mismo VPS, así que es una lectura de disco local disfrazada de HTTP. Meter
    una cola acá rompería la decisión cerrada 3 (nada de Redis ni Celery en
    Fase 0) para resolver una latencia que no existe.

    Ningún camino de falla levanta: el webhook contesta 200 igual. Una foto
    perdida es barata, un 500 por evento hace que WAHA reintente en loop.
    """
    settings = get_settings()
    if mensaje.media_url is None or mensaje.media_suffix is None:
        return None, "media_sin_url_descargable"
    if not is_trusted_media_url(mensaje.media_url, settings.waha_base_url):
        return None, "media_url_no_confiable"
    if not settings.waha_api_key:
        return None, "sin_api_key_de_waha"

    contenido = fetch_media(
        mensaje.media_url,
        settings.waha_api_key,
        timeout=settings.waha_timeout,
    )
    if contenido is None:
        return None, "descarga_fallida"

    destino = inbox_dir / f"whatsapp-{uuid.uuid4().hex}{mensaje.media_suffix}"
    destino.write_bytes(contenido)
    return destino, None


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Procesa un evento `message`/`message.any` de WAHA.

    Devuelve 200 en todos los casos que WAHA no debería reintentar (firma
    mala aparte): canal no registrado, canal apagado, mensaje sin texto
    utilizable. Solo la falla de autenticación devuelve un código de error,
    porque ahí sí hay algo que corregir del lado de la configuración.
    """
    settings = get_settings()
    if not settings.whatsapp_webhook_hmac_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY no configurado",
        )

    raw_body = await request.body()
    signature = request.headers.get("X-Webhook-Hmac", "")
    if not verify_hmac(settings.whatsapp_webhook_hmac_key.encode(), raw_body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Firma inválida")

    try:
        body = await request.json()
    except ValueError:
        logger.warning("webhook.descartado motivo=json_invalido")
        return JSONResponse({"ingested": 0, "reason": "json inválido"})

    mensaje = parse_message(body, settings.sender_hash_key)
    if mensaje is None:
        # Acá muere la foto que WAHA anunció con `hasMedia` pero sin `media.url`,
        # y también el sticker webp. Los dos son pérdidas reales de captura.
        jid = body.get("payload", body).get("from") if isinstance(body, dict) else None
        logger.warning(
            "webhook.descartado motivo=sin_mensaje_utilizable %s", _contexto(jid=jid)
        )
        return JSONResponse({"ingested": 0, "reason": "sin mensaje utilizable"})

    channel = db.execute(
        select(Channel).where(
            Channel.external_id == mensaje.chat_id, Channel.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if channel is None:
        # El JID es lo único que hace falta para dar de alta el canal que falta.
        logger.warning(
            "webhook.descartado motivo=canal_no_registrado %s", _contexto(mensaje=mensaje)
        )
        return JSONResponse({"ingested": 0, "reason": "canal no registrado o inactivo"})

    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    if mensaje.text:
        temporal = settings.inbox_dir / f"whatsapp-{uuid.uuid4().hex}.txt"
        temporal.write_text(mensaje.text, encoding="utf-8")
    else:
        # Foto sin caption (docs/decisions.md, 2026-08-21): 183 de 200
        # mensajes de un grupo que publica solo fotos, medido el 2026-08-18.
        temporal, motivo = _write_media_to_inbox(mensaje, settings.inbox_dir)
        if temporal is None:
            logger.warning(
                "webhook.descartado motivo=%s canal=%s %s",
                motivo,
                channel.name,
                _contexto(mensaje=mensaje),
            )
            return JSONResponse({"ingested": 0, "reason": "no se pudo traer el archivo"})
    try:
        report = ingest_inbox(
            db,
            settings.inbox_dir,
            settings.raw_store_dir,
            paths=[temporal],
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference=channel.name,
            captured_at=mensaje.timestamp,
            channel_id=channel.id,
            sender_hash=mensaje.sender_hash,
        )
    finally:
        temporal.unlink(missing_ok=True)

    if report.ingested:
        logger.info(
            "webhook.ingerido canal=%s %s", channel.name, _contexto(mensaje=mensaje)
        )
    elif report.duplicates:
        # INFO y no WARNING: WAHA manda DOS eventos por cada mensaje (los POST
        # llegan de a pares en el journal del VPS), así que el duplicado es la
        # mitad del tráfico normal y no una pérdida.
        logger.info(
            "webhook.descartado motivo=duplicado canal=%s %s",
            channel.name,
            _contexto(mensaje=mensaje),
        )
    else:
        # `skipped`: extensión que `classify` no reconoce. No debería pasar,
        # porque el sufijo lo elige este mismo módulo.
        logger.warning(
            "webhook.descartado motivo=ingesta_salteada canal=%s %s",
            channel.name,
            _contexto(mensaje=mensaje),
        )

    return JSONResponse({"ingested": len(report.ingested)})
