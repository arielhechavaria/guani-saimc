"""Sección educativa: lecciones cortas para el carrusel diario «Educativo».

Cada lección es un carrusel de 4–5 diapositivas de la tarjeta `leccion`
(bloques libres de `options.FREE_TYPES`: título, texto, llamada). Es texto
fijo en código, sin tabla ni migración: cambiar una lección es editar este
archivo. Sin cifras de precios ni porcentajes de precisión; la voz sigue
`docs/narrativa-guani.md` (tuteo, Guani enseña y no aconseja).

`lesson_for_day` rota por día igual que `daily.candidate_order`: determinista,
así que volver a correr el lote no cambia de lección.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from saimc.marketing.options import FreeBlock

LESSON_CARD = "leccion"


def _t(text: str) -> FreeBlock:
    return FreeBlock("titulo", text)


def _p(text: str) -> FreeBlock:
    return FreeBlock("texto", text)


def _c(text: str) -> FreeBlock:
    return FreeBlock("chip", text)


_SEP = FreeBlock("separador")


@dataclass(frozen=True)
class Lesson:
    slug: str
    title: str
    #: Una tupla de bloques libres por diapositiva, en orden.
    slides: tuple[tuple[FreeBlock, ...], ...]
    #: Texto del post (sin hashtags: los pone `caption_for`).
    summary: str


LESSONS: tuple[Lesson, ...] = (
    Lesson(
        slug="de-los-grupos-a-la-cifra",
        title="De los grupos a la cifra",
        summary=(
            "Así llega un anuncio a ser un precio en Guani: se captura, se lee, se valida, "
            "se agrupa y se muestra como rango. Desliza y mira cada paso."
        ),
        slides=(
            (_t("De los grupos a la cifra"), _SEP, _p("Cómo un anuncio llega a ser un precio."),
             _c("Desliza")),
            (_t("1. Se captura"),
             _p("Todo empieza con un anuncio que alguien publicó en un grupo o canal. "
                "Guani guarda el original tal como salió.")),
            (_t("2. Se lee"),
             _p("De cada anuncio se sacan tres cosas: qué producto es, cuánto cuesta y en qué "
                "provincia se ofrece.")),
            (_t("3. Se valida"),
             _p("Un anuncio que no se entiende, o con un precio disparatado, se aparta con una "
                "regla publicada en la metodología. No entra en la cifra.")),
            (_t("4. Se muestra como rango"),
             _p("Los anuncios del mismo producto y la misma provincia se agrupan. Lo que ves es "
                "un rango, y detrás de él, los anuncios."),
             _c("Guarda esto")),
        ),
    ),
    Lesson(
        slug="rango-y-mediana",
        title="Rango y mediana",
        summary=(
            "Rango y mediana: qué es cada uno y por qué un solo anuncio puede engañar. "
            "Guani enseña el precio. Tú decides qué hacer con él."
        ),
        slides=(
            (_t("Rango y mediana"), _SEP,
             _p("Dos palabras que verás en cada tarjeta de precios."), _c("Desliza")),
            (_t("El rango"),
             _p("Va del precio más bajo al más alto que se vio en los anuncios. Te dice entre qué "
                "valores se mueve el producto.")),
            (_t("La mediana"),
             _p("Es el precio del medio: si ordenas todos los anuncios de menor a mayor, el que "
                "queda justo en el centro.")),
            (_t("Un solo anuncio puede engañar"),
             _p("Uno puede ser un caso raro: un error, una oferta única, un precio de otra "
                "cosa. Por eso Guani mira el conjunto y no el anuncio suelto.")),
            (_t("Mira las dos juntas"),
             _p("El rango muestra hasta dónde llega el mercado. La mediana muestra dónde se "
                "junta la mayoría."),
             _c("Guarda esto")),
        ),
    ),
    Lesson(
        slug="muestra-en-formacion",
        title="Muestra en formación",
        summary=(
            "Qué significa «muestra en formación» y por qué lo ves rotulado: pocos anuncios "
            "todavía. Desliza para entenderlo."
        ),
        slides=(
            (_t("Muestra en formación"), _SEP,
             _p("Qué quiere decir este rótulo y por qué lo ponemos."), _c("Desliza")),
            (_t("Pocos anuncios todavía"),
             _p("Cuando una cifra sale de pocos anuncios, Guani la marca como muestra en "
                "formación. Se publica, pero con ese aviso.")),
            (_t("¿Por qué se rotula?"),
             _p("Con pocos anuncios, uno solo mueve mucho el resultado. Preferimos avisarte "
                "a hacer pasar por firme una cifra que todavía no lo es.")),
            (_t("Cada cifra dice cuántos anuncios tiene"),
             _p("Siempre ves cuántos anuncios hay detrás. Así sabes cuánto pesa la cifra "
                "antes de usarla.")),
            (_t("Crece con la comunidad"),
             _p("Cuantos más anuncios se sumen, más estable es la cifra y el rótulo "
                "desaparece solo."),
             _c("Mándaselo a quien hace las compras")),
        ),
    ),
    Lesson(
        slug="cada-precio-con-sus-anuncios",
        title="Cada precio con sus anuncios",
        summary=(
            "Cómo comprobar una cifra de Guani abriendo los anuncios que la sostienen. "
            "Cada precio con su cuenta."
        ),
        slides=(
            (_t("Cada precio con sus anuncios"), _SEP,
             _p("Cómo comprobar una cifra por tu cuenta."), _c("Desliza")),
            (_t("Empieza por la cifra"),
             _p("En el sitio, cada precio viene con la cantidad de anuncios que lo sostienen. "
                "Esa cantidad es la puerta de entrada.")),
            (_t("Abre los anuncios"),
             _p("Desde el precio puedes ver los anuncios de texto que lo forman: la fecha y el "
                "precio de cada uno.")),
            (_t("Haz tu propia cuenta"),
             _p("Mira los anuncios y fíjate si el rango tiene sentido. Si un caso te "
                "parece raro, ya sabes dónde buscarlo.")),
            (_t("Comprobar es parte del trato"),
             _p("Guani no te pide fe: te enseña de dónde sale cada cifra para que la "
                "compruebes."),
             _c("Guarda esto")),
        ),
    ),
)


def lesson_for_day(day: date) -> Lesson:
    """La lección que toca el `day`: rota una por día, determinista."""
    return LESSONS[day.toordinal() % len(LESSONS)]
