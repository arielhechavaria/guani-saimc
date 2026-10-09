# Runbook — Rutina diaria de curaduría

Todos los comandos del operador, con su porqué, están en [`comandos.md`](comandos.md).

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

**Aviso de corte de ingesta.** Si un canal activo lleva callado más de lo normal para
él (piso de 24 h), arriba de `/curation` aparece una tarjeta ámbar con el canal y las
horas; si no entra nada por ningún canal, dice «No entra nada desde hace N h» y lo más
probable es que WAHA esté caído. Sin tarjeta, todo entra con su ritmo. El mismo aviso
está en la tarjeta «Última captura» de `/control`.

**El número que se sigue no es «pendientes totales»**, sino dos pestañas de
`/curation/cola`: **🎯 En foco** (debería quedar cerca de 0 al terminar la
hora) y **🖼️ Sin transcribir** (debería bajar semana a semana).

---

## Paso 1 — Pre-cálculo del asistente (corre solo cada 3 horas)

El pre-cálculo transcribe las imágenes y deja lista la propuesta del modelo
para cada pendiente en foco. Desde C1 lo corre solo un temporizador de systemd
(`saimc-prefill.timer`): cada 3 horas, uno a la vez, del más viejo al más nuevo,
con los carriles 0 si están encendidos en el `.env`. Normalmente no hace falta
lanzar nada: abrí la cola y las propuestas ya están.

**Tope de gasto.** Cada corrida corta antes de gastar USD 0,15
(`SAIMC_PREFILL_MAX_COST` en `saimc-prefill.service`). Con 8 corridas por día, el
peor caso es **USD 1,20 por día**. Si cambiás el tope o el horario, ese número
cambia: recalculalo.

**Cómo ver que anda**

- `/control`, franja «Estado del sistema», tarjeta **Pre-cálculo**: verde
  («ok, hace N h»), ámbar («sin correr hace N h» si pasaron más de 6 h, o «sin
  registro» si nunca corrió) o roja («falló (salida N) el DD/MM HH:MM UTC»). Es
  el aviso: no hay mail ni Telegram.
- `systemctl list-timers saimc-prefill.timer`: cuándo corrió y cuándo toca.
- `journalctl -u saimc-prefill.service -n 50`: el log de la última corrida.

**Correrlo ahora.** `sudo systemctl start saimc-prefill.service` (se queda
esperando hasta que termina; `journalctl -fu saimc-prefill.service` en otra
terminal para verlo avanzar). Dos corridas reales no se solapan, se lancen por
systemd o con `python -m ...`: la segunda encuentra el candado
`data/prefill.lock`, avisa y sale sin gastar, con código 0.

**Frenarlo.** `sudo systemctl disable --now saimc-prefill.timer`. Para volver:
`sudo systemctl enable --now saimc-prefill.timer`.

**Instalarlo en el VPS** (una vez, igual que `saimc-backup`):

```
sudo cp systemd/saimc-prefill.service systemd/saimc-prefill.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saimc-prefill.timer
```

Si cambiás un archivo de `systemd/`, volvé a copiarlo y repetí `daemon-reload`.

**Si frena por saldo o por la clave**, la tarjeta se pone roja. Cargá saldo o
corregí la clave (ver `docs/runbook-asistente-llm.md`, «Pre-cálculo de la
cola»); la próxima corrida del temporizador retoma sola, o lanzala con el
comando de arriba.

### Fallback: a mano, si el temporizador no está

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
- Con el temporizador activo, una corrida a mano que se solape con la suya
  duplica el gasto: frená el temporizador antes (`disable --now`) o usá
  `systemctl start`.

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

## Una vez por semana — Informe del modo sombra (en el VPS, 1 minuto)

Dice cuánto podría resolver solo el motor de validación y con qué error, sobre
lo que ya decidiste a mano. No gasta API, no escribe en la base y no enciende
nada: el motor sigue sin tocar la cola.

Desde la raíz del proyecto, el mismo día de cada semana:

```
.venv/bin/python -m saimc.validation.shadow --gold data/llm-backtest-results-v6.jsonl --grid --execute
```

- `--grid` prueba la grilla de parámetros declarada en el módulo. Los
  parámetros no tienen valor por defecto: para una sola combinación, en vez de
  `--grid` pasá `--min-cell-n`, `--channel-min-history` y
  `--channel-max-edit-rate`.
- `--execute` agrega una línea a `docs/shadow-runs.jsonl` (commiteala: es el
  registro semana a semana). Sin `--execute` corre en seco y no escribe nada.
- Leé tres cosas, cada una con su n y su cota al 95%: la **tasa de
  automatización**, los **falsos descartes del carril 0** (texto e imagen por
  separado) y el **error del carril 1**. Un 0% sobre un n chico no dice nada:
  mirá la cota, no el porcentaje.
- La parte de **Evaluación** crece sola con cada anuncio que curás o descartás
  de a uno con la propuesta a la vista. Los descartes en lote no cuentan.

Se corre cada semana hasta que el n alcance para decidir G1a y G1b
(`docs/plan-factor-humano.md`). Contrato del informe:
`docs/spec-motor-validacion.md`, «Protocolo del modo sombra».

## Cuando haga falta — Revalidar las filas (en el VPS, 1 minuto)

Desde B1 cada fila de `prices_history` guarda el estado que le dio el motor de
validación, como una foto del día en que se validó. No es parte de la hora
diaria: se corre después de un backfill de tasas o de base de precio, cuando
cambia la versión del motor o `min_cell_n`, y una vez al desplegar B1, para las
filas anteriores.

```
.venv/bin/python -m saimc.validation.revalidate
.venv/bin/python -m saimc.validation.revalidate --execute
```

- Sin `--execute` corre en seco: dice cuántas filas cambiarían de estado, por
  estado, y no escribe nada. Con `--execute` guarda estado, puntaje y detalle;
  no toca la procedencia ni ninguna otra columna.
- No gasta API. El resultado se ve en la sección «Validación» de `/control`.

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
