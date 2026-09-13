# Checklist de revisión — cerrar un slice sin que pudra en 3 meses

## Por qué existe

Un diff puede verse bien, pasar los tests, y aun así estar podrido por dentro.
Es el problema que ya están pagando caro empresas grandes que aceptan código
generado por IA sin un control objetivo: el PR es largo, se ve prolijo, los
tests están en verde — y algo huele raro que nadie puede señalar con el dedo,
hasta que explota meses después. Leer el diff de punta a punta no alcanza como
control: hace falta una lista chica y mecánica que cualquiera pueda aplicar,
incluida una persona que no escribió el código (humana o no).

Esto no es una lista inventada. Cada punto corresponde a una práctica de
ingeniería con nombre propio — se cita entre paréntesis para poder buscarla si
hace falta más contexto — y varias ya están exigidas en `CLAUDE.md` para la
capa de extracción/validación; acá se generalizan como preguntas de revisión
para todo el repositorio.

## Cuándo se usa

En todo slice, antes de darlo por cerrado. Se suma al criterio de cierre que
ya fija `CLAUDE.md` § Alcance (tests pasan + entrada en `decisions.md` si
aplica + commit hecho): este documento es la parte de "tests + decisión" hecha
verificable por un tercero, no solo por quien escribió el código.

No reemplaza el flujo de especificación completo (`openspec/`) que `CLAUDE.md`
ya exige para esquema de datos o motor de validación — ahí este checklist se
aplica **además**, no en lugar de.

Tampoco reemplaza a `ruff`/`mypy` (`.venv/bin/ruff check .`,
`.venv/bin/mypy`): corré esos primero. Son gratis, mecánicos y no requieren
criterio — no vale la pena gastar los cuatro ejes de abajo revisando algo que
una herramienta ya señala sola.

## El checklist

### 1. Trazabilidad — ¿el diff es lo que dice ser?

- [ ] Todo archivo tocado se explica por el slice declarado. Un archivo "de
  paso" es scope creep: se saca del diff o se declara como slice aparte.
- [ ] Si hubo una decisión no obvia, está en `docs/decisions.md` con su
  contexto, descartado y consecuencia — no alcanza con que esté en el commit.

### 2. Calidad del test — el punto crítico contra código de IA

- [ ] El caso esperado se definió antes de correr el código y sin mirar su
  comportamiento (el *problema del oráculo* en testing: un test derivado del
  propio código bajo prueba no prueba nada, solo lo documenta).
- [ ] Prueba de mutación mental: si comento la lógica nueva, ¿el test falla?
  Si sigue en verde, es un test tautológico — decora, no protege.
- [ ] Cubre al menos un caso borde o de error, no solo el camino feliz.

### 3. Smells típicos del diff que "se ve bien pero pudre"

(*code smells*, en el sentido de Fowler: no son bugs, son señales de que algo
va a costar caro después)

- [ ] Ninguna excepción atrapada y silenciada sin re-lanzar ni loguear.
- [ ] Ningún mock o stub reemplaza justo la parte riesgosa que el cambio
  supuestamente ejercita — el test pasa porque no llegó a probar nada.
- [ ] Los nombres dicen lo que la función hace hoy, no lo que hacía antes ni
  lo que el autor esperaba que hiciera.
- [ ] La validación de invariantes vive en el borde correcto del sistema
  (entrada de usuario, API externa) y no está duplicada ni dispersa por capas
  que deberían poder confiar en esa entrada ya validada.

### 4. Blast radius — ¿qué se rompe si esto está mal?

- [ ] Si el cambio toca `raw_ingestions` (inmutable), el motor de validación,
  o el identificador de usuario origen (nunca expuesto en la capa pública), el
  blast radius es alto.
- [ ] Si el blast radius es alto, ¿correspondía el flujo `openspec/` completo
  y no se usó? Si no correspondía, decilo explícitamente y por qué.

## Diffs grandes o generados en una sesión larga de IA

No alcanza con "leer todo de corrido" — la atención se diluye exactamente en
los diffs donde más se necesita. Aplicá el checklist función por función, no
PR completo. Para cambios de blast radius alto, Claude Code tiene revisión
doble ciega disponible (`judgment-day`): no es obligatoria hoy, pero es la
herramienta a usar cuando este checklist no alcanza para dar confianza.
