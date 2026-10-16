# Prompts del plan del MVP para inversores

Fecha: 2026-10-09. Un prompt por tarea de `docs/plan-mvp-inversores.md`, para pegar en
una sesión nueva. Un prompt por sesión: un slice se cierra antes de abrir el siguiente.

## Orden

1. T0 está hecha: falta que el operador la corra en el VPS; su salida ordena T3 y T4.
2. T2, T4, T5a, T6, el aviso de corte y la definición de la home no dependen de nada:
   van en cualquier orden.
3. T3 espera dos cosas del operador: 60 auditados (T1) y la salida de T0.
4. T1 (auditar) y T7 (expansión) no tienen prompt: T1 es trabajo del operador en
   `/curation/auditoria`, y T7 necesita que elija la tanda.

Cuando una tarea se cierra, su prompt se borra de acá.

## T2 · Fecha por defecto en hora de La Habana

**Lista para abrir · Sonnet · Sesión corta.** Recupera lo capturado entre las 00:00 y las 04:00 UTC: 7 de 80 hoy. También te ahorra corregir la fecha en la cola.

```text
Tarea T2 de docs/plan-mvp-inversores.md: la fecha de publicación por defecto tiene que salir en hora de La Habana.

Contexto
- La fecha por defecto de una fila sale de `captured_at.date()`, en UTC. Un anuncio capturado entre las 00:00 y las 04:00 UTC queda fechado un día después del día de La Habana.
- Dos efectos: el carril 1 deja el anuncio pendiente (7 de 80 el 2026-10-09), y en la cola el operador tiene que corregir la fecha prellenada, que el formulario rechaza.
- Leé primero: docs/decisions.md, entrada «2026-10-09 — B2b-2» (consecuencia sobre el desfase), src/saimc/curation_rows.py (lo que comparten la cola y el carril) y `_published_date` en src/saimc/validation/lane1.py.

Qué hay que hacer
- Que la fecha por defecto se calcule en la zona de La Habana, en un solo lugar compartido por la cola y el carril 1.
- Revisá si el proyecto ya tiene una función o constante para el día de La Habana y usala; no agregues una segunda.
- Buscá los otros lugares donde se use `captured_at.date()` como fecha del anuncio y listámelos. Arreglá solo los que alimentan la fecha por defecto; el resto, anotalo.

Límites
- No se tocan filas ya guardadas: ningún backfill. Si creés que hace falta corregir históricas, decímelo y lo decidimos aparte.
- No se toca el motor de validación ni `lane1.CONFIG`.
- Las fotos de WhatsApp tienen su propia fecha prellenada (slice A7): no la rompas.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/fecha-habana -b worktree-fecha-habana`.
- Tests primero, con el resultado esperado escrito a mano: capturas a las 23:30, 00:30, 03:59, 04:00 y 05:00 UTC, y un caso en horario de verano de Cuba.
- El test que cura el mismo anuncio por el formulario y por el carril y compara las columnas tiene que seguir pasando.
- Entrada en docs/decisions.md.
- Pasó docs/checklist-pr.md. Commit convencional, local, sin push.
- Entregame los pasos de verificación: qué anuncio abrir en la cola, qué fecha tiene que aparecer prellenada, y el comando en seco del carril 1 para ver que el conteo sube.
```

## T4 · Bug P1 del extractor por reglas

**Lista para abrir · Sonnet · Sesión completa.** Un catálogo de tienda sin match pierde productos y precios. Un bug por sesión: para P2 y P3 repetí el prompt cambiando el número.

```text
Tarea T4 de docs/plan-mvp-inversores.md: arreglar el pendiente P1 de docs/pendientes-engine.md («Bloque escalonado sin match de catálogo», bug 4).

Contexto
- Dónde: `_tiered_proposals` en src/saimc/extraction/engine.py.
- Qué pasa: la guarda que impide leer un catálogo de tienda como un único bloque de precios por volumen solo corre cuando algún producto matcheó el catálogo. Sin match, un anuncio con varios productos colapsa en dos filas a nombre del encabezado y se pierden los productos y precios intermedios.
- Por qué importa para la automatización: cuando las reglas y el modelo no coinciden, el anuncio sale del carril 1, aunque el que se equivocó sean las reglas.
- Leé primero: la sección «P1» de docs/pendientes-engine.md, el commit `2a466e4` (arregló la misma clase de bug para productos de catálogo) y la clase de tests `TestCatalogoNoEsBloqueEscalonado`.

Qué hay que hacer
- Solo P1. Si aparece otro bug, anotalo en docs/pendientes-engine.md y no lo arregles.

Regla de esta capa (CLAUDE.md)
- El caso esperado se escribe antes que el código, y la respuesta correcta se define a mano, sin correr el extractor. Nunca derives un test del comportamiento del extractor ya escrito.
- Antes de escribir código, mostrame los casos y qué filas tiene que dar cada uno, y esperá mi OK. Base: el ejemplo de «Reproducción» de P1.
- Incluí casos que NO tienen que cambiar: un bloque escalonado legítimo de un solo producto.

Límites
- No se cambia el esquema ni el motor de validación.
- No corras backtests pagados ni el pre-cálculo.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/engine-p1 -b worktree-engine-p1`.
- La suite de extracción completa en verde.
- Borrá P1 de docs/pendientes-engine.md y dejá el porqué en docs/decisions.md.
- Pasó docs/checklist-pr.md. Commit convencional, local, sin push.
- Entregame los pasos de verificación: un texto para pegar en la cola y qué filas tiene que proponer.
```

## T5a · Imágenes: máscara de teléfonos y nombres (C2a)

**Lista para abrir · Opus · Sesión completa o dos.** Primer paso de tres para que una imagen pueda aceptarse sola. Todavía no llama a ningún modelo de visión ni gasta.

```text
Tarea T5 de docs/plan-mvp-inversores.md, primer paso: C2a del plan de factor humano. Máscara de teléfonos y nombres sobre la imagen, usando las cajas del OCR.

Contexto
- Hoy ninguna imagen se acepta sola: van por OCR y después como texto, con 55,6 % de extracción completa sobre el oro. C2 las lleva a un modelo de visión en tres pasos: C2a (esta), C2b (backtest pagado de visión contra OCR más texto) y C2c (integración desconectable con el apartamiento de privacidad registrado).
- Para mandar una imagen a un modelo externo, antes hay que tapar teléfonos y nombres: `spec.md` §10.6 prohíbe capturar o guardar datos de contacto.
- Leé primero: docs/spec-map.md (filas de §6.1 «OCR, extracción asistida» y §10.6) y solo esos rangos de docs/spec.md; la sección «C2» y el cierre de K3 en docs/plan-factor-humano.md; docs/spec-slice-K3.md (la transcripción se guarda redactada, con cajas y versión); src/saimc/privacy.py (la redacción de teléfonos que ya existe).

Qué hay que hacer
- Empezá por una especificación corta, docs/spec-slice-C2a.md, y esperá mi aprobación antes de programar. Tiene que decir: qué se tapa, cómo se decide que una caja es un teléfono o un nombre, qué pasa cuando el OCR no encontró la caja de un teléfono que sí está en la imagen, y si esto toca el esquema.
- Decime explícitamente en qué se aparta de la especificación, si se aparta.
- Después: una función que recibe la imagen y las cajas y devuelve la imagen tapada, sin tocar el archivo original.

Límites
- `raw_ingestions` es inmutable: la imagen original no se modifica ni se borra. La tapada es un derivado.
- No se llama a ningún modelo de visión y no se gasta API: eso es C2b y se aprueba aparte.
- No se enchufa a la cola ni al pre-cálculo: eso es C2c.
- Si la máscara no puede garantizar que un teléfono quede tapado, el comportamiento por defecto es no enviar la imagen.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/c2a-mascara -b worktree-c2a-mascara`.
- Tests primero, sobre imágenes sintéticas generadas en el test, con los formatos de teléfono que ya cubre privacy.py («+53 5 234 5678» incluido) y un caso donde el OCR parte el número en dos cajas.
- Ninguna imagen real de un anuncio entra al repo.
- Entrada en docs/decisions.md y estado de C2 actualizado en docs/plan-factor-humano.md y en docs/plan-mvp-inversores.md.
- Pasó docs/checklist-pr.md. Commit convencional, local, sin push.
- Entregame los pasos de verificación y la estimación de costo de C2b para que la apruebe.
```

## T6 · Métrica de la Fase 0 redefinida (B3)

**Lista para abrir · Opus · Sesión completa.** No sube el volumen: define el número que se le muestra a un inversor. Toca el esquema, así que empieza por especificación.

```text
Tarea T6 de docs/plan-mvp-inversores.md: B3 del plan de factor humano. Conjunto de referencia congelado y métrica de la Fase 0 redefinida.

Contexto
- La marca de conjunto de referencia que pide `spec.md` §6.1 no existe. El oro se recalcula en cada corrida y `/edit` puede cambiarlo.
- Con el carril 1 andando, H0 ya no describe el sistema: lo describe la tasa de automatización más el error auditado. H0 queda como medición del extractor sobre un conjunto congelado.
- Un anuncio aceptado solo queda fuera de H0 hasta que se audite.
- Leé primero: docs/spec-map.md (filas de §3, §6.1, §12.5 y §9 «Criterio de salida», y la tabla de apartamientos) y solo esos rangos de docs/spec.md; la sección «B3» de docs/plan-factor-humano.md; «Pendiente — qué significa H0 ahora» en docs/plan-asistente-llm-cola-completa.md; docs/runbook-medir-h0.md; y la sección «Qué quiere decir listo para inversores» de docs/plan-mvp-inversores.md.

Qué hay que hacer
- Toca el esquema de datos, así que va el flujo completo: explorar, proponer, especificar, y recién después implementar. En esta sesión llegá hasta una especificación aprobable, docs/spec-slice-B3.md, y pará ahí.
- La especificación tiene que cerrar: cómo se congela (lista de ids con el hash de sus valores, u otra cosa, con su motivo), qué pasa cuando `/edit` toca un curado congelado, cómo se define «tasa de automatización» (sobre qué denominador, y si cuenta los descartes del carril 0), cómo se publica junto al error auditado, y qué cambia en `measure.py` y en el runbook de H0.
- Dejá a la vista los dos números que hoy se mezclan: de todo lo que entra, cuánto se resuelve sin el operador; y de los anuncios válidos, cuántos se publican solos.
- Listá las preguntas que tengo que responder yo, con tu recomendación en cada una.
- No inventes umbrales: `spec.md` §12.6 lo prohíbe. Si hace falta uno, queda como pregunta.

Límites
- No se programa ni se migra nada en esta sesión.
- Las corridas viejas de docs/h0-runs.jsonl no se reescriben.
- Solo lectura contra la base, si hace falta consultar; sin gasto de API.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/b3-spec -b worktree-b3-spec`.
- Los apartamientos de §3 y §12.5 quedan propuestos para docs/spec-map.md y docs/decisions.md, sin escribirlos hasta que apruebe.
- Commit convencional de la especificación, local, sin push.
- Decime en cuántos slices lo partirías y con qué modelo cada uno.
```

## Aviso de corte de ingesta fuera del panel

**Lista para abrir · Sonnet · Sesión corta.** Hoy un corte de WhatsApp solo se ve al abrir `/curation` o `/control`.

```text
Tarea menor de docs/plan-mvp-inversores.md: que el aviso de corte de ingesta llegue sin abrir el panel.

Contexto
- K4 ya calcula el hueco de ingesta por canal (piso de 24 h y 2 × p95) y lo muestra en `/curation` y `/control`. Si no abro ninguna de las dos, un corte pasa desapercibido.
- El pre-cálculo ya corre con un temporizador de systemd cada 3 h (C1). No hay cola de trabajos ni la va a haber: decisión cerrada 3 de CLAUDE.md.
- Leé primero: el cierre de K4 y de C1 en docs/plan-factor-humano.md, el código del aviso de K4 y `systemd/saimc-prefill.{service,timer}`.

Qué hay que hacer
- Proponeme un solo canal de aviso (mail o Telegram) con su motivo, y esperá mi OK antes de programar. Tené en cuenta qué credenciales hacen falta y dónde quedarían.
- Un comando que revisa los huecos y avisa solo cuando hay uno nuevo: no repite el mismo aviso cada tres horas.
- Que lo dispare un temporizador de systemd, igual que el pre-cálculo. Un temporizador no es una cola.

Límites
- El mensaje no lleva datos personales ni el identificador de ningún emisor: solo el nombre del canal y cuánto hace que no entra nada.
- Sin Redis, sin Celery, sin servicios nuevos.
- Apagado por defecto, con su variable en el `.env`.
- No mandes ningún mensaje real desde la sesión: la prueba la hago yo.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/aviso-ingesta -b worktree-aviso-ingesta`.
- Tests del comando con el envío simulado: avisa una vez, no repite, vuelve a avisar si el canal se recupera y se corta de nuevo.
- El comando nuevo queda en docs/comandos.md y la decisión en docs/decisions.md.
- Pasó docs/checklist-pr.md. Commit convencional, local, sin push.
- Entregame los pasos para instalar el temporizador en el VPS y para probar un aviso.
```

## Home pública · definir antes de programar

**Lista para abrir · Opus · Sesión de definición.** No escribe código. Sale con una decisión y, si alcanza, un prototipo a 390 px para que apruebes.

```text
Sesión de definición, sin código de producción: qué cambia en la home pública de Guani.

Contexto
- Quiero cambiar la home pública pensando en mostrar el MVP a inversores. Qué cambia no está definido.
- Hoy `/` es la landing: un precio de portada con su rango, la cobertura, seis fortalezas y el recorrido «Así trabajo» en cinco capturas.
- Leé primero: la sección «Home pública — por definir» y «Qué quiere decir listo para inversores» de docs/plan-mvp-inversores.md; docs/plan-marca-leira-studio.md, que manda en todo texto público; docs/spec-map.md (filas de §6.4, §6.4.1 y §6.7) y solo esos rangos de docs/spec.md; la plantilla de la landing y src/saimc/web/landing_view.py; y lo que ya publica `/metodologia`.

Qué hay que hacer
- Haceme las preguntas de a una y esperá mi respuesta antes de la siguiente. Las tres que ya están abiertas: para quién es la home, si es una página o dos (pública e inversores), y qué números muestra.
- En cada pregunta dame tu recomendación y por qué, no un menú.
- Cuando estén respondidas, escribí la definición en docs/plan-mvp-inversores.md, en la sección de la home, y la entrada en docs/decisions.md.
- Si alcanza la sesión, armá un prototipo estático a 390 px en docs/prototipos/ y pará para que lo apruebe. La página real es otro slice.

Límites
- Nada de datos personales ni del identificador de ningún emisor en la capa pública.
- Lo que se le muestra a un tercero es el número auditado, nunca uno estimado.
- El usuario final cubano tiene conexión lenta: render en servidor, assets desde el repo, sin SPA ni CDN.
- No se cita docs/pitch-v3.5.md como fuente de nada: está congelado y sin vigencia.
- Mobile first: se decide a 390 px y después escritorio.

Cómo se cierra
- Worktree propio: `git worktree add .claude/worktrees/home-definicion -b worktree-home-definicion`.
- Commit convencional de la documentación y del prototipo, local, sin push.
- Decime en cuántos slices partirías la implementación y con qué modelo cada uno.
```

## T3 · Subir el tope de filas del carril 1

**Espera T1 y T0 · Opus · Sesión corta.** No lo abras antes de tener 60 auditados y la salida de T0. Completá los dos datos entre corchetes antes de pegarlo.

```text
Tarea T3 de docs/plan-mvp-inversores.md: decidir si se sube el tope de filas del carril 1.

Datos que traigo
- Auditados del carril 1: [N auditados], con [N errores] errores. Lo dice el bloque «Carril 1» de `/control`.
- Salida del desglose de frenos (T0): [pegá acá la salida del comando].

Contexto
- El carril 1 acepta solo anuncios de una fila (`lane1.CONFIG`, tope 1). El 2026-10-09 entraban 80 con tope 1, 100 con tope 2 y 123 sin tope.
- El tope se eligió por cautela: sobre el oro, sin tope y sin el freno de canal sucio, hubo 15 errores en 61.
- La meta de error es 10 % por anuncio y el freno se dispara con la cota al 95 %.
- Leé primero: docs/spec-slice-B2b.md, las entradas del 2026-10-09 sobre G1b y B2b en docs/decisions.md, y docs/spec-motor-validacion.md («Precisiones de G1b» y «Precisiones de B2b»).

Qué hay que hacer
- Con los datos de arriba, calculá la cota del error auditado y decime si alcanza para subir el tope a 2, a sin tope, o si no alcanza. Una recomendación, con su cuenta a la vista.
- Corré el informe de sombra sobre el oro con cada tope para comparar, sin gasto de API.
- Tené en cuenta que lo auditado hasta ahora son anuncios de una fila: no dice nada del error en anuncios de dos o más. Decime cómo cubrirías ese hueco (por ejemplo, auditar todo lo que entre con más de una fila durante un arranque propio).
- Pará y esperá mi decisión antes de tocar `lane1.CONFIG`.

Límites
- Es el motor de validación: nada se cambia sin mi OK explícito.
- No corras `--execute` ni nada contra producción.

Cómo se cierra, si digo que sí
- Worktree propio: `git worktree add .claude/worktrees/lane1-tope -b worktree-lane1-tope`.
- Tests primero con el caso esperado a mano.
- Entrada en docs/decisions.md que diga a cuál reemplaza, «Precisiones» nuevas en docs/spec-motor-validacion.md, y revisá si el párrafo público de `/metodologia` sigue siendo cierto.
- Pasó docs/checklist-pr.md. Commit convencional, local, sin push.
- Entregame el comando en seco para ver cuántos entran con el tope nuevo.
```
