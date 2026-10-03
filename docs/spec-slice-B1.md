# Slice B1 — Persistir la validación: estado, puntaje y procedencia

Parte del plan `docs/plan-factor-humano.md`. Toca esquema (migración 0027): esta
especificación se aprueba antes de programar. Se apoya en
`docs/spec-motor-validacion.md` (A5a) y en lo que A5b y A5c dejaron escrito en
`docs/decisions.md` (2026-10-02). Incluye K6.

## Problema

1. `prices_history.confidence_score` existe desde la 0002 y nada lo escribe. No
   hay `validation_status`, ni detalle, ni procedencia. El carril 1 no se puede
   encender sin saber qué filas hizo una persona y cuáles el sistema.
2. El motor (`validate_ad`) valida **la propuesta del modelo**, no la fila que se
   guarda. El operador corrige, agrega y borra filas en el formulario; al editar
   no hay propuesta. Sin propuesta, o con propuesta degradada, el motor no
   devuelve ninguna fila. Lo que hay que persistir es el estado de la fila
   guardada.
3. No hay ningún parámetro fijado (A5c: «no se fija ningún parámetro todavía»).
   Sin `min_cell_n` las reglas 2 y 6 dan `unknown`: ninguna fila puede salir
   `anomaly` ni `inconsistent`, y el caso del plan «un precio fuera de la banda
   queda como anomalía» no se puede cumplir.
4. `llm_proposals.prompt_version` es `varchar(32)` y la versión con glosario ocupa
   30. PostgreSQL no trunca: rechaza el insert, y el pre-cálculo se caería (K6).

## Qué se construye

### Esquema: migración 0027

Sobre `prices_history`:

| Columna | Tipo | Nulo | Notas |
| --- | --- | --- | --- |
| `validation_status` | `String(24)` | sí | CHECK `IS NULL OR IN (...)` con los siete estados del motor |
| `validation_detail` | `JSON` | sí | `sa.JSON`, como `llm_proposals` y `curation_events` |
| `provenance` | `String(16)` | no | CHECK `IN ('human','auto','audited')` |

- `confidence_score` no cambia de tipo ni de CHECK: pasa a llenarse.
- `provenance` no tiene valor por defecto en el modelo: quien crea una fila lo
  dice explícitamente. La migración completa `'human'` en todas las filas
  existentes: todas salieron de `submit_curation` con evento `curated/individual`.
- El plan llama `origin` a esta columna. Se propone `provenance` porque `origin`
  ya nombra tres cosas en el código: el eje de `ProductVariant`, un campo del
  formulario de la cola y el módulo `saimc.ingestion.origin`.
- `audited` solo se alcanza desde `auto` (B2b), así que no pierde el dato de que
  la fila nació automática. En B1 nada escribe `auto` ni `audited`.
- `llm_proposals.prompt_version` pasa de 32 a 64 caracteres (K6).
- Mismo patrón que las migraciones 0008, 0016 y 0026 (`batch_alter_table`), con
  su test de migración.

### El motor: validar una fila guardada

Se agrega una función pública al motor, pura, que evalúa las seis reglas sobre
**una fila ya resuelta** (producto, provincia, precio, moneda, `price_cup`,
cantidad, unidad, base del precio), con su celda y las filas recientes del
canal. Es el mismo patrón que `decide_lane_0`: reusa `rules.py` y `_status`, y
`validate_ad` no cambia de comportamiento.

- **No decide carril.** El carril es del anuncio y de la propuesta; B1 no
  automatiza nada.
- **Verificación.** `approved` exige, además de las seis reglas en `pass`, que la
  fila esté verificada. Las condiciones 1 a 3 del carril 1 (cita, acuerdo con las
  reglas, sin abstenciones) existen para reemplazar la mirada de una persona. Una
  fila con `provenance = human` la tuvo: cuenta como verificada. B2b decide qué
  lleva una fila `auto`.
- **Parámetros.** La función recibe `min_cell_n` y, opcionales, los dos de la
  regla 4. Lo que no se pasa deja su regla en `unknown`, como hoy.
- **Versión.** Constante `ENGINE_VERSION` en `saimc/validation/`, que se sube a
  mano cuando cambian las reglas, el orden de estados o el cerco.

### Parámetros

- `min_cell_n` se fija para **persistir**, no para automatizar: gobierna cuándo
  las reglas 2 y 6 opinan. Valor propuesto: **5**, el del medio de la grilla de
  A5c (3/5/10). Es decisión del operador. No hay medición que elija
  entre los tres: en el oro, la celda sin referencias suficientes dejó afuera
  entre 16 y 22 anuncios según el valor.
- `channel_min_history` y `channel_max_edit_rate` **siguen sin fijar**, como
  decidió A5c. Consecuencia: la regla 4 da `unknown` en toda fila, ninguna sale
  `approved` y el puntaje máximo es 83. Una fila sana queda `unverified`.
- Va como constante en código con referencia a la entrada de `decisions.md`, igual
  que las metas de G1a. No es configuración del despliegue.
- Cada fila guarda el valor con el que se validó, así que cambiarlo después no
  deja filas sin explicación.

### Qué se guarda en cada fila

- `validation_status`: el estado, con la precedencia de la especificación del
  motor.
- `confidence_score`: reglas en `pass` sobre seis, por cien. Sigue siendo
  descriptivo: nada lo compara contra un umbral.
- `validation_detail`:

  ```json
  {
    "rules": {"structure": "pass", "price": "fail", "location": "pass",
              "trust": "unknown", "duplicate": "pass", "quantity_price": "pass"},
    "reasons": ["el precio cae fuera de la banda de su celda", "..."],
    "engine_version": "2026-10-03.1",
    "config": {"min_cell_n": 5},
    "validated_at": "2026-10-03T14:20:00+00:00"
  }
  ```

  No repite estado ni puntaje, que tienen columna propia. Guarda todas las reglas
  que fallaron, no solo la que ganó.

### Cuándo corre

- **Al curar** (`submit_curation`): después de armar las filas y de resolver la
  tasa, antes del `commit`, en la misma transacción. Cada fila sale con
  `provenance = human` y su validación.
- **Al editar** (`submit_edit`): se revalidan **todas** las filas que quedan del
  anuncio, no solo la tocada. `provenance` no cambia.
- **Borrar** una fila o el anuncio entero no revalida nada.
- Es una **foto**: describe la fila contra el mercado conocido en ese momento, con
  fecha y versión. Las altas, ediciones y borrados de otros anuncios no revalidan
  filas ajenas.
- Si el motor falla, el guardado **no** se cae: la fila se guarda con estado,
  puntaje y detalle nulos y el error va al log. El operador ya decidió; un fallo
  de validación no puede costarle la curaduría.

### El contexto de una fila guardada

Se arma con lo que ya tiene `saimc/validation/context.py`, solo `SELECT`:

- **Celda:** precios de `prices_history` de días estrictamente anteriores a la
  fecha del anuncio, sin las filas del propio anuncio.
- **Filas recientes del canal:** las de las 24 h previas, sin el propio anuncio.
- **Precio en CUP:** el `price_cup` que quedó en la fila después de
  `apply_resolution`, no una segunda consulta a la tasa. Así el motor mira el
  mismo número que se publica. Sin `price_cup`, las reglas 2 y 6 dan `unknown`.
- **Catálogo:** pilotos y provincias de hoy.
- **Historial del canal:** no se calcula mientras la regla 4 no tenga parámetros.
  No se paga una consulta cuyo resultado se descarta.
- No hace falta el texto ni el OCR: las reglas sobre una fila guardada no leen el
  anuncio.

### Revalidación: `python -m saimc.validation.revalidate`

- En seco por defecto; `--execute` escribe. Mismo estilo que el pre-cálculo.
- Recorre las filas de `prices_history` y les escribe estado, puntaje y detalle
  con la versión y los parámetros vigentes. No toca `provenance` ni ninguna otra
  columna.
- Sirve para tres cosas: validar las filas anteriores a B1, reaplicar un cambio de
  `min_cell_n` o de versión del motor, y refrescar después de un backfill de tasas
  o de base de precio.
- Cada fila se valida contra las referencias anteriores a **su** anuncio, así que
  el resultado no depende del orden en que se recorran.
- El resumen informa cuántas filas cambian de estado, por estado.

### `/control`

Una sección «Validación» junto a «Horas y automatización», a 390 px primero:

- cuántas filas hay en cada estado, con su nombre en español y una línea que dice
  qué significa;
- cuántas filas siguen sin validar (estado nulo);
- una nota fija mientras la regla 4 no tenga parámetros: «Ninguna fila figura como
  aprobada: la reputación de los canales todavía no se mide.»

`/control` es público. Lo que se muestra son conteos por estado; ningún detalle
por fila, ningún canal y ningún emisor.

### Textos que quedan viejos y se corrigen

`models/price_history.py` (comentarios de `confidence_score`), el docstring de
`submit_curation`, el de `validation/__init__.py`, `docs/requisitos.md` (módulo 05
«ausente»), `docs/spec-slice-3a.md` (`varchar(32)`) y el test de curaduría que
afirma que `confidence_score` queda nulo.

## Contraste con `spec.md`

- **§7.2** lista los campos de `prices_history` y no trae `validation_status`,
  detalle ni procedencia. B1 los agrega. Se anota en `docs/spec-map.md`.
- **§6.2, «Resultado de validación»:** un solo JSON con `validation_status`,
  `confidence_score` y `rules`. Acá el estado y el puntaje son columnas y el JSON
  guarda las reglas, los motivos, la versión y los parámetros.
- **§6.2 regla 1 y regla 3:** la regla 3 admite «provincia no determinada» y el
  esquema permite `province_id` nulo. La especificación del motor, ya aprobada,
  exige provincia en la regla 1. Una fila sin provincia queda `incomplete`. No se
  reabre acá.
- **§6.5** (linaje: «¿por qué el sistema confía en él?») usa otras claves que
  §6.2. B1 guarda lo necesario para responder; la forma pública es de B4.
- **§12.6** prohíbe inventar umbrales. `min_cell_n = 5` no sale de una medición
  que lo elija: es una decisión del operador para poder describir las filas, sin
  efecto sobre ningún carril. Se registra como tal.

## Casos esperados (se escriben antes que el código)

Función del motor, pura, con `min_cell_n = 5`:

1. Fila completa, celda con 5 referencias, precio dentro de la banda, sin
   duplicado, base coherente: `price`, `structure`, `duplicate` y
   `quantity_price` en `pass`; `trust` en `unknown`; estado `unverified`;
   puntaje 83.
2. La misma fila con un precio diez veces mayor que la banda: `price = fail`,
   estado `anomaly`.
3. La misma fila con 4 referencias en la celda: `price` y `quantity_price` en
   `unknown`, estado `unverified`.
4. Fila sin provincia: estado `incomplete`, aunque el precio esté fuera de la
   banda (gana la regla 1).
5. Fila cuyo precio leído con la otra base queda más cerca de la celda:
   `quantity_price = fail`, `price = unknown`, estado `inconsistent`.
6. Fila igual a otra del mismo canal en las 24 h previas: estado
   `possible_duplicate`.
7. Fila en divisa sin `price_cup`: `price` y `quantity_price` en `unknown`.
8. Con los dos parámetros de la regla 4 pasados y un canal limpio, la fila del
   caso 1 sale `approved` con puntaje 100. Sin ellos, nunca.

Guardado:

9. Al curar, cada fila queda con estado, puntaje, detalle y `provenance = human`.
   El detalle trae las seis reglas, `engine_version`, `config.min_cell_n` y
   `validated_at`.
10. Un anuncio sin propuesta del modelo se valida igual: no depende de
    `proposal_id`.
11. Al curar dos filas del mismo anuncio, ninguna entra en la celda ni en el
    duplicado de la otra.
12. Al editar el precio de una fila hasta sacarlo de la banda, pasa a `anomaly`;
    las otras filas del anuncio también se revalidan; `provenance` sigue `human`.
13. Si el motor lanza una excepción, la curaduría se guarda con los tres campos
    de validación nulos.

Migración y revalidación:

14. La 0027 sube y baja. Las filas existentes quedan con `provenance = human` y
    estado nulo. El CHECK rechaza un estado y una procedencia desconocidos.
15. `llm_proposals.prompt_version` acepta 64 caracteres después de la 0027 (se
    comprueba inspeccionando la columna: SQLite no hace cumplir el largo).
16. `revalidate` en seco no escribe; con `--execute` llena las filas con estado
    nulo y no toca `provenance`.
17. `revalidate` da el mismo resultado para una fila cualquiera sea el orden.

`/control`:

18. Muestra el conteo por estado y las filas sin validar; no muestra canales,
    emisores ni detalle por fila.

## Qué no entra

- Automatizar nada: ningún carril usa lo que B1 guarda.
- Fijar los parámetros de la regla 4 (G1b).
- Guardar las filas caídas (`dropped`) de `llm_proposals`, que A5c le había
  pedido a B1. Es una entrada del carril 1, no de la validación de una fila
  guardada. Pasa a «Pendientes sueltos», para B2b.
- Sumar el estado de validación al `detail` de los eventos de la bitácora.
- La forma pública del linaje por dato (B4).
- Revalidar solo dentro de los backfills de tasas y de base: se corre
  `revalidate` a mano después.
- Refactor del guardado compartido entre alta y edición (B2b).

## Costo

Ninguno de API. El guardado suma dos consultas por anuncio (celdas y filas
recientes del canal).

## Preguntas al operador — resueltas el 2026-10-03

El operador aprobó la especificación con las seis respuestas por defecto.

1. **`min_cell_n = 5`** para persistir. Alternativas de la grilla: 3 (opina con
   menos referencias, más `anomaly` falsos) o 10 (casi todo `unknown` hoy).
2. **Una fila curada por una persona cuenta como verificada.** Si no, ninguna
   fila anterior al modelo podría llegar nunca a `approved`.
3. **La regla 4 queda sin parámetros**: ninguna fila sale `approved` hasta G1b, y
   `/control` lo dice. La alternativa es fijarlos ahora sin medición.
4. **`revalidate` entra en B1**, y con él se validan las filas existentes. La
   alternativa es dejarlas con estado nulo y hacer el comando aparte.
5. **La columna se llama `provenance`**, no `origin` como decía el plan.
6. **La distribución de estados se publica en `/control`**, como dice el plan,
   solo con conteos.
