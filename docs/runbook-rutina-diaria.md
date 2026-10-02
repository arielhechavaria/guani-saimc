# Runbook — Rutina diaria de curaduría

Una hora por día, siempre en el mismo orden. Lo que no entra en la hora queda
para mañana: la cola no se vacía a fuerza de jornadas enteras.

## Por qué así y no «curar hasta bajar la cola»

Números del 2026-09-28 (base local, copia de prod):

- Por WhatsApp entran **300–730 anuncios por semana**. A mano se curan
  **10–100 filas por semana**. Vaciar la cola una vez no sirve: en una semana
  vuelve a estar igual.
- De lo que entra, alrededor del **86% se descarta**. La mayor parte (920 de
  1401) se fue en lote por estar fuera de foco.
- Los 404 pendientes de ese día eran **112 textos en foco** y **292 imágenes
  sin transcribir**. No quedaba ningún texto fuera de foco.

Las imágenes son el problema de fondo. La cola no puede saber si una imagen
habla de un producto del foco sin pasarle OCR, y eso solo pasa cuando la abrís
o cuando corre el pre-cálculo (paso 1). Mientras no se transcriben, quedan
mezcladas con el resto sin que nadie sepa si sirven.

**El número que se sigue no es «pendientes totales»**, sino dos pestañas de
`/curation/cola`: **🎯 En foco** (debería quedar cerca de 0 al terminar la
hora) y **🖼️ Sin transcribir** (debería bajar semana a semana).

---

## Paso 1 — Pre-cálculo del asistente (en el VPS, antes de sentarte)

El pre-cálculo transcribe las imágenes y deja lista la propuesta del modelo
para cada pendiente en foco. Procesa un anuncio a la vez, del más viejo al más
nuevo, así que lanzalo primero y curá mientras avanza.

Desde la raíz del proyecto, dentro de una sesión de `tmux` para que no se corte
si se cae la conexión:

```
tmux new -s prefill
.venv/bin/python -m saimc.extraction.prefill
```

Esa primera corrida es en seco: no gasta nada. Te dice cuántos pendientes no
tienen propuesta y cuánto costaría. Si la cifra tiene sentido:

```
.venv/bin/python -m saimc.extraction.prefill --execute --max-cost 1
```

- `--max-cost 1` corta antes de gastar 1 USD. A $0,0029 por anuncio (promedio
  medido, `docs/costos.md`), alcanza para unos 340 anuncios: más que lo que
  entra en foco en un día.
- **La primera vez**, con el backlog acumulado (~400), alcanza con
  `--max-cost 2` (≈$1,2 esperado, con margen).
- Salí de `tmux` sin cortarlo con `Ctrl+b` y después `d`. Para volver:
  `tmux attach -t prefill`.
- Si se corta, relanzá el mismo comando: salta lo que ya tiene propuesta.
- Si frena por saldo o por la clave, cargá saldo o corregí la clave y relanzalo
  (ver `docs/runbook-asistente-llm.md`, «Pre-cálculo de la cola»).

## Paso 2 — Descartar fuera de foco (2 minutos)

Abrí **`/curation/sin-foco`**. La lista son los textos que no mencionan ningún
producto del foco. Revisala por arriba y confirmá el descarte en lote.

Casi siempre va a estar vacía o casi vacía. Si aparece algo que sí te
interesa, no lo descartes: significa que falta un producto o un alias en
`/catalog`.

## Paso 2b — Descartar imágenes en lote (3 minutos, después del pre-cálculo)

Es la otra mitad de **`/curation/sin-foco`**: tocá el chip **Imágenes** (o abrí
**`/curation/sin-foco/imagenes`**). Muestra las imágenes pendientes que el modelo
propone descartar, con su motivo («Fuera de foco» o «Sin datos de precio»), el
canal y la fecha. Tocá una miniatura para ampliarla.

- **Vienen todas marcadas.** Mirá la grilla de arriba abajo; la que no debería
  irse, la **Rescatás** (se desmarca y queda pendiente en la cola).
- Confirmá con **Descartar las N marcadas**. Quedan `discarded`, nunca se borran.
- Las imágenes **sin propuesta no aparecen**: el aviso de arriba dice cuántas son.
  Si el número es alto, corré el pre-cálculo (paso 1) y recargá.

No es un sello: el freno es mirar la grilla antes de confirmar. Cada lote
queda en la bitácora (`curation_events`, modo `batch_model`) con la hora en que
se mostró y la hora en que se confirmó.

## Paso 3 — Curar lo que está en foco (el grueso de la hora)

Abrí **`/curation`**. Te muestra de a un pendiente, en este orden:

1. Primero los textos en foco, y dentro de ellos, primero aquellos en que el
   asistente y las reglas coinciden (los más rápidos).
2. Después las imágenes.

En cada anuncio:

- **Con propuesta que está bien:** revisá cada fila contra el texto y confirmá
  con **Aceptar** o con **Ctrl+Enter**.
- **Con propuesta que difiere o falla:** corregí los campos marcados y curá.
- **El asistente sugiere descartar:** el botón con el motivo sugerido aparece
  primero. Un clic.

Nunca aceptes sin mirar porque «lo puso la IA». Es exactamente el error que
`/control` vigila (tasa de aceptación sin edición).

## Paso 4 — Imágenes, si queda tiempo

Abrí **`/curation/cola?foco=imagen`**. Las que ya pasaron por el paso 1 tienen
propuesta. Abrí cada una con el enlace de la fila:

- **El asistente sugiere descartar** (anuncio de otro producto, saludo, foto
  sin precio): normalmente ya se fue en el lote del paso 2b; si quedó alguna
  (por ejemplo, la que rescataste), descartá con un clic.
- **Propone filas:** es un anuncio en foco; curalo como en el paso 3.
- **Sin propuesta:** el pre-cálculo todavía no llegó a ella, o el OCR no leyó
  texto. Abrirla transcribe en el momento, pero es lento. Dejala para cuando
  termine el pre-cálculo.

## Paso 5 — Cerrar la hora (1 minuto)

Mirá en `/curation/cola` los contadores de **🎯 En foco** y **🖼️ Sin transcribir**
y anotalos donde lleves el registro. En `/control`, fijate que la aceptación
sin edición no esté subiendo sin motivo.

Paraste a la hora aunque haya cola. Lo que quedó, sigue mañana.

---

## Cómo saber si la rutina funciona

Al cabo de una semana:

| Señal | Bien | Mal |
|---|---|---|
| 🎯 En foco al terminar la hora | cerca de 0 | crece día a día |
| 🖼️ Sin transcribir | baja | sube o se estanca |
| Gasto del pre-cálculo (`/control`) | ≈ 1 USD por día | mucho más: revisá con qué entra |

Si **En foco** crece aunque cumplas la hora, la entrada supera a una persona.
Eso no se arregla con más horas: hay que elegir qué entra (canales, productos
del foco) o hacer que el descarte de imágenes necesite menos clics.

## Lo que esta rutina no resuelve

- **Descartar imágenes sin propuesta.** El lote del paso 2b solo cubre las que
  el modelo ya vio y propuso descartar. Lo demás sigue entrando por la cola
  (paso 4).
- **Qué mide H0 ahora que todo pasa por el asistente.** Sigue abierto (ver
  `docs/plan-asistente-llm-cola-completa.md`, sección final).
