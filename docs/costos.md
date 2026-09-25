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
| Claude Pro | *a completar* | Herramienta de desarrollo del operador, no del sistema. **No cubre la API**: ver la sección del asistente. |
| Dominio | *a completar* | |
| API de elTOQUE (TRMI) | *a completar* | Requiere `api_key` (`SAIMC_ELTOQUE_API_KEY`). Si el plan en uso es gratuito, poné 0 y dejalo escrito: la gratuidad de un tercero es un supuesto que caduca. |
| WAHA | 0 | Se autoaloja en el mismo VPS; no suma costo aparte. |

---

## Asistente de extracción por LLM (construido, apagado hasta que se configure)

Corresponde a `openspec/changes/llm-extraction-assist/`. El modelo solo entra
donde las reglas ya no resolvieron, y **solo cuando vos apretás el botón** — el
costo no corre solo. Sin clave configurada el botón no aparece y no se gasta
nada; los pasos para encenderlo están en el runbook del asistente.

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

### Es un costo ADICIONAL al plan Pro, no está incluido

Son dos facturas separadas y conviene tenerlo claro antes de mirar la tabla:

| | Qué cubre | Relación con esto |
| --- | --- | --- |
| **Claude Pro** (~$20/mes, ya se paga) | Claude en la app web/móvil y Claude Code | **No incluye créditos de API.** No paga nada de lo de abajo. |
| **API de Anthropic** | Llamadas programáticas desde el código de SAIMC | Es lo que costaría este asistente, con saldo propio. |

El acceso programático se cobra por token contra un saldo que se carga aparte
en `console.anthropic.com`. El primer desembolso no es el costo mensual sino la
carga mínima de créditos que pida la consola; a este consumo, esa carga dura
varios meses.

**La alternativa de costo cero existe**: hacer la asistencia a mano con el plan
Pro que ya se paga, pegando el texto del anuncio difícil y leyendo la
respuesta. Lo que compran los dos dólares es no copiar y pegar ~20 veces por
día y que el valor caiga directo en el formulario. A este volumen la cuenta
cierra, pero la opción de no gastar nada es real y queda escrita acá.

### Costo por modelo

Precios oficiales de la API de Anthropic, por millón de tokens:

| Modelo | Entrada | Salida | **Costo mensual** |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | $1,00 | $5,00 | **$0,93** |
| **Claude Sonnet 5** (recomendado) | $2,00 | $10,00 | **$1,86** |
| Claude Opus 5 | $5,00 | $25,00 | **$4,65** |

La cuenta, para que la puedas rehacer: `(0,48 × precio_entrada) + (0,09 ×
precio_salida)`.

### Medido con el backtest (2026-09-25) — reemplaza los supuestos de arriba

Cuando el asistente cura **toda la cola** (una llamada por anuncio con todas las
filas, `saimc/extraction/llm_propose_ad.py`), el supuesto de ~800 tokens de
entrada por llamada deja de valer: los catálogos suman ~730 tokens fijos y las
listas de WhatsApp son largas. Medido sobre 100 anuncios reales con Sonnet 5 y el
prompt actual (`2026-09-25.3`, que además pide la cita de cada fila):
**~2.407 tokens de entrada y ~245 de salida por anuncio, $0,0073 por anuncio**
(100 anuncios costaron $0,7262; con el prompt anterior, sin cita, eran $0,0060).

| Modelo | Por anuncio | 1.200 anuncios al mes | Backlog de 371 |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | ~$0,0036 | ~$4,4 | ~$1,3 |
| **Claude Sonnet 5** | **$0,0073** | **~$8,7** | **~$2,7** |
| Claude Opus 5 | ~$0,0182 | ~$21,8 | ~$6,7 |

Sonnet 5 sale de la medición; Haiku y Opus son la misma cuenta con sus tarifas, no
están medidos. La cuenta: `(tokens_entrada × precio_entrada + tokens_salida ×
precio_salida) / 1.000.000`. Si Haiku alcanza en calidad, ahorra ~$4,3 al mes: eso
se decide midiéndolo (`llm_backtest --model`), no antes. Volumen de referencia: el
ritmo real de WAHA, a confirmar al ejecutar el plan
(`docs/plan-asistente-llm-cola-completa.md`).

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

## Dónde ver el gasto real

Desde que el asistente hace su primera llamada, `/control` muestra la sección
**«Consumo del asistente»** con las invocaciones, los tokens y el gasto
estimado, calculado con las mismas tarifas de la tabla de arriba.

**Las tarifas están en dos lugares**: acá para leerlas, y en
`saimc/extraction/llm_assist.py` para calcular. Si cambian, hay que moverlas
en los dos — está anotado también del lado del código.

## Cuándo actualizar este documento

- Cuando cambie el volumen de curaduría de forma sostenida (los supuestos de
  arriba dejan de valer).
- Cuando se adopte o se cambie un modelo.
- Cuando aparezca un servicio pago nuevo.
- Cuando completes una fila marcada *a completar*: borrá la marca y dejá el
  número real.
