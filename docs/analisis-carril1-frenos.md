# Análisis: por qué el carril 1 deja afuera a los textos pendientes (2026-10-13)

Solo lectura. No cambia el motor, los parámetros, el `.env` ni la base. Cifras de
una foto de producción del 2026-10-13 (motor de `master` b13706f): **240 textos
pendientes con propuesta** (no 248: la cola se movió) y **0 entrarían ahora**. Los 11
de Groupej del en seco anterior ya fueron aceptados: el carril aceptó 346 anuncios en
total, 331 de ellos hoy. Lo que queda en la cola es, por construcción, lo que el
carril ya rechazó una vez; por eso el resto parece «solo Groupej» (ver §7).

Los motivos coinciden con la salida del operador (77 / 69 / 56 / 42 / 42 / 38 / 20 /
5 / 5 / 4 / 1 / 1). Los anuncios con propuesta de prompt viejo (sin glosario) son 72
de 239.

Método: volcado de cada anuncio con sus filas, citas y motivos (script de la sesión,
fuera del repo); lectura manual de las muestras; error medido sobre las 465
revisiones individuales de textos curados (`llm_reviews`), con la regla de B2b: la
provincia que impone el canal no cuenta como edición, y completar la compra mínima
tampoco.

**Corrección a la consigna:** el vencimiento a 7 días **ya no rige**. La decisión
del 2026-10-13 «Lo prioritario es la última jornada» lo apagó
(`SAIMC_QUEUE_MAX_AGE_DAYS=0`). Los 77 del §2 no vencen.

## 1. Tope de filas (69 anuncios, 42 frenados solo por esto)

**Por qué 4.** No es una medición: es una decisión del operador del 2026-10-13
(«El carril 1 corre sin tope de sin auditar y con hasta 4 filas»), que reemplazó el
tope de 1 fila de B2b. Descartó subir a más de 4 porque el oro v7 mostraba que
«desde 3–4 filas crece el error». Lo que dijo el oro v7 (decisión del 2026-10-05,
sin tocar / tocados por filas propuestas): 1 fila 37/12, 2 filas 26/27, 3 filas 6/19,
4 o más 3/20. Ese «tocado» incluía la provincia impuesta, la compra mínima y 21
casos de «solo filas borradas» que el propio texto de la decisión sospecha de oro
incompleto: sobrestima el error.

**Distribución.** 69 anuncios con 5 a 13 filas (5 filas: 27; 6: 13; 7: 7; 8: 7;
9: 6; 10 o más: 9). Por canal: Mercancía Patry 28 (0 decisiones del operador),
Groupej 8, Ventas mayoristas 8, El Gallo 6, Rey 6. Los 42 «solo tope» tienen todas
las filas con cita verificada, sin abstención y sin freno de reglas: 5 filas 18,
6 filas 8, 7 filas 4, 8 filas 3, 9 filas 4, 10 a 13 filas 5.

**Error medido con las decisiones reales** (cota de Clopper-Pearson al 95 %):

| Propuesta | Anuncios | Con error | Cota |
| --- | --- | --- | --- |
| 1 a 4 filas, todas con cita verificada y sin abstención | 95 | 5 (5,3 %) | 10,7 % |
| 1 a 4 filas, con alguna cita o abstención | 18 | 8 (44 %) | 65,9 % |
| 5 a 8 filas, «limpia» | 3 | 1 | 86,5 % |
| 5 a 8 filas, no limpia | 3 | 1 | 86,5 % |

Por fila, en propuestas limpias de 1 a 4 filas: 5 filas cambiadas de 131 (3,8 %;
cota 7,9 %). De esos 5 errores, 2 son `packaging`, 1 cantidad y unidad, 2 son una
fila de más que se borró y 1 toca el precio. Con p por fila de 3,8 % un anuncio de
8 filas tendría ~27 % de error y uno de 13 filas ~39 %; con la cota 7,9 %, ~49 % y
~66 %. **Para listas largas no hay evidencia propia**: el operador curó 6 anuncios de
5 filas o más. La auditoría tampoco ayuda aún: de los 67 anuncios auditados del
carril, ninguno tenía 4 o más filas (47 de una fila, 11 de dos, 7 de tres, 0 errores
en los 18 de 2 y 3 filas, cota 15,3 %); los 15 de 4 filas siguen sin auditar.

**Muestra de calidad** (3 listas de «solo tope»; lectura mía, no del operador).
Las tres tienen filas dudosas que ninguna regla de fila marca:
- `445c4717` (Patry, 5 filas): «MDM 50lb» quedó como Pollo; en otros anuncios es
  Picadillo.
- `20cc8edf` (Patry, 9 filas): «Picadillo 400 gr» quedó como 400 `unidad` con base
  total.
- `7c3af902` (Rey, 13 filas): filas de contenedor con cantidad 33.600 y 600 `unidad`,
  filas duplicadas y arroz guyanés en 100 lb en un bloque y 50 kg en otro.

**Conclusión.** Quitar el tope sin más no se sostiene: el error por fila medido es
bajo pero se acumula, y las listas largas tienen errores de otra clase (producto
por sigla, unidad de gramo, contenedores) que hoy ningún freno ve.

**Qué proponer, con costo y riesgo.**
- *Motor, bajo costo:* frenos de fila para esas clases: cantidad mayor a 1.000 con
  unidad `unidad`, `unidad` con cantidad de 100 o más en `paquete`, filas repetidas
  dentro del mismo anuncio. Escribir primero los casos esperados (CLAUDE.md).
- *Medición, sin inventar umbral:* subir el tope a 6 como prueba (suma 26 anuncios
  de los 42) con **auditoría del 100 % de los de 5 a 6 filas** hasta juntar n. Con
  0 errores, 29 anuncios dan cota de 9,8 % y 59 dan 5,0 %. La meta de 10 % por
  anuncio ya es del operador (B2b); lo que se mide es si la cota baja de ahí. Si
  aparece 1 error en 18 la cota es 23,8 %: se corta.
- *Alternativa de fondo (más grande, fuera de alcance):* aceptar por fila. Las
  filas limpias entran y las dudosas van a la cola. Cambia el motor y la auditoría;
  no se propone ahora.

## 2. «El modelo descarta, pero las reglas ven un producto piloto» (77, los 77 solo por esto)

Se leyeron los 77. Clasificación manual:

| Clase | Anuncios | Qué es |
| --- | --- | --- |
| A. Otro rubro con una palabra piloto en el nombre | 39 | «Aceite de Argán», «sabor a Pollo», «ajo molido en aceite», «Spam de pollo», pienso, cosméticos, tanques IBC, azúcar de exportación |
| B. Agotado, aviso o sin precio | 23 | «AGOTADO ARROZ», «Pollo de 33lb», reservas, «próximamente» |
| C. Anuncio válido con precio de producto piloto que el modelo descartó | 15 | ver abajo |

Los A y B (62 de 77, 81 %) son descartes correctos del modelo. El carril 0 los
deja en la cola porque las reglas ven la palabra y hay un dígito. No se pierden
(siguen pendientes, no vencen); le cuestan al operador descartarlos a mano.

Los 15 de la clase C son la pérdida real: seis listas de El Gallo con arroz,
azúcar y pollo a precio (`c2c3b423`, `aacfc5fc`, `fdb7e84b`, `91a37378`, `4c796793`,
`c5bacd19`), dos de ALZA con azúcar ICUMSA en saco (`6837b211`, `33ab522a`),
Aceite COAMO a 3,25 (`605873b9`, `bd7074b1`), Arroz Aloy a 1,03 USD (`41d73a06`),
«Pollo entero 850 CUP x libra» (`5f03d782`), y tres dudosas (`21435ecf`,
`8348cfb6`, `6357d03b`). El modelo descartó listas que otras veces del
mismo canal sí leyó: es inconsistencia del modelo, no de las reglas.

**Recomendación.**
- Los 15: no son recuperables por el carril 1 (no tienen filas). Costo: volver a
  pedir la propuesta cuesta ~0,0073 USD por anuncio, 0,11 USD los 15; el modelo no
  es determinista, así que puede o no cambiar. Riesgo: ninguno de datos.
- Los 62: un refinamiento del carril 0 (que la palabra piloto no cuente dentro de
  «aceite de argán», «en aceite», «sabor a pollo», o junto a «agotado») ahorraría
  hasta 62 descartes manuales. **Cambia el motor y la decisión cerrada 2 / G1a**
  (el descarte automático se mide con 10 % de falsos descartes y auditoría semanal):
  no se implementa sin tu decisión. Medición propuesta: correr la regla nueva en
  seco contra los descartes auditados de la semana y contra estos 15; tiene que
  dar 0 de 15 capturados.

## 3. Abstenciones en unidad, cantidad y base (56 / 42 / 38)

**No es falta de dato en el texto: es prompt viejo.** De los 239 anuncios con
propuesta, 72 se generaron antes del glosario (`2026-09-27.1` y `2026-09-25.3`,
sin hash de glosario). Esos 72 concentran la abstención:

| Abstención | Anuncios | De prompt viejo | Con glosario |
| --- | --- | --- | --- |
| unidad | 56 | 39 | 17 |
| cantidad | 42 | 37 | 5 |
| base del precio | 38 | 35 | 3 |

Filas de arroz o azúcar sin cantidad: 40, y **39 son de prompt viejo**. La entrada
de glosario «Arroz o azúcar sin cantidad → saco de 100 lb» (2026-10-02) ya las
resuelve. Los 30 anuncios de Groupej con abstención son todos de prompt viejo, y 24
de ellos tienen la abstención como único freno (`2ec72f99`, `963fe751`, `a79d3261`).

Segunda causa: la unidad. 15 de las 22 filas con cantidad pero sin unidad son
gramos (Picadillo 400 g, 500 g: `d56a74b9`). La `g` entró al vocabulario el
2026-10-13 (rama `unidad-gramos`) y esas propuestas son anteriores. Tampoco cambió
`PROMPT_VERSION`, así que no hay forma de ver cuáles quedaron viejas por eso.

Tercera: abstención real. «Caja de pollo» sin peso (`108dcf15`), «Pollo de 33lb» sin
precio. El prompt dice «abstenerse es preferible a adivinar», conservador a
propósito.

**Qué hace el operador cuando el modelo se abstiene** (decisiones individuales,
`llm_reviews`): llenó **14 de 14** cantidades y unidades (arroz y azúcar 100 lb: 9;
pollo 40 lb: 3; otros 2) y **17 de 23** bases, todas como `total`. Nunca dejó el
campo vacío.

**Recomendación.**
1. *Re-proponer los 72 anuncios de prompt viejo* (motor de extracción: un comando
   nuevo, porque `select_candidates` salta los que ya tienen propuesta). Costo
   ~0,53 USD (72 × 0,0073). Sin cambios de motor ni de prompt. Es lo de mayor
   retorno de todo el análisis. Riesgo: ninguno de datos; el modelo no es
   determinista y una fila buena puede salir distinta.
2. *Glosario, sin inventar:* «un precio de lote con cantidad mayor a 1 y sin `c/u`
   ni `por lb` es `total`». 17 de 17 en las decisiones del operador (cota
   superior de excepciones 16,2 %). Contraejemplos que vigilar: el propio modelo
   dice `unit` con 900 ml cuando el precio es por botella (§5). Medir con la
   auditoría.
3. *No* fijar «caja de pollo = 40 lb» como default: n = 3 y Patry vende cajas de
   33 lb (`445c4717`).

## 4. Cita no verificada (42 anuncios, 30 solo por esto)

Se clasificaron las **46 filas** que fallan (no una muestra de 15):

| Clase | Filas | Qué pasa |
| --- | --- | --- |
| B1. Solo difieren signos, emoji o saltos de línea | 28 | La cita es literal salvo que el modelo omite un emoji o el salto de línea |
| B2. Trozos literales unidos con «...» | 5 | Dos fragmentos reales del texto separados por puntos suspensivos |
| E. Cita hallada pero sin el precio | 6 | `Pacas de 20u` para 15.300; y 4 con «8mil» (Pollo 8000) que el lector de precios no entiende |
| F. Fila sin precio | 7 | No es una oferta (`Picadillo None`): el freno es correcto |

**Ninguna cita es inventada.** Ejemplo: texto `AZUCAR BLANCA MAGDALENA 25KG‼️\n\n▪️$34.50USD`,
cita `AZUCAR BLANCA MAGDALENA 25KG ▪️$34.50USD`. `verify_evidence` normaliza solo
mayúsculas, tildes y blancos (`llm_checks._squash`), no emoji ni signos.

**Recomendación (motor de extracción, `llm_checks.verify_evidence`).**
- Comparar solo alfanuméricos y verificar cada trozo separado por «...». Libera 25
  anuncios que hoy frena solo la cita (31 de 42 limpian la cita). Costo bajo.
  Riesgo bajo: la cita sigue pidiendo todas las letras y números en orden y
  contiguos por trozo, y `price_in_evidence` sigue pidiendo el precio dentro de la
  cita. Escribir primero los casos esperados.
- Leer «8mil» como 8000 en `_price_in_fragment`: 4 filas.
- Ojo: `verify_evidence` corre al proponer y se guarda; para aplicarlo a lo pendiente
  hay que recalcular al leer o re-proponer (§3 ya lo hace para 72).
- Cambiar la definición de «cita verificada» se registra en `docs/decisions.md`.

## 5. «Precio leído con la otra base queda más cerca de su celda» (20 / 12 solo)

Se leyeron 16 de los 20. **La celda está bien; el que se equivoca es el modelo, y la
regla 6 lo atrapa.** Cinco clases:
- Aceite 900 ml con `price_basis = unit` y cantidad 900 ml, cuando el precio es por
  botella (`0e109832`, `126a84bb`, `cada97b2`, `298e3761`): el precio quedaría por
  mililitro. Debería ser `total` o cantidad 1 botella.
- Arroz 1 kg en pacas de 30 con cantidad 30 kg y base total, cuando 720 es por paca
  de 1 kg (`debc7c47`); Aceite COAMO 2.450 con 24 `unidad` y base total
  (`6d7aaa73`).
- «Parcial de Pollo 2 lb – 700 cup. Venta x libras»: 700 es por libra, no por 2 lb
  (`7b372419`, `ffe56857`, `915156fb`, `feabe4ad`, `720a6d5b`).
- Precios en USD sin marca leídos como CUP (`a4230789`, `c0b47314`: 4,00 por 4 lb).
- «Pollo de 10 lb a $800» (`1b3c2819`), ambiguo: 800 por libra encaja con la celda.

**Recomendación.** Dejar el freno como está: es correcto. Mejora de prompt, no de
motor: una línea que diga que «c/u», «x pomo» o «por libra» fijan el precio como de
una unidad, y que la cantidad es la del envase vendido. Costo: bajo; se mide en el
backtest con el oro v7 antes de aplicar. Riesgo: tocar el prompt invalida
comparaciones con corridas viejas.

## 6. Los restantes (confirmados)

- **Canal sucio (5):** todos de El Bodegón Doble Vía. Dos auditorías recientes del
  mismo canal dieron «no era un anuncio válido» (`8cb31516`, `230a3abb`). Freno
  correcto.
- **Falta campo (5):** filas sin precio, el modelo propone «Picadillo None»,
  «Pollo None» (`ad4a8729`, `b45a8d4a`, `18c5b3f8`). Freno correcto. El prompt
  podría no devolver filas sin precio.
- **Fuera de banda (4):** Groupej `f85deffb` y `338c89b2` traen Picadillo y Azúcar
  con cantidad 1 lb base `unit` y precio 27.750: el modelo leyó mal la base del saco
  o caja. `11060cf5` es aceite de oliva a 18,50 en CUP (es USD y no es el aceite
  piloto). Freno correcto.
- **Sin filas (1) y filas caídas (1, `292e02d6`, caldo de pollo):** correctos.

## 7. Por qué solo entra Groupej

**El filtro no favorece a Groupej por historial.** Desde B2b-1 el canal sin
historial no frena; Groupej tiene 28 decisiones y otros canales con 0, y aun así
se aceptaron 346 anuncios de más de 25 canales (331 hoy). Lo que se ve es **lo que queda**
después de que el carril ya procesó el histórico: lo bueno entró y lo malo
sigue. Groupej entra primero porque publica una plantilla estable y de pocas filas, y
cada post nuevo aparece como pendiente nuevo.

Y justo el mismo Groupej tiene 39 pendientes, 24 de ellos frenados solo por
abstención de prompt viejo. Por canal, el primer freno:

| Canal | Pendientes | Freno principal |
| --- | --- | --- |
| Groupej | 39 | abstención 30 (todas prompt viejo), tope 8 |
| Mercancía Patry | 30 | tope 28 (listas de 5 a 11 filas) |
| El Gallo | 27 | descarta+piloto 15, otra base 9 |
| ALZA | 17 | cita 10, descarta+piloto 7 |
| Rey (productos al por mayor) | 14 | tope 6, descarta+piloto 5 |
| GUAMAY | 11 | descarta+piloto 11 (no vende producto piloto) |

Nota: Groupej tiene 50 anuncios aceptados sin auditar y la especificación decía
«Groupej queda fuera mientras siga sucio». Su estado de limpieza no se examinó acá.

## Resumen y decisiones

Estimaciones de anuncios que pasarían (simulación sobre estos 239, **cotas
superiores**: asumen que re-proponer arregla toda abstención de prompt viejo; con
glosario todavía abstiene ~10 %).

| Grupo | Anuncios recuperables | Error esperado (cota 95 %) | Esfuerzo | Decisión tuya |
| --- | --- | --- | --- | --- |
| §3 Re-proponer 72 viejos | 33 (solo abstención) | sin medir; auditar el 100 % al inicio | Chico: un comando, ~0,53 USD | Aprobar el gasto y el comando |
| §4 Cita con alfanuméricos y «...» | 25 | solo cambia la verificación; auditar | Chico: función + casos | Aprobar el cambio de definición de «cita verificada» (registrar) |
| §1 Tope 4 → 6 con auditoría total | +26 (solo tope ≤ 6) | medir: 0 errores en 29 = 9,8 %, en 59 = 5,0 % | Medio: frenos de fila + auditoría | Aprobar la prueba y el tope 6 |
| §1 Tope 4 → 13 | +16 más (tope 7 a 13) | sin evidencia (n = 3) | — | No recomendado ahora |
| §2 Refinar carril 0 | 62 descartes manuales menos | falsos descartes: debe seguir ≤ 10 % | Medio-alto: motor y G1a | Decidir si se toca G1a |
| §2 los 15 anuncios válidos descartados | 0 automáticos | — | 0,11 USD si se re-proponen | Opcional |
| §5 Base y moneda | 0 (el freno es correcto) | — | Prompt, con backtest | Aprobar el cambio de prompt |
| §6 Restantes | 0 | — | — | Ninguna |

Combinando §3, §4 y el tope 4: **hasta 58** de los 239 (24 %); con tope 6: 89;
con tope 8: 98; sin tope: 109. Son cotas superiores; el piso es la mitad menos lo
que el modelo cambie al re-proponer.

Las decisiones que dependen de ti: **(1)** gastar ~0,53 USD en re-proponer los 72
viejos; **(2)** registrar el cambio de «cita verificada»; **(3)** si se prueba el
tope 6 con auditoría total; **(4)** si se toca el descarte automático del carril 0
(apartamiento de G1a).

Todo lo que propone cambiar el motor o apartarse de la especificación **no se
implementó**. Cifras de una sola foto de producción: la cola cambia cada 3 h.
