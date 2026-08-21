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


def _write_media_to_inbox(mensaje: ParsedMessage, inbox_dir: Path) -> Path | None:
    """Baja la foto de WAHA y la deja en la bandeja, o `None` si no se pudo.

    La descarga es sincrónica dentro del webhook a propósito: WAHA corre en el
    mismo VPS, así que es una lectura de disco local disfrazada de HTTP. Meter
    una cola acá rompería la decisión cerrada 3 (nada de Redis ni Celery en
    Fase 0) para resolver una latencia que no existe.

    Todos los caminos de falla devuelven `None` y el webhook contesta 200: una
    foto perdida es barata, un 500 por evento hace que WAHA reintente en loop.
    """
    settings = get_settings()
    if mensaje.media_url is None or mensaje.media_suffix is None:
        return None
    if not is_trusted_media_url(mensaje.media_url, settings.waha_base_url):
        return None
    if not settings.waha_api_key:
        return None

    contenido = fetch_media(
        mensaje.media_url,
        settings.waha_api_key,
        timeout=settings.waha_timeout,
    )
    if contenido is None:
        return None

    destino = inbox_dir / f"whatsapp-{uuid.uuid4().hex}{mensaje.media_suffix}"
    destino.write_bytes(contenido)
    return destino


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
        return JSONResponse({"ingested": 0, "reason": "json inválido"})

    mensaje = parse_message(body)
    if mensaje is None:
        return JSONResponse({"ingested": 0, "reason": "sin mensaje utilizable"})

    channel = db.execute(
        select(Channel).where(
            Channel.external_id == mensaje.chat_id, Channel.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if channel is None:
        return JSONResponse({"ingested": 0, "reason": "canal no registrado o inactivo"})

    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    if mensaje.text:
        temporal = settings.inbox_dir / f"whatsapp-{uuid.uuid4().hex}.txt"
        temporal.write_text(mensaje.text, encoding="utf-8")
    else:
        # Foto sin caption (docs/decisions.md, 2026-08-21): 183 de 200
        # mensajes de un grupo que publica solo fotos, medido el 2026-08-18.
        temporal = _write_media_to_inbox(mensaje, settings.inbox_dir)
        if temporal is None:
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
        )
    finally:
        temporal.unlink(missing_ok=True)

    return JSONResponse({"ingested": len(report.ingested)})
