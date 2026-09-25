# Runbook — Poner en marcha el asistente de extracción

Pasos para activar el asistente por LLM, en orden. Seguilos como están; cada
uno tiene una verificación que confirma que salió bien antes de pasar al
siguiente.

**Hoy el asistente está construido pero apagado.** Sin clave configurada el
botón no se renderiza y la cola funciona exactamente como siempre. Nada de lo
que sigue es obligatorio: si no hacés ningún paso, no cambia nada y no se
gasta un peso.

Qué hace y por qué está así: `docs/decisions.md`, entrada del 2026-09-01.
Cuánto cuesta: `docs/costos.md`.

---

## Antes de empezar: lo que tenés que saber sobre la plata

**La API de Anthropic se factura aparte de tu plan Pro.** El Pro cubre la app
y Claude Code; no incluye créditos de API. Son dos cuentas distintas.

**Es prepago.** Cargás saldo, el uso lo consume, y cuando se agota las llamadas
fallan. Que fallen es seguro: el asistente degrada a «sin propuesta», la cola
sigue andando y completás a mano, igual que hoy.

**Dejá el auto-reload APAGADO.** Una vez encendido recarga la tarjeta sola al
cruzar el umbral, sin pedir confirmación. Con él apagado, tu techo de gasto es
literalmente lo que cargaste.

---

## Paso 1 — Cargar saldo en la consola

1. Entrá a `console.anthropic.com` con tu cuenta.
2. Cargá el mínimo que te ofrezca. Con el volumen actual (~20 llamadas por día)
   eso dura varios meses.
3. **Revisá que auto-reload esté apagado.**

**Verificación:** la consola muestra el saldo cargado y el auto-reload en off.

---

## Paso 2 — Crear la clave de API

1. En la consola, creá una API key nueva.
2. Copiala en ese momento: no se vuelve a mostrar.

**Verificación:** tenés una cadena que empieza con `sk-ant-`.

---

## Paso 3 — Instalar la dependencia en el servidor

```
cd ~/guani/guani-saimc
.venv/bin/pip install -e ".[ocr,llm]"
```

El extra `llm` trae el SDK de Anthropic. Se instalan los dos extras juntos
porque `ocr` ya estaba en uso desde el 2026-08-31 y `pip install -e ".[llm]"`
solo dejaría ese.

**Verificación:**

```
.venv/bin/python -c "import anthropic; print(anthropic.__version__)"
```

Tiene que imprimir una versión. Si dice `ModuleNotFoundError`, el paso falló y
el asistente va a seguir apagado (sin romper nada).

---

## Paso 4 — Configurar la clave

Agregá al `.env` del servidor:

```
SAIMC_ANTHROPIC_API_KEY=sk-ant-...
```

Opcionales, con sus valores por defecto:

```
SAIMC_LLM_ASSIST_MODEL=claude-sonnet-5
SAIMC_LLM_ASSIST_TIMEOUT=30.0
SAIMC_LLM_ASSIST_LOG_PATH=data/llm-assist-log.jsonl
```

Reiniciá el server.

**Verificación:** abrí `/curation` en un anuncio que tenga campos vacíos. Tiene
que aparecer el botón **«Completar huecos con IA»**. Si el anuncio está
completo, el botón no aparece — es correcto, no hay nada que preguntar.

---

## Paso 5 — La primera llamada real

1. En `/curation`, parate en un anuncio de imagen con campos vacíos.
2. Apretá **«Completar huecos con IA»**.
3. Esperá unos segundos.

**Verificación, en este orden:**

- Los campos que estaban vacíos llegan llenos y **con contorno marcado**.
- Los campos que las reglas ya habían resuelto **no cambiaron**.
- Editá uno de los marcados: la marca desaparece. Deja de ser propuesta del
  modelo y pasa a ser tuya.
- El registro tiene una línea nueva:

  ```
  tail -1 data/llm-assist-log.jsonl
  ```

Si el botón no hace nada visible, no es un error: puede ser que el modelo se
haya abstenido, o que la llamada haya degradado. El registro lo dice
(`"degraded": true`).

---

## Paso 6 — Confirmar que H0 sigue siendo comparable

Después de curar algunos anuncios con asistencia:

```
python -m saimc.extraction.measure --no-record
```

**Verificación:** el reporte ahora imprime una línea **«Excluidos por
asistencia»** con los anuncios que quedaron fuera. Las precisiones de abajo
miden **solo lo no asistido** — ese es el número comparable con todo el
histórico.

En `/control`, el historial de H0 muestra un chip «N asistidos fuera» al lado
de la corrida.

---

## Dónde mirar el consumo

Una vez que hagas la primera llamada, `/control` suma la sección **«Consumo del
asistente»**: invocaciones, gasto estimado, tokens y las fallas por tipo.

Es el gasto **de SAIMC**, medido sobre lo que la propia API reportó consumir en
cada respuesta — no el total de tu cuenta. Si algún día usás la misma clave
para otra cosa, ese número queda por debajo.

**Si el saldo se agota, la sección lo dice con un aviso destacado.** Importa
porque de otro modo es invisible: quedarse sin créditos hace que el botón deje
de proponer, pero la cola sigue funcionando igual y nada más cambia en
pantalla.

Una salvedad sobre ese aviso: reconocer «saldo insuficiente» entre todas las
fallas posibles es una calibración razonada, **no verificada contra un error
real** de la API — hace falta una cuenta sin créditos para producirlo. Si
alguna vez te quedás sin saldo y la sección lo reporta como falla genérica en
vez de decirlo, avisá: se ajusta con el mensaje real en la mano.

## Cómo apagarlo

Sacá `SAIMC_ANTHROPIC_API_KEY` del `.env` y reiniciá. El botón desaparece y la
cola vuelve a comportarse como antes. No hace falta desinstalar nada ni tocar
código.

Para apagarlo del todo, incluida la dependencia:

```
.venv/bin/pip uninstall anthropic
```

También funciona: sin el paquete, el asistente degrada igual.

---

## Qué vigilar con el tiempo

**El rubber-stamping es el riesgo vivo.** El peligro de esta capa no es que el
modelo se equivoque: es que se equivoque de forma convincente sobre un
formulario que recorrés rápido, una hora por día. Una respuesta fluida es más
fácil de confirmar sin mirar que un campo en blanco.

Dos cosas ayudan y ninguna alcanza sola:

- La **marca visual** te dice qué mirar con más atención en el momento.
- El **registro** hace el problema detectable después.

La señal concreta: si el número de H0 asistido y el no asistido divergen mucho,
es que las filas asistidas se están confirmando sin revisar de verdad. Ahí
conviene frenar y revisar unas cuantas a mano contra la evidencia.

**Un anuncio asistido no suma al umbral de 300** de `spec.md` §3. Usar el
asistente tiene ese costo: esos anuncios no sirven como evidencia limpia de
cuánto resuelven las reglas solas.

---

## Backtest

Mide el modelo contra lo que curaste a mano, separado por texto e imagen, y lo
compara con las reglas sobre los mismos anuncios. Es la línea base para decidir
si confiar en el modelo con toda la cola. **No da veredicto ni umbral**: el
umbral lo ponés vos al ver los números (`spec.md` §12.6).

Corré todo desde la raíz del proyecto (usa la base y `data/` de ahí).

1. **En seco** (por defecto): no llama a nada, no necesita clave. Imprime
   cuántos anuncios entran por tipo y el costo estimado por modelo.

   ```
   .venv/bin/python -m saimc.extraction.llm_backtest
   ```

2. **Piloto** con 10 anuncios (gasta centavos; sirve para ver que todo anda):

   ```
   .venv/bin/python -m saimc.extraction.llm_backtest --execute --limit 10
   ```

3. **Corrida completa** (mismo comando sin `--limit`). Es reanudable: si se
   corta, volvé a lanzarlo y salta lo ya hecho; los anuncios degradados se
   reintentan.

   ```
   .venv/bin/python -m saimc.extraction.llm_backtest --execute
   ```

`--execute` exige `SAIMC_ANTHROPIC_API_KEY` y se niega sin ella. No cambies
`--seed` ni `--sample` entre el piloto y la corrida completa (mezclarías
muestras distintas en el mismo archivo). Para cambiar de modelo: `--model`.

**Re-correr con un prompt nuevo (v2 en adelante).** Los archivos por defecto
son la línea base v1: no los pises. Usá rutas aparte para que las dos corridas
queden comparables:

```
.venv/bin/python -m saimc.extraction.llm_backtest --execute \
  --results data/llm-backtest-results-v2.jsonl \
  --calls-log data/llm-backtest-calls-v2.jsonl \
  --report data/llm-backtest-report-v2.md
```

Con la misma `--seed` (42 por defecto) y el mismo `--limit`, la lista de
anuncios es la misma (el oro se ordena y el muestreo es determinista), así que
una corrida con `--limit 100` cubre los mismos 100 anuncios que la v1 mientras
no cambien los curados ni el registro del asistente. Cada resultado y cada
llamada registran `prompt_version` (`llm_propose_ad.PROMPT_VERSION`); los de la
v1 no lo tienen y el reporte los rotula «v1 (sin registro)». El reporte incluye
las «filas de más» (lo que el modelo propone y el operador no dejó): no son
error de H0, pero cuestan borrados, y el oro puede estar incompleto.

**Prompt v3 y «Calidad de las señales».** La v3 (`2026-09-25.3`) pide por fila
la evidencia (fragmento literal del anuncio) y `currency_explicit`, y explicita la
regla del precio en dos monedas. El reporte suma la sección «Calidad de las
señales» (por tipo y total, cada porcentaje con su n): si la evidencia se halla
en el texto, si el precio está en ella, y el acierto de las filas cortado por
evidencia hallada / no hallada, moneda explícita / asumida y acuerdo con las
reglas (coincide / difiere), más los tokens de salida por llamada (v2: 17958
para 100 anuncios). Los resultados de la v2 no traen esas señales y salen n/d.
Re-corrida (cuesta plata; decisión del operador):

```
.venv/bin/python -m saimc.extraction.llm_backtest --execute --limit 100 \
  --results data/llm-backtest-results-v3.jsonl \
  --calls-log data/llm-backtest-calls-v3.jsonl \
  --report data/llm-backtest-report-v3.md
```

Dónde queda todo (bajo `data/`, que el repositorio no versiona):

- `data/llm-backtest-results.jsonl`: un resultado por anuncio (propuesta del
  modelo, de las reglas, tokens, OCR de las imágenes).
- `data/llm-backtest-report.md`: el reporte en español. Se regenera sin llamar
  a nada con `--report-only`.
- `data/llm-backtest-calls.jsonl`: registro de llamadas. Es un archivo aparte
  del del asistente: el backtest **no** saca anuncios de la medición de H0.

El oro son los curados que nunca pasaron por el asistente. La muestra de
descartados (por defecto, tantos como oro por tipo) solo mide el veredicto de
descarte.

---

## Qué NO hacer

- No enciendas auto-reload en la consola sin decidirlo a conciencia.
- No corras el smoke test real por costumbre. Exige `SAIMC_LLM_SMOKE=1` además
  de la clave justamente para que correr la suite no gaste:

  ```
  SAIMC_LLM_SMOKE=1 .venv/bin/pytest tests/test_llm_assist.py
  ```

- No edites `data/llm-assist-log.jsonl` a mano. Es de solo-anexado y lo escribe
  el sistema; tocarlo corrompe la separación entre lo asistido y lo que no.
- No confirmes una fila asistida sin mirarla porque «la puso la IA». Es
  exactamente el modo de falla que este runbook te está pidiendo vigilar.
