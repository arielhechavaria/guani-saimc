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

### Medido en producción real (`llm_proposals`, 2026-09-27) — reemplaza la proyección del backtest

**Desactualizado por un fix del mismo día: ver la nota al final de esta
sección.** Lo de abajo se midió con `propose_ad` corriendo `thinking` en su
valor por defecto (adaptive, sin pedirlo desactivado); ese mismo día se
descubrió que ese razonamiento invisible se facturaba como `output_tokens`
sin aparecer en `raw_response`, y se corrigió (`docs/decisions.md`). El costo
real bajó: la re-medición posterior al fix está en la sección siguiente,
y es la que vale.

El backtest de arriba corrió sobre una muestra armada (100 anuncios ya curados a
mano). Desde que «Completar con IA» está en uso real, cada llamada queda en
`llm_proposals` con sus tokens — ya no hace falta asumir nada, se lee directo.
Al 2026-09-27: **26 llamadas sobre 25 anuncios distintos** (2026-09-25 y
2026-09-26; ventana corta, muestra chica igual que el backtest, a re-medir con
más volumen acumulado).

| | Entrada | Salida |
| --- | --- | --- |
| Promedio por llamada | 2.858 | 1.098 |
| Mediana | 2.970 | 681 |

El promedio de salida está inflado por dos casos reales: un anuncio con muchas
filas (3.582 tokens de salida — la cita de cada fila pesa) y un caso que llegó
al tope de `max_tokens` (4.096 tokens de salida, **truncado**, no es una falla
de la API) — es el anuncio `62cd0e01` que ya motivó el slice 3c (glosario y
recorte del texto antes de enviarlo, tarea #7 del backlog), todavía sin
implementar. La mediana representa mejor el caso típico.

Costo real gastado hasta ahora: **$0,4341** (26 llamadas, Sonnet 5, tarifas de
la tabla de arriba). Por llamada: **$0,0167 de promedio / $0,0128 de mediana**
— contra los $0,0073 del backtest: la cola real, con listas completas de
WhatsApp, cuesta más que la muestra de 100 anuncios ya resueltos.

| Modelo | Por anuncio (mediana) | 1.200 anuncios al mes | Backlog de 371 |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | ~$0,0064 | ~$7,7 | ~$2,4 |
| **Claude Sonnet 5** | **$0,0128** | **~$15,3** | **~$4,7** |
| Claude Opus 5 | ~$0,0319 | ~$38,3 | ~$11,8 |

La cuenta, igual que arriba: `(tokens_entrada × precio_entrada + tokens_salida
× precio_salida) / 1.000.000`, con la mediana de tokens de esta sección. Solo
Sonnet 5 tiene dato real; Haiku y Opus son la misma cuenta con sus tarifas.

### Re-medido tras apagar `thinking` (`llm_proposals`, 2026-10-02) — la cifra vigente

Solo cuentan las llamadas con prompt `2026-09-27.1` (el primero con `thinking`
apagado), `status = 'ok'`, 2026-09-27 al 09-28. Sonnet 5, tarifas de arriba.

| Tipo de anuncio | Llamadas | Entrada (mediana) | Salida (mediana) | Costo total | **Por anuncio (promedio)** |
| --- | --- | --- | --- | --- | --- |
| Texto | 46 | 452 | 144 | $0,2029 | **$0,0044** |
| Imagen | 55 | 607 | 27 | $0,0872 | **$0,0016** |
| Ambos | 101 | — | — | $0,2901 | **$0,0029** |

Contra los $0,0128 de la medición anterior, es entre 3 y 8 veces menos: el
razonamiento invisible ya no se factura. Se usa el **promedio** (no la mediana)
porque el tope de `--max-cost` corta por suma, y el promedio de texto está por
encima de su mediana ($0,0023) por los anuncios largos.

| Modelo | Por anuncio (promedio, ambos) | 1.200 anuncios al mes | Backlog de ~400 |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | ~$0,0014 | ~$1,7 | ~$0,6 |
| **Claude Sonnet 5** | **$0,0029** | **~$3,4** | **~$1,2** |
| Claude Opus 5 | ~$0,0072 | ~$8,6 | ~$2,9 |

Solo Sonnet 5 tiene dato real; Haiku y Opus son la misma cuenta con sus
tarifas. **Muestra chica y ventana de dos días** (101 llamadas): a re-medir
cuando haya más volumen. La consulta para rehacerla: `llm_proposals` unida a
`raw_ingestions.content_type`, filtrada por `prompt_version` y `status = 'ok'`,
sumando `input_tokens` y `output_tokens`.

### Por qué Sonnet 5 y no el más barato

Los casos que llegan a esta capa son, por construcción, los que las reglas ya no
pudieron resolver — o sea los ambiguos. Ahí conviene criterio, y la diferencia
contra Haiku es de **~$1,7 al mes** (promedio de producción real, sección
re-medida). A este volumen el precio no es una restricción real: la elección la
debería gobernar la calidad.

Si la medición contra la cola real muestra que Sonnet no alcanza, subir a Opus
cuesta **~$5 más por mes**. Eso se decide con el número de la medición en la
mano, no antes.

### Palanca disponible si el volumen crece

Los catálogos cerrados y el esquema son un prefijo estable en cada llamada: con
*prompt caching* ese tramo se cobra a una fracción. Hoy no hace falta —el total
proyectado con Sonnet 5 son ~$3,4 al mes—, pero si el volumen se multiplica, ahí
está el ahorro y conviene medirlo antes de cambiar de modelo por costo.

---

## El asistente por Vercel AI Gateway (2026-10-09)

El asistente puede llamar a Claude por Vercel AI Gateway en vez de por la API
de Anthropic directo (`SAIMC_LLM_PROVIDER=vercel`; cómo cambiar, en
[comandos.md](comandos.md#proveedor-del-asistente)). Es el mismo modelo y la
misma API; cambian la URL, la clave y a quién se le paga.

- **El modelo se registra y se tarifa con su nombre canónico**
  (`claude-sonnet-5`), vaya por donde vaya. El prefijo `anthropic/` que pide el
  gateway viaja solo en la llamada. Por eso el historial de `llm_proposals` y
  de «Consumo del asistente» no se parte en dos nombres, y el tope de gasto del
  pre-cálculo sigue frenando.
- **No está verificado que el gateway cobre lo mismo que Anthropic directo.**
  Todo gasto estimado de este documento y de `/control` se calcula con las
  tarifas de Anthropic de la tabla de arriba, también para las llamadas que
  salieron por el gateway. Si el gateway cobra distinto, el estimado y el tope
  del pre-cálculo quedan corridos en esa proporción: lo que vale es la factura
  de Vercel. A comparar contra ella después de las primeras corridas.
- El saldo que se gasta es el del gateway. Con la misma clave que Jev
  (`SAIMC_LLM_JEV_API_KEY`) es **el mismo saldo de Jev**: los topes son aparte
  (`SAIMC_PREFILL_MAX_COST` y `SAIMC_LLM_JEV_MAX_COST`), el saldo no.
- El aviso «sin saldo» de `/control` reconoce el saldo agotado del gateway por
  el status 402. Tampoco está verificado contra un error real: si el gateway
  responde otra cosa, la falla sale como genérica.

---

## Jev, la señal en sombra (A6b)

Segundo proveedor, aparte de Claude: Jev (TypeSafe AI, vía Vercel AI Gateway).
Con `SAIMC_LLM_JEV_ENABLED=true` y su clave, el pre-cálculo le hace tres
preguntas cerradas a cada texto pendiente (`docs/spec-slice-A6b.md`). Apagado
no cuesta nada.

- **Tarifa:** 0,042 USD por millón de tokens de entrada; la salida no se cobra.
- **Por texto, medido** (A6, 2026-10-04, sobre 1.409 pedidos): una llamada
  pesa entre 403 y 1.574 tokens, es decir entre **0,000017 y 0,000066 USD**.
  La cifra «del orden de 0,0001 USD por anuncio» que figura en documentos
  anteriores era una estimación previa a la medición y quedó alta.
- **Por día, esperado:** a 35 textos son menos de 0,0025 USD (con el pico de
  49: 0,0032).
- **Tope:** `SAIMC_LLM_JEV_MAX_COST`, 0,01 USD por corrida del pre-cálculo.
  Con 8 corridas por día el peor caso es **0,08 USD diarios**. Es un tope
  aparte del de Claude (`SAIMC_PREFILL_MAX_COST`): ninguno se come el del otro.
- **Dónde verlo:** `/control`, sección «Jev (señal en sombra)», que suma el
  costo que informó el gateway en cada respuesta guardada (`jev_signals.cost_usd`).

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
