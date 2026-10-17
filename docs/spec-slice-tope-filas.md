# Slice tope-filas-carril1 — el tope de filas del carril 1, medido

Estado: **aprobada por el operador el 2026-10-17**: el tope se mantiene en 4.
Toca el motor de validación: flujo completo (`CLAUDE.md`, «Proceso»).

## 1. La pregunta

`lane1.CONFIG.lane1_max_rows = 4` no salió de una medición: lo fijó el operador
el 2026-10-13 («El carril 1 corre sin tope de sin auditar y con hasta 4 filas
por anuncio», `docs/decisions.md`), apoyado en el oro v7, que mostraba que
«desde 3–4 filas crece el error». Hoy hay auditoría del carril 1. La pregunta:
¿qué tope sostiene la meta de error por anuncio (`lane1.ERROR_TARGET`, 10 %)
**con la cota superior**, no con la tasa?

## 2. Medición (base local, 2026-10-17, solo lectura)

Error = la definición vigente del carril (`lane1._verdicts`): `corrected` o
`invalid`; completar la compra mínima o un formato vacío no cuenta. Cota
superior de Clopper-Pearson unilateral al 95 % (`validation/stats.py`). Filas =
`row_count` del evento `curated/auto`, lo que el carril aceptó.

### 2.1 Por número de filas

428 aceptados solos, 198 auditados.

| Filas | Auditados (todo) | Errores | Tasa | Cota sup. | Desde 2026-10-15 | Cota sup. |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 127 | 9 | 7,1 % | 12,0 % | 1 en 34 | 13,2 % |
| 2 | 40 | 4 | 10,0 % | 21,4 % | 0 en 13 | 20,6 % |
| 3 | 22 | 0 | 0 % | 12,7 % | 0 en 7 | 34,8 % |
| 4 | 9 | 0 | 0 % | 28,3 % | 0 en 6 | 39,3 % |
| 5 o más | 0 | — | — | 100 % | 0 | 100 % |

Acumulado (lo que mide el freno, que no separa por filas):

| Tope | Todo | Cota sup. | Desde 2026-10-15 | Cota sup. |
| --- | --- | --- | --- | --- |
| 1 | 9 en 127 | 12,0 % | 1 en 34 | 13,2 % |
| 2 | 13 en 167 | 12,1 % | 1 en 47 | 9,7 % |
| 3 | 13 en 189 | 10,7 % | 1 en 54 | 8,5 % |
| 4 | 13 en 198 | 10,2 % | 1 en 60 | **7,7 %** |

Lo que hoy rige el freno es la columna «desde 2026-10-15»
(`SAIMC_LANE1_AUDIT_SINCE`): 1 en 60, cota 7,7 %, bajo la meta.

### 2.2 Lo que dicen los números

1. **Con 1 a 4 filas el error no crece con las filas.** 3 y 4 filas juntas:
   0 en 31, cota 9,2 %, la única franja que por sí sola cumple la meta. El
   oro v7 decía lo contrario, pero su «tocado» contaba la provincia impuesta,
   la compra mínima y filas borradas por oro incompleto
   (`docs/analisis-carril1-frenos.md` §1).
2. **Los errores son de canal, no de largo.** 11 de los 13 vienen de dos
   canales: VENTAS D' ALE (7 correcciones) y EL BODEGÓN DOBLE VÍA (4: 3 «no
   era válido» y 1 corrección). Eso lo ataca la regla del canal sucio, no el
   tope.
3. **De 5 filas o más no hay ninguna auditoría**: el carril nunca aceptó una.
   Su cota es 100 %.
4. **Por largo del anuncio tampoco hay señal** (líneas con algún dígito en el
   texto, todo lo auditado): 1–4 líneas 10 en 128; 5–9, 1 en 12; 10–19, 2 en
   33; 20 o más, 0 en 25. Un anuncio-catálogo largo del que el modelo saca
   pocas filas no se equivoca más.
5. **Lo que el tope frena hoy** (en seco, 252 pendientes con propuesta): 79
   anuncios de 5 a 13 filas, 43 de ellos **solo** por el tope (5 filas: 20;
   6: 8; 7: 6; 8 a 13: 9).

Evidencia descartada: lo curado a mano. Hay 4 propuestas de 5–6 filas que hoy
entrarían al carril sin tope, y la vara de la curaduría («el operador tocó
algo», que cuenta la provincia del canal) no es la de la auditoría.

## 3. Propuesta: el tope queda en 4, ahora medido

**Recomendación: no cambiar el valor.** Lo que cambia es el fundamento: 4 deja
de ser un juicio sobre el oro v7 y pasa a ser lo que la auditoría sostiene.

**Por qué no 5 o más.** El freno mira el error de todos los aceptados juntos.
Si entran anuncios de un largo nunca auditado, la única cota honesta para ellos
es 100 %, y la cota del conjunto queda, en el peor caso, en
`U + s·(1 − U)`, con `U` la cota de lo auditado y `s` la parte de los
aceptados que serían largos. Para no pasar la meta, `s ≤ (meta − U)/(1 − U)`:

- desde 2026-10-15 (`U` = 7,7 %): `s` ≤ **2,5 %**;
- con todo lo auditado (`U` = 10,2 %): `s` ≤ 0.

Los 43 que frena solo el tope son ~9 % de lo que el carril aceptaría (43 de
428 + 43). Pasan el margen por casi cuatro veces. Y hay un problema peor que la
cota: con 9 % del flujo, un estrato de 5 filas con 30 % de error sube el error
del conjunto unos 2,5 puntos, y el freno no lo nota. Un tope más alto escondería
el estrato malo en el promedio.

**Por qué no bajar a 3.** 3 y 4 filas no muestran más error que 1 y 2 (§2.2,
punto 1), y bajar dejaría afuera 19 aceptados de 4 filas sin ninguna evidencia
en contra.

**Por qué no un tope que dependa del tamaño del catálogo.** Ni el largo del
anuncio (§2.2, punto 4) ni la proporción filas propuestas / líneas con dígito
muestran señal: con menos de la mitad de las líneas, 8 en 112 (7,1 %); con la
mitad o más, 5 en 55 (9,1 %); con todas, 0 en 31. Y el catálogo de productos
de SAIMC no cambia el riesgo de un anuncio: el tope es riesgo por anuncio
(`docs/plan-bloque-mvp.md`, T11b).

**Cómo se llega a 5, si se quiere** (fuera de alcance; decide el operador): no
con más medición del carril, que nunca va a ver un anuncio de 5 filas mientras
el tope sea 4. Hace falta un estrato propio: aceptar los de 5–6 filas con un
freno propio y auditándolos todos, hasta 29 seguidos sin error (0 en 29 da
cota 9,8 %). Es un cambio del motor (un freno por estrato), no de un número.

## 4. Lo que se construye

Solo un comando de lectura para que la medición se repita sin un script suelto
(la medida caduca, como toda medida contra datos vivos):

```
.venv/bin/python -m saimc.validation.lane1 --por-filas
```

Imprime, para todo lo auditado y para lo auditado desde
`SAIMC_LANE1_AUDIT_SINCE`: auditados, errores y cota superior por número de
filas, el acumulado hasta cada tope y la parte de anuncios sin auditar que ese
acumulado todavía tolera (`admissible_share`). No escribe nada.

El motor no cambia: `lane1_max_rows` sigue en 4 y `ENGINE_VERSION` no se toca.

## 5. Casos (escritos antes del código)

Los esperados salen de la definición, no de correr el código.

1. **Estratos por filas** (puro). Respuestas: nueve `ok` y un `invalid` de 1
   fila; cuatro `ok` y un `corrected` de 2 filas; un `completed` de 3 filas.
   Esperado: 1 fila, 10 auditados, 1 error; 2 filas, 5 y 1; 3 filas, 1 y 0
   (`completed` no es error). Ordenados por filas.
2. **Acumulado hasta un tope** (puro). Con los estratos del caso 1: hasta 2,
   15 auditados y 2 errores; hasta 3, 16 y 2; hasta 1, 10 y 1.
3. **Parte tolerada** (puro). Cota 7,7 % y meta 10 %: (0,10 − 0,077)/(1 − 0,077)
   = 0,02492. Cota 12 %: 0. Cota exactamente 10 %: 0.
4. **El comando** (base). Tres anuncios de 1 fila aceptados solos: uno
   auditado «está bien», uno «no era válido», uno sin auditar. `--por-filas`
   sale con 0, muestra el estrato de 1 fila con 2 auditados y 1 error (el sin
   auditar no cuenta) y no escribe nada: el sin auditar sigue curado y sin
   respuesta.
5. **El tope sigue en 4**: lo fija `test_los_parametros_son_los_que_aprobo_el_operador`
   (ya existe).
