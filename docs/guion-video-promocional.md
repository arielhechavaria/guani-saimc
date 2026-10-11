# Guion del video promocional de Guani

Estado: **propuesta, sin aprobar** (decisión 1 resuelta: 720×1280, ~1 MB). Escrito por un subagente Opus; verificado contra el código por él, no por el operador.

Reemplaza a `guani-flujo.mp4` (retirado con `FLOW_VIDEO_ENABLED = False`, entrada de `docs/decisions.md` del 2026-10-11 «La landing cuenta el recorrido real»). Solo guion: ni audio, ni video, ni código.

Manda `docs/plan-marca-leira-studio.md`: firma «Guani, por Leira Studio», ningún nombre de persona, solo se afirma lo que ya funciona. Narrativa vigente: «una máquina lee; una persona supervisa», con Guani en tercera persona. (El plan de marca todavía dice «cada precio aceptado pasa por revisión humana», que ya no es cierto con el carril 1; se sigue la narrativa del 2026-10-11.)

## Punto de partida (el video de hoy)

- Kit en `~/guani/landing-video-kit/`:
  - `slides.py` dibuja cuadros fijos de 720×1280 con Playwright: barra con avatar, «Guani» en Alice y una píldora «PASO n»; escenario con la captura en marco de teléfono; banda inferior con el subtítulo quemado en Roboto sobre `#05668d`.
  - `tts.py` genera una pista por frase con Edge TTS (`es-CU-ManuelNeural`, `rate=-4%`).
  - `build.py` monta los cuadros con fundidos de 0,2 s y 0,45 s de silencio entre frases (H.264 `stillimage` CRF 27, AAC 64 kbps mono, `+faststart`).
  - `capture_flujo.py` toma las cinco capturas a 390 px.
- El video actual tiene seis cuadros, unos 31 s y pesa 749 KB, con poster aparte (`guani-flujo-poster.webp`, 20 KB). La voz va en primera persona y el paso 4 es falso.

## Formato

- Vertical 9:16. Se mira en una columna de 390 px; se monta a 720×1280 con un tope de ~1 MB (decidido por el operador).
- Sin autoplay, `preload="none"`, poster aparte. Ningún JavaScript nuevo.
- Subtítulo quemado: la banda inferior repite la locución de la escena, porque se mira sin sonido por defecto. El «texto en pantalla» es el rótulo grande de la escena.
- Misma plantilla visual del kit, con la paleta del atardecer de la landing (tokens `--ph-dusk-*`) en la apertura y el cierre.

## Escenas

Duración total: 74 s. Salen de la locución a unas 2,5 palabras por segundo más el silencio entre frases; se ajustan con el audio real.

| n.º | s | Imagen / pantalla | Texto en pantalla | Locución |
|---|---|---|---|---|
| 1 | 4 | Atardecer de la portada: cielo en degradé, sol, Guani grande sobre el sol, silueta de la ciudad. **SVG** (el del héroe, quieto). | Precios reales del mercado informal cubano. | ¿Cuánto cuesta algo hoy en tu provincia? |
| 2 | 6 | Chat de un grupo de ventas con un anuncio de pollo. **Recreación** (`flujo-1`, reusado). | PASO 1 · Alguien publica su oferta | Todo empieza cuando un emprendedor publica su oferta en un grupo o canal de WhatsApp. |
| 3 | 5 | El globo del anuncio sale del chat y viaja a una tarjeta «Guani». **SVG**, dos posiciones con fundido. | PASO 2 · Escucha. Llega solo, tal cual se escribió | El anuncio le llega a Guani solo, tal cual se escribió. Nadie tiene que escribirle nada. |
| 4 | 6 | El mismo anuncio en su página pública: «Tal como se publicó, sin editar». **Captura real** de `/anuncio/…` a 390 px, sin barra de dirección. | PASO 3 · Entiende: producto, precio, cantidad, provincia | Un asistente saca el producto, el precio, la cantidad y la provincia. |
| 5 | 6 | Cuatro etiquetas (producto, precio, cantidad, provincia) que se llenan con los valores de ese anuncio, y una flecha a «misma unidad · en CUP». **Tarjeta** con los valores de la fila curada de ese anuncio. | Misma unidad, misma moneda | Y lo lleva todo a la misma unidad y a pesos cubanos, con la tasa informal de elTOQUE. |
| 6 | 7 | Tres carriles; se ilumina el primero: un mensaje que no es un precio («¿alguien sabe…?») sale hacia «Apartado». **SVG**. | PASO 4 · Filtra. Lo que no es un precio, se aparta | Lo que no es un anuncio de precio se aparta solo, y cada semana una persona revisa una muestra. |
| 7 | 8 | Se iluminan los otros dos carriles: «Claro → lo acepta el sistema → auditoría» y «Difícil → una persona». Un icono de freno junto al carril del sistema. **SVG**. | Lo claro lo acepta el sistema. Lo difícil, una persona. Con freno. | Lo claro lo acepta el sistema y una persona lo audita después; lo difícil lo decide una persona. |
| 8 | 7 | Tablero público con una celda resaltada: rango, mediana y número de anuncios. **Captura real** de `/precios?vista=tablero` a 390 px. | PASO 5 · Te lo enseña con su cuenta | En el tablero ves el rango, la mediana y cuántos anuncios hay detrás de cada precio. |
| 9 | 5 | Acercamiento a la línea de confianza de esa celda: «N revisados a mano · M aceptados por el sistema». **Captura real** (recorte de la anterior). | A mano o por el sistema: siempre a la vista | Al lado va cuántos revisó una persona y cuántos aceptó el sistema. |
| 10 | 8 | Tres tarjetas que entran una tras otra: «Cada cifra enseña sus anuncios», «Provincia por provincia», «Un precio disparatado no manda». **Tarjetas** con los textos de «Por qué puedes fiarte». | Por qué puedes fiarte | Guani no te pide fe: cada cifra enseña sus anuncios, va provincia por provincia y un precio disparatado no manda. |
| 11 | 6 | Tres líneas con su icono: «Si compras o vendes», «Si revendes o decides», «Si investigas o informas». **Tarjetas** con los textos de «Para quién es». | Para quién es | Sirve si compras o vendes, si revendes o decides, y si investigas o informas. |
| 12 | 6 | Vuelve el atardecer con Guani sobre el sol; botón «Ver los precios de hoy»; debajo, «Guani, por Leira Studio». **SVG** + botón dibujado. | Ver los precios de hoy · Una muestra en formación, no un censo. | Es una muestra en formación, no un censo. Mira los precios de hoy en Guani. |

Comprobado en el código por el subagente:

- Escena 3: el webhook de WAHA recibe y guarda el anuncio en el mismo request (`ingestion/whatsapp_webhook.py`). La extracción no es al instante (corre en el pre-cálculo), por eso no se dice «al momento».
- Escena 6: el carril 0 descarta texto e imagen y saca cada semana una muestra para auditar (`validation/lane0.py`).
- Escena 7: el carril 1 acepta solo anuncios de texto (`validation/lane1.py`), guarda las filas como automáticas y se audita después; se frena si hay demasiados sin auditar o un error auditado por encima de la meta. «Con freno» queda en pantalla y no en la voz.
- Escenas 8 y 9: `_confianza.html`, `/metodologia`.
- Escena 5: conversión con la tasa informal de elTOQUE (`/metodologia`).

No dice a propósito: «datos abiertos», «uno por uno», «ninguna cifra es una estimación», «yo / soy Guani», cifras de cobertura, nombres de grupos, teléfonos ni ids.

## Capturas reales por tomar

A 390 px, tema claro y movimiento reducido, contra el sitio con datos reales y solo con `GET`. Mismo cuidado que `tools/grabar_entradas.py`: parar si en el texto visible aparece un id, un teléfono o un enlace de grupo.

1. Página pública de un anuncio de texto (`/anuncio/…`): solo el cuerpo, sin barra de dirección ni URL a la vista y sin teléfono en el texto.
2. Tablero (`/precios?vista=tablero`), con la celda del mismo producto y provincia del anuncio de la escena 4 si tiene dato.
3. Recorte de la línea «revisados a mano / aceptados por el sistema» de esa celda (sale de la captura 2).
4. Los valores de la fila curada de ese anuncio, para dibujar la tarjeta de la escena 5.

No se usan las capturas 2 a 4 del pase actual (`flujo-2..4`): muestran la herramienta del operador, con voseo, y la 2 además un id.

## Qué se reusa del kit

- `slides.py`: plantilla de cuadro, fuentes Alice y Roboto y el avatar SVG. Cambia la lista `SLIDES` y se agrega el fondo del atardecer para las escenas 1 y 12.
- `tts.py`: tal cual, con las doce líneas nuevas en `LINES`.
- `build.py`: el montaje por frase con fundidos. Cambia el número de cuadros (hoy fijo en seis), la resolución y las tasas de bits.
- `capture_flujo.py`: solo el chat recreado (`flujo-1`) y la toma del tablero.

## Voz sugerida

`es-CU-ManuelNeural` de Edge TTS a `rate=-4%` (la del video actual). Alternativa: `es-CU-BelkysNeural`. Audio AAC mono a 32 kbps, unos 300 KB para 74 s.

## Decisiones abiertas para el operador

1. **Peso frente a duración. DECIDIDO:** 720×1280 y tope de ~1 MB (el operador subió el tope de los ~750 KB del video actual).
2. **Ejemplo de punta a punta.** ¿Las escenas 2 a 5 siguen con el anuncio de pollo recreado, o se elige un anuncio de texto real con página pública limpia y se recrea el chat a partir de él?
3. **Apertura.** ¿Pregunta («¿Cuánto cuesta algo hoy en tu provincia?») o titular de la landing dicho en voz («Precios reales del mercado informal cubano»)?
