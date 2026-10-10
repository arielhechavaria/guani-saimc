# Catálogo de comandos del operador

Cuando un slice agrega un comando para el operador, su entrada se suma acá en el mismo commit.

Todos los comandos se corren **desde la raíz del repo** (`~/guani/guani-saimc`), en el VPS salvo que
la entrada diga «desarrollo». Cada bloque de código es una sola línea (o varias con `\`), sin
heredocs: se pega tal cual aunque el copiado agregue espacios al principio.

Una entrada marcada «sin verificar» es un comando que está escrito en otro documento o en el código
pero que no se probó al armar este catálogo. Si falla, el catálogo es el que está mal: corregilo.

## ¿Cuál era el de…?

| Comando | Para qué | Cambia algo | Gasta |
|---|---|---|---|
| [`psql` con la URL del `.env`](#abrir-psql) | Entrar a la base | no (solo lee) | no |
| [`saimc.ingestion`](#ingerir-la-bandeja-de-entrada) | Ingerir archivos de la bandeja | sí: filas crudas | no |
| [`systemctl start saimc-prefill`](#pre-cálculo-ahora-a-mano) | Pre-calcular propuestas ya (y preguntarle a Jev, si está encendido) | sí: propuestas y señales de Jev | sí, tope 0,15 USD (más 0,01 de Jev) |
| [Después de un deploy](#después-de-un-deploy-la-secuencia) | Backup, pull, migrar, reiniciar | sí | no |
| [`saimc.validation.measure_n`](#measure_n) | Estabilidad de la mediana por N | no | no |
| [`saimc.extraction.measure`](#measure-h0) | Medir H0 contra la cola | `--no-record` no; sin él, anexa a `h0-runs.jsonl` | no |
| [`saimc.validation.shadow`](#informe-del-modo-sombra) | Qué resolvería solo el motor, y qué cambia con Jev | en seco no; `--execute` anexa a `shadow-runs.jsonl` | no |
| [`saimc.validation.lane1`](#carril-1-en-seco) | Qué textos pendientes aceptaría solo el carril 1 | en seco no; `--execute` crea filas ([ver](#carril-1-escribiendo)) | no |
| [`saimc.validation.revalidate`](#revalidar-en-seco) | Cuántas filas cambiarían de estado | no (en seco) | no |
| [`saimc.extraction.prefill` en seco](#pre-cálculo-en-seco) | Estimar el costo del pre-cálculo | no | no |
| [`saimc.llm.provider`](#proveedor-del-asistente) | Ver por dónde llama el asistente (Anthropic o Vercel) y con qué modelo | no | no |
| [`scripts/lint_drift.py --no-autofix`](#lint-drift) | Hallazgos nuevos de ruff y mypy | no | no |
| [Huecos por canal (SQL)](#huecos-por-canal) | Cuánto calla cada canal | no | no |
| [Verificaciones de B5 y K3 (SQL)](#verificaciones-de-b5-y-k3) | Comprobar hash de emisor y OCR | no | no |
| [Señal de Jev (SQL)](#señal-de-jev) | Ver qué juntó Jev y qué quedó anotado | no | no |
| [`saimc.validation.jev_signal` en seco](#relleno-de-jev) | Cuántos textos le faltan a Jev y cuánto costaría | no | no |
| [`systemctl` / `journalctl`](#temporizadores-y-servicios) | Estado, logs, frenar temporizadores | solo si frenás | no |
| [`saimc.validation.revalidate --execute`](#revalidar-escribiendo) | Re-guardar estado de las filas | sí: `prices_history` | no |
| [`saimc.validation.jev_signal --decided --execute`](#relleno-de-jev) | Preguntarle a Jev por los textos ya decididos | sí: `jev_signals` | sí, tope `SAIMC_LLM_JEV_MAX_COST` o `--max-cost` |
| [`saimc.basis_backfill --apply`](#backfill-de-base-de-precio) | Completar `price_basis` viejo | sí | no |
| [`saimc.exchange_rate.backfill --apply`](#backfill-de-tasas-de-cambio) | Completar tasas pendientes | sí | no; consulta la API de elTOQUE |
| [`scripts/redact_existing.py --apply`](#redactar-teléfonos-ya-guardados) | Quitar teléfonos del texto guardado | sí: `original_text` | no |
| [`scripts/add_focus_aliases.py --apply`](#alias-del-foco) | Agregar alias al catálogo del foco | sí: productos | no |
| [`saimc.extraction.llm_backtest`](#backtest-del-asistente) | Backtest del asistente contra lo curado | `--execute` escribe archivos | `--execute` sí |
| [`scripts/jev_eval.py`](#prueba-de-jev) | Medir la calibración de Jev contra lo ya decidido (A6) | en seco no; `--execute` escribe en `data/jev_eval/` | `--execute` sí, tope 0,20 USD |
| [`pytest`, `ruff`, `mypy`, `alembic revision`](#desarrollo-local) | Suite, estilo, tipos, migración nueva | no (revision crea un archivo) | no |

> **Atención.** Gastan plata o reescriben datos: `prefill --execute`, `llm_backtest --execute`,
> `scripts/jev_eval.py --execute` (y su `--check`, una sola llamada),
> `saimc.validation.jev_signal --execute`, `revalidate --execute`, `alembic upgrade head`, y todo lo que lleva `--apply`. Hacé backup antes
> de cualquiera que escriba en la base. Dos corridas reales del pre-cálculo no pueden solaparse,
> se lancen como se lancen: la segunda encuentra el candado `data/prefill.lock`, avisa y sale sin
> gastar (ver [«Pre-cálculo escribiendo, fuera de systemd»](#pre-cálculo-escribiendo-fuera-de-systemd)).

---

## Rutina diaria

### Abrir psql

```
psql "$(grep -E '^SAIMC_DATABASE_URL=' .env | cut -d= -f2- | sed 's/postgresql+psycopg:/postgresql:/')"
```

- **Para qué**: abre una consola de PostgreSQL con la URL de la base que ya está en `.env` (la misma
  conversión que hace [`scripts/backup_db.sh`](../scripts/backup_db.sh): sin el sufijo `+psycopg`).
- **Cuándo**: cuando una entrada de abajo pide SQL, o para mirar algo a mano.
- **Cambia algo**: no, solo lee, mientras no escribas vos. **Gasta**: no.
- Salir: `\q`. Para una consulta suelta, agregá `-P pager=off -c '…'` (las entradas de abajo lo hacen).

### Ingerir la bandeja de entrada

```
.venv/bin/python -m saimc.ingestion
```

- **Para qué**: toma los archivos (capturas, texto) de la carpeta de entrada y los guarda como
  registros crudos en la base. Opciones vistas en `--help`: `--inbox` y `--store`; por defecto salen
  de `SAIMC_INBOX_DIR` y `SAIMC_RAW_STORE_DIR` del `.env`.
- **Cuándo**: cuando dejaste archivos nuevos en la bandeja. Lo de WhatsApp no pasa por acá: entra
  solo por el webhook de WAHA.
- **Cambia algo**: sí, crea filas en `raw_ingestions` y copia archivos al almacén. **Gasta**: no.
- **Cómo leer la salida**: lista cada archivo ingerido o «salteado (extensión no reconocida)».
- Más: [README](../README.md), sección «Rutina de curaduría».

### Pre-cálculo ahora, a mano

```
sudo systemctl start saimc-prefill.service
```

- **Para qué**: pide al modelo la propuesta de cada pendiente de la cola, para que al abrir
  `/curation` ya estén. Normalmente lo hace solo el temporizador cada 3 h; esto lo adelanta.
- **Cuándo**: después de ingerir un lote grande y antes de sentarte a curar.
- **Cambia algo**: sí, guarda propuestas (y, con los interruptores del carril 0 encendidos en el
  `.env`, descarta solo lo que el carril 0 decide). **Gasta**: sí, **tope de 0,15 USD por corrida**
  (`SAIMC_PREFILL_MAX_COST` en la unidad).
- Se queda esperando hasta que termina. En otra terminal: `journalctl -fu saimc-prefill.service`.
- **Jev (A6b)**: con `SAIMC_LLM_JEV_ENABLED=true` y `SAIMC_VERCEL_API_KEY` en el `.env`, la corrida
  empieza con una pasada que le hace tres preguntas a cada texto pendiente y guarda la respuesta en
  `jev_signals`. Tiene su propio tope, **`SAIMC_LLM_JEV_MAX_COST` (0,01 USD por corrida)**, aparte
  del de arriba. Un fallo de Jev no frena el resto ni pone en rojo la tarjeta «Pre-cálculo»: queda
  en `journalctl` (líneas `jev.fallo`) y en `/control#jev`. Ver [Señal de Jev](#señal-de-jev).
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Paso 1».

---

## Después de un deploy: la secuencia

Es una secuencia, en este orden. El push lo hace el operador desde su máquina; esto es lo que se
corre en el VPS.

```
scripts/backup_db.sh
```

```
git pull
```

```
.venv/bin/alembic upgrade head
```

```
sudo systemctl restart saimc.service
```

- **Para qué**: 1) guardar un respaldo de la base; 2) traer el código; 3) aplicar las migraciones
  pendientes; 4) reiniciar la aplicación para que cargue el código nuevo.
- **Cuándo**: cada vez que subís un slice. El `alembic upgrade head` se corre solo si el slice trae
  migración; si no la trae, se saltea ese paso (y el backup igual conviene).
- **Cambia algo**: sí. `backup_db.sh` escribe `backups/saimc_<fecha>.sql.gz` y borra los de más de 7
  días; `alembic` cambia el esquema; el reinicio corta la app unos segundos. **Gasta**: no.
- **Atención**: `alembic upgrade head` toca el esquema de producción. Sin backup previo, no.
- Si el slice agrega una variable al `.env` (por ejemplo `SAIMC_SENDER_HASH_KEY` en B5), editala
  antes del reinicio.
- **Sin verificar**: `saimc.service` es el nombre que usan los specs, pero su archivo de unidad no
  está en `systemd/` del repo (se instaló a mano en el VPS). Si el nombre no coincide,
  `systemctl list-units 'saimc*'` lo muestra.
- El respaldo diario automático existe aparte: ver [temporizadores](#temporizadores-y-servicios).

---

## Medir y diagnosticar

### measure_n

```
.venv/bin/python -m saimc.validation.measure_n
```

- **Para qué**: responde «¿con cuántos datos por celda la mediana deja de moverse?». Para cada
  tamaño de muestra de 2 a 15, dice cuánto se aparta la mediana de la celda entera.
- **Cuándo**: cuando haga falta decidir N (G1b); es la verificación del slice C3.
- **Cambia algo**: no, solo lee. **Gasta**: no. No tiene flags.
- **Cómo leer la salida**: por cada k, mediana y percentil 90 del error relativo y sobre cuántas
  celdas se midió. Fija nada: el número lo elegís vos.
- Más: [spec-slice-C3.md](spec-slice-C3.md), «C1 — Medir N».

### Medir H0

```
.venv/bin/python -m saimc.extraction.measure --no-record
```

- **Para qué**: calcula el porcentaje de extracción completa correcta contra la cola de curaduría
  real. Con `--no-record` solo lo mirás.
- **Cuándo**: para ver el número sin ensuciar el histórico. Para dejar la corrida registrada, el
  mismo comando **sin** `--no-record`, siguiendo el runbook.
- **Cambia algo**: con `--no-record`, no. Sin él, agrega una línea a `docs/h0-runs.jsonl` (commiteala).
  **Gasta**: no.
- Más: [runbook-medir-h0.md](runbook-medir-h0.md).

### Informe del modo sombra

```
.venv/bin/python -m saimc.validation.shadow --gold data/llm-backtest-results-v6.jsonl --grid
```

- **Para qué**: dice cuánto podría resolver solo el motor de validación, y con qué error, sobre lo
  que ya decidiste a mano. El motor no toca la cola.
- **Cuándo**: una vez por semana. Ese comando es en seco. Para dejar registro, el mismo con
  `--execute` agregado (anexa una línea a `docs/shadow-runs.jsonl`, que se commitea).
- **Cambia algo**: en seco, no. **Gasta**: no (no llama a la API ni escribe en la base).
- `--gold` es obligatorio. Sin `--grid` hay que pasar `--min-cell-n`, `--channel-min-history` y
  `--channel-max-edit-rate`.
- `--lane1-max-rows N` es opcional: manda al carril 2 toda propuesta con más de `N` filas. Sin él
  no hay tope. Sirve con `--grid` y sin ella, igual para todas las combinaciones, y sale en la
  línea de parámetros y en el registro. Para mirar solo los anuncios de una fila:

  ```
  .venv/bin/python -m saimc.validation.shadow --gold data/llm-backtest-results-v7.jsonl --grid --lane1-max-rows 1
  ```

  G1b lo fijó en 1 (2026-10-09). Para ver el carril 1 con los valores que aprobó el
  operador (`docs/spec-slice-B2b.md`):

  ```
  .venv/bin/python -m saimc.validation.shadow --gold data/llm-backtest-results-v7.jsonl --min-cell-n 5 --channel-min-history 3 --channel-max-edit-rate 0.5 --lane1-max-rows 1
  ```
- **Cómo leer la salida**: mirá la cota al 95%, no el porcentaje: un 0% sobre un n chico no dice
  nada.
- **El bloque «Jev»** (A6b-2) cierra cada conjunto. Abre con `n = … de … textos`: cuántos textos
  del conjunto tienen señal de Jev con las preguntas vigentes; solo esos entran. Lee la tabla
  `jev_signals`: no llama a Jev, no depende de `SAIMC_LLM_JEV_ENABLED` y no escribe en la base. Con
  `n = 0` no hay nada que medir: primero el [relleno](#relleno-de-jev). Trae tres tablas:
  1. *Carril 1, con Jev y sin Jev.* «Saca» son los anuncios que estaban en el carril 1 y con la
     señal pasan al 2; «eran errores» son los que habías editado (Jev los frenó bien) y «estaban
     bien», automatización perdida. Va para «cualquier desacuerdo» y para 0,5, 0,7 y 0,9, con cada
     una de las dos probabilidades de Jev. «Entran» tiene que dar siempre 0.
  2. *Triage al lado del carril 0.* Para P(sí) menor que 0,1, 0,2, 0,3 y 0,5: cuántos descartaría
     Jev, cuántos de esos habías conservado (falsos descartes), y cuántos descartaría solo el
     motor, solo Jev o los dos.
  3. *Campos.* Cuántas veces Jev acierta el producto y la moneda contra las filas curadas.

  Las grillas son para mirar: el informe no elige ningún umbral (eso es de G1). Con `--execute`,
  la línea de `docs/shadow-runs.jsonl` suma la clave `jev`.
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Una vez por semana»; el bloque
  «Jev», en [spec-slice-A6b.md](spec-slice-A6b.md), punto 8.

### Carril 1 en seco

Qué textos pendientes, de los que ya tienen propuesta, aceptaría solo el carril 1 con los
valores que aprobó el operador (`docs/spec-slice-B2b.md`: tope de 1 fila, canal sucio con 3
decisiones y más de la mitad de las filas tocadas, celda de 5).

```
.venv/bin/python -m saimc.validation.lane1
```

- **Cambia algo**: no. **Gasta**: no (usa las propuestas guardadas).
- Lista cada anuncio con su canal y cierra con «Entrarían al carril 1: N de M». `--limit N`
  corta cuántos mira.
- Después dice por qué se queda afuera el resto («Quedan afuera: N de M») con una tabla por
  motivo y dos columnas: **anuncios** (cuántos tienen ese motivo; un anuncio puede tener varios,
  así que la columna suma más que el total) y **solo** (cuántos frena únicamente ese motivo:
  es lo que entraría quitando ese freno). Los motivos son los que devuelve el motor
  (`validate_ad`), más la celda que diverge de sus filas de control y la fecha por defecto
  posterior al día de La Habana. Con `--execute` no se imprime.
- El freno de auditoría no es un motivo del anuncio: si está puesto, un aviso lo dice y
  `--execute` no aceptaría ninguno. La fila en divisa sin tasa no aparece, porque en seco no se
  resuelve la tasa.
- En seco no se resuelve la tasa de cambio: una fila en divisa sin tasa no entra al aceptar.
- Un anuncio capturado entre las 00:00 y las 04:00 UTC no entra: su fecha por defecto cae un
  día después del día de La Habana, y el formulario tampoco la aceptaría.

### Revalidar en seco

```
.venv/bin/python -m saimc.validation.revalidate
```

- **Para qué**: dice cuántas filas de `prices_history` cambiarían de estado si se las validara de
  nuevo con el motor y los parámetros de hoy.
- **Cuándo**: antes de un `--execute`, o después de cambiar `min_cell_n` o la versión del motor.
- **Cambia algo**: no (es el modo por defecto, equivale a `--dry-run`). **Gasta**: no.
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Revalidar las filas».

### Pre-cálculo en seco

```
.venv/bin/python -m saimc.extraction.prefill
```

- **Para qué**: estima cuántos anuncios entrarían al pre-cálculo y cuánto costaría, sin llamar al
  modelo. Con Jev encendido suma una línea: cuántos textos le preguntaría y el costo estimado.
- **Cuándo**: antes de una corrida grande, para saber el costo.
- **Cambia algo**: no (por defecto es `--dry-run`). **Gasta**: no.
- Más: [runbook-asistente-llm.md](runbook-asistente-llm.md), sección del pre-cálculo.

### Proveedor del asistente

```
.venv/bin/python -m saimc.llm.provider
```

- **Para qué**: muestra la configuración efectiva del asistente («Completar con IA», pre-cálculo y
  backtest): proveedor, modelo con que se registra y se tarifa, modelo que se manda, base URL,
  timeout, y de la clave solo si está y de qué variable salió. Nunca imprime la clave.
- **Cuándo**: después de tocar las variables del asistente en `.env`, antes de reiniciar; y cuando
  `/control` muestre el asistente «apagado» o el pre-cálculo falle por la clave.
- **Cambia algo**: no. **Gasta**: no: no hace ninguna llamada, así que no prueba que la clave
  sirva ni que haya saldo. Eso se ve en la primera llamada real.
- **Cómo leer la salida**: sale con código 1 y el motivo si no hay configuración válida (proveedor
  desconocido, o falta la clave: dice qué variable hay que definir). Si en el `.env` o en el entorno
  siguen escritas `SAIMC_LLM_API_KEY` o `SAIMC_LLM_BASE_URL`, que se retiraron el 2026-10-09 y ya no
  hacen nada, lo avisa (sin mostrar su valor) para que las borres; el aviso no cambia el código. Lee el `.env` del directorio desde el que
  se corre: desde la raíz del repo.

Las variables, en `.env` (los valores vacíos cuentan como no puestos):

| Variable | Por defecto | Qué gobierna |
|---|---|---|
| `SAIMC_LLM_PROVIDER` | `anthropic` | el interruptor: `anthropic` (directo) o `vercel` (Vercel AI Gateway). Fija también la URL y de qué variable sale la clave |
| `SAIMC_ANTHROPIC_API_KEY` | sin valor | la clave, si el proveedor es `anthropic`. Con `vercel` no se usa |
| `SAIMC_VERCEL_API_KEY` | sin valor | la clave, si el proveedor es `vercel`. Con `anthropic` el asistente no la usa (Jev sí, siempre) |
| `SAIMC_LLM_MODEL` | `claude-sonnet-5` | el modelo, **sin** prefijo `anthropic/`: lo agrega el código al llamar por el gateway |
| `SAIMC_LLM_TIMEOUT` | `30` | segundos por llamada desde la cola; el pre-cálculo y el backtest usan su `--timeout` |

`SAIMC_LLM_ASSIST_MODEL` y `SAIMC_LLM_ASSIST_TIMEOUT` son los nombres anteriores de las dos últimas
y siguen valiendo cuando las nuevas no están. Lo mismo `SAIMC_LLM_JEV_API_KEY`, el nombre anterior
de `SAIMC_VERCEL_API_KEY` (la clave del gateway, que usan el asistente y Jev): si están las dos,
gana la nueva.

Para pasar el asistente a Vercel AI Gateway:

1. En `.env`: `SAIMC_LLM_PROVIDER=vercel`. La clave del gateway ya está si Jev está configurado
   (`SAIMC_VERCEL_API_KEY`); si no, ponela ahí. No hace falta que Jev
   esté encendido. El modelo va sin prefijo.
2. Correr el comando de arriba: tiene que decir `vercel`, `anthropic/claude-sonnet-5` como modelo
   que se manda y de qué variable sale la clave.
3. `sudo systemctl restart saimc.service`: la aplicación lee el `.env` una sola vez, al arrancar.
4. El pre-cálculo no necesita nada: cada corrida es un proceso nuevo que lee el `.env`, así que la
   siguiente del temporizador ya va por el gateway. Para no esperar,
   [`sudo systemctl start saimc-prefill.service`](#pre-cálculo-ahora-a-mano) (gasta).

Para volver: `SAIMC_LLM_PROVIDER=anthropic` y reiniciar.

- **Sin verificar** (al 2026-10-09): nadie hizo todavía una llamada real por el gateway desde este
  código; si el gateway cobra lo mismo que Anthropic directo (el gasto de `/control` se estima con
  las tarifas de Anthropic); y qué error devuelve el gateway sin saldo (se reconoce el 402).
- Más: [costos.md](costos.md), «El asistente por Vercel AI Gateway»; `docs/decisions.md`, 2026-10-09.

### Lint drift

```
.venv/bin/python scripts/lint_drift.py --no-autofix
```

- **Para qué**: compara los hallazgos de ruff y mypy contra `docs/lint-baseline.json` y dice cuáles
  son nuevos.
- **Cuándo**: en desarrollo, antes de cerrar un slice. Sin `--no-autofix` también aplica
  `ruff check --fix` (los arreglos seguros) y puede dejar un commit local; con `--no-autofix` no toca nada.
- **Cambia algo**: no. **Gasta**: no. Otros flags vistos en `--help`: `--update-baseline`, `--json`.
- Nota: el plan dice que el baseline estaba desfasado al 2026-10-03; los «nuevos» pueden no serlo.

### Prueba de Jev

Slice A6: mide si la probabilidad que devuelve Jev (TypeSafe AI, vía Vercel AI Gateway) está
calibrada sobre anuncios cubanos, por pregunta, contra lo que ya decidiste en la cola. Se corre en
**desarrollo**, contra la base local. `--check` y `--execute` necesitan `SAIMC_VERCEL_API_KEY` en
el `.env`; en seco no hace falta.

```
.venv/bin/python scripts/jev_eval.py --check
```

```
.venv/bin/python scripts/jev_eval.py
```

```
.venv/bin/python scripts/jev_eval.py --execute --limit 50
```

```
.venv/bin/python scripts/jev_eval.py --execute
```

- **Para qué**: 1) `--check` hace **una** llamada con un anuncio inventado y dice si la clave anda y
  si la respuesta trae la forma esperada; 2) sin flags, en seco: cuenta la muestra y estima tokens y
  costo sin llamar a nada (`--dry-run` es lo mismo, dicho en voz alta); 3) `--execute --limit N`
  hace a lo sumo N llamadas nuevas; 4) `--execute` solo, la corrida completa.
- **Cuándo**: en ese orden, una vez, antes de decidir qué se hace con Jev. El gasto de la corrida
  completa se aprueba antes.
- **Cambia algo**: no toca la base (solo lee). Con `--execute` escribe
  `data/jev_eval/responses.jsonl` (el caché) y `data/jev_eval/report.md`; `data/` no va a git.
  `--check` y el modo en seco no escriben nada.
- **Gasta**: en seco, no. `--check`, una llamada (estimado: menos de 0,0001 USD; imprime el costo
  real). `--execute` sí, hasta el tope de `--max-cost` (por defecto **0,20 USD**), que cuenta
  también lo ya gastado en corridas anteriores con las mismas preguntas. `--limit` y `--max-cost`
  solo tienen efecto con `--execute`. La estimación en seco usa el precio de referencia de Jev
  (0,042 USD por millón de tokens); el reporte muestra el costo que informó el gateway.
- El script no trunca: un anuncio que no entra en el contexto del modelo da error, se anota y
  sigue.
- Se puede cortar y volver a correr: lo ya respondido queda en el caché y no se vuelve a pagar. Un
  anuncio que dio error se reintenta en la corrida siguiente. Con todo respondido, `--execute`
  solo regenera el reporte.
- **Cómo leer la salida**: `--execute` imprime el reporte y lo deja en
  `data/jev_eval/report.md`. Por pregunta da el n, el Brier, el ECE y los tramos de
  confiabilidad (probabilidad media contra frecuencia observada). En las preguntas de elección hay
  **dos tablas**, porque la respuesta trae dos señales distintas: la probabilidad de la opción
  elegida (`probabilities[choice]`) y `confidence`, que no es lo mismo y no se sabe cómo se
  calcula. Mirá las cotas, no el porcentaje: los curados son pocos. El reporte no
  decide ni fija umbrales. Al final dice cuántos quedaron sin preguntar si se llegó al tope.
- Si `--check` dice «la clave no fue aceptada» (401), el problema es la clave, no el script. Si
  dice «la clave autentica, pero el gateway denegó el acceso» (403), la clave está bien y falta
  acceso al modelo: al 2026-10-04 el plan gratuito de Vercel AI Gateway no incluye a Jev y hay
  que cargar crédito pago. El plan gratuito además limita a 5 pedidos por minuto; el script espera
  lo que pide el gateway y sigue.
- Más: [plan-factor-humano.md](plan-factor-humano.md), «A6 — Prueba de Jev fuera de línea».

### Señal de Jev

Slice A6b-1: Jev en sombra. Pregunta dentro del [pre-cálculo](#pre-cálculo-ahora-a-mano) y anota
al curar; para los textos que se decidieron antes está el [relleno](#relleno-de-jev). Se gobierna con cuatro variables del
`.env` (después de cambiarlas, `sudo systemctl restart saimc.service`):

| Variable | Por defecto | Qué gobierna |
|---|---|---|
| `SAIMC_LLM_JEV_ENABLED` | `false` | el interruptor |
| `SAIMC_VERCEL_API_KEY` | sin valor | la clave de Vercel AI Gateway; sin ella Jev queda inactivo aunque el interruptor esté encendido. `SAIMC_LLM_JEV_API_KEY`, su nombre anterior, sigue valiendo |
| `SAIMC_LLM_JEV_TIMEOUT` | `10` | segundos por llamada |
| `SAIMC_LLM_JEV_MAX_COST` | `0.01` | USD por corrida del pre-cálculo; en `0` no se llama |

Qué juntó (dentro de [psql](#abrir-psql)):

```
SELECT source, count(*), sum(cost_usd) FROM jev_signals GROUP BY 1;
```

Qué quedó anotado en las últimas filas curadas:

```
SELECT validation_status, provenance, validation_detail->'jev' FROM prices_history ORDER BY created_at DESC LIMIT 3;
```

- **Para qué**: comprobar que la pasada de Jev guardó respuestas y que al curar se anotan.
- **Cuándo**: al encender Jev, y cuando `/control#jev` muestre algo raro.
- **Cambia algo**: no. **Gasta**: no (las consultas; la pasada sí, con su tope).
- **Cómo leer la salida**: la primera da una fila por origen (`prefill`, y `backfill` para lo que
  preguntó el [relleno](#relleno-de-jev) con `--decided`) con cuántas señales hay y cuánto costaron. La segunda trae `null` en la tercera columna cuando la fila no tiene señal: el
  anuncio se curó antes de que pasara el temporizador, es una imagen, o Jev está apagado. Cuando
  la tiene, el bloque trae `triage`, `product` y `currency`, cada uno con su probabilidad; `agrees`
  dice si lo que eligió Jev coincide con alguna fila del anuncio.
- Jev solo anota: ninguna fila cambia de estado, de carril ni de `provenance` por lo que diga.
  Para apagarlo, `SAIMC_LLM_JEV_ENABLED=false` y reiniciar: lo ya guardado queda, deja de anotarse.
- Más: [spec-slice-A6b.md](spec-slice-A6b.md); costo en [costos.md](costos.md).

### Relleno de Jev

En seco (no llama a nadie): cuántos textos ya decididos le faltan a Jev y cuánto costaría.

```
.venv/bin/python -m saimc.validation.jev_signal --decided
```

Preguntando de verdad, con un tope para esta corrida:

```
.venv/bin/python -m saimc.validation.jev_signal --decided --execute --max-cost 0.10
```

Lo mismo sobre los pendientes (lo que hace sola la pasada del pre-cálculo), en seco:

```
.venv/bin/python -m saimc.validation.jev_signal
```

- **Para qué**: le pregunta a Jev por los textos que no tienen señal con las preguntas vigentes,
  para que el bloque «Jev» del [informe del modo sombra](#informe-del-modo-sombra) tenga n desde el
  primer día. Con `--decided` toma los textos ya curados o descartados y guarda las filas con
  `source = backfill`; sin él, los pendientes, con `source = prefill`.
- **Cuándo**: una vez al encender Jev (slice A6b-2), y de nuevo si cambia el catálogo de pilotos
  (cambian las preguntas y las señales viejas dejan de contar). Siempre el en seco primero.
- **Cambia algo**: en seco, no. Con `--execute` agrega filas a `jev_signals` y nada más: ninguna
  fila de precio cambia y `raw_ingestions` no se toca. **Gasta**: solo con `--execute`; unos
  0,00002 a 0,00007 USD por texto.
- `--max-cost` es el tope de esta corrida en USD; sin él vale `SAIMC_LLM_JEV_MAX_COST` (0,01), que
  no alcanza para los ~1.400 textos decididos: el en seco avisa cuando el costo estimado supera el
  tope. `--limit N` pregunta solo los N más viejos.
- Con `--execute` hace falta Jev activo (`SAIMC_LLM_JEV_ENABLED=true` y la clave). Si no, dice
  «No se puede correr», no llama a nadie y sale con código 1. El en seco no necesita la clave.
- **Cómo leer la salida**: en seco, `N textos sin señal vigente por preguntar, costo estimado …`.
  Con `--execute`, `N preguntados de M, F fallos, I respuestas ilegibles, costo …`. Si la pasada
  se corta (tope de gasto, clave rechazada, cupo agotado, tres fallos seguidos) lo dice en la
  línea siguiente: lo ya preguntado quedó guardado y volver a correrlo sigue por donde quedó. Los
  fallos van al registro como `jev.fallo`, sin el texto del anuncio.
- Más: [spec-slice-A6b.md](spec-slice-A6b.md), punto 8; qué juntó, en [Señal de Jev](#señal-de-jev).

### Huecos por canal

```
psql "$(grep -E '^SAIMC_DATABASE_URL=' .env | cut -d= -f2- | sed 's/postgresql+psycopg:/postgresql:/')" -P pager=off -c '
WITH m AS (
  SELECT channel_id, captured_at,
         captured_at - lag(captured_at) OVER (PARTITION BY channel_id ORDER BY captured_at) AS gap
  FROM raw_ingestions
  WHERE channel_id IS NOT NULL AND captured_at > now() - make_interval(days => 30)
)
SELECT c.name, c.is_active, count(*) AS mensajes,
       round((extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)) / 3600)::numeric, 1) AS hueco_mediano_h,
       round((extract(epoch FROM percentile_cont(0.95) WITHIN GROUP (ORDER BY gap)) / 3600)::numeric, 1) AS hueco_p95_h,
       round((extract(epoch FROM max(gap)) / 3600)::numeric, 1) AS hueco_max_h,
       round((extract(epoch FROM now() - max(captured_at)) / 3600)::numeric, 1) AS horas_desde_el_ultimo
FROM m JOIN channels c ON c.id = m.channel_id
GROUP BY c.name, c.is_active
ORDER BY mensajes DESC;
'
```

- **Para qué**: responde «¿cuánto tiempo suele callar cada canal, y hace cuánto no llega nada de
  cada uno?». Es la medición detrás de la regla de aviso de K4.
- **Cuándo**: cuando el aviso de hueco de `/curation` o `/control` salte, o cuando sospeches que un
  canal dejó de ingerir. Se corrió en producción el 2026-10-03.
- **Cambia algo**: no, solo lee. **Gasta**: no. Ventana: últimos 30 días.
- **Cómo leer la salida**: compará `horas_desde_el_ultimo` con `hueco_p95_h` del mismo canal: si lo
  supera con holgura, ese canal está más callado que lo normal. La regla exacta (piso de 24 h y
  2 × p95) está en `src/saimc/ingestion/channel_gaps.py`.

### Verificaciones de B5 y K3

Las consultas que los specs dan para comprobar un slice, reescritas con `-c '…'` y sin comillas
simples adentro. Significan lo mismo que en el spec.

Mensajes de la última hora y cuántos tienen hash de emisor (B5):

```
psql "$(grep -E '^SAIMC_DATABASE_URL=' .env | cut -d= -f2- | sed 's/postgresql+psycopg:/postgresql:/')" -P pager=off -c 'SELECT count(*), count(sender_hash) FROM raw_ingestions WHERE created_at > now() - make_interval(hours => 1);'
```

Los cinco hashes que más se repiten (vendedores que publican más de una vez):

```
psql "$(grep -E '^SAIMC_DATABASE_URL=' .env | cut -d= -f2- | sed 's/postgresql+psycopg:/postgresql:/')" -P pager=off -c 'SELECT sender_hash, count(*) FROM raw_ingestions WHERE sender_hash IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5;'
```

Que el log no cuente JIDs de personas (solo deben aparecer los de grupo, `@g.us`):

```
journalctl -u saimc.service --since "1 hour ago" | grep -c "@c.us"
```

Transcripciones de OCR guardadas, por motor y versión (K3):

```
psql "$(grep -E '^SAIMC_DATABASE_URL=' .env | cut -d= -f2- | sed 's/postgresql+psycopg:/postgresql:/')" -P pager=off -c 'SELECT engine, ocr_version, count(*) FROM ocr_transcriptions GROUP BY 1, 2;'
```

- **Cambia algo**: no, solo leen. **Gasta**: no.
- **Cómo leer**: con la clave puesta, las filas de grupos tienen hash; si todas quedan en nulo,
  WAHA manda el emisor en otro campo. El `grep -c` tiene que dar 0. En K3, una fila por imagen.
- **Sin verificar**: la reescritura de comillas es de este catálogo; el spec original las traía con
  `interval '1 hour'`. Tampoco se corrió el `grep -c "@c.us"` acá.
- Más: [spec-slice-B5.md](spec-slice-B5.md) y [spec-slice-K3.md](spec-slice-K3.md), «Verificación del operador».

---

## Temporizadores y servicios

Hay tres unidades: `saimc.service` (la aplicación, **sin archivo en `systemd/`**),
`saimc-backup.service` + `.timer` (respaldo diario 03:00) y `saimc-prefill.service` + `.timer`
(pre-cálculo cada 3 h). Las dos últimas están en [`systemd/`](../systemd/saimc-prefill.service).

**Estado y cuándo toca**

```
systemctl list-timers saimc-prefill.timer
```

```
systemctl list-timers saimc-backup.timer
```

```
systemctl status saimc.service
```

**Logs**

```
journalctl -u saimc-prefill.service -n 50
```

```
journalctl -u saimc-backup.service -n 50
```

```
journalctl -u saimc.service -n 100
```

**Correr ahora**

```
sudo systemctl start saimc-prefill.service
```

```
sudo systemctl start saimc-backup.service
```

**Frenar y volver** (el temporizador del pre-cálculo; el de backup es análogo, sin verificar)

```
sudo systemctl disable --now saimc-prefill.timer
```

```
sudo systemctl enable --now saimc-prefill.timer
```

**Instalar o actualizar una unidad** (una vez, y cada vez que cambia un archivo de `systemd/`)

```
sudo cp systemd/saimc-prefill.service systemd/saimc-prefill.timer /etc/systemd/system/
```

```
sudo systemctl daemon-reload
```

- **Para qué**: ver si los procesos automáticos corrieron, leer qué dijeron, adelantarlos o
  frenarlos. El pre-cálculo además se ve en `/control`, tarjeta «Pre-cálculo».
- **Cuándo**: cuando `/control` muestre ámbar o rojo, o antes de una tarea que no debe solaparse
  con el pre-cálculo.
- **Cambia algo**: los `status`, `list-timers` y `journalctl` no. `start` corre la unidad;
  `disable --now` frena el temporizador hasta que lo vuelvas a habilitar; `cp` + `daemon-reload`
  instalan. **Gasta**: solo `saimc-prefill.service`, con tope de 0,15 USD por corrida (8 corridas
  por día: peor caso 1,20 USD diarios).
- **Sin verificar**: instalar `saimc-backup` con el mismo `cp` (el runbook lo documenta solo para
  prefill y dice «igual que `saimc-backup`»); los comandos de `saimc-backup` por analogía.
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Paso 1».

---

## Mantenimiento y casos raros

> Todo lo de esta sección escribe en la base. Backup antes (`scripts/backup_db.sh`) y, cuando el
> comando tiene modo en seco, corré el seco primero.

### Revalidar escribiendo

```
.venv/bin/python -m saimc.validation.revalidate --execute
```

- **Para qué**: guarda en cada fila de `prices_history` el estado, el puntaje y el detalle que le da
  el motor de hoy. No toca nada más (ni la procedencia).
- **Cuándo**: después de un backfill de tasas o de base de precio, al cambiar `min_cell_n` o la
  versión del motor, y una vez al desplegar B1. Antes, el [en seco](#revalidar-en-seco).
- **Cambia algo**: sí, `prices_history`. **Gasta**: no.
- Reescribe el detalle entero, así que también pone al día la clave `jev` (A6b): la anota en las
  filas de anuncios que tienen señal guardada si Jev está activo, y la quita si está apagado. No
  llama a Jev.
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md); contrato en [spec-slice-B1.md](spec-slice-B1.md).

### Carril 1 escribiendo

Acepta de verdad lo que lista el comando en seco: guarda las filas con procedencia `auto`,
deja el anuncio curado y las filas salen en el tablero.

```
.venv/bin/python -m saimc.validation.lane1 --execute
```

- **Cambia algo**: sí, crea filas en `prices_history`. **Gasta**: no.
- Exige `SAIMC_LANE1_ENABLED=true` en el `.env`; apagado, avisa y sale con código 2 sin
  escribir.
- Con el interruptor encendido, el pre-cálculo hace lo mismo solo con cada propuesta nueva.
- **El freno.** Si está puesto, el comando lo dice («Carril 1 con el freno puesto: …») y no
  acepta nada. Se pone solo cuando hay 30 aceptados sin auditar y todavía no se llegó a 60
  auditados, o cuando el error auditado supera la meta de 10 %. Lo primero se levanta
  auditando en `/curation/auditoria`. Lo segundo solo lo levanta el operador, fijando
  `SAIMC_LANE1_AUDIT_SINCE=AAAA-MM-DD` en el `.env`: la cota vuelve a contar desde ese día.

### Backfill de base de precio

```
.venv/bin/python -m saimc.basis_backfill
```

```
.venv/bin/python -m saimc.basis_backfill --apply
```

- **Para qué**: completa `price_basis` en las filas anteriores a ese campo, infiriéndolo contra la
  mediana del vecindario.
- **Cuándo**: una vez; el primero muestra el plan sin escribir, el segundo lo aplica y anexa su log.
- **Cambia algo**: solo con `--apply`. **Gasta**: no.
- Después conviene [revalidar](#revalidar-escribiendo). Más: `docs/decisions.md`, 2026-09-02.

### Backfill de tasas de cambio

```
.venv/bin/python -m saimc.exchange_rate.backfill
```

```
.venv/bin/python -m saimc.exchange_rate.backfill --apply
```

- **Para qué**: completa las tasas de cambio pendientes. Es idempotente.
- **Cuándo**: cuando haya filas sin tasa. Sin `--apply` solo imprime lo que haría; `--limit N`
  acota cuántas filas procesa. Después, [revalidar](#revalidar-escribiendo).
- **Cambia algo**: solo con `--apply`. **Gasta**: no cuesta plata, pero consulta la API de
  elTOQUE (una llamada por tasa y día que falte), con el token `SAIMC_EL_TOQUE_API` del `.env`.

### Redactar teléfonos ya guardados

```
.venv/bin/python scripts/redact_existing.py
```

```
.venv/bin/python scripts/redact_existing.py --apply
```

- **Para qué**: quita teléfonos del texto ya almacenado en `raw_ingestions.original_text`. El
  archivo preservado en `data/raw/` no se toca.
- **Cuándo**: una vez, o si se mejora la redacción y hay que repasar lo viejo. Primero sin `--apply`.
- **Cambia algo**: solo con `--apply`. **Gasta**: no.
- Más: `docs/decisions.md`, 2026-08-11.

### Alias del foco

```
.venv/bin/python scripts/add_focus_aliases.py
```

```
.venv/bin/python scripts/add_focus_aliases.py --apply
```

- **Para qué**: agrega al catálogo los alias de producto («muslo de pollo», «saco de azúcar»…) que
  el filtro de la cola necesitaba para no esconder anuncios que sí eran del foco. Idempotente.
- **Cuándo**: una vez por base; correrlo de nuevo no cambia nada (motivo en `docs/decisions.md`,
  2026-08-20). Sin `--apply` solo muestra qué haría.
- **Cambia algo**: solo con `--apply`. **Gasta**: no.

### Backtest del asistente

```
.venv/bin/python -m saimc.extraction.llm_backtest
```

- **Para qué**: compara lo que propondría el asistente contra lo que curaste a mano, sobre un oro
  de anuncios. Sin flags solo estima el costo.
- **Cuándo**: al evaluar un modelo o un prompt nuevo. `--execute` hace la corrida real
  (**gasta plata y exige la clave del [proveedor del asistente](#proveedor-del-asistente)**); `--limit N` para un piloto; `--report-only`
  regenera el reporte sin llamar a nada.
- **Cambia algo**: en seco, no; con `--execute` escribe archivos de resultados y registro de llamadas.
  **Gasta**: solo con `--execute`.
- No cambies `--seed` ni `--sample` entre el piloto y la corrida completa. Más: [runbook-asistente-llm.md](runbook-asistente-llm.md).

### Pre-cálculo escribiendo, fuera de systemd

```
.venv/bin/python -m saimc.extraction.prefill --execute --max-cost 1
```

- **Para qué**: la corrida manual del pre-cálculo fuera de systemd: cuando el temporizador no está
  instalado, o para una corrida larga con un tope mayor que el de la unidad (en `tmux`).
- **Cuándo**: para lo de todos los días alcanza
  [`sudo systemctl start saimc-prefill.service`](#pre-cálculo-ahora-a-mano).
- **Candado**: una corrida real toma `data/prefill.lock` y lo suelta al terminar, también si muere
  (`kill -9`, corte de luz): no hay nada que limpiar y el archivo no se borra. Si otra corrida real
  está viva —esta o la del temporizador— la nueva dice «Ya hay otra corrida del pre-cálculo en
  marcha», no gasta y sale con código 0: en `systemctl status` queda como terminada bien y
  `/control` sigue mostrando la última que sí corrió. El modo en seco no toma el candado ni lo mira.
  Ya no hace falta detener el temporizador antes de una corrida larga.
- **Cambia algo**: sí, propuestas. **Gasta**: sí, hasta el tope de `--max-cost` (en USD).
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Fallback: a mano».

---

## Desarrollo local

Estos se corren en tu máquina, no en el VPS.

```
.venv/bin/pytest
```

```
.venv/bin/ruff check .
```

```
.venv/bin/mypy
```

```
.venv/bin/alembic revision -m "descripcion corta"
```

```
.venv/bin/uvicorn saimc.web.app:app --reload
```

- **Para qué**: correr la suite (tarda ~100 s), el estilo, los tipos; crear una migración vacía de
  Alembic; levantar la app con recarga automática.
- **Cuándo**: antes de cerrar un slice (los tres primeros); al cambiar el esquema (`alembic
  revision`); para probar a mano (`uvicorn`).
- **Cambia algo**: `alembic revision` crea un archivo en `alembic/versions/`; ninguno toca la base
  salvo que la suite o la app lo hagan sobre la base de desarrollo. **Gasta**: no.
- **Sin verificar**: `alembic revision --autogenerate` (el `env.py` define `target_metadata`, pero
  no se probó). `SAIMC_LLM_SMOKE=1 .venv/bin/pytest tests/test_llm_assist.py` **sí gasta** (llamada
  real a la API): no lo corras por costumbre.
- Limpiar worktrees ya mergeados: `scripts/clean_worktrees.sh` (simulacro) y
  `scripts/clean_worktrees.sh --apply` (borra worktrees y ramas locales ya integradas en master).

---

## Qué no está acá

Hay cosas que se hacen desde la web y no tienen comando. No las busques en este archivo.
Todas piden la contraseña de operador (`SAIMC_CURATION_PASSWORD`):

- **`/curation`**: curar y descartar anuncios (también `/curation/cola` y `/curation/sin-foco`).
- **`/curation/auditoria`**: la auditoría semanal de los descartes automáticos.
- **`/catalog`**: el botón de backfill de un canal o grupo de WhatsApp.
- **`/control`**: el estado del sistema (pre-cálculo, huecos de ingesta, validación, señal de Jev).
- **`/marketing`**: las tarjetas para redes, con vista previa y descarga en PNG.
- **`/docs`**: este catálogo y el resto de los documentos, con el acceso del operador.

Tampoco está WAHA (corre en Docker, ver `deploy/waha/`): sus comandos no se catalogaron.
