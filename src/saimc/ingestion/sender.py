"""Emisor de un mensaje de grupo como hash con clave (slice B5).

`docs/spec-slice-B5.md`. En un grupo de WhatsApp el emisor viaja en
`participant`, y su JID suele ser el número de teléfono: guardarlo en claro
viola `spec.md` §10.6. Lo que se guarda es `HMAC-SHA256(clave, JID
normalizado)` en hexadecimal.

HMAC con clave y no un SHA-256 pelado: un móvil cubano tiene ocho dígitos, y
recorrer todos los números posibles contra un hash sin clave lleva minutos.

Funciones puras: no tocan la base, el filesystem ni la configuración. La clave
la pasa quien llama. Nada de este módulo registra el JID, el hash ni la clave.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

# Largo mínimo de `SAIMC_SENDER_HASH_KEY`. Una clave más corta se trata como
# ausente: el hash quedaría al alcance de la misma fuerza bruta que la clave
# existe para impedir.
MIN_KEY_LENGTH = 32

# WhatsApp nombra al mismo número con dos servidores según por dónde llegue el
# mensaje. Se igualan para que el webhook y el backfill den el mismo hash.
_SERVER_ALIASES = {"s.whatsapp.net": "c.us"}


def _normalize(jid: object) -> str | None:
    """JID en su forma canónica, o `None` si no tiene forma de JID.

    Minúsculas, sin el sufijo de dispositivo (`5355512345:3@…` es el mismo
    número desde su tercer dispositivo) y con `@s.whatsapp.net` igualado a
    `@c.us`. `@lid` NO se iguala a nada: es otro identificador y no hay forma
    de unirlo con el número sin guardar la correspondencia.
    """
    if not isinstance(jid, str):
        return None
    user, separator, server = jid.strip().lower().partition("@")
    user = user.partition(":")[0]
    if not user or not separator or not server:
        return None
    return f"{user}@{_SERVER_ALIASES.get(server, server)}"


def sender_hash(jid: object, key: str | None) -> str | None:
    """Hash del emisor, o `None` si no hay emisor o no hay clave utilizable.

    `None` es un resultado normal, no un error: un canal de difusión no tiene
    emisor, y sin clave el sistema sigue ingiriendo igual.
    """
    if not key or len(key) < MIN_KEY_LENGTH:
        return None
    normalized = _normalize(jid)
    if normalized is None:
        return None
    return hmac.new(key.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def warn_if_key_too_short(key: str | None) -> None:
    """Avisa, al arrancar, de una clave configurada pero demasiado corta.

    Sin clave no se avisa: es un estado soportado. Una clave corta sí, porque
    el operador cree que el emisor se está guardando y no es así. El mensaje
    nunca incluye la clave.
    """
    if key and len(key) < MIN_KEY_LENGTH:
        logger.warning(
            "SAIMC_SENDER_HASH_KEY tiene menos de %d caracteres: se trata como ausente "
            "y el emisor de los grupos no se guarda.",
            MIN_KEY_LENGTH,
        )
