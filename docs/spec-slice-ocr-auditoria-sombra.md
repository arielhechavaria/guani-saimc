# Slice «Auditoría en sombra de las imágenes (carril 1)»

Continúa `docs/spec-slice-ocr-como-texto.md` y la entrada del 2026-10-20 de `docs/decisions.md`.
Motivo: el en seco del carril 1 dice que 131 de 280 imágenes entrarían solas (los textos, 0 de 121),
contra 11,5 % de extracción completa de las reglas sobre OCR. Esa cifra es lo que el carril
*aceptaría*, no lo que *acertaría*. Antes de encender `SAIMC_LANE1_IMAGE_ENABLED` hay que medir el error.

## 1. Medición en sombra (sin escribir filas, sin migración)

Comando de solo lectura: `python -m saimc.validation.lane1 --sombra-imagenes [--n N]`.

**A. Hacia atrás (ya hay datos).** Universo: imágenes ya curadas por una persona (`provenance`
`human`) con una propuesta `ok` del modelo y OCR guardado. Para cada una, el carril 1 decide con las
reglas de HOY (misma función `decide`, mismo texto de OCR redactado, sin freno ni escritura) si la
habría aceptado. De las que habría aceptado, se compara la propuesta del modelo con las filas
finales curadas, por fila y con el mismo criterio de «correcto» que la auditoría del carril 1
(`corrected`/`invalid` cuentan como error; `completed` no). Salida: n, aciertos, error y su cota
superior (mismo cálculo de cota que el carril 1 usa por tipo), y los motivos de las que quedan
afuera. Aviso en la salida: la persona vio la propuesta antes de curar (anclaje), así que el error
real es **igual o mayor**; es un piso, no la cifra final.

**B. Hacia adelante.** Lista las imágenes pendientes que el carril 1 aceptaría hoy (id corto, canal,
resumen de la propuesta), hasta `--n`, para que la persona las cure primero en la cola. Su curaduría
alimenta A en la corrida siguiente. No cambia el orden de la cola ni crea eventos.

No toca `raw_ingestions`, no crea filas de precio, no escribe eventos. Con el interruptor de
imágenes apagado funciona igual (es un en seco).

## 2. Tope de arranque propio de las imágenes

`SAIMC_LANE1_IMAGE_WEEKLY_CAP` (entero; por defecto **20**; 0 = sin tope): mientras las imágenes
auditadas sean menos que el arranque (60), el carril 1 acepta como máximo ese número de imágenes
por semana ISO. Superado el arranque el tope deja de aplicar. No afecta a los textos. Si
`SAIMC_LANE1_MAX_ROWS` ya limita, rige el menor. Negativo falla al arrancar (igual que
`SAIMC_LANE1_MAX_ROWS`). Se documenta en `env.example` y `docs/comandos.md`. El interruptor
`SAIMC_LANE1_IMAGE_ENABLED` sigue apagado por defecto.

## Tests primero (casos definidos sin mirar el código)

- Imagen curada por una persona con propuesta idéntica a las filas finales y que cumple las reglas
  del carril 1: cuenta como acierto. Misma imagen con el precio corregido por la persona: error.
- Imagen que el carril 1 rechazaría (cita no verificada): no entra al universo de aceptadas y su
  motivo aparece en la salida.
- Texto nativo curado: no entra a la medición de imágenes.
- El comando no escribe nada (conteo de filas de `prices_history`, `curation_events`, `llm_reviews`
  igual antes y después) y funciona con `SAIMC_LANE1_IMAGE_ENABLED` apagado.
- Tope: con tope 2 y 5 imágenes aceptables en la misma semana ISO, se aceptan 2; con 60 imágenes ya
  auditadas, se aceptan las 5; con tope 0, las 5; los textos no se limitan en ningún caso.
- Valor negativo del tope falla al construir la configuración.
- Toda la suite actual sigue en verde.

## Fuera de alcance

Cambiar la verificación de la cita para tolerar ruido de OCR, Jev sobre OCR, usar las cajas del
OCR, ocultar del público las filas auto de imagen, reordenar la cola. Encender interruptores.
