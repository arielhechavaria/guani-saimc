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
