# SAIMC — Handoff a Claude Code

Documento autosuficiente. Después de la Parte 6, todo el trabajo ocurre dentro de
Claude Code.

**Restricción de diseño:** este setup consume **únicamente** lo incluido en el plan
Claude. Cero gasto adicional. Por eso el agente es Claude Code (primera parte) y no
pi (tercero, factura por token contra extra usage).

**Convención:** `$` = terminal WSL, fuera del agente. `/` = dentro de la sesión de
Claude Code.

**Sobre los comandos con `/`:** los nombres cambian entre versiones de Claude Code.
Los que aparecen acá son los vigentes al momento de escribir esto. Si alguno no
existe en tu instalación, escribí solo `/` para ver la lista real, o `/help`.

**Entorno:** todo dentro de WSL. Proyecto en `~/guani`, nunca en `/mnt/c/...`.

---

# PARTE 0 — Verificación crítica de facturación

**Hacé esto antes que nada.**

```bash
$ echo $ANTHROPIC_API_KEY
```

Si devuelve algo, Claude Code va a autenticarse por API key y **facturarte por token**
en lugar de consumir tu plan. Eliminala:

```bash
$ unset ANTHROPIC_API_KEY
```

Y quitá cualquier línea que la exporte en `~/.bashrc`, `~/.profile` o `~/.zshrc`.

Más adelante (Parte 3) vas a confirmar con `/status` que el modo de auth es
suscripción y no API.

---

# PARTE 1 — Verificar qué tenés

```bash
$ claude --version
$ gentle-ai --version
$ engram --version
$ node --version
```

- Los cuatro responden → Parte 3.
- Falta `gentle-ai` → Parte 2.2.
- Falta todo → Parte 2 completa.

Si venías de pi, podés dejarlo instalado sin problema. No interfiere.

---

# PARTE 2 — Instalación

## 2.1 Claude Code

```bash
$ npm install -g @anthropic-ai/claude-code
$ claude --version
```

## 2.2 Gentle AI para Claude Code

```bash
$ gentle-ai install --agent claude-code
```

Esto configura: prompt de sistema con el orquestador SDD en `~/.claude/CLAUDE.md`,
skills en `~/.claude/skills/`, agentes SDD por fase, y Engram como servidor MCP.

Durante la instalación te va a preguntar por componentes y preset. Para este proyecto:
**engram sí** (memoria), **sdd sí** (workflow), **skills sí**, **context7 opcional**.

---

# PARTE 3 — Autenticación (el paso que define el gasto)

```bash
$ cd ~/guani
$ claude
```

En el primer arranque elegí **iniciar sesión con tu cuenta Claude** (suscripción).
**No** elijas la opción de API key.

## Cómo verificar que estás en modo suscripción

**No dependas de nombres de comandos.** Claude Code los cambia entre versiones. Para
ver los que existen en TU instalación, escribí solo `/` dentro de la sesión y leé la
lista, o usá `/help`.

La verificación de facturación no necesita ningún comando:

1. `echo $ANTHROPIC_API_KEY` en la terminal → debe estar vacío (Parte 0).
2. En el primer arranque iniciaste sesión con tu cuenta Claude, no con API key.

Si ambas cosas se cumplen, estás consumiendo el plan.

## Cómo vigilar el consumo

La fuente autoritativa es **claude.ai/settings/usage** en el navegador: muestra el
panel combinado de chat y Claude Code con los tiempos de reseteo.

Dentro del CLI puede existir un comando de uso o de contexto según tu versión —
escribí `/` y fijate. Si no existe, el navegador alcanza.

**Señal de alarma:** si el saldo de *extra usage* baja mientras usás Claude Code de
forma interactiva, algo está mal configurado. Volvé a la Parte 0.

---

# PARTE 4 — Trabajar dentro de los límites del plan

Tu plan comparte una sola bolsa entre el chat de Claude y Claude Code. Estas medidas
no son opcionales: son lo que hace que un slice completo entre en una ventana.

| Medida | Efecto |
| :--- | :--- |
| Nunca volcar el whitepaper completo | Referenciar secciones concretas. 1.500 líneas por fase es el mayor desperdicio |
| Un slice por sesión | Sesiones largas arrastran historial muerto |
| Compactar cuando el contexto suba | Resume lo viejo y conserva lo reciente (`/compact` en versiones actuales) |
| Limpiar contexto al empezar un slice nuevo | Sin arrastre del anterior (`/clear` en versiones actuales) |
| Dejar que SDD delegue en subagentes | Cada fase corre en contexto aislado; solo vuelve el resumen |
| Trabajar en bloques, no todo el día | Los límites son por ventana de 5 horas: repartir rinde más que maratonear |

Si te quedás sin ventana a mitad de un slice: **no compres nada**. Esperá el reset y
seguí. El backlog está diseñado para tolerar interrupciones — cada slice termina en
un estado commiteable.

---

# PARTE 5 — Memoria

```bash
$ engram projects list
```

Debe listar sin error. Dentro de Claude Code, el comando de MCP (`/mcp` en versiones
actuales — verificá con `/`) debe mostrar el servidor de Engram conectado. Si no
aparece:

```bash
$ gentle-ai install --agent claude-code
```

---

# PARTE 6 — Estructura y contexto permanente

```bash
$ cd ~/guani
$ mkdir -p docs/decisions
$ mv whitepaper.md docs/whitepaper.md
$ cp CLAUDE.md ./CLAUDE.md          # el archivo que acompaña a este documento
$ git add -A && git commit -m "docs: whitepaper v3.4 + contexto de proyecto"
```

`CLAUDE.md` es la pieza clave del handoff: Claude Code lo carga automáticamente en
cada sesión. Contiene las decisiones cerradas, H0, las reglas de trabajo y el
contexto del entorno. No tenés que repetir nada.

Dentro de Claude Code, inicializar SDD:

```
/sdd-init
```

```bash
$ git add -A && git commit -m "chore: inicializar SDD"
```

---

# PARTE 7 — Prueba de humo

1. `¿cuál es la hipótesis H0 y por qué gobierna la Fase 0?`
   Debe responder sin que le pases ningún archivo (sale de CLAUDE.md).
2. Salir. Volver a entrar.
3. `¿qué decidimos sobre el stack y por qué?`
   Si responde bien, CLAUDE.md + Engram están funcionando.

**El setup termina acá. No más configuración.**

---

# PARTE 8 — Los ADR iniciales

```
Creá docs/decisions/001-stack-python-unico.md como ADR corto
(contexto, decisión, alternativas descartadas, consecuencias)
basado en la sección 13.1 de docs/whitepaper.md.
```

```
Creá docs/decisions/002-curaduria-antes-de-automatizar.md como ADR
basado en la sección 6.1 y el riesgo 10.6 de docs/whitepaper.md.
```

Commitear ambos.

---

# PARTE 9 — Backlog de slices

Uno por sesión. En orden. **No saltear.**

Empezá cada slice con el contexto limpio (`/clear` en versiones actuales).

## Slice 1 — Bandeja de entrada y cola de curaduría

```
Usá SDD.

Leé docs/whitepaper.md secciones 6.1 (etapa 1) y 7 (modelo de datos).

Objetivo: bandeja de entrada + cola de curaduría. La captura y la curaduría
son actos separados en el tiempo.

Alcance EXACTO:
- FastAPI + PostgreSQL + Alembic. Python únicamente.
- Tablas: raw_ingestions (con file_path, content_hash, content_type,
  processing_status), products (con alias), provinces, prices_history.
- Seed: 5 productos bandera (pollo congelado, arroz, aceite, leche en polvo,
  huevos) y las 16 provincias + Isla de la Juventud.
- Comando de ingesta: escanea un directorio de entrada, registra cada archivo
  nuevo como raw_ingestion con hash, y omite los duplicados por hash.
  Acepta imágenes y archivos de texto.
- Cola de curaduría (Jinja2 + HTMX): muestra un elemento pendiente por vez con
  su imagen o texto original, formulario para producto, precio, moneda,
  cantidad, unidad, provincia, canal origen y fecha de publicación.
  Botones: guardar, descartar, marcar como conjunto de referencia.
- Al guardar: crea el registro en prices_history y marca el raw_ingestion
  como curated, conservando la referencia al archivo original.
- Listado de registros curados con filtro por producto y provincia.
- Tests: modelos, deduplicación por hash, y el flujo de curaduría completo.

FUERA de alcance: OCR, extracción por reglas, validación, forecasting,
dashboard público, auth, Docker, colas de trabajo, integración con Telegram.

Terminado cuando: copio 20 archivos al directorio de entrada, corro la ingesta,
los curo desde la cola y los veo en el listado filtrado.
```

**Después del slice 1 empieza la rutina diaria.** Captura oportunista mientras usás
las redes, una hora de curaduría en bloque. Es el trabajo más valioso del proyecto:
sin ese conjunto no hay forma de medir H0, y es donde la mayoría abandona porque
programar features se siente más productivo. No lo es.

## Slice 1.5 — OCR como asistente de la cola

```
Usá SDD.

Leé docs/whitepaper.md sección 6.1 (rol del OCR).

Objetivo: pre-rellenar la cola de curaduría a partir de imágenes.

Alcance EXACTO:
- Al abrir un elemento de tipo imagen, ejecutar OCR y mostrar el texto extraído
  junto a la imagen original.
- El texto extraído se persiste en original_text del raw_ingestion.
- El operador puede editar el texto antes de curar.
- Tests con imágenes reales del directorio de entrada.

FUERA de alcance: extracción estructurada de campos, decisiones automáticas.
El OCR propone texto; el humano sigue completando los campos.

Terminado cuando: al curar una captura de pantalla, el texto ya viene transcrito.
```

## Slice 2 — Extracción por reglas en modo propuesta

```
Usá SDD.

Leé docs/whitepaper.md sección 6.1 (etapa 2).

Objetivo: motor de extracción por reglas que PROPONE, no decide.

Alcance EXACTO:
- Módulo de extracción: dado un texto en bruto devuelve producto, precio,
  moneda, cantidad, unidad y provincia, cada campo con nivel de confianza.
- Técnica: regex + diccionario de alias de productos + catálogo geográfico.
  Sin LLM en este slice.
- Endpoint que recibe texto y devuelve la propuesta estructurada.
- Tests con casos reales tomados del conjunto de referencia ya cargado.

FUERA de alcance: interfaz de confirmación, LLM, OCR, cálculo de métricas.

Terminado cuando: paso 20 textos del conjunto de referencia y obtengo
propuestas estructuradas.
```

## Slice 3 — Confirmación y medición de H0

```
Usá SDD.

Leé docs/whitepaper.md secciones 6.1 (etapa 2) y 12.5 (métricas).

Objetivo: cerrar el ciclo de medición de H0.

Alcance EXACTO:
- Integrar la propuesta del slice 2 en la cola de curaduría existente: los campos
  llegan pre-rellenados y el operador confirma o corrige cada uno.
- Cada confirmación/corrección se persiste como evento de evaluación.
- Reporte de precisión: producto, precio, provincia, cantidad/unidad y
  extracción completa correcta, calculadas contra el conjunto de referencia.

FUERA de alcance: todo lo demás.

Terminado cuando: tengo un número de precisión de extracción completa correcta.
```

## ⛔ PUNTO DE DECISIÓN — leer antes de continuar

Con el número del slice 3 en la mano:

| Resultado | Acción |
| :--- | :--- |
| ≥85% | Avanzar al slice 4. La hipótesis se sostiene. |
| 70–85% | Avanzar, pero la revisión humana queda como paso obligatorio del pipeline. Documentar en ADR. |
| <70% | **Detener.** No programar más. Analizar qué falla: ¿productos con unidad inestable? ¿provincia ausente en el texto? ¿jerga no cubierta? La respuesta correcta puede ser acotar los productos bandera, no mejorar el código. |

Este es el único punto del proyecto donde el resultado correcto puede ser parar.
Respetalo.

## Slice 4 — Validation Engine

```
Usá SDD.

Leé docs/whitepaper.md sección 6.2, reglas 1, 2, 4 y 5.

Objetivo: motor de validación configurable.

Alcance EXACTO:
- Regla 1: estructura mínima.
- Regla 2: coherencia de precio (70% promedio provincial + 30% tendencia nacional).
- Regla 4: reputación de fuente.
- Regla 5: deduplicación.
- Cada registro obtiene validation_status, confidence_score y detalle por regla.
- Tests por regla con casos límite.

FUERA de alcance: reglas 3 y 6, forecasting, dashboard.
```

## Slice 5 — Dashboard público

```
Usá SDD.

Leé docs/whitepaper.md sección 6.7.

Objetivo: dashboard público de solo lectura.

Alcance EXACTO:
- Mapa de calor de Cuba por provincia (MapLibre GL), color por precio relativo
  al promedio nacional. Gris cuando no hay datos suficientes.
- Tarjetas de producto: precio actual, promedio semanal, tendencia, disponibilidad,
  confianza del dato.
- Gráfico de líneas simple de 30 días (Chart.js). Sin velas.
- Enlace al anuncio original como evidencia, sin exponer identidad del anunciante.
- Renderizado en servidor. Debe ser usable con conexión lenta.

FUERA de alcance: forecasting, alertas, API pública, aportes de comunidad, auth.
```

---

# PARTE 10 — Operación diaria

## Ritmo

| Regla | Motivo |
| :--- | :--- |
| Un slice por sesión | El contexto se degrada; el costo crece |
| Commit al terminar cada fase SDD | El historial de git es la red de seguridad |
| Referenciar secciones, nunca el whitepaper entero | Costo y ruido |
| `/compact` cuando el contexto suba | Evita arrastrar historial muerto |
| Decisión no obvia → ADR | El vos de dentro de seis meses no recuerda el porqué |

## Cuando el agente se sale de alcance

Ocurre. La respuesta correcta no es dejarlo:

```
Eso está fuera del alcance del slice. Revertí lo que agregaste de más
y limitate a lo declarado. Si creés que hace falta, anotalo como
propuesta para un slice futuro, no lo implementes ahora.
```

## Cuando el agente propone romper una decisión cerrada

```
Eso contradice el ADR 00X. Si tenés un argumento para cambiarlo,
escribilo como propuesta de ADR nuevo y lo evalúo. No lo cambies
por tu cuenta.
```

## Diagnóstico

| Síntoma | Causa probable | Acción |
| :--- | :--- | :--- |
| Te cobran por token | `ANTHROPIC_API_KEY` seteada | `unset ANTHROPIC_API_KEY`, limpiar `~/.bashrc`, volver a iniciar sesión con la cuenta |
| Un comando `/algo` no existe | Cambió entre versiones | Escribir `/` y leer la lista real; `/help` |
| Engram no aparece en `/mcp` | MCP sin configurar | `gentle-ai install --agent claude-code` |
| CLAUDE.md no se aplica | Archivo mal ubicado | Debe estar en la raíz del proyecto; reiniciar sesión |
| Agentes SDD ausentes | Instalación incompleta | `gentle-ai install --agent claude-code` |
| "Límite alcanzado" | Ventana de 5h agotada | **Esperar el reset. No comprar nada.** Ver claude.ai/settings/usage |
| Comando de shell no responde | Lo escribiste dentro del agente | Salir y correrlo en la terminal |
| Sesión lenta, respuestas confusas | Contexto saturado | Compactar, o limpiar si empezás algo nuevo |

---

# Resumen de una línea

Verificás que no te vayan a cobrar de más (Parte 0), configurás el entorno una vez
(Partes 1–6), comprobás que la memoria funciona (Parte 7), registrás las decisiones
(Parte 8), y a partir de ahí ejecutás el backlog de slices uno por sesión, parando
en el punto de decisión de H0. Todo dentro de lo que ya pagaste.
