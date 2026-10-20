# Slice «OCR como texto»

Decisión: `docs/decisions.md`, 2026-10-20. Apartamiento consciente de la decisión 2 de `CLAUDE.md`.
Spec rectora: `spec.md` §6.1 («Rol del OCR y la extracción asistida»): el sistema propone y la
persona confirma; este slice abre la aceptación automática del OCR bajo cotas propias.

## Medición previa (2026-10-20, conjunto de referencia v2, `scripts/ocr_vs_native.py`)

| | imagen (OCR) | texto nativo |
|---|---|---|
| filas / anuncios | 87 / 73 | 266 / 147 |
| precio confirmado aparece en el texto | 75,9 % | 83,8 % |
| reglas: producto / precio / completo | 80,5 / 32,2 / 11,5 % | 86,8 / 62,0 / 47,7 % |

Jev se calibró solo con textos (oro v7); su umbral 0,2 no vale para OCR hasta remedirlo.

## Reglas del diseño

1. **Un solo texto del anuncio.** Para imagen es `ocr_store.stored_transcription` (ya redactada;
   `redact_personal_data` es idempotente). Ningún carril lee `original_text` de una imagen.
2. **Tres interruptores, todos apagados por defecto, en `.env` y `env.example`:**
   - `SAIMC_LANE0_IMAGE_ENABLED` (ya existe): descarte automático de imágenes.
   - `SAIMC_LANE1_IMAGE_ENABLED` (nuevo; exige `SAIMC_LANE1_ENABLED`): aceptación automática de imágenes.
   - `SAIMC_JEV_OCR_ENABLED` (nuevo; exige Jev activo): Jev sobre el texto del OCR, como señal
     (`jev_signals`), como segundo juez y como árbitro del carril 0.
3. **Errores del OCR aparte de los del texto.** El carril 1 tiene freno, auditoría y cotas por
   tipo: el freno de imágenes sale solo de filas auto de imagen auditadas (mismo criterio: error
   auditado > 10 %, primeros 60 auditados completos); el de texto, solo de las de texto. Un
   freno puesto en un tipo no frena al otro. Los informes (`/control`, resumen, cotas) muestran
   los dos por separado.
4. **Origen visible.** El evento `auto` de una fila aceptada por OCR guarda
   `detail["text_source"] = "ocr"` (sin migración; `detail` es JSON). `provenance` no cambia.
5. **Fuera de H0.** Una fila aceptada por OCR queda fuera del conjunto de referencia como toda
   fila auto. No se toca `reference_set`.
6. **Publicación.** `cells.evidence_is_public` sigue siendo solo texto: una fila de imagen
   aceptada sale en las series sin enlace a la evidencia. No se cambia en este slice.
7. **Pre-cálculo.** `select_stale_candidates` deja de excluir imágenes (el OCR ya está guardado,
   rehacer no suma OCR). `select_candidates` y `run_prefill` pasan el texto del OCR (redactado) a
   carril 0, carril 1 y Jev. El foco de una imagen con OCR guardado se decide con ese texto.
8. **Fecha.** `_published_date` sobre texto de OCR puede fallar o errar: si no sale una fecha
   confiable, el anuncio queda pendiente (el comportamiento actual de `None`); no se inventa.
9. **Sin migración** salvo que un test demuestre que hace falta. Si hace falta, parar y avisar.

## Tests primero (casos independientes del código)

- Con `SAIMC_LANE1_IMAGE_ENABLED` apagado, una imagen con OCR perfecto NO se acepta sola; encendido
  y con propuesta completa, sí, y el evento trae `text_source = "ocr"`.
- Freno por tipo: 60 imágenes auto auditadas con error > 10 % frenan las imágenes y NO frenan los
  textos; y al revés.
- `jev_signal.select_pending` incluye una imagen con OCR guardado solo con `SAIMC_JEV_OCR_ENABLED`;
  un texto, siempre como hoy. Una imagen sin OCR guardado no entra (no se paga OCR para preguntar).
- Carril 0: con el interruptor de Jev-OCR apagado una imagen nunca tiene segundo juez; encendido, sí.
- `run_prefill` pasa a las tres piezas el texto redactado (sin teléfonos) de la imagen.
- Los textos nativos no cambian de comportamiento: toda la suite actual sigue en verde.

## Fuera de alcance

Publicar evidencia de imágenes, visión directa, remedir H0, y encender cualquier interruptor en
producción (lo decide el operador tras el en seco).
