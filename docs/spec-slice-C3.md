# Slice C3 — Controles de §10.7 antes del carril 1

Parte del plan `docs/plan-factor-humano.md`. Define qué controla lo que se publica,
así que se aprueba antes de programar. **No cambia el esquema ni el motor de
validación:** todo lo que construye es de solo lectura.

## Problema

`spec.md` §10.7 declara C3 y C4 «no aplicables» en la Fase 0–1 porque la barrera
contra el dato falso es el operador que revisa cada fila. El carril 1 (B2b) saca al
operador de esa barrera: una fila con `provenance = auto` llega a una celda pública
sin que nadie la mire. El plan (revisión del 2026-10-02) exige que los controles
estén operativos antes.

Lo que hay hoy, relevado sobre el código:

| Control | Ya existe | Falta |
| --- | --- | --- |
| C1, N mínimo | El n y la composición de la muestra, visibles. Ningún piso de publicación | La medición de N que pide §12.6 |
| C2, robustos | Mediana en todos los agregados. Cerco por IQR sobre el logaritmo (`fence.py`) en la regla 2 y en la cifra pública | MAD, que la spec nombra y el código no usa |
| C3, índice de control | Nada. `provenance` (B1) distingue filas humanas de automáticas | El índice y su divergencia |
| C4, coordinación | Emisores y días por celda en `/control`. `sender_hash` (B5), que nadie lee | Todas las señales |

§12.6 prohíbe inventar umbrales, y casi ninguno se puede medir todavía: no hay filas
automáticas y `sender_hash` empezó a llenarse el 2026-10-03. Por eso este slice
construye **mediciones**, y deja un solo criterio que actúa, hecho con parámetros
que ya existen.

## Qué se construye

### C1 — Medir N

Un comando de solo lectura, `python -m saimc.validation.measure_n`, que el operador
corre en el VPS. Es el análisis que pide §12.6: estabilidad de la mediana frente al
tamaño de muestra.

- Toma las celdas con 20 o más filas en la ventana pública de 30 días
  (`DEFAULT_WINDOW_DAYS`). El 20 es del análisis, no de la publicación.
- Para cada tamaño k de 2 a 15, saca 1.000 submuestras sin reposición con semilla
  fija y mide cuánto se aparta la mediana de la submuestra de la mediana de la celda.
- Informa, por k: la mediana y el percentil 90 de ese error relativo, y sobre cuántas
  celdas se midió.
- No fija N. El operador elige el error que tolera y el dato da el N, en G1b.

La publicación no cambia: C1 sigue «informativa» como manda §10.7 para la Fase 0–1.

### C2 — Registrar el apartamiento

Sin código. Se anota en `docs/spec-map.md` que el atípico se detecta con el cerco de
IQR sobre el logaritmo y no con MAD, que es lo que nombra §10.7. Cambiar el
estimador obligaría a revalidar todo sin evidencia de que mejore.

### C3 — Índice de control

- **Índice principal:** la mediana de la celda con todas sus filas, como hoy.
- **Índice de control:** la misma celda calculada solo con filas `human` o `audited`.
- **Diverge** cuando la mediana principal cae fuera del cerco (`fence.py`, el mismo
  de la regla 2) de las filas de control. Hace falta que el control tenga al menos
  `MIN_CELL_N` filas (5, decisión del 2026-10-03); con menos, la celda queda **sin
  ancla** y se informa así.
- No introduce ningún umbral nuevo.

Es un apartamiento de §10.7, que define la fuente de alta confianza por emisor
(historial, consistencia, antigüedad). Esos criterios no se pueden medir todavía. En
esta fase la fuente de alta confianza es la fila que verificó una persona.

### C4 — Señales de coordinación, como medición

Por celda y sobre la misma ventana de 30 días, sin umbral:

1. **Concentración por canal:** qué parte de las filas aporta el canal que más aporta.
2. **Concentración por emisor:** lo mismo por `sender_hash`, y cuántas filas no
   tienen emisor.
3. **Sincronía:** cuántas filas de canales distintos caen a menos de
   `burst_window_minutes` (10, ya configurado) una de otra.
4. **Texto casi idéntico:** cuántas filas de canales o emisores distintos tienen el
   mismo texto una vez normalizado (minúsculas, sin tildes, sin dígitos ni espacios).
   Es una igualdad, no un puntaje de similitud.

Dos señales de §10.7 quedan fuera porque hoy no hay con qué medirlas: cuentas de
creación reciente (el emisor no tiene historia) y divergencia de un precio contra el
historial de su vendedor.

### Dónde se ve y quién lo usa

- Una función pura, `cell_controls(...)`, que devuelve por celda el índice de
  control, si diverge o está sin ancla, y las cuatro señales. B2b y B2c la consumen.
- Una sección nueva en `/control`, «Controles de manipulación (§10.7)»: arriba,
  cuántas celdas divergen y cuántas están sin ancla; debajo, una fila por celda con
  n, n de control, las dos medianas y las cuatro señales. Primero las que divergen.
  A 390 px sigue el patrón de la matriz de los pilotos.
- El emisor nunca se muestra, ni su hash: solo conteos y porcentajes.
- La capa pública no cambia.

Con el carril 1 apagado no hay filas automáticas: ninguna celda puede divergir y la
sección sirve para ver las señales de C4 y cuántas celdas tendrían ancla.

## Qué no entra

- Frenar el carril 1 cuando una celda diverge: es B2c, que lee `cell_controls`.
- Sacar una fila del carril 1 por una señal de C4: los umbrales salen de la
  distribución medida y se fijan en G1b.
- Afinar la regla 5 con el emisor: toca el motor y tiene su propio pendiente.
- Ocultar celdas públicas por N, o «datos insuficientes» en la capa pública.
- Guardar el índice de control o un historial de divergencias: se calcula al pedirlo.
  «Divergencia sostenida» queda sin definir hasta que haya filas automáticas.

## Tests primero

Casos definidos acá, antes del código:

1. Una celda con 6 filas humanas alrededor de 100 y 7 automáticas alrededor de 300:
   diverge.
2. La misma celda con las automáticas alrededor de 105: no diverge.
3. Una celda con 4 filas humanas y 10 automáticas: sin ancla, no «diverge».
4. Una celda solo con filas humanas: no diverge y su índice de control es igual al
   principal.
5. Una fila `audited` cuenta como control.
6. Concentración por canal: 8 filas de un canal y 2 de otro dan 80%.
7. Concentración por emisor: con 3 filas sin `sender_hash` de 10, informa 3 sin
   emisor y calcula la parte sobre las 7 restantes.
8. Sincronía: dos filas de canales distintos a 5 minutos cuentan; a 15 minutos, no;
   dos del mismo canal a 5 minutos, tampoco.
9. Texto casi idéntico: «Pollo 4x10 a 11500» y «pollo 4x10  a 12000» de canales
   distintos cuentan; del mismo canal y mismo emisor, no.
10. `measure_n` con una celda sintética de valores todos iguales da error 0 para
    todo k; con la misma semilla, dos corridas dan lo mismo; una celda de 19 filas no
    entra.
11. `/control` muestra la sección, y en su HTML no aparece ningún `sender_hash`.

## Verificación del operador

1. Push y reinicio de `saimc.service`. No hay migración.
2. Abrir `/control`: aparece «Controles de manipulación (§10.7)», con 0 celdas que
   divergen y las señales por celda.
3. Mirar una celda conocida de un grupo grande: su concentración por canal coincide
   con lo que ya muestra la matriz de los pilotos.
4. Correr `python -m saimc.validation.measure_n` en el VPS y pegar la salida: es el
   insumo de N para G1b.
5. **Lo que no tiene que pasar:** un hash de emisor en pantalla, una celda pública
   que cambie, o un cambio en `validation_status` de alguna fila.

## `decisions.md`

Sí: el índice de control son las filas verificadas por una persona, no un conjunto
de emisores; C4 entra como medición sin umbrales; MAD se reemplaza por el cerco de
IQR; y N queda sin fijar hasta la medición.
