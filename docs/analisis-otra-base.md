# Regla 6: ¿la base declarada o la «otra»? (análisis, 2026-10-18)

Slice `analizar-otra-base`, solo lectura. Pregunta: en los anuncios que
`rules.check_quantity_price` marca y que ya se curaron, ¿cuántas veces era
correcta la base que declaró el modelo y cuántas la «otra»? ¿Cambia algo con
`min_references` más alto o con una distancia mínima entre las dos lecturas
(`cells.evaluate_basis`)?

Marco: `spec.md` §6.2, «Regla 6 — Coherencia cantidad/precio»; §12.6 (ningún
umbral se inventa, se mide).

## Resultado

- **20 filas marcadas, en las 20 la correcta era la «otra»; en 0 la
  declarada.** Error de la regla: 0/20, cota superior 13,9 % (Clopper-Pearson
  95 %).
- **El operador no corrigió ninguna de las 20.** Las guardó con la base del
  modelo. En las 1.085 filas propuestas y revisadas de `llm_reviews`, el
  operador cambió `price_basis` **una sola vez** (`unit` → `total`).
- **`min_references` y la distancia mínima no cambian nada útil.** Con
  `min_references` de 3 a 20 el conjunto marcado es el mismo (las celdas tienen
  de 30 a 136 referencias). Una distancia mínima entre lecturas de ×3, o una
  ventaja mínima de ×1,5, saca una sola fila —el Picadillo de 500 g— y esa
  fila la regla la acertaba: pasaría a `unknown`, que no frena el carril 1.
  Error esperado de esos cambios: +1 sobre 20, ninguno a favor.

## Cómo se midió

1. Todas las revisiones `curated` de `llm_reviews` (681 anuncios, 1.114 filas
   propuestas). Para cada fila se armó el mismo contexto que el motor
   (`context._row_contexts`: referencias anteriores al día del anuncio, celda
   propia con repliegue a todas las provincias) y se corrió `evaluate_basis`.
2. Deduplicado por anuncio y fila (los anuncios del carril 1 que después se
   auditaron tienen dos revisiones): 805 filas, 381 con dos lecturas distintas
   (cantidad ≠ 1). Con `min_cell_n = 5`, 20 marcadas.
3. **La verdad no sale de la fila final del operador**, porque nunca toca la
   base (punto 2 del resultado): medido así daba 529/529 «correcta la
   declarada», un número sin información. Las 20 marcadas se etiquetaron
   leyendo el texto del anuncio, con la respuesta definida antes de mirar qué
   lectura prefería la regla.
4. Las que la regla deja pasar no se etiquetaron una por una. Se revisaron las
   que tienen la lectura declarada a más de ×2 de su celda (5): en las 5 la
   base declarada es la correcta; lo que está mal es la moneda o la cantidad,
   que la regla 6 no mira.

## Las 20 filas

| Patrón | Filas | Qué dice el anuncio | Qué guardó el modelo | Correcta |
| :--- | ---: | :--- | :--- | :--- |
| Aceite 900 ml / 700 ml «c/u» | 18 | 2300 CUP por botella de 900 ml | `unit`, 900 ml: 2300 CUP **por ml** | `total` |
| Aceite 1 l, caja de 15 | 1 | 1,86 € por botella de 1 l | `total`, 15 l: 0,12 €/l | `unit` |
| Picadillo 500 g | 1 | 0,91 USD el paquete de 500 g | `unit`, 0,5 kg: 0,91 USD/kg | `total` |

El error de las 18 es del factor de conversión (×1.000): 2300 CUP «por ml»
son 2.300.000 CUP/l. `detect_basis_suspects` lo describe con el mismo caso
(`1,90 USD · 900 ml · basis='unit'`, 2026-08-22). El formulario pregunta bien
(«Precio por mililitro» contra «Precio de los 900 mililitros juntos»), pero la
opción llega preseleccionada con la respuesta del modelo y el operador no la
cambia.

## Consecuencia que ya está en la base

Las 20 filas están guardadas: 13 curadas a mano y 7 por el carril 1 (5 `auto`,
2 `audited`). Por qué el carril 1 aceptó filas que hoy la regla marca no está
verificado; la reconstrucción usa las referencias de hoy, que incluyen filas
cargadas después con fecha anterior, y en su momento la celda pudo no llegar a
5. Todas quedan `inconsistent`, pero `compute_retail_ranges` no filtra por
estado: en la base local, la celda Aceite · La Habana · caja 900 ml da un
máximo de **2.415.000 CUP/l** en el rango de 30 días.

## Propuesta (sin implementar)

**No tocar `min_references` ni agregar distancia mínima.** El dato no los
pide y la única fila que mueven es un acierto.

**Cuando la regla 6 sale `fail`, el formulario de la cola preselecciona la
otra base** y muestra las dos lecturas en CUP por unidad al lado de la mediana
de su celda. El operador sigue decidiendo; lo que cambia es qué opción ve
marcada. Error esperado de la preselección: 0/20, cota 13,9 %. No toca el
motor (la regla ya calcula todo) ni el esquema: es plantilla y armado de la
fila del formulario.

Descartado por ahora:

- *Que el carril 1 dé vuelta la base solo y acepte el anuncio.* Mismo 0/20,
  pero sin nadie mirando y con n = 20; toca el motor y pide flujo completo.
  Tiene sentido cuando la preselección junte más casos sin corrección.
- *Corregir el prompt* («c/u» de una botella de 900 ml = `total`). Ataca la
  causa, pero cuesta re-proponer y no arregla lo que ya está guardado.

Aparte de la propuesta, las 20 filas guardadas hay que corregirlas.
«Bases sospechosas» de `/control` (30 días) muestra 18: no ve la del aceite
marcada `total` ni la del Picadillo. Muestra además 4 filas de Aceite con el
mismo error que no salieron de la cola (48,00 ×2, 3,35 y 2,90 por 900 ml).
