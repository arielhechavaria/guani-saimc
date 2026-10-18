"""Texto del post que acompaña a cada tarjeta (F1 de `docs/plan-fabrica-marketing.md`).

Sale de una plantilla con los datos que ya trae el contexto de la tarjeta
—las mismas cifras que la imagen, nunca otro cómputo— más hashtags fijos. El
operador lo puede editar a mano en el editor; esta función es solo el punto
de partida. Privacidad: lista fecha y precio de cada anuncio, y nada más.
"""

from __future__ import annotations

from saimc.marketing.cards import ComoSeMovioCard, DatoDelDiaCard, DeDondeSaleCard
from saimc.marketing.context import CardType

#: Límites de Instagram para el pie de una publicación.
CAPTION_MAX = 2200
HASHTAGS_MAX = 30

#: Fijos a propósito: el tema no cambia de una publicación a otra y un
#: hashtag por producto repartiría el alcance en etiquetas de cola larga.
HASHTAGS: tuple[str, ...] = ("#guani", "#preciosencuba", "#cuba", "#mercadocubano")

_FUENTE = "Fuente: canales de WhatsApp de emprendedores cubanos. Muestra en formación."
_GENERICO = (
    "Parece un relajo, pero tiene su orden. Lo que se anuncia en Cuba, provincia por provincia, "
    "con los anuncios detrás de cada cifra. Guani enseña el precio. Tú decides qué hacer con él."
)


def _anuncios(n: int) -> str:
    return f"{n} anuncio{'s' if n != 1 else ''}"


def _dato_del_dia(card: DatoDelDiaCard) -> str:
    lineas = [
        f"{card.product_name}: precio por {card.unit_noun}, "
        f"semana del {card.week_from} al {card.week_to}.",
        "",
    ]
    for fila in card.rows:
        if not fila.has_data:
            lineas.append(f"{fila.name}: sin datos esta semana")
            continue
        lineas.append(
            f"{fila.name}: {fila.range_text} {card.currency}/{card.unit} "
            f"(mediana {fila.median_text}, {_anuncios(fila.count)})"
        )
    barata = next((f for f in card.rows if f.is_cheapest), None)
    if barata:
        lineas += ["", f"Mediana más baja de la semana: {barata.name}."]
    return "\n".join([*lineas, "", _FUENTE])


def _como_se_movio(card: ComoSeMovioCard) -> str:
    lineas = [
        f"Cómo se movió el {card.product_name} (precio por {card.unit_noun}): "
        f"{card.before_label} frente a {card.now_label}.",
        "",
    ]
    for fila in card.rows:
        if not fila.has_line:
            lineas.append(f"{fila.name}: sin comparación esta semana")
            continue
        lineas.append(
            f"{fila.name}: {fila.before_text} → {fila.now_text} "
            f"{card.currency}/{card.unit} ({fila.change_text})"
        )
    return "\n".join([*lineas, "", _FUENTE])


def _de_donde_sale(card: DeDondeSaleCard) -> str:
    lineas = [
        f"De dónde sale el precio del {card.product_name} en {card.province_name}: "
        f"{card.range_text} {card.currency}/{card.unit}.",
        "",
        f"{_anuncios(card.count)} detrás de esta cifra. Los más recientes:",
    ]
    lineas += [f"- {a.day_text}: {a.price_text} {card.currency}/{card.unit}" for a in card.rows]
    return "\n".join([*lineas, "", "La imagen original de cada anuncio no se publica."])


def build_caption(kind: CardType, context: dict) -> str:
    """El texto del post de `kind` con los datos de `context` (lo que arma
    `build_context`), terminado en los hashtags fijos.

    Nunca pasa de `CAPTION_MAX` caracteres ni de `HASHTAGS_MAX` hashtags: si el
    cuerpo no cabe se recorta, y los hashtags quedan siempre al final."""
    card = context.get("card")
    if kind.slug == "dato-del-dia" and isinstance(card, DatoDelDiaCard):
        cuerpo = _dato_del_dia(card)
    elif kind.slug == "como-se-movio" and isinstance(card, ComoSeMovioCard):
        cuerpo = _como_se_movio(card)
    elif kind.slug == "de-donde-sale" and isinstance(card, DeDondeSaleCard):
        cuerpo = _de_donde_sale(card)
    else:
        cuerpo = _GENERICO

    cola = "\n\n" + " ".join(HASHTAGS[:HASHTAGS_MAX])
    lugar = CAPTION_MAX - len(cola)
    if len(cuerpo) > lugar:
        cuerpo = cuerpo[: lugar - 1].rstrip() + "…"
    return cuerpo + cola
