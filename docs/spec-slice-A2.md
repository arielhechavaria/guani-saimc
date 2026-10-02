# Slice A2 — Bitácora de curaduría

Parte del plan `docs/plan-factor-humano.md` (A2a especifica, A2b implementa).
Toca esquema: esta especificación se aprueba antes de programar.

## Problema

1. No se puede medir cuánto tiempo humano cuesta un anuncio. La hora en que se
   mostró (`shown_at`) solo se guarda al curar y solo si había una propuesta del
   modelo a la vista (`llm_reviews.shown_at`).
2. El descarte sin propuesta, el lote «sin foco» y la eliminación de un curado no
   guardan ninguna hora. `raw_ingestions` no tiene `updated_at`.
3. Editar o eliminar un anuncio ya curado no deja rastro: el valor anterior de la
   fila se pierde.

Sin esto no hay línea de base de horas por 1.000 anuncios (A2d), ni auditoría de
los carriles automáticos (B2), ni historial en `/edit` (K1).

## Hechos de partida, corregidos

El mapa del código del 2026-10-02 corrige dos de los hechos que anotaba el plan:

- «Ningún descarte guarda la hora» vale a medias. Un descarte individual con
  propuesta a la vista escribe `llm_reviews`, y su `created_at` es la hora del
  descarte. Lo que le falta es `shown_at`.
- «`discard_curation` ignora el `shown_at` que el formulario le envía» es
  impreciso. La función no declara el parámetro, y el formulario solo lo manda
  cuando hay propuesta (los botones reusan `curation-form`). Los otros tres
  formularios de descarte de `curation.html` no lo llevan: `discard-form`, el
  atajo «sin candidatos» y el de «probable descarte».

Otros dos hechos que condicionan el diseño:

- `shown_at` se sella en el servidor al armar la vista (`_curation_view`), no en
  el navegador. «Completar con IA» vuelve a renderizar y **reinicia el reloj**.
- `delete_curated` deja el anuncio en `discarded` con `discard_reason` nulo.

## Qué se construye

### Tabla `curation_events` (una fila por decisión del operador, inmutable)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `ingestion_id` | UUID FK → `raw_ingestions.id`, RESTRICT, índice | |
| `action` | varchar(16), CHECK | `curated` / `discarded` / `deleted` / `edited` |
| `mode` | varchar(24), CHECK | `individual` / `batch_out_of_focus`. `batch_model` (A1b) y `auto` (B2) amplían el CHECK en su propia migración |
| `discard_reason` | varchar(16) null, CHECK `off_focus`/`no_data` | solo con `action = discarded` |
| `proposal_id` | UUID null, FK → `llm_proposals.id`, RESTRICT | la propuesta que estaba a la vista, si había |
| `shown_at` | timestamptz null | cuándo se mostró lo que el operador decidió |
| `decided_at` | timestamptz | cuándo se guardó la decisión (`utc_now()`) |
| `detail` | JSON null | ver abajo |

Convenciones del repo, sin novedad: `String` + `CheckConstraint`
(`ck_curation_events_<columna>`), `sa.JSON` y no JSONB, hora puesta por Python,
índice `idx_curation_events_ingestion_id`. El molde es la migración 0021.

Nunca se actualiza ni se borra: el código no tiene camino de escritura sobre una
fila existente.

### Qué escribe cada camino

Los cinco están en `web/routers/curation.py` y cada uno tiene un único
`db.commit()`. El evento se agrega a la sesión **antes** de ese commit: o quedan
el cambio y su evento, o no queda ninguno.

| Camino | `action` | `mode` | `discard_reason` | `shown_at` | `detail` |
|---|---|---|---|---|---|
| `submit_curation` | `curated` | `individual` | — | el del formulario | `{"row_count": N}` |
| `discard_curation` | `discarded` | `individual` | el elegido | el del formulario | nulo |
| `discard_out_of_focus` | `discarded` | `batch_out_of_focus` | `off_focus` | el de la lista | `{"batch_size": N}` |
| `delete_curated` | `deleted` | `individual` | — | nulo | filas borradas |
| `submit_edit` | `edited` | `individual` | — | el del formulario | antes y después |

- `proposal_id` se llena con la misma propuesta que resuelve
  `llm_store.resolve_proposal`. Un `proposal_id` ajeno queda nulo, igual que hoy
  no escribe revisión.
- **Lote sin foco:** un evento por anuncio efectivamente descartado (los ids que
  ya no califican no escriben nada). Todos comparten el mismo `shown_at` y el
  mismo `decided_at`, tomado una sola vez. El lote se reconoce por
  `(mode, decided_at)`; `batch_size` permite repartir el tiempo sin recontar.
- **Un guardado rechazado (422) no escribe evento.** El `shown_at` original viaja
  en el re-render, así que el tiempo de corregir el error queda dentro de la
  duración.

### `shown_at`

- Lo sella el servidor al armar la vista y viaja en un campo oculto. Se suma a
  los tres formularios de descarte que hoy no lo llevan, a `/curation/sin-foco`
  y a `/curation/{id}/edit`.
- Un ayudante único lo parsea: vacío o malformado da nulo y no bloquea nada
  (como hoy al curar). Una hora sin zona se toma como UTC.
- **«Completar con IA» conserva el `shown_at` original** en vez de reiniciarlo:
  el tiempo de espera del modelo es tiempo del operador frente al anuncio.
  Cambia también lo que recibe `llm_reviews.shown_at`. Ver pregunta 1.
- `delete_curated` no lleva `shown_at`: se dispara desde el historial, donde no
  hay un «momento en que se mostró» que signifique algo.

### `detail`

Los valores van como texto, igual que en `llm_reviews.rows`. Solo campos de
`prices_history`; nunca texto del anuncio ni notas.

Campos de fila: `product_id`, `province_id`, `price`, `currency`, `quantity`,
`unit`, `sale_format`, `price_basis`, `format_id`, `variant_id`,
`min_purchase_quantity`, `published_date`, `price_cup`, `exchange_rate`,
`exchange_rate_date`, `exchange_rate_source`.

- **`deleted`:** `{"rows": [{"price_history_id": …, "before": {…campos de fila…}}]}`,
  una entrada por fila borrada.
- **`edited`:**
  ```json
  {
    "rows": [
      {"price_history_id": "…", "op": "updated",
       "before": {"price": "850.00"}, "after": {"price": "900.00"}},
      {"price_history_id": "…", "op": "deleted", "before": {…campos de fila…}}
    ],
    "source_reference": {"before": "…", "after": "…"}
  }
  ```
  En `updated`, `before` y `after` traen solo los campos que cambiaron. Una fila
  sin cambios no aparece. `source_reference` aparece solo si cambió.
- Una edición guardada sin ningún cambio escribe igual su evento, con
  `"rows": []`: el operador abrió, miró y confirmó, y ese tiempo cuenta.

### Relación con `llm_reviews`

No la reemplaza y no se toca. Las revisiones comparan propuesta y valor final;
la bitácora registra la acción y su tiempo, haya o no propuesta.
`llm_reviews.shown_at` se sigue escribiendo: lo lee la métrica de sello
automático (A2c).

### Pausas

Una duración absurda (el operador se levantó a mitad de un anuncio) se guarda
tal cual. Lo mismo un `shown_at` posterior a `decided_at`. El recorte se decide
al medir (A2d), no al escribir.

### Migración

`0024_curation_events.py` (la cabeza hoy es `0023`). Crea la tabla y su índice;
el downgrade los elimina. Sin backfill: el pasado no tiene hora.

## Contraste con `spec.md`

- §6.5 pide que todo dato publicado pueda responder «¿qué procesos atravesó?».
  La bitácora aporta a eso; no contradice nada.
- §7.1: `raw_ingestions` no cambia. La tabla nueva es aditiva y suma una cuarta
  FK RESTRICT hacia ella.
- §10.6: la bitácora no guarda texto del anuncio, notas ni identificadores de
  anunciante.
- No hay apartamiento que registrar.

## Qué no entra

- Leer la bitácora: el panel de horas (A2d) y el historial de `/edit` (K1).
- `batch_model` y `auto` como valores de `mode` (A1b y B2).
- Corregir que `llm_reviews.rows` quede desfasado después de una edición o una
  eliminación. Es así por diseño desde el slice 3a.
- `catalog.delete_format`, que pone `format_id` en nulo en bloque sin dejar
  rastro, y los backfills por línea de comandos. No son decisiones de la cola;
  quedan anotados en «Pendientes sueltos» del plan.

## Descartado

- **Una columna `decided_at` en `raw_ingestions`.** Da una sola hora por
  anuncio, no distingue lote de individual, y no sirve para auditar ni para el
  historial de `/edit`. Además muta la tabla que se quiere tocar lo menos
  posible.
- **Sumar `shown_at` y el modo a `llm_reviews`.** Esa tabla exige una propuesta
  (`proposal_id` NOT NULL), y la mayoría de los descartes no tiene.
- **Una columna `batch_id`.** `(mode, decided_at)` ya identifica el lote, porque
  la hora se toma una sola vez.
- **Sellar `shown_at` en el navegador.** Mediría mejor, pero depende del reloj
  del teléfono. La diferencia con el sello del servidor es el tiempo de carga de
  la página, que también es espera del operador.

## Casos de prueba (se escriben antes que el código)

1. Curar un anuncio de 2 filas: un evento `curated`, `individual`,
   `detail.row_count == 2`, con `shown_at` y `decided_at`.
2. Curar con propuesta a la vista: el evento lleva ese `proposal_id`.
3. Un `proposal_id` ajeno al anuncio: el evento se escribe con `proposal_id`
   nulo.
4. Descartar sin propuesta: evento `discarded`, `individual`, con el motivo
   elegido y `shown_at` no nulo.
5. Descartar con propuesta: lo mismo, más `proposal_id`.
6. Un `shown_at` malformado o vacío queda nulo y la acción se guarda.
7. Un `shown_at` sin zona se guarda como UTC.
8. Lote sin foco con 3 ids, de los cuales uno ya no califica: 2 eventos
   `batch_out_of_focus` con `off_focus`, mismo `shown_at`, mismo `decided_at` y
   `batch_size == 2`. El tercero no tiene evento.
9. Eliminar un curado de 2 filas: un evento `deleted` con las 2 filas en
   `detail.rows`, `shown_at` nulo.
10. Editar el precio de una fila de dos: un evento `edited` con una sola entrada
    `updated`, y `before`/`after` solo con `price` (y los derivados de tasa si
    cambiaron).
11. Editar borrando una fila: entrada `op = deleted` con todos sus campos.
12. Editar sin cambiar nada: evento `edited` con `rows` vacío.
13. Cambiar `source_reference` en la edición: aparece con antes y después.
14. Un guardado que da 422 (curar o editar) no escribe evento.
15. Atomicidad: si la escritura del evento falla, el anuncio sigue `pending` y
    no hay filas nuevas en `prices_history`.
16. «Completar con IA» y después curar: `shown_at` es el de la primera vista, no
    el del re-render.
17. La migración 0024 crea y elimina la tabla (upgrade y downgrade).

## Preguntas abiertas para el operador

Cada una trae una respuesta por defecto. Si no se contesta otra cosa, A2b
implementa el defecto.

1. **¿«Completar con IA» conserva el reloj o lo reinicia?** Defecto: lo
   conserva. Consecuencia: los tiempos de `llm_reviews` posteriores a A2b dejan
   de ser comparables con los anteriores en los anuncios donde se pidió
   asistencia a mano. Con el pre-cálculo son la minoría.
2. **¿Una edición sin cambios escribe evento?** Defecto: sí.
3. **¿Eliminar un curado necesita motivo?** Defecto: no. Hoy no se pide, y
   agregarlo es un cambio de pantalla que no hace falta para medir.

## Registro

Entrada en `docs/decisions.md`: la bitácora es una tabla propia de solo altas,
no reemplaza a `llm_reviews`, el reloj lo sella el servidor y sobrevive a
«Completar con IA», y las duraciones se guardan sin recortar.
