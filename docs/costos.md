# Costos de operación

Qué cuesta tener SAIMC funcionando, y qué costaría cada capacidad que todavía no
está construida. Existe para que ninguna decisión técnica se tome sin el número
al lado.

**Regla de este documento: nada de cifras inventadas.** Lo que está calculado
trae su cuenta a la vista para que puedas rehacerla cuando cambie el volumen. Lo
que solo sabés vos —lo que efectivamente pagás— queda marcado *a completar*, en
blanco, nunca estimado por aproximación.

---

## Lo que ya se paga

| Concepto | Costo mensual | Nota |
| --- | --- | --- |
| VPS | *a completar* | Hetzner, 2 vCPU, 3,7 GB RAM, 38 GB de disco, sin GPU. Corre Postgres, la app y WAHA. |
| Dominio | *a completar* | |
| API de elTOQUE (TRMI) | *a completar* | Requiere `api_key` (`SAIMC_ELTOQUE_API_KEY`). Si el plan en uso es gratuito, poné 0 y dejalo escrito: la gratuidad de un tercero es un supuesto que caduca. |
| WAHA | 0 | Se autoaloja en el mismo VPS; no suma costo aparte. |

---

## Asistente de extracción por LLM (propuesto, todavía no construido)

Corresponde a `openspec/changes/llm-extraction-assist/`. El modelo solo entra
donde las reglas ya no resolvieron, y **solo cuando vos apretás el botón** — el
costo no corre solo.

### Supuestos de la cuenta

Salen del dump de producción del 2026-08-30, no de una estimación de escritorio:

| Supuesto | Valor | De dónde sale |
| --- | --- | --- |
| Anuncios por día | ~40 | 860 ingestas en ~3 semanas |
| Llamadas por día | ~20 | Solo los anuncios con campos sin resolver, y solo los que decidís asistir |
| Llamadas por mes | ~600 | 20 × 30 |
| Tokens de entrada por llamada | ~800 | Catálogos cerrados + esquema JSON + el texto del anuncio ya redactado |
| Tokens de salida por llamada | ~150 | El JSON de respuesta, corto por diseño |

Eso da **480.000 tokens de entrada** y **90.000 de salida** al mes.

### Costo por modelo

Precios oficiales de la API de Anthropic, por millón de tokens:

| Modelo | Entrada | Salida | **Costo mensual** |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | $1,00 | $5,00 | **$0,93** |
| **Claude Sonnet 5** (recomendado) | $2,00 | $10,00 | **$1,86** |
| Claude Opus 5 | $5,00 | $25,00 | **$4,65** |

La cuenta, para que la puedas rehacer: `(0,48 × precio_entrada) + (0,09 ×
precio_salida)`.

### Por qué Sonnet 5 y no el más barato

Los casos que llegan a esta capa son, por construcción, los que las reglas ya no
pudieron resolver — o sea los ambiguos. Ahí conviene criterio, y la diferencia
contra Haiku es **menos de un dólar al mes**. A este volumen el precio no es una
restricción real: la elección la debería gobernar la calidad.

Si la medición contra la cola real muestra que Sonnet no alcanza, subir a Opus
cuesta **$2,79 más por mes**. Eso se decide con el número de la medición en la
mano, no antes.

### Palanca disponible si el volumen crece

Los catálogos cerrados y el esquema son un prefijo estable en cada llamada: con
*prompt caching* ese tramo se cobra a una fracción. Hoy no hace falta —el total
es de dos dólares—, pero si el volumen se multiplica, ahí está el ahorro y
conviene medirlo antes de cambiar de modelo por costo.

---

## Lo que NO cuesta lo que parecía

Quedan anotados dos descartes, para no volver a discutirlos desde cero:

**Modelo local en el VPS.** Se evaluó y no entra: 2 vCPU, sin GPU, y con
Chromium abierto quedan ~431 MB libres. El VLM más chico útil necesita 1–3 GB
residentes. No es "más lento pero anda": es tumbar la producción. Un modelo
local dejaría de ser inviable solo con otro VPS, y ese costo es mayor que los
dos dólares mensuales de la API.

**Más RAM en el VPS para el LLM.** No hace falta. El asistente es una llamada
por red a la API; el modelo corre del otro lado. La restricción de hardware
aplicaba al modelo local descartado, no a esto.

**OCR.** RapidOCR corre local sobre los modelos PP-OCR: 417 MB de disco y
~3,5 s por imagen, **cero costo por uso**. Tesseract queda de respaldo, también
gratis.

---

## Cuándo actualizar este documento

- Cuando cambie el volumen de curaduría de forma sostenida (los supuestos de
  arriba dejan de valer).
- Cuando se adopte o se cambie un modelo.
- Cuando aparezca un servicio pago nuevo.
- Cuando completes una fila marcada *a completar*: borrá la marca y dejá el
  número real.
