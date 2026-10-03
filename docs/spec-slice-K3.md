# Slice K3 — Guardar la transcripción del OCR

Parte del plan `docs/plan-factor-humano.md`. Toca esquema (migración 0028): esta
especificación se aprueba antes de programar. No toca el motor de validación ni
`prices_history`.

## Problema

1. El texto del OCR solo queda en `raw_ingestions.original_text`, y solo cuando el
   operador cura la imagen. Lo que queda ahí es lo que el operador confirmó o
   corrigió, no lo que el OCR leyó.
2. Cada imagen se transcribe más de una vez: en el pre-cálculo
   (`llm_propose_ad.ad_text`), al abrirla en la cola (`_curation_view`) y otra vez
   cuando el motor arma su contexto (`validation.context.ad_text`). Con RapidOCR
   son cerca de 0,9 s por pasada.
3. Las imágenes descartadas nunca guardan su texto: no se pueden auditar por texto
   ni entrar a A6.
4. `ocr._transcribe_rapidocr` se queda con `txts` y tira las cajas (`boxes`) y los
   puntajes (`scores`) que RapidOCR devuelve. C2a las necesita para enmascarar.

## Qué se construye

### Tabla `ocr_transcriptions`

Derivada y de solo altas. No toca `raw_ingestions` (`spec-map.md`: la salida
derivada es aditiva).

| Columna | Tipo | Nota |
| --- | --- | --- |
| `id` | uuid, PK | |
| `ingestion_id` | uuid, FK a `raw_ingestions.id`, `RESTRICT` | con índice |
| `engine` | varchar(16), CHECK | `rapidocr` / `tesseract` |
| `ocr_version` | varchar(32) | la constante `OCR_VERSION` de `ocr.py` al transcribir |
| `text` | text, no nulo | redactado con `redact_personal_data` |
| `boxes` | JSON, nulo | solo con `rapidocr`; ver abajo |
| `created_at` | timestamptz | |

- `UNIQUE (ingestion_id, ocr_version)`: una transcripción por imagen y por versión
  del OCR. Dos procesos que transcriben la misma imagen a la vez no dejan dos filas.
- El código no tiene camino de escritura sobre una fila existente ni de borrado.
- `boxes`: una lista, en el orden de `text`, de
  `{"text": …, "box": [[x, y] × 4], "score": 0.97, "redacted": true|false}`.
  `text` va redactado por renglón; `redacted` dice si ese renglón traía un teléfono.
  Es lo que C2a necesita para saber qué caja tapar sin guardar el número.
- Tesseract no da cajas: `boxes` queda nulo.

### Privacidad (`spec.md` §10.6)

Nunca se guarda el texto sin redactar, ni en `text` ni en `boxes`. La redacción de
hoy saca teléfonos, no nombres: es la misma garantía que ya tiene
`raw_ingestions.original_text` y K3 no la empeora ni la mejora. Los nombres son de
C2a.

### `ocr.py`

- `transcribe(path)` sigue devolviendo un `str` y no cambia de comportamiento: lo
  usan `measure` y el backtest, que quedan como están.
- Se suma `transcribe_detailed(path) -> OcrResult` (`text`, `engine`, `boxes`), que
  es la que conserva las cajas. `transcribe` pasa a ser `transcribe_detailed(...).text`.
- `OCR_VERSION`: una constante que se sube a mano cuando cambia algo que cambia el
  resultado (el orden de la cascada, el `--psm`, la versión de los modelos).

### Un solo camino de lectura

Una función nueva, `stored_transcription(db, ingestion, raw_store_dir)`:

1. Si hay fila para `(ingestion_id, OCR_VERSION)`, devuelve su texto. No corre el OCR.
2. Si no, transcribe, redacta, guarda la fila y devuelve el texto.
3. Si la transcripción sale vacía **no** guarda nada: un archivo que todavía no
   está o un fallo puntual no quedan congelados como «sin texto». Esas imágenes se
   vuelven a intentar, como hoy.

La usan los tres lugares que hoy transcriben por su cuenta: el pre-cálculo, la cola
al abrir una imagen y el contexto del motor. La semántica de cada uno no cambia:

- el modelo recibe el mismo texto (ya se redactaba antes de mandarlo);
- el motor opina sobre el mismo texto que vio el modelo;
- el campo editable de la cola muestra la transcripción guardada. Hoy muestra el
  OCR sin redactar y redacta al guardar; pasa a mostrarlo ya redactado.

### Qué no cambia

- `raw_ingestions.original_text` sigue siendo lo que confirmó el operador al curar.
- `measure` y el backtest siguen transcribiendo de nuevo: H0 mide el extractor de
  hoy contra el oro, y una transcripción vieja guardada lo falsearía si
  `OCR_VERSION` no se subió. Queda fuera.

## Qué no entra

- Transcribir en la ingesta (el webhook no corre OCR: decisión cerrada 3 y §6.1).
- Un backfill de las imágenes viejas. Se llenan solas al pasar por el pre-cálculo o
  la cola; las ya curadas o descartadas quedan sin fila. Si A6 las necesita, el
  backfill es un comando aparte, en A6.
- Enmascarar la imagen (C2a) y mandar imágenes al modelo de visión (C2).
- Mostrar las cajas en la interfaz.

## Tests primero

Casos definidos acá, antes del código:

1. Abrir una imagen pendiente en la cola guarda una fila con `engine`, `ocr_version`
   y el texto.
2. Abrirla otra vez no vuelve a correr el OCR (el transcriptor de prueba cuenta sus
   llamadas: 1) y no agrega una segunda fila.
3. El pre-cálculo y después la cola sobre la misma imagen: una sola transcripción.
4. Un texto de OCR con un teléfono se guarda sin el número, en `text` y en `boxes`,
   y la caja de ese renglón queda con `redacted = true`.
5. Con RapidOCR, `boxes` tiene una entrada por renglón, en el mismo orden que `text`.
   Con Tesseract, `boxes` es nulo.
6. Una transcripción vacía no guarda fila, y la siguiente apertura vuelve a intentar.
7. Subir `OCR_VERSION` hace que se transcriba de nuevo y agrega una fila; la vieja
   sigue ahí.
8. Una imagen descartada después de pasar por el pre-cálculo conserva su fila.
9. `transcribe(path)` devuelve lo mismo que antes para los tests que ya existen.
10. La migración sube y baja.

## Verificación del operador

1. `pg_dump`, push, `alembic upgrade head`, reinicio de `saimc.service`.
2. Correr el pre-cálculo sobre imágenes pendientes y abrir una de ellas en
   `/curation`: abre sin la espera del OCR.
3. `SELECT engine, ocr_version, count(*) FROM ocr_transcriptions GROUP BY 1, 2;`
   muestra filas, una por imagen.
4. Descartar una imagen: su fila sigue en la tabla.
5. **Lo que no tiene que pasar:** un teléfono en `text` o en `boxes`, dos filas de
   la misma imagen con la misma versión, o un cambio en `raw_ingestions`.

## `decisions.md`

Sí: la transcripción se guarda redactada en una tabla derivada, con versión del OCR,
y `measure` sigue transcribiendo de nuevo.
