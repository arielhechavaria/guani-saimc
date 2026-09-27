"""Redacción de datos personales del texto capturado.

Implementa la regla de `CLAUDE.md`: «Nada de datos personales: no capturamos ni
almacenamos teléfonos, direcciones exactas ni datos de contacto de
anunciantes» (riesgo §10.6 de la especificación).

Alcance deliberado: **solo teléfonos**. Las direcciones exactas son un problema
de reconocimiento de entidades, no de expresiones regulares, y hoy no hay
volumen que justifique resolverlo (mismo criterio que `docs/decisions.md`
2026-08-08 sobre reservar el LLM para lo que las reglas no alcanzan).

Límite conocido y declarado: esto redacta **texto**, no píxeles. La captura de
WhatsApp preservada en `data/raw/` sigue mostrando el número en la imagen. Ese
archivo es la evidencia inmutable de la Transparency Layer (spec.md §6.1,
Etapa 1b) y su tratamiento es la contradicción 1 de `docs/spec-map.md`, todavía
abierta.
"""

from __future__ import annotations

import re

PHONE_PLACEHOLDER = "[teléfono removido]"

# Móvil cubano: 8 dígitos que empiezan en 5. Dos formas, por orden de confianza.
#
# 1. Con prefijo de país explícito, que es como el OCR transcribe el encabezado
#    de un chat de WhatsApp ("+53 5 5550101", o agrupado "+53 5 234 5678"). Se
#    tolera separador delante de cada uno de los 7 dígitos que siguen al "5"
#    inicial, no solo entre "53" y "5", porque el prefijo ya hace el match
#    inequívoco sea cual sea el agrupamiento.
# 2. Suelto, sin prefijo. Acá NO se toleran separadores: un run de 8 dígitos
#    partido por espacios es indistinguible de dos precios seguidos en una
#    lista ("... 500 g 1760.00 ..."), y comerse un precio es peor que dejar un
#    número sin redactar.
#
# Se usa `(?<!\d)` en vez de `\b` a propósito: en el corpus real el número
# aparece pegado a un hashtag ("#llame_escriba_55550303"), donde el guión bajo
# es carácter de palabra y `\b` no encuentra frontera.
_PHONE_RES = (
    re.compile(r"(?<!\d)\+?53[\s.·-]?5(?:[\s.·-]?\d){7}(?!\d)"),
    re.compile(r"(?<!\d)5\d{7}(?!\d)"),
)


def redact_personal_data(text: str | None) -> str | None:
    """Devuelve `text` sin teléfonos, reemplazados por `PHONE_PLACEHOLDER`.

    Idempotente: el marcador no contiene dígitos, así que volver a pasar un
    texto ya redactado no lo cambia. Eso es lo que permite correr el backfill
    más de una vez sin degradar nada.

    `None` y `""` pasan derecho — se llama sobre columnas nulables.
    """
    if not text:
        return text
    for pattern in _PHONE_RES:
        text = pattern.sub(PHONE_PLACEHOLDER, text)
    return text
