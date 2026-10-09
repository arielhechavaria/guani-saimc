# Plan — MVP listo para mostrar a inversores

Fecha: 2026-10-09. Estado: **propuesta**. Nada de lo que dice «propuesta» está
decidido: lo decide el operador.

Este documento responde una pregunta: ¿hasta qué punto está automatizado el camino
de un anuncio, y qué falta para mostrarlo a un inversor? Ordena pendientes que ya
existen en `docs/plan-factor-humano.md` y en `docs/pendientes-engine.md`; no los
reemplaza. El detalle de cada slice sigue en esos dos documentos.

Los prompts para abrir cada tarea están en `docs/prompts-mvp-inversores.md`.

Las cifras salen de `docs/decisions.md` (entradas del 2026-10-09). Son una foto de
ese día: la cola cambia cada tres horas con el pre-cálculo. Al escribir esto no se
consultó la base de producción.

---

## Dónde estamos

| Etapa | Estado | Lo que sigue a mano |
| --- | --- | --- |
| Ingesta | Automática: WAHA entrega por webhook lo de unos 43 canales activos | Dar de alta un canal. El aviso de corte de ingesta solo se ve al abrir `/curation` o `/control` |
| Extracción | Automática: el pre-cálculo corre cada 3 h, con tope de costo y candado entre corridas | Las imágenes se leen por OCR y después como texto: 55,6 % de extracción completa sobre el oro, contra 77 % en texto |
| Descarte (carril 0) | Automático, para texto e imagen | Auditoría semanal de 30 textos y 15 imágenes |
| Aceptación (carril 1) | Automática solo para textos de una fila, con auditoría y freno | Todo lo demás |
| Publicación | Automática: lo que el carril 1 acepta sale en el tablero sin esperar | — |

### El número que no era el esperado

«73 de 599» quiere decir que **73 entran solos y 526 siguen pasando por el
operador**. De cada 100 textos pendientes con propuesta, el carril 1 acepta 12.

Hay dos números distintos y conviene no mezclarlos:

| Qué mide | Cuenta | Resultado |
| --- | --- | --- |
| De todo lo que entra, cuánto se resuelve sin el operador | 2.179 descartes automáticos + 73 aceptaciones, sobre 2.179 + 615 textos + 205 imágenes pendientes | cerca de 75 % (estimado, sin consultar la base) |
| De los anuncios válidos, cuántos se publican solos | 73 de los 497 textos pendientes con filas propuestas | 15 % |
| De las imágenes, cuántas se aceptan solas | 0 de 205 | 0 % |

El primero es alto porque la mayor parte de lo que llega no es un anuncio de un
producto piloto, y eso el sistema ya lo descarta solo. El segundo es el que importa
para un inversor: es el que dice si el dato se produce sin una persona.

### Por qué son 73

De los 497 textos con filas propuestas:

| Freno | Cuántos frena | Fuente |
| --- | --- | --- |
| Alguna señal de que el modelo pudo leer mal (desacuerdo entre reglas y modelo, precio fuera de la banda de su celda, canal sucio, abstención, cita sin verificar, fila caída) | 374 | 497 menos los 123 que pasan sin tope de filas |
| El tope de una fila por anuncio | 43 | 123 sin tope contra 80 con tope 1 |
| La fecha por defecto cae un día después del día de La Habana | 7 | 80 contra 73, medido en B2b-2 |
| **Entran** | **73** | |

**No hay desglose de los 374.** Es el dato que falta y es el primero que hay que
conseguir: sin él, el orden de las tareas de abajo es una hipótesis.

Un límite que ninguna tarea de abajo mueve por sí sola: sobre el oro, el operador
tocó 79 de los 151 anuncios con filas, y en 44 corrigió el precio. El modelo lee
bien el anuncio entero en tres de cada cuatro textos. El carril 1 no puede aceptar
más de lo que el modelo lee bien sin publicar errores.

---

## Qué quiere decir «listo para inversores»

La especificación no lo define: `spec.md` §9 fija la meta de la Fase 0 (500
registros validados de 3 a 5 productos en 2 provincias; al 2026-10-09 hay 381) y el
plan de factor humano fija los hitos I1 a I3. Lo que sigue es una **propuesta** de
tres niveles. Los umbrales están en blanco a propósito: `spec.md` §12.6 prohíbe
inventarlos.

| Nivel | Qué se puede mostrar | Qué hace falta | Cuándo |
| --- | --- | --- | --- |
| 1. El sistema funciona solo, de punta a punta | Un anuncio de texto entra por WhatsApp y sale publicado sin que nadie lo toque, con el error auditado a la vista en `/metodologia` | 60 anuncios auditados del carril 1, para que haya una cota y no una promesa | Depende solo de auditar: no hay desarrollo pendiente |
| 2. El número se sostiene en el tiempo | El error auditado semana a semana durante cuatro semanas (hitos I3a e I3 del plan) | Tiempo y auditoría sostenida | Carril 0: desde el 2026-10-31. Carril 1: desde el 2026-11-06 |
| 3. La mayor parte del dato se produce sola | Aceptación automática de los anuncios válidos por encima de una meta | Las tareas de abajo. **La meta la fija el operador** | Sin fecha hasta medir el desglose (T0) |

Lo que se le dice a un tercero es siempre el número auditado, nunca uno estimado
(plan de factor humano, «Adelantar G1b»).

**A definir por el operador:** con qué nivel se sale a hablar con inversores, y qué
porcentaje de aceptación automática es la meta del nivel 3.

---

## Tareas, en orden

El orden es por cuánto destraba y cuánto cuesta. El modelo es con el que se abre la
sesión, con el criterio de `docs/plan-factor-humano.md`.

| # | Tarea | Qué mueve | Tamaño | Modelo | Viene de |
| --- | --- | --- | --- | --- | --- |
| T0 | Desglose de los frenos del carril 1 | Nada por sí sola: dice cuál de las siguientes vale la pena | S | Sonnet | nueva |
| T1 | Auditar los primeros 60 aceptados solos | Da el número del nivel 1 y habilita decidir el tope | trabajo del operador | — | B2b-3a |
| T2 | Fecha por defecto en hora de La Habana | +7 de 599 hoy; todo lo capturado entre las 00:00 y las 04:00 UTC | S | Sonnet | consecuencia de B2b-2 |
| T3 | Subir el tope de filas del carril 1 | +20 con tope 2, +43 sin tope | S | Opus | G1b |
| T4 | Bugs del extractor por reglas (P1 a P3) | Baja el desacuerdo entre reglas y modelo; magnitud desconocida hasta T0 | M | Sonnet | `docs/pendientes-engine.md` |
| T5 | Imágenes con modelo de visión (C2) | Hoy ninguna imagen se acepta sola: 205 pendientes | L | Opus | plan de factor humano, C2 |
| T6 | Métrica de la Fase 0 redefinida (B3) | No sube volumen: es el número que se muestra | M | Opus | plan de factor humano, B3 |
| T7 | Expansión de canales, productos y provincias (C4) | Más entrada | por tanda | Sonnet | plan de factor humano, C4 |

### T0 — Desglose de los frenos del carril 1

`python -m saimc.validation.lane1` en seco dice cuántos entrarían y cuáles. No dice
por qué se queda afuera el resto. Falta que cuente los pendientes por motivo de
freno. Es de solo lectura, no toca el motor y no gasta API.

Con esa tabla se sabe si los 374 son sobre todo desacuerdo entre reglas y modelo
(entonces T4 va primero), precio fuera de banda (entonces es densidad por celda, y
se arregla con volumen), o canal sucio (entonces es un canal, y se decide sobre el
canal).

### T1 — Auditar los primeros 60

No es desarrollo. Hasta los 60 auditados el carril 1 se frena solo al juntar 30
aceptados sin auditar, así que **el carril avanza al ritmo de la auditoría**: si no
se audita, no entra nada nuevo. La pantalla es `/curation/auditoria`, sección
«Aceptados solos».

### T2 — Fecha por defecto en hora de La Habana

La fecha de publicación por defecto sale de `captured_at.date()`, en UTC. Un
anuncio capturado entre las 00:00 y las 04:00 UTC queda fechado un día después del
día de La Habana, y el carril 1 lo deja pendiente. Le pasa también al operador en
la cola, que tiene que corregir la fecha prellenada. Es un cambio de la cola y del
carril a la vez, porque comparten `saimc/curation_rows.py`.

### T3 — Subir el tope de filas

El tope de una fila se eligió por cautela: sobre el oro, sin tope y sin el freno de
canal sucio, hubo 15 errores en 61. Se decide con el error auditado de T1, no
antes. Subirlo cambia `lane1.CONFIG` y lleva su entrada en `docs/decisions.md`.

### T4 — Bugs del extractor por reglas

`docs/pendientes-engine.md`, prioridades 1 a 3. El más grave (P1): un catálogo de
tienda sin producto del catálogo se lee como un único bloque de precios por volumen
y se pierden productos y precios intermedios. Cada vez que las reglas y el modelo
no coinciden, el anuncio sale del carril 1, aunque el que se equivocó sean las
reglas. Es la capa de extracción: el caso esperado se escribe antes que el código.

### T5 — Imágenes con modelo de visión

Tres pasos, en `docs/plan-factor-humano.md`, sección C2: máscara de teléfonos y
nombres sobre las cajas del OCR, backtest pagado de visión contra OCR más texto
(alrededor de 1 USD cada 100 imágenes, se aprueba antes), e integración
desconectable con el apartamiento de privacidad registrado. Las imágenes no entran
al carril 1 hasta que ese backtest dé un número.

### T6 — Métrica de la Fase 0 redefinida

Hoy H0 mide el extractor. Con el carril 1 andando, el número que describe el
sistema es la tasa de automatización más el error auditado. B3 congela el conjunto
de referencia y cambia `measure.py`. Toca el esquema: lleva especificación.

### Menores, cuando toquen

- Guardar las filas caídas de `llm_proposals`: hoy se recalculan contra el catálogo
  del día (Sonnet).
- API por lotes en el pre-cálculo: la mitad del costo, cuando el volumen lo
  justifique (Sonnet).
- Aviso de corte de ingesta por mail o Telegram (Sonnet).

### Lo que no entra en este plan

No sube la automatización: la capa pública por celda (B4), el lote manual de textos
(A1c), el menú lateral de `/control`, las capturas de la landing, Docker de
desarrollo. La regla 5 (posible duplicado) tampoco: desde B2b un posible duplicado
se publica y no frena el carril 1.

---

## Home pública — por definir

El operador pidió cambiarla (2026-10-09). Qué cambia no está definido, y no se
programa nada hasta que lo esté.

Hoy `/` es la landing: un precio de portada con su rango, la cobertura, seis
fortalezas y el recorrido «Así trabajo» en cinco capturas.

Lo que hay que decidir antes de abrir el slice:

1. **Para quién es.** Hoy le habla a quien busca un precio. Un inversor busca otra
   cosa: cuánto dato hay, cuánto se produce solo y con qué error.
2. **Si es la misma página o son dos.** Una home para el público y una página
   aparte para inversores, o una sola que sirva a los dos.
3. **Qué números muestra.** Los de `/metodologia` (filas por procedencia y error
   auditado) ya son públicos; subirlos a la portada es una decisión distinta.

Cuando esté definido, es un slice de interfaz: prototipo a 390 px aprobado primero,
página real después, las dos comparadas lado a lado.
