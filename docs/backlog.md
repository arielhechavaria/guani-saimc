# Backlog

Documento vivo de tareas pendientes, en orden de prioridad. No reemplaza a
`docs/decisions.md` (evidencia de decisiones ya tomadas, mirando atrás) ni al
whitepaper (documento rector, congelado en v3.5). Acá vive lo que falta, por
qué importa, y qué lo bloquea si algo.

Formato de entrada, sin ceremonia:

```
## Título corto
Por qué importa: ...
Bloqueado por: ... (o "nada")
```

Es un archivo de anexado igual que `decisions.md`: cuando una tarea se hace,
se saca de acá y su motivo queda en `decisions.md` si ameritó una decisión.

---

## Revisar el diccionario de abreviaturas de provincia contra uso real

Por qué importa: el diccionario en `extraction/engine.py` es una estimación
razonable de jerga cubana, no validada contra anuncios reales (ver
`decisions.md`, 2026-08-07).

Bloqueado por: volumen real de anuncios curados que lo ejerciten.

## Persistir `unit` por fila en `prices_history`

Por qué importa: hoy la métrica de cantidad/unidad de H0 se aproxima contra
la unidad canónica del catálogo, no contra una confirmación real por fila
(`decisions.md`, 2026-08-07). Si el catálogo de productos bandera no alcanza
para mantener unidad estable, esto se vuelve necesario.

Bloqueado por: evidencia de que la aproximación actual no alcanza. Si se
activa: cambio de esquema → flujo completo de especificación (CLAUDE.md).

## `provinces.aliases` como columna en vez de diccionario en código

Por qué importa: simetría con `products.aliases`, permitiría editar sin
deploy.

Bloqueado por: nada urgente — la jerga de provincia es acotada y las 16
provincias son fijas. Solo vale la pena si el diccionario en código se vuelve
difícil de mantener.

## Campo "fecha de publicación" en `prices_history`

Por qué importa: el whitepaper (§6.1c) lo pide como campo de la cola de
curaduría; hoy no existe la columna. `raw_ingestions.captured_at` (inmutable,
derivado de la fecha de modificación del archivo) es la única fecha
disponible, y no siempre coincide con cuándo se publicó el anuncio.

Bloqueado por: nada técnico. Cambio de esquema → flujo completo de
especificación si se decide agregar.

## Flag "conjunto de referencia" en `prices_history`

Por qué importa: el whitepaper (§6.1c) pide poder marcar un registro como
parte del conjunto de referencia. En Fase 0 es innecesario — el 100% de lo
curado ya es referencia, porque no hay otra fuente (`decisions.md`,
2026-08-07). Vuelve a ser relevante cuando existan fuentes automáticas.

Bloqueado por: Fase 2 — fuentes automáticas (whitepaper §6.1, Etapa 3).
