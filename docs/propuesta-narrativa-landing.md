# Narrativa de la landing — propuesta de copy (sin aprobar)

Slice `landing-narrativa`. Solo texto: ningún cambio de datos y un solo bloque
nuevo de diseño («Para quién es»). Manda `docs/plan-marca-leira-studio.md`
(firma «Guani, por Leira Studio», ningún nombre de persona, solo se afirma lo
que ya funciona). Tuteo, nunca voseo; ningún número compuesto ni «tasa de
automatización».

## Qué es falso hoy (verificado en el código)

El sistema ya no es «una persona revisa todo»: el carril 0 descarta solo lo que
no es un anuncio de precio, el carril 1 acepta solo los anuncios claros (con
auditoría y freno), y el tablero ya dice «24 a mano y 9 por el sistema».

| Dónde | Texto actual | Por qué no es cierto |
|---|---|---|
| Landing, «Por qué puedes fiarte» | «Una persona revisa cada precio… ninguno cuenta hasta que una persona lo revisa» | Una parte entra por el sistema. |
| Landing, tarjetas de respaldo del recorrido | «Los reviso uno por uno» | Ídem. |
| Landing, paso 4 del recorrido | «Una persona lo revisa y lo corrige si hace falta» | Cierto para ese anuncio, falso como regla general. |
| Landing, video `guani-flujo.mp4` | La voz dice el paso 4 anterior | Hay que regrabarlo o retirarlo. |
| Pie compartido (`_public_shell.html`, `public_home.html`) | «verificados a mano, uno por uno. Ninguna cifra de esta página es una estimación» | Ídem; además «ninguna es una estimación» promete de más. |
| `/preguntas` | «los anuncios cuentan cuando una persona los…», «empieza a contar cuando una persona lo revisa» | Ídem. |
| `/terminos` | «Los anuncios se revisan uno por uno» | Ídem. |

`/metodologia` ya está corregida (B3 y B4) y es la fuente: los demás textos
enlazan a ella en vez de repetirla.

## La promesa

- Titular: **«Precios reales del mercado informal cubano.»**
- Bajada: «Guani lee los anuncios donde ya se vende, los pasa a una misma unidad
  y te los enseña con su cuenta. Una máquina lee; una persona supervisa.»
- Se quita «Hola, soy Guani»: es una herramienta, no un personaje.
- Botón: «Ver los precios de hoy» (sin cambio).

## El recorrido: cuatro pasos (sustituye a «Así trabajo»)

1. **Escucha.** Lee los grupos y canales donde ya se vende. Nadie tiene que
   escribirle nada.
2. **Entiende.** Un asistente saca producto, precio, cantidad y provincia, y lo
   lleva todo a la misma unidad y moneda.
3. **Filtra.** Aparta lo que no es un anuncio de precio y frena lo dudoso. Lo
   difícil lo decide una persona; lo claro, el sistema lo acepta y una persona
   lo audita después.
4. **Te lo enseña con su cuenta.** Rango, mediana y cuántos anuncios hay detrás,
   separando los revisados a mano de los aceptados por el sistema.

Las cinco capturas de hoy cuentan **un** anuncio de punta a punta y siguen
siendo ciertas. Propuesta: se quedan, con el pie «Así pasó con un anuncio real»,
y se corrige solo el texto del paso 4 de ese pase («Si hay dudas lo revisa una
persona; si es claro, el sistema lo acepta y una persona lo audita después»).

## Por qué puedes fiarte (cinco tarjetas, una cambia)

- **Cada cifra enseña sus anuncios.** Sin cambio.
- **Provincia por provincia.** Sin cambio.
- **Compara lo comparable.** Sin cambio.
- **Sabes quién la revisó** (reemplaza «Una persona revisa cada precio»):
  «Al lado de cada cifra va cuántos anuncios revisó una persona y cuántos
  aceptó el sistema solo. Y una persona audita lo que el sistema aceptó.»
- **Un precio disparatado no manda.** Sin cambio.
- Cierre: «Preferimos mostrar poco y cierto antes que mucho y flojo. Todo el
  cálculo está en [Cómo calculamos los precios].»

## Para quién es (sección nueva, tres líneas)

- **Si compras o vendes:** ¿cuánto cuesta esto hoy en mi provincia?
- **Si revendes o decides:** qué se mueve y dónde.
- **Si investigas o informas:** los datos en CSV, con su metodología.

Cada línea enlaza a su vista (tablero, mapa y Explorar, descarga).

## Orden de la página

1. Promesa y precio de ejemplo.
2. «Así se lee un precio».
3. El recorrido en cuatro pasos (con las cinco capturas como ejemplo).
4. Por qué puedes fiarte.
5. Para quién es.
6. Entra por donde quieras.

## Pie, `/preguntas` y `/terminos`

- Pie: «Cada cifra dice de cuántos anuncios sale y cuántos revisó una persona.
  Es una muestra en formación, no un censo.»
- `/preguntas`: dos frases pasan a «los anuncios cuentan cuando una persona los
  revisa **o el sistema los acepta con auditoría**», con enlace a `/metodologia`.
- `/terminos`: «Los anuncios se revisan uno por uno» pasa a «Los anuncios los
  revisa una persona o los acepta el sistema con auditoría, pero aun así…».

## Decisiones que pide

1. **El video del recorrido.** Su voz cuenta el recorrido viejo. Propuesta: se
   retira de la página hasta regrabarlo (el kit está en
   `~/guani/landing-video-kit/`) y el archivo queda en el repo.
2. **Cuatro pasos nuevos + cinco capturas como ejemplo**, o rehacer las capturas
   para que sean cuatro. Propuesta: lo primero, que no cuesta nada.
3. **«Para quién es»** es lo único que agrega diseño: se prototipa a 390 px antes
   de programarlo.

## Fuera de alcance

Rehacer el diseño de la landing, `/metodologia` (ya corregida), el pie fuera de
estas frases y la presentación para inversores.
