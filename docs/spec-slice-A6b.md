# Slice A6b — Jev como señal desconectable del motor, en modo sombra

Parte del plan `docs/plan-factor-humano.md` («A6b»). Toca esquema (migración 0030)
y el motor de validación: esta especificación se aprueba antes de programar.
Implementa el contrato de «Señales externas» de `docs/spec-motor-validacion.md`
(A5a) con lo que A6 dejó en pie (`docs/decisions.md`, 2026-10-04, «A6 cerrada»).

**Estado: aprobada por el operador el 2026-10-04**, con las cuatro decisiones
del final resueltas según la recomendación. Se implementa en dos slices:
A6b-1 primero, A6b-2 después.

## Problema

1. A6 midió a Jev fuera de línea y salió favorable en tres preguntas (triage
   95,6 %, producto 96,8 %, moneda 94,3 %), pero esa medición es una foto sobre
   1.409 textos ya decididos. Falta saber cuánto **aporta al motor**: qué saca
   del carril 1 y qué descartaría además del carril 0.
2. El contrato de señales existe en el código (`ExternalSignal`,
   `AdContext.signals`, `_signal_reasons`) y nadie lo usa:
   `context.build_ad_context` pasa `signals=()` siempre.
3. La respuesta de Jev no tiene dónde vivir. El triage se pregunta sobre
   anuncios que en su mayoría se descartan, y un descartado no tiene fila en
   `prices_history`.
4. El cliente de Jev ya probado vive en `scripts/jev_eval.py`, que el sistema
   tiene prohibido importar.

## Hechos de partida

Verificados en el código el 2026-10-04.

- **El motor es puro.** `validation/engine.py`, `rules.py`, `types.py` y
  `fence.py` no consultan la base ni la red. Leer la base es de `context.py`;
  escribir, de `persist.py`.
- **Lo que se persiste no pasa por `validate_ad`.** Al curar y al editar,
  `persist.validate_and_store` llama a `validate_stored_row`, que valida la fila
  guardada, no decide carril y **no lee señales**. `validate_ad` (el que sí lee
  `ctx.signals`) solo lo llama el informe de sombra. `lane0.apply_lane_0` usa
  `decide_lane_0`, que tampoco lee señales.
- **`ExternalSignal` es por fila y trae una sola probabilidad**
  (`row_index`, `field`, `value`, `probability`). Jev contesta **por anuncio**
  y, en las preguntas de elección, trae dos (`probabilities[choice]` y
  `confidence`).
- **El contrato no tiene pregunta de triage.** `SIGNAL_FIELDS` es
  `("product", "currency", "province", "sale_format")`.
- **Si llegan señales, `validate_ad` cambia el carril**: un desacuerdo saca del
  carril 1 (con `signal_high_probability = None`, cualquier desacuerdo).
- **No hay pantalla que muestre `validation_detail`.** Solo lo escribe
  `persist.py`; ninguna plantilla lo lee.
- **El tope del pre-cálculo no es un campo de `Settings`.**
  `SAIMC_PREFILL_MAX_COST` es un `Environment=` de
  `systemd/saimc-prefill.service` que llega como `--max-cost`.
- **El test `test_nada_del_sistema_importa_el_script`** busca la cadena del
  nombre del script en todo `.py` de `src/saimc`: lo rompe también un
  comentario que lo nombre.
- Última migración: `0029`.

## Qué se construye

### 1. Dónde corre la llamada: en el pre-cálculo, nunca al guardar

Una pasada propia dentro de `python -m saimc.extraction.prefill --execute`,
**antes** del bucle de Claude:

- recorre los anuncios `pending` de tipo `text` que no tienen señal guardada
  con la huella de preguntas vigente, más viejo primero;
- una llamada por anuncio con las **tres** preguntas, alta de la fila, `commit`;
- no depende de Claude: corre aunque falte `SAIMC_ANTHROPIC_API_KEY`, y no
  mira el foco (el triage se midió sobre todos los textos, en foco o no);
- va antes porque el carril 0, si está encendido, descarta en ese mismo bucle:
  después, el anuncio ya no estaría pendiente y el triage llegaría tarde;
- no cambia el código de salida del pre-cálculo. Un fallo de Jev no pone en
  rojo el indicador del temporizador.

En seco, el pre-cálculo informa cuántos textos preguntaría y el costo estimado,
sin red.

Al curar y al editar **no se llama a nadie**: `persist` lee la señal guardada
y la anota. Un anuncio que el operador decide antes de que pase el temporizador
queda «sin señal», que es un estado soportado.

*Descartado — llamar al guardar.* El triage después de la decisión no predice
nada; mete una llamada de red en la request del operador (el plan exige que la
curaduría nunca falle por Jev); y obligaría a guardar igual en otra tabla para
los descartados.
*Descartado — llamar dentro del motor.* Rompe su pureza y la de sus tests.
*Descartado — un temporizador propio.* Otra unidad de systemd para cinco
llamadas por corrida. El `oneshot` del pre-cálculo ya impide solapar.

### 2. Dónde se guarda: tabla derivada `jev_signals`

De solo altas, como `llm_proposals` y `ocr_transcriptions`. No toca
`raw_ingestions`. Modelo `JevSignal` en `src/saimc/models/jev_signal.py`.

| Columna | Tipo | Nota |
| --- | --- | --- |
| `id` | uuid, PK | |
| `ingestion_id` | uuid, FK a `raw_ingestions.id`, `RESTRICT` | no nulo |
| `model` | varchar(64), no nulo | el que informa el gateway; si no lo informa, el pedido |
| `questions_hash` | varchar(16), no nulo | huella del modelo, las instrucciones y las opciones |
| `answers` | JSON, no nulo | el objeto `answers` de la respuesta, tal cual llegó |
| `product_keys` | JSON, no nulo | `{"arroz": "<uuid>", …}`: qué producto era cada opción al preguntar |
| `input_tokens` | integer, no nulo | |
| `output_tokens` | integer, no nulo | |
| `cost_usd` | numeric(12, 8), no nulo | el que informa el gateway; si falta, estimado por tokens |
| `source` | varchar(16), CHECK | `prefill` (anuncio pendiente) o `backfill` (ya decidido) |
| `created_at` | timestamptz, no nulo | |

- `UNIQUE (ingestion_id, questions_hash)`, nombre
  `uq_jev_signals_ingestion_hash`. Su índice ya sirve para buscar por
  `ingestion_id`: no se agrega otro.
- **Solo se guarda una respuesta 200 con `answers`.** Un fallo de transporte no
  deja fila, así que la corrida siguiente lo reintenta sola.
- **Re-preguntar.** Con la misma huella no se llama: la fila existe. Si cambia
  una instrucción, una opción (un piloto nuevo o renombrado) o el modelo, la
  huella cambia, se pregunta de nuevo y se **agrega** una fila; la vieja queda.
  «La señal de un anuncio» es siempre la fila de la huella vigente.
- Dos procesos sobre el mismo anuncio: el segundo choca con el `UNIQUE`, se
  ataja con un savepoint y se descarta.
- `answers` no trae el texto del anuncio ni la clave: solo claves de opción y
  probabilidades.
- No se guardan columnas ya interpretadas. La lectura (`jev_client.parse_answer`)
  es una función pura sobre `answers` y `product_keys`.

*Descartado — solo `validation_detail`.* Deja afuera a los descartados, que son
el 89 % de la muestra de A6 y el objeto del triage.
*Descartado — guardar los fallos como filas.* Con el `UNIQUE` bloquearían el
reintento; sin él, la tabla junta ruido. Van al registro (ver «Fallos»).

### 3. Las preguntas

Tres, en una sola llamada, a todo texto: `triage` (tipo `noul`), `product` y
`currency` (tipo `choice`). Las instrucciones y las etiquetas son las de A6,
sin cambios. Opciones:

- `product`: los pilotos del catálogo (clave = `slugify(nombre)`) más `other`.
- `currency`: `CUP`, `USD`, `EUR`, `MLC`.

Provincia y formato de venta no se preguntan.

**Diferencia con A6, a propósito:** A6 mandó cinco preguntas a los curados y
una a los descartados, porque ya sabía cuál era cuál. Antes de la decisión no
se sabe, así que van las tres siempre. La huella es otra y los números de A6
no se heredan: el informe (punto 7) los vuelve a medir con este juego.

### 4. Cómo entra al motor sin romper su pureza

`engine.py`, `rules.py` y `types.py` **no cambian de comportamiento ni
importan nada nuevo**. Hay dos caminos, y ninguno escribe un carril.

**Camino persistido (curar, editar, `revalidate`).** `persist` busca la señal
vigente del anuncio y le pasa la respuesta ya leída a una función pura,
`jev_signal.annotate(reading, rows)`, que devuelve el bloque `jev` de cada
fila. `persist.validation_detail` lo suma como una clave más. El estado, el
puntaje y `provenance` salen de `validate_stored_row`, igual que hoy: el bloque
se calcula aparte y no entra a ninguna regla.

- Con Jev inactivo, o sin señal, la clave `jev` **no existe** (no va en `null`).
- Si buscar o leer la señal falla, se ataja ahí: la fila conserva su
  validación y queda sin `jev`.
- `revalidate --execute` reescribe `validation_detail`, así que también anota
  (o quita la clave si Jev está inactivo). Sirve de relleno para las filas
  viejas.

**Camino de sombra (solo el informe).** Un adaptador puro,
`jev_signal.to_external_signals(reading, rows)`, convierte la respuesta en
`ExternalSignal` para `AdContext.signals`. El informe corre `validate_ad` dos
veces, sin señales y con ellas, y compara. Nada de eso se guarda.

**Acuerdo: por anuncio, como en A6.** Jev elige «el producto principal» de un
anuncio que puede tener varias filas. Coincide si su elección es el valor de
**alguna** fila del anuncio. Un producto que no es piloto vale `other`.

Esto obliga a tres precisiones sobre el contrato de A5a, que es por fila:

| Contrato de A5a | En A6b | Motivo |
| --- | --- | --- |
| una señal por fila | una por anuncio y pregunta: si coincide, apunta a la primera fila que coincide; si no, a la fila 0 | Jev no ve filas |
| una probabilidad | se guardan las dos; el adaptador recibe cuál usar y el informe muestra ambas | A6: el umbral futuro tiene que decir sobre cuál se fija |
| sin triage | el triage no es un `ExternalSignal`: se guarda y se informa aparte | es del anuncio y del carril 0, no de un campo |

`signal_high_probability` **no se fija**. El comentario de `ValidationConfig` y
la tabla de parámetros de `spec-motor-validacion.md` dicen «lo fija A6b»: se
corrigen a «G1».

### 5. La entrada `jev` en `validation_detail`

```json
{
  "rules": {"structure": "pass", "price": "pass", "location": "pass",
            "trust": "unknown", "duplicate": "pass", "quantity_price": "pass"},
  "reasons": ["el canal no tiene historial suficiente"],
  "engine_version": "2026-10-03.1",
  "config": {"min_cell_n": 5},
  "validated_at": "2026-10-05T14:20:00+00:00",
  "jev": {
    "signal_id": "0b6f6c1e-3a1d-4a55-9a43-6d1f2f0c9b11",
    "model": "typesafe-ai/jev",
    "questions_hash": "9c41d07ab3e5f210",
    "asked_at": "2026-10-05T12:00:07+00:00",
    "source": "prefill",
    "triage": {"status": "ok", "probability": 0.97},
    "product": {"status": "ok", "answer": "arroz", "probability": 0.91,
                "confidence": 0.9, "row_value": "arroz", "agrees": true},
    "currency": {"status": "ok", "answer": "USD", "probability": 0.88,
                 "confidence": 0.86, "row_value": "CUP", "agrees": false}
  }
}
```

- `triage.probability` es P(sí). No lleva `agrees`: decir sí o no pide un
  umbral, y los umbrales son de G1.
- `probability` es `probabilities[choice]`; `confidence` es el campo
  `confidence`. Si vino una sola, la otra va en `null`.
- `row_value` es el valor de **esta** fila; `agrees` es por anuncio. Con las
  dos se lee también el acuerdo por fila (`answer == row_value`).
- Una pregunta ilegible: `{"status": "invalid", "reason": "…"}`, sin el resto.
- Se compara contra la fila **guardada** (lo que curó el operador). El plan
  decía «el valor extraído»: esa comparación, contra la propuesta del modelo,
  existe solo en el informe de sombra.
- `ENGINE_VERSION` no se sube: las reglas no cambian.

### 6. Configuración

Cuatro campos nuevos en `Settings` (`src/saimc/config.py`):

| Variable | Campo | Defecto | Qué gobierna |
| --- | --- | --- | --- |
| `SAIMC_LLM_JEV_ENABLED` | `llm_jev_enabled` | `false` | el interruptor |
| `SAIMC_LLM_JEV_API_KEY` | `llm_jev_api_key` | sin valor | la clave del gateway (ya está en el `.env`) |
| `SAIMC_LLM_JEV_TIMEOUT` | `llm_jev_timeout` | `10.0` | segundos por llamada |
| `SAIMC_LLM_JEV_MAX_COST` | `llm_jev_max_cost` | `0.01` | USD por corrida del pre-cálculo |

La URL (`https://ai-gateway.vercel.sh/typesafe/v1/systemone`) y el modelo
(`typesafe-ai/jev`) son constantes de `jev_client.py`: cambiarlos cambia la
huella y pide medir de nuevo, no es configuración del despliegue.

«Activo» es un solo predicado, `jev_signal.is_active(settings)`: interruptor
encendido **y** clave no vacía. Gobierna las llamadas y la anotación.

| Interruptor | Clave | Llama | Anota | Resultado |
| --- | --- | --- | --- | --- |
| apagado | cualquiera | no | no | idéntico a antes de A6b |
| encendido | falta | no | no | idéntico; aviso al arrancar y en la salida del pre-cálculo |
| encendido | está | sí | sí | sombra |

- Tope en `0`: no llama; sigue anotando lo ya guardado.
- Tiempo límite o tope que no son un número positivo: se usa el valor por
  defecto y se avisa en el registro. Un `.env` mal escrito no tumba la
  aplicación.
- El tope de Jev es aparte del de Claude: ninguno se come el presupuesto del
  otro.

Costo esperado: una llamada de tres preguntas pesa entre 403 y 1.574 tokens
(los extremos medidos en A6), es decir entre 0,000017 y 0,000066 USD. A 35
textos por día son menos de 0,0025 USD diarios (pico de 49: 0,0032). El tope
de 0,01 por corrida da un peor caso de 0,08 USD por día y alcanza para vaciar
el atraso inicial en una o dos corridas.

### 7. Fallos

En todos, la curaduría y el motor terminan igual y el anuncio queda sin señal.

| Qué pasa | Qué hace la pasada | Fila | Dónde queda |
| --- | --- | --- | --- |
| Red, 5xx | un reintento; si sigue, pasa al siguiente | no | registro + contador |
| Tiempo agotado | igual que red | no | registro + contador |
| 401, clave rechazada | corta la pasada (`auth`) | no | registro + estado de la corrida + `/control` |
| 403, acceso denegado | corta la pasada (`forbidden`), con el motivo del gateway | no | ídem |
| 429 | espera lo que pide `Retry-After` (tope 120 s) y reintenta una vez; si sigue, corta (`rate_limit`) | no | ídem |
| 200 que no es JSON o sin `answers` | pasa al siguiente (`bad_response`) | no | registro + contador |
| Respuesta de tipo inesperado u opción fuera del catálogo | sigue | **sí**, con `answers` tal cual | esa pregunta se lee `invalid`; contador `invalid` |
| Tope alcanzado | corta (`max_cost`) | las hechas | estado de la corrida + `/control` |
| Tres fallos seguidos | corta (`failures`) | — | ídem |

- «Registro» es `logger.warning("jev.fallo …")` con el tipo, el código HTTP y
  el id de la ingesta; sale en `journalctl -u saimc-prefill.service`. La clave
  se quita del mensaje (`_scrub`) y el texto del anuncio nunca se registra.
- «Estado de la corrida»: `prefill_status.PrefillRun` suma cuatro campos con
  valor por defecto (`jev_asked`, `jev_failed`, `jev_cost_usd`, `jev_stopped`),
  para que un archivo anterior a A6b se siga leyendo.
- Después de cualquier corte, el bucle de Claude corre normal.
- El tope se revisa antes de cada llamada: lo gastado más lo que se espera de
  la siguiente (la estimación por caracteres o la llamada más cara vista, lo
  que sea mayor).

### 8. Informe de sombra con Jev y sin Jev

`python -m saimc.validation.shadow`, mismo comando y mismos conjuntos
(calibración sobre el oro, evaluación sobre las revisiones individuales, sin
mezclar). Suma un bloque «Jev» por conjunto. Lee la tabla; no gasta API y no
depende del interruptor.

Entran solo los casos de texto con señal de la huella vigente. El bloque abre
con ese n sobre el total de textos del conjunto. Cada porcentaje va con su n y
su cota de Clopper-Pearson al 95 %.

1. **Carril 1, lado a lado.** Por cada combinación de parámetros: anuncios en
   el carril 1 y su error, sin Jev y con Jev. De los que Jev saca: cuántos eran
   errores (el operador había editado algo) y cuántos estaban bien
   (automatización perdida). Se muestra para «cualquier desacuerdo» y para 0,5,
   0,7 y 0,9, con cada una de las dos probabilidades.
2. **Triage al lado del carril 0.** Para P(sí) menor que 0,1, 0,2, 0,3 y 0,5:
   cuántos descartaría, cuántos falsos descartes, y el cruce con el carril 0
   del motor (solo el motor, solo Jev, los dos).
3. **Campos.** Acierto de producto y de moneda contra la fila curada, con la
   definición de A6. Es la medición de A6 repetida con el juego de tres
   preguntas: la referencia es 96,8 % y 94,3 %.

Las grillas son para mirar: el informe no elige ningún valor. Sin señales, el
bloque dice «n = 0» y el informe no falla. Con `--execute`, la línea de
`docs/shadow-runs.jsonl` suma una clave `jev`.

**Dónde se ve:** en la terminal y en `docs/shadow-runs.jsonl`. El resultado de
la primera corrida va a `docs/decisions.md`.

**Con qué n.** Sin relleno, solo cuentan los textos que pasaron por el
pre-cálculo desde el encendido: unos 245 por semana para el triage, pero una
veintena de curados. Para tener n desde el primer día hace falta el relleno:

`python -m saimc.validation.jev_signal`, en seco por defecto.
- sin opciones: los pendientes (lo mismo que hace la pasada del pre-cálculo);
- `--decided`: los textos ya curados o descartados sin señal vigente, con
  `source = backfill`;
- `--execute`, `--limit`, `--max-cost` (por defecto, el de la configuración).

### 9. `/control`

Una sección «Jev (señal en sombra)», `id="jev"`, debajo de «Validación», a
390 px primero:

- estado: encendida, apagada o sin clave;
- textos con señal vigente, y cuántas filas hay en total en la tabla;
- filas de precio anotadas (las que tienen `jev` en su detalle);
- gasto acumulado y de los últimos 7 días (suma de `cost_usd`);
- última corrida: preguntados, fallos y, si cortó, por qué.

En la franja de estado, un indicador solo cuando Jev está activo y la última
corrida cortó por `auth`, `forbidden` o `rate_limit`. Es ámbar, no rojo: es una
señal en sombra.

`/control` es público: solo conteos y gasto. Ningún texto, canal ni respuesta.

### 10. Qué sale del script y qué queda

A `src/saimc/validation/jev_client.py` (sin base de datos):
`API_URL`, `MODEL`, las constantes de reintento, `slugify`, las etiquetas de
moneda, el texto de las tres preguntas, `questions_hash`, `build_request`,
la estimación de tokens y costo, `JevError`, `ApiResult`, `Answer`,
`parse_response`, `parse_answer`, `_scrub`, `_error_message` y `ask` (que suma
los parámetros de tiempo límite y de reintentos).

A `src/saimc/validation/jev_signal.py` (el adaptador que nombra el plan):
`is_active`, el armado del catálogo de opciones desde la base, la selección de
anuncios, la pasada, el alta en la tabla, la búsqueda de la señal vigente,
`annotate`, `to_external_signals` y la entrada de línea de comandos.

En `scripts/jev_eval.py` queda lo que es de A6: la muestra (`Ad`, `load_ads`),
su juego de cinco preguntas armado con las piezas compartidas, el caché, la
corrida, las métricas, el reporte y `--check`. Pasa a importar el cliente de
`saimc.validation.jev_client`.

- El script importa al sistema; el sistema no importa al script. El test
  existente sigue en pie, y los módulos nuevos no pueden nombrar el archivo del
  script ni en un comentario.
- La huella de las cinco preguntas de A6 no puede cambiar con la mudanza: el
  caché de `data/jev_eval/` dejaría de valer. Se fija con un test antes de
  mover nada.

### 11. Migración 0030

`0030_jev_signals.py`, `down_revision = "0029"`. Crea la tabla con su FK, el
CHECK de `source` y el `UNIQUE`. `downgrade` la borra. No toca ninguna tabla
existente y no hay relleno en la migración.

## Privacidad y contraste con `spec.md`

**Qué sale hacia el proveedor** (Vercel AI Gateway y, detrás, TypeSafe AI):

- el texto del anuncio pasado por `redact_personal_data()` en el borde del
  pedido, aunque ya venga redactado de la ingesta;
- las tres preguntas: nombres de los productos piloto y de las monedas;
- la clave, en la cabecera.

No sale el id de la ingesta, el canal, el emisor (`sender_hash`) ni la
provincia. La redacción saca teléfonos, no nombres: es la misma garantía con
la que ese texto ya sale hacia Anthropic. A6b suma un segundo destinatario,
no un dato nuevo.

- **§10.6** (minimización, seudonimización): sin apartamiento.
- **§6.1** (asistentes de la cola, no etapas autónomas): sin apartamiento.
  Jev no decide nada.
- **§12.6** (ningún umbral se inventa): sin apartamiento. No se fija ninguno.
- **§7.1** (`raw_ingestions` inmutable): sin apartamiento.
- **§6.2, «Resultado de validación»:** el JSON suma una clave que la
  especificación no trae. Extiende el apartamiento ya anotado por B1: se
  actualiza esa fila de `docs/spec-map.md` («…y, si hay, la señal `jev`»).
- **§7:** `jev_signals` no figura. Es salida derivada y aditiva, igual que
  `ocr_transcriptions`; se suma a la misma nota de `docs/spec-map.md`.
- **§6.5** (linaje): la forma pública del linaje es de B4. `jev` no se publica.

## Qué no entra

- Cambiar `validation_status`, el carril o `provenance` por lo que diga Jev.
- Fijar umbrales, incluido `signal_high_probability` (G1).
- Provincia y formato de venta. Imágenes (C2).
- Llamar a Jev al guardar o al abrir la cola.
- Una pantalla para ver `validation_detail`.
- Usar la señal en `validate_ad` fuera del informe.
- Reutilizar el caché de A6 como señales: son otras preguntas y otra huella.

## Casos esperados (se escriben antes que el código)

Fixture común: pilotos arroz, pollo y aceite; café existe y no es piloto. Fila
base **F**, la del caso 1 de `spec-slice-B1.md`: arroz, 320 CUP la libra, La
Habana, celda con 5 referencias dentro de la banda, sin duplicado. Sin Jev da
`unverified`, puntaje 83, `provenance = human`.

**Primera parte (A6b-1)**

Los seis del plan:

1. **Apagado.** Hay una señal guardada para el anuncio de F. Con el
   interruptor apagado, y también con el interruptor encendido y sin clave:
   `unverified`, 83, `human`, y las claves de `validation_detail` son
   exactamente `rules`, `reasons`, `engine_version`, `config`, `validated_at`.
   El pre-cálculo hace 0 llamadas a Jev.
2. **Coincide con probabilidad alta.** Señal: triage 0,98; producto `arroz`
   (0,97 / 0,96); moneda `CUP` (0,99 / 0,98). Resultado: `unverified`, 83,
   `human`, las cinco claves iguales a las del caso 1, y `jev.product.agrees`
   y `jev.currency.agrees` en `true`.
3. **Discrepa con probabilidad alta.** Señal: producto `pollo` (0,93 / 0,92).
   Resultado: `jev.product.agrees = false`, `row_value = "arroz"`; estado,
   puntaje y `provenance` iguales a los del caso 1.
4. **Tipo inesperado y opción fuera del catálogo.** Respuesta 200 con
   `triage: {"noul": "alto"}`, `product: {"choice": "cafe", …}` y una moneda
   válida (`CUP`, 0,90 / 0,88). Se guarda la fila con `answers` tal cual;
   `jev.triage` y `jev.product` quedan `invalid` con su motivo;
   `jev.currency` queda `ok`; el contador `invalid` de la corrida vale 2.
5. **Falla de red, de clave o de tiempo.** Tres pendientes.
   a) Error de conexión en el primero, en sus dos intentos: sin fila, y los
   otros dos se preguntan.
   b) 401 en el primero: la pasada corta con `auth`, 0 filas, 1 sola llamada.
   c) Tiempo agotado: como (a).
   En los tres el bucle de Claude corre, el código de salida no cambia, y curar
   después cualquiera de esos anuncios guarda la fila sin `jev`.
6. **Tope de gasto.** Tope 0,00005; tres pendientes cortos; el gateway cobra
   0,00003 por llamada. Se hace una llamada, la segunda no (0,00003 + 0,00003
   pasa el tope): 1 fila, `jev_stopped = "max_cost"`, dos anuncios sin señal y
   el bucle de Claude corre.

Los que agrega el diseño:

7. **Selección.** Entran: un texto pendiente sin señal, y uno con señal de una
   huella vieja (queda con dos filas). No entran: una imagen pendiente, un
   texto ya curado, un texto con señal vigente, un texto vacío.
8. **Redacción.** Texto «Arroz 320 CUP la libra, llamar al 5 234 5678»: el
   `state` del pedido no contiene el número y sí `[teléfono removido]`. El pedido tiene
   exactamente las claves `model`, `state` y `questions`.
9. **Preguntas.** Exactamente `triage`, `product` y `currency`. Las opciones de
   producto son `arroz`, `pollo`, `aceite` y `other`; las de moneda, `CUP`,
   `USD`, `EUR` y `MLC`.
10. **Huella.** El mismo catálogo en otro orden da la misma huella; sumar un
    piloto da otra.
11. **Sin duplicados.** Un segundo alta con el mismo anuncio y la misma huella
    no agrega fila y no lanza hacia afuera.
12. **Anuncio de dos filas** (arroz y pollo). Jev dice `arroz`: las dos filas
    con `agrees = true`; la de pollo con `row_value = "pollo"`. Jev dice
    `aceite`: las dos con `agrees = false`.
13. **Producto fuera del foco.** Fila de café y Jev dice `other`:
    `row_value = "other"`, `agrees = true`.
14. **Una sola probabilidad.** Viene `confidence` y no `probabilities`:
    `status = ok`, `probability = null`, `confidence` con su valor.
15. **Falla al leer la señal.** La consulta a `jev_signals` lanza: la fila se
    guarda con su validación completa y sin `jev`.
16. **Editar.** Al editar el precio de una fila de un anuncio de dos, las dos
    quedan anotadas y `provenance` sigue en `human`.
17. **`revalidate --execute`.** Con Jev activo anota una fila vieja que tiene
    señal; con Jev inactivo le quita la clave. En ningún caso cambia
    `provenance` ni el estado respecto de una corrida sin Jev.
18. **429.** Con `Retry-After: 30` y después 200: espera 30 s (reloj de
    prueba) y guarda la fila. Con 429 dos veces: corta con `rate_limit`.
19. **403.** Corta con `forbidden`, distinto de `auth`, y conserva el motivo
    que mandó el gateway.
20. **La clave no se filtra.** Un error cuyo cuerpo repite la clave se registra
    con `[clave]` en su lugar.
21. **Tres fallos seguidos** cortan la pasada con `failures`.
22. **Migración.** La 0030 sube y baja; el `UNIQUE` rechaza un duplicado.
23. **`/control`.** Apagado, dice «apagada». Con tres señales de 0,00003 cada
    una: 3 textos con señal y 0,0001 USD de gasto. La página no trae ningún
    texto de anuncio ni nombre de canal.
24. **Pureza.** `engine.py`, `rules.py`, `types.py` y `fence.py` no importan
    `httpx`, `jev_client` ni `jev_signal`. El test de A6 sigue en verde.
25. **Mudanza del script.** Para el catálogo fijo de `tests/test_jev_eval.py`,
    la huella de las cinco preguntas es la misma antes y después de mover el
    código. Es una fijación de la mudanza, no una verdad de validación: el
    valor se captura antes de tocar el script.

**Segunda parte (A6b-2)**

26. **Adaptador.** Para F con producto `arroz` (0,97 / 0,96): un
    `ExternalSignal` con `field = "product"`, `row_index = 0`, `value` igual al
    id de arroz y `probability = 0,97`; pidiendo `confidence`, 0,96. Una
    pregunta `invalid` no produce señal. El triage nunca produce señal.
27. **Jev saca del carril 1.** El caso 21 de `spec-motor-validacion.md`: sin
    señales, carril 1; con Jev en desacuerdo sobre el producto, carril 2. El
    informe cuenta 1 anuncio sacado.
28. **Jev no mete en el carril 1.** El caso 22: carril 2 con y sin la señal.
29. **Tabla de triage.** Cuatro descartados con P(sí) 0,05, 0,10, 0,15 y 0,60,
    y dos conservados con 0,22 y 0,95. Con 0,2: descartaría 3, falsos descartes
    0 de 3. Con 0,5: descartaría 4, falsos descartes 1 de 4.
30. **Sin señales.** El bloque informa n = 0 y el informe termina con código 0.
31. **El informe no escribe.** Antes y después, ninguna fila de
    `prices_history` cambió y `jev_signals` tiene las mismas filas.
32. **Relleno.** `--decided` en seco no llama a la red e informa cuántos
    faltan. Con `--execute` pregunta solo los decididos sin señal vigente y
    guarda `source = backfill`.

## Verificación del operador

**Primera parte**

1. `pg_dump`, push, `alembic upgrade head`, reinicio de `saimc.service`.
2. Con el `.env` sin tocar, abrir `/control#jev`: dice «apagada». Curar un
   texto en `/curation`: nada cambia.
3. Poner `SAIMC_LLM_JEV_ENABLED=true`, reiniciar, y correr
   `sudo systemctl start saimc-prefill.service`. En
   `journalctl -u saimc-prefill.service` aparece la pasada de Jev con cuántos
   preguntó y cuánto gastó.
4. `SELECT source, count(*), sum(cost_usd) FROM jev_signals GROUP BY 1;`
   muestra filas con `prefill`.
5. Curar en `/curation` un texto que estaba pendiente antes de esa corrida y
   mirar:
   `SELECT validation_status, provenance, validation_detail->'jev' FROM prices_history ORDER BY created_at DESC LIMIT 3;`
   La fila trae el bloque `jev` con las tres preguntas.
6. `/control#jev`: «encendida», textos con señal, filas anotadas y gasto.
7. Poner `SAIMC_LLM_JEV_ENABLED=false`, reiniciar y curar otro texto: la
   consulta del paso 5 devuelve `null` en esa fila y nada falla.
8. **Lo que no tiene que pasar:** una fila con `provenance = auto`; un
   `validation_status` distinto del que da `revalidate` en seco con Jev
   apagado; una curaduría que falle o tarde más; un teléfono en `answers`; dos
   filas del mismo anuncio con la misma huella; el indicador del pre-cálculo en
   rojo por un fallo de Jev; un cambio en `raw_ingestions`.

**Segunda parte**

9. `.venv/bin/python -m saimc.validation.jev_signal --decided`: en seco, dice
   cuántos textos preguntaría y cuánto costaría. Con `--execute`, los pregunta.
10. `.venv/bin/python -m saimc.validation.shadow --gold data/llm-backtest-results-v6.jsonl --grid`:
    el bloque «Jev» de cada conjunto, con su n, y las tres tablas.
11. **Lo que no tiene que pasar:** que el informe escriba en la base, que elija
    un umbral, o que un anuncio entre al carril 1 por Jev.

## Documentos que se tocan

- `docs/decisions.md`: Jev entra en sombra con tres preguntas, en el
  pre-cálculo y con tabla propia; el acuerdo es por anuncio; los umbrales
  quedan para G1. En la segunda parte, el resultado de la primera corrida.
- `docs/spec-map.md`: las dos notas de «Privacidad y contraste».
- `docs/spec-motor-validacion.md`: «lo fija A6b» pasa a «G1», en una nota al
  pie de la tabla de parámetros (el documento aprobado no se reescribe).
- `docs/comandos.md`: `saimc.validation.jev_signal` y las variables nuevas.
- `docs/costos.md` y la tabla de costo del plan: 0,0001 USD por anuncio era
  una estimación; lo medido es del orden de 0,00002 a 0,00007.
- `docs/plan-factor-humano.md`: A6b partido en dos, y el paso de verificación
  que pedía «abrir su detalle de validación».

## Tamaño estimado

| Parte | Archivos | Líneas |
| --- | --- | --- |
| A6b-1, código | `jev_client.py` (nuevo, ~330, casi todo mudado), `jev_signal.py` (nuevo, ~260), `models/jev_signal.py` (~55), migración (~55), `config.py` (+25), `prefill.py` (+45), `prefill_status.py` (+8), `persist.py` (+35), `revalidate.py` (+5), `control.py` y `health_strip.py` (+60), `control.html` (+45), `scripts/jev_eval.py` (−300) | ~920 nuevas, ~300 mudadas |
| A6b-1, tests | `test_jev_client.py`, `test_jev_signal.py`, y altas en `test_prefill.py`, `test_validation_persist.py`, `test_control.py`, `test_jev_eval.py` | ~650 |
| A6b-2, código | `jev_signal.py` (+90: adaptador y línea de comandos), `shadow.py` (+230) | ~320 |
| A6b-2, tests | `test_shadow_report.py`, `test_jev_signal.py` | ~300 |

Unos 16 archivos y 2.200 líneas contando tests. **Conviene partirlo en dos**:
la primera parte deja datos entrando y es verificable sola; la segunda no
toca esquema ni el camino de curaduría y necesita esos datos para mostrar algo.

## Decisiones que necesitan al operador

**Resueltas el 2026-10-04.** El operador aprobó las cuatro según la
recomendación: (1) se parte en A6b-1 y A6b-2; (2) hay relleno sobre los textos
ya decididos, en A6b-2; (3) se aprueba el gasto recurrente con tope de 0,01 USD
por corrida; (4) Jev no se llama al guardar. Se conservan abajo con sus
alternativas.

1. **¿Se parte en A6b-1 y A6b-2?**
   - *Sí (recomendado):* dos sesiones, dos commits, cada una con su
     verificación. La señal empieza a juntarse al cerrar la primera.
   - *No:* un solo slice de unas 2.200 líneas que toca esquema, pre-cálculo,
     guardado, `/control` e informe a la vez; más difícil de revisar y de
     revertir.

2. **¿Relleno sobre los textos ya decididos?** Son unos 1.400 textos, entre
   0,02 y 0,09 USD, una vez, y vuelve a mandar esos textos (redactados) al
   proveedor.
   - *Sí (recomendado), en A6b-2:* el informe arranca con n ≈ 1.400 para el
     triage y ≈ 157 curados para producto y moneda.
   - *No:* no se gasta ni se reenvía nada, pero el informe arranca en n = 0 y
     los curados suben de a unos 20 por semana; G1b espera meses por ese
     número.

3. **¿Se aprueba el gasto recurrente?** Tope de 0,01 USD por corrida del
   pre-cálculo: peor caso 0,08 USD por día, esperado menos de 0,003.
   - *Sí (recomendado).*
   - *Otro tope:* más bajo alarga el vaciado del atraso inicial; `0` deja a
     Jev sin preguntar.

4. **¿Se confirma que Jev no se llama al guardar?** El plan dejaba abierta la
   alternativa.
   - *No se llama (recomendado):* la curaduría no depende de la red; un anuncio
     decidido antes de que pase el temporizador (cada 3 h) queda sin señal y
     lo cubre el relleno.
   - *Se llama al guardar si falta la señal, con tiempo límite corto:*
     cobertura total de los curados, a cambio de hasta ese tiempo de espera en
     cada guardado sin señal y de una dependencia de red en la request del
     operador.
