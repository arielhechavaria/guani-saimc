# Plan — Curaduría de toda la cola con el asistente LLM

**Estado: plan aprobado, sin ejecutar.** Se ejecuta cuando el operador encienda el
asistente (clave de API configurada, ver `docs/runbook-asistente-llm.md`) y avise.
Hasta entonces no se toca código.

Fecha del plan: 2026-09-25.

---

## Qué cambia

Hoy el operador cura cada anuncio a mano y el asistente solo completa huecos, a
pedido, uno por uno (`docs/decisions.md`, 2026-09-01). El camino nuevo:

- **El asistente hace la curaduría de toda la cola**: propone el formulario
  completo y, cuando corresponde, el descarte con su motivo.
- **El operador solo verifica** el formulario contra el anuncio y da **Aceptar**
  (o corrige y acepta, o descarta).
- La confirmación humana se mantiene: nada llega a `prices_history` sin que el
  operador la dé.

## Decisiones del operador (2026-09-25)

1. **Todo pasa por el asistente.** No se reserva una muestra sin asistencia.
2. **La medición de H0 queda por redefinir** (ver «Pendiente»). Este es el camino
   de ahora en adelante.

## Apartamientos que hay que registrar en `docs/decisions.md`

Se anexan al ejecutar el slice 2, junto con el código (regla del proyecto):

| Qué se aparta | De dónde | Motivo |
| --- | --- | --- |
| «LLM solo para lo que las reglas no resuelven» | `EXT-07`, `spec.md` §6.1 | El cuello es el triage (68% de descarte), no los huecos. Se mantiene «con confirmación humana». |
| «Solo a pedido, nunca en lote» | decisión 2026-09-01 | El pre-cálculo por lotes es lo que permite que el operador solo revise. |
| «H0 se mide sobre lo no asistido» | decisión 2026-09-01, `spec.md` §3 | Si todo es asistido, ese conjunto queda vacío. Redefinición pendiente. |
| «La curaduría es manual» | decisión cerrada 2 | Pasa a ser curaduría asistida con revisión humana anuncio por anuncio. |

**Decisión cerrada 3 (sin cola de trabajos):** se respeta. El pre-cálculo es un
comando que lanza el operador, no un worker ni un cron. Si algún día se quiere
programarlo, es una decisión aparte.

## Lo que NO cambia

- La imagen nunca sale del VPS: el modelo recibe solo el texto (OCR) ya pasado por
  `redact_personal_data`. Un modelo de visión sigue descartado (privacidad).
- Catálogos cerrados, revalidados de nuestro lado: nada se da de alta desde la
  salida del modelo.
- `raw_ingestions` inmutable; toda extracción conserva referencia al registro crudo.
- Sin clave o sin paquete, el asistente degrada a «sin propuesta» y la cola
  funciona a mano como siempre.
- Registro de solo-anexado por invocación (`data/llm-assist-log.jsonl`).

---

## Slices (uno por sesión; el siguiente no se abre hasta que el operador confirme)

### Slice 1 — Backtest (sin cambiar el producto)

Correr el modelo sobre los anuncios **ya curados a mano** y comparar su propuesta
con el valor final del operador.

- Entrega: precisión por campo (producto, precio, moneda, cantidad, unidad,
  provincia, formato de venta) y acierto del veredicto de descarte.
- Es la línea base contra la que se comparan todos los números futuros, y la única
  medición del modelo contra etiqueta humana independiente que existirá una vez que
  todo sea asistido.
- Va contra el corpus real curado a mano, nunca contra casos sintéticos (regla de
  medición de `CLAUDE.md`).
- **Umbral para seguir:** lo fija el operador al ver los números. No se inventa
  (`spec.md` §12.6).
- Costo: una llamada por anuncio curado; se informa el gasto antes de correr.

### Slice 2 — Propuesta completa y veredicto de descarte

- El modelo propone **todas** las filas (no solo huecos) y `descartar` con motivo
  corto.
- Las reglas pasan a ser segunda opinión: cada campo queda marcado como
  **coincide** (modelo y reglas iguales) o **difiere** (se resalta). Es la defensa
  principal contra el rubber-stamping.
- Anuncios con varias filas (listas de precios): una fila por producto del foco,
  igual que hoy.
- Se anexan las entradas de `decisions.md` de la tabla de arriba.

### Slice 3 — Pre-cálculo por lotes (toca esquema → flujo completo de especificación)

- Comando `python -m saimc.extraction.prefill`: recorre los pendientes, hace OCR y
  llamada al modelo, y guarda la propuesta. Una a la vez (VPS de 2 vCPU).
- Tabla nueva para las propuestas (ingestion, payload, modelo, estado, fecha).
  Migración con Alembic.
- `/curation` lee la propuesta guardada en vez de llamar al abrir.
- Al cargar el backlog inicial (~267) se informa el costo estimado antes de correr.
- Idempotente: reanudable si se corta, sin repetir lo ya procesado.

### Slice 4 — Interfaz de revisión

- **Aceptar** con un clic y atajo de teclado; el formulario sigue editable.
- Descarte propuesto aceptable con un clic, mostrando el motivo.
- Cola ordenada: primero lo coincidente, al final lo dudoso o en desacuerdo.
- Verificación contra el diseño: página real renderizada a 390 px y a escritorio
  (mobile first), no solo tests.

### Slice 5 — Vigilancia del rubber-stamping en `/control`

- Tasa de aceptación sin edición, por campo.
- Tiempo por decisión.
- Correcciones del operador sobre propuestas del modelo (lo que hoy da la señal de
  calidad).
- Aviso si la aceptación sin edición sube y el tiempo por decisión baja a la vez.

## Costo

Con ~$0,0031 por llamada (`docs/costos.md`): ~1200 llamadas al mes ≈ $4; el backlog
inicial de ~267 ≈ menos de $1. Adicional al plan Pro; la API se factura aparte y con
auto-reload apagado. Rehacer la cuenta con el volumen real de WAHA al ejecutar.

## Fuera de alcance

- Aceptar sin revisión del operador (auto-aceptar por confianza).
- Modelo de visión sobre la imagen cruda.
- Worker, cron o cola de trabajos.

---

## Pendiente — qué significa H0 ahora

Con todo asistido, «precisión de extracción contra etiqueta humana» cambia de
sentido: la etiqueta ya no es independiente del extractor, porque el operador
confirma lo que el modelo propuso. **No se resuelve en este plan.** Puntos de
partida para cuando se aborde:

- El backtest del slice 1 es la última medición independiente disponible.
- «Aceptación sin edición» mide el trabajo que ahorra el asistente, no su precisión
  (el operador puede aceptar mal).
- El umbral de 300 anuncios etiquetados (`spec.md` §3) y la meta de 500 registros
  validados (§9) hay que releerlos a la luz de esto.
- Toca `spec.md` §3, §9 y §12.5; el apartamiento se registra en `docs/decisions.md`.

## Antes de ejecutar

1. El operador enciende el asistente (`docs/runbook-asistente-llm.md`, pasos 1–5) y
   avisa.
2. Confirmar el estado del repo (sin cambios sin commitear) y abrir el slice 1.
