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
| [`systemctl start saimc-prefill`](#pre-cálculo-ahora-a-mano) | Pre-calcular propuestas ya | sí: propuestas | sí, tope 0,15 USD |
| [Después de un deploy](#después-de-un-deploy-la-secuencia) | Backup, pull, migrar, reiniciar | sí | no |
| [`saimc.validation.measure_n`](#measure_n) | Estabilidad de la mediana por N | no | no |
| [`saimc.extraction.measure`](#measure-h0) | Medir H0 contra la cola | `--no-record` no; sin él, anexa a `h0-runs.jsonl` | no |
| [`saimc.validation.shadow`](#informe-del-modo-sombra) | Qué resolvería solo el motor | en seco no; `--execute` anexa a `shadow-runs.jsonl` | no |
| [`saimc.validation.revalidate`](#revalidar-en-seco) | Cuántas filas cambiarían de estado | no (en seco) | no |
| [`saimc.extraction.prefill` en seco](#pre-cálculo-en-seco) | Estimar el costo del pre-cálculo | no | no |
| [`scripts/lint_drift.py --no-autofix`](#lint-drift) | Hallazgos nuevos de ruff y mypy | no | no |
| [Huecos por canal (SQL)](#huecos-por-canal) | Cuánto calla cada canal | no | no |
| [Verificaciones de B5 y K3 (SQL)](#verificaciones-de-b5-y-k3) | Comprobar hash de emisor y OCR | no | no |
| [`systemctl` / `journalctl`](#temporizadores-y-servicios) | Estado, logs, frenar temporizadores | solo si frenás | no |
| [`saimc.validation.revalidate --execute`](#revalidar-escribiendo) | Re-guardar estado de las filas | sí: `prices_history` | no |
| [`saimc.basis_backfill --apply`](#backfill-de-base-de-precio) | Completar `price_basis` viejo | sí | no |
| [`saimc.exchange_rate.backfill --apply`](#backfill-de-tasas-de-cambio) | Completar tasas pendientes | sí | no (sin verificar) |
| [`scripts/redact_existing.py --apply`](#redactar-teléfonos-ya-guardados) | Quitar teléfonos del texto guardado | sí: `original_text` | no |
| [`scripts/add_focus_aliases.py --apply`](#alias-del-foco) | Agregar alias al catálogo del foco | sí: productos | no |
| [`saimc.extraction.llm_backtest`](#backtest-del-asistente) | Backtest del asistente contra lo curado | `--execute` escribe archivos | `--execute` sí |
| [`pytest`, `ruff`, `mypy`, `alembic revision`](#desarrollo-local) | Suite, estilo, tipos, migración nueva | no (revision crea un archivo) | no |

> **Atención.** Gastan plata o reescriben datos: `prefill --execute`, `llm_backtest --execute`,
> `revalidate --execute`, `alembic upgrade head`, y todo lo que lleva `--apply`. Hacé backup antes
> de cualquiera que escriba en la base. Una corrida manual del pre-cálculo **no** va por
> `python -m …`: va por `sudo systemctl start saimc-prefill.service`, porque systemd no arranca el
> servicio dos veces a la vez y dos corridas juntas gastan el doble (ver
> [`systemd/saimc-prefill.service`](../systemd/saimc-prefill.service) y
> [el runbook de la rutina diaria](runbook-rutina-diaria.md)).

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
- **Cómo leer la salida**: mirá la cota al 95%, no el porcentaje: un 0% sobre un n chico no dice
  nada.
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md), «Una vez por semana».

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
  modelo.
- **Cuándo**: antes de una corrida grande, para saber el costo.
- **Cambia algo**: no (por defecto es `--dry-run`). **Gasta**: no.
- Más: [runbook-asistente-llm.md](runbook-asistente-llm.md), sección del pre-cálculo.

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
- Más: [runbook de la rutina diaria](runbook-rutina-diaria.md); contrato en [spec-slice-B1.md](spec-slice-B1.md).

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
- **Cambia algo**: solo con `--apply`. **Gasta**: **sin verificar**: no se leyó si consulta una
  fuente externa; mirá el código antes de correrlo en masa.

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
  (**gasta plata y exige `SAIMC_ANTHROPIC_API_KEY`**); `--limit N` para un piloto; `--report-only`
  regenera el reporte sin llamar a nada.
- **Cambia algo**: en seco, no; con `--execute` escribe archivos de resultados y registro de llamadas.
  **Gasta**: solo con `--execute`.
- No cambies `--seed` ni `--sample` entre el piloto y la corrida completa. Más: [runbook-asistente-llm.md](runbook-asistente-llm.md).

### Pre-cálculo escribiendo, fuera de systemd

```
.venv/bin/python -m saimc.extraction.prefill --execute --max-cost 1
```

- **Para qué**: la corrida manual del pre-cálculo fuera de systemd. Es el **fallback**, para cuando
  el temporizador no está instalado.
- **Cuándo**: casi nunca. Con el temporizador activo, usá
  [`sudo systemctl start saimc-prefill.service`](#pre-cálculo-ahora-a-mano): esta forma no comparte
  su candado y dos corridas juntas gastarían el doble.
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

Hay cosas que se hacen desde la web y no tienen comando. No las busques en este archivo:

- **`/curation`**: curar y descartar anuncios (también `/curation/cola` y `/curation/sin-foco`).
- **`/curation/auditoria`**: la auditoría semanal de los descartes automáticos.
- **`/catalog`**: el botón de backfill de un canal o grupo de WhatsApp.
- **`/control`**: el estado del sistema (pre-cálculo, huecos de ingesta, validación).
- **`/docs`**: este catálogo y el resto de los documentos, con el acceso del operador.

Tampoco está WAHA (corre en Docker, ver `deploy/waha/`): sus comandos no se catalogaron.
