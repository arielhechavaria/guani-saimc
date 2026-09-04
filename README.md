# SAIMC

Sistema Abierto de Inteligencia de Mercado para Cuba.

Documento rector: `docs/spec.md`. Para consultarlo sin leerlo entero,
`docs/spec-map.md`. Instrucciones de trabajo: `CLAUDE.md`. Qué falta y
por qué, en orden de prioridad: `docs/backlog.md`.

`docs/pitch-v3.5.md` es el documento de inversión, congelado. No rige: si
contradice a `docs/spec.md`, gana `docs/spec.md`.

---

## Puesta en marcha

### Entorno Python

Ubuntu 24.04 trae Python 3.12 pero marca el intérprete del sistema como
*externally managed* (PEP 668), y sin el paquete `python3-venv` el módulo `venv`
no puede sembrar pip. Si no querés instalar nada con `sudo`, el zipapp de
`virtualenv` resuelve las dos cosas: trae sus propias wheels y crea un entorno
aislado donde PEP 668 no aplica.

```bash
curl -sSLo /tmp/virtualenv.pyz https://bootstrap.pypa.io/virtualenv.pyz
python3 /tmp/virtualenv.pyz .venv
.venv/bin/pip install -e ".[dev]"
```

La alternativa con permisos de administrador es `sudo apt install python3-venv`
y después `python3 -m venv .venv`.

### Base de datos

```bash
sudo apt install postgresql postgresql-contrib
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER saimc WITH PASSWORD 'saimc' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE saimc OWNER saimc;"

cp env.example .env      # ajustar si hace falta
.venv/bin/alembic upgrade head
```

### Variables de entorno

`env.example` trae todas con su motivo documentado inline. Las que hacen falta
según qué vayas a usar:

- `SAIMC_DATABASE_URL`, `SAIMC_INBOX_DIR`, `SAIMC_RAW_STORE_DIR` — siempre.
- `SAIMC_CURATION_PASSWORD` — sin esto, `/curation/*` responde 503. El usuario
  por defecto es `operador` (`SAIMC_CURATION_USERNAME` para cambiarlo).
  `/control`, el panel de solo lectura, no lo requiere.
- `SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY`, `SAIMC_WAHA_BASE_URL`, `SAIMC_WAHA_API_KEY`,
  `SAIMC_WAHA_SESSION` — solo si vas a recibir mensajes de WhatsApp vía WAHA
  (ver «Captura automática por WhatsApp» abajo). Sin la clave HMAC,
  `/webhooks/whatsapp` rechaza todo.
- `SAIMC_ANTHROPIC_API_KEY` — opcional. Habilita el botón de asistente de
  extracción por LLM en `/curation` (ver «Rutina de curaduría» abajo). Sin
  ella el botón no aparece y la cola funciona igual que hoy.

### Tests

```bash
.venv/bin/pytest
```

Corren sobre SQLite en memoria: no hace falta PostgreSQL para ejecutarlos.

---

## Rutina de captura

La captura y la curaduría son actos separados en el tiempo. Al capturar no se
estructura nada: alcanza con que el archivo llegue a la bandeja. Hay tres
formas de que algo llegue ahí — las tres terminan en el mismo `ingest_inbox`,
así que heredan igual el hash de contenido, la deduplicación y la redacción
de datos personales.

### Manual: bandeja de archivos

1. Guardá el anuncio como imagen o texto en `inbox/`.
2. Cuando arranques la sesión de curaduría, ingerí la bandeja:

   ```bash
   .venv/bin/python -m saimc.ingestion
   ```

Qué hace la ingesta:

- registra cada archivo en `raw_ingestions` con estado `pending`,
- mueve la evidencia a `data/raw/`, nombrada por hash de contenido,
- manda las capturas repetidas a `inbox/duplicates/` sin borrar nada,
- deja donde está lo que no reconoce, y lo reporta.

### Manual: pegado de texto

Desde `/curation` hay un campo para pegar texto de anuncios directo, sin pasar
por un archivo. Nació de medir que las capturas de imagen se sincronizaban
solas desde el teléfono pero el texto no tenía dónde ir (`docs/decisions.md`,
2026-08-15) — y un anuncio de texto rinde varios precios más que uno de
imagen.

### Automática: webhook de WhatsApp (WAHA)

Si el operador tiene una instancia de [WAHA](https://waha.devlike.pro/) propia
corriendo (típicamente en Docker en el mismo VPS), SAIMC puede recibir sus
eventos de mensaje directo en `POST /webhooks/whatsapp`, firmados con HMAC
(`SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY`). Cada mensaje de un canal seguido (ver
«Catálogo» abajo) entra a la misma bandeja que la captura manual — no es un
camino de ingesta paralelo. Es un apartamiento consciente y registrado de la
decisión cerrada 2 (nada de scraping/bots en Fase 0) y 4 (nada de Docker en
Fase 0): WAHA corre aparte, en Docker, y este router es el único punto de
contacto (`docs/decisions.md`, 2026-08-15).

Los mensajes que el webhook descarta (fuera de horario, canal no seguido, sin
producto piloto mencionado, etc.) se loguean con el motivo, no se pierden en
silencio.

El OCR **no** corre en la ingesta, sea cual sea la vía. Asiste a la cola de
curaduría, donde el sistema propone y el operador confirma (spec.md §6.1,
«Rol del OCR y la extracción asistida»).

`inbox/` y `data/` no van a git: contienen datos personales de terceros.

---

## Rutina de curaduría

Con la bandeja ya ingerida (paso anterior), levantá el servidor:

```bash
.venv/bin/uvicorn saimc.web.app:app --reload
```

`/curation/*` y `/evidence/{id}` piden HTTP Basic (usuario `operador` por
defecto, contraseña en `SAIMC_CURATION_PASSWORD`) — ver «Variables de
entorno». `/control`, el panel público de solo lectura, no.

1. Abrí `http://127.0.0.1:8000/curation`. Muestra el elemento `pending` más
   antiguo, uno por vez, con el contexto de ráfaga (otros mensajes del mismo
   canal en una ventana de ±10 min, `SAIMC_BURST_WINDOW_MINUTES`) como
   referencia de solo lectura.
2. Si es texto, el motor de reglas ya propuso producto, precio, moneda,
   cantidad y provincia: confirmá o corregí cada campo. Si es imagen, no hay
   propuesta — no hay OCR en este slice — y se completa todo a mano mirando
   la evidencia.
3. Si quedaron campos sin resolver y `SAIMC_ANTHROPIC_API_KEY` está puesta,
   aparece un botón para pedirle al asistente que proponga solo esos huecos
   (`docs/decisions.md`, 2026-09-01). Nunca corre solo — lo dispara el
   operador — y cada campo que llena queda marcado visualmente para que se
   revise antes de confirmar. Sin la clave, este paso no existe y el flujo es
   el de siempre.
4. **Curar** guarda el registro en `prices_history` y pasa al siguiente.
   **Descartar** lo marca `discarded` cuando el anuncio no trae información
   económica útil.
5. `/curation/history` lista lo ya procesado; desde ahí se puede editar o
   borrar un registro curado.

Swagger está apagado a propósito: publicaba el mapa completo de endpoints sin
autenticación, `/webhooks/whatsapp` incluido (`docs/decisions.md`,
2026-08-30). `/docs`, con la misma auth que `/curation`, es el gestor de
documentos del repositorio — spec, decisiones, este mismo README — legible
desde el navegador, no la API.

Cada confirmación o corrección es, al mismo tiempo, el dato que después mide
H0 (spec.md §6.1: «la cola de curaduría es simultáneamente la herramienta
de trabajo y el instrumento de medición»).

## Catálogo (`/catalog`)

Las listas que gobiernan el foco de la Fase 0 (`docs/decisions.md`,
2026-08-15): qué **productos piloto** puede proponer la curaduría, y qué
**canales** de WhatsApp se siguen. Toda la sección pide la misma
autenticación que `/curation`.

Cada producto declara además sus **formatos de venta** — envase, cantidad y
unidad, por ejemplo «saco, 40, lb». No es metadata: es la identidad de la
serie de precios (`docs/decisions.md`, 2026-09-04). El precio de un saco de 40
lb y el de uno de 33 lb no son el mismo dato normalizado a distinta cantidad;
son dos series distintas, porque así se compra. Un formato con observaciones
asociadas no se puede borrar sin más: se edita, o se elimina y las
observaciones quedan sin formato para recurarse a mano.

Desde acá también se dispara el backfill de histórico de un canal contra la
API de WAHA (`SAIMC_WAHA_*`), para traer mensajes anteriores al alta del
canal.

## Panel de control (`/control`)

Espejo del operador, sin autenticación. Abre con una franja de estado —
WhatsApp, última captura, cola pendiente, tasa de cambio— ordenada por
gravedad: en un día sano no exige nada, y lo roto sube solo al primer lugar
(`docs/decisions.md`, 2026-09-02). Debajo responde dos preguntas: cuánto falta
contra la meta de `spec.md` §9 (500 registros, 3–5 productos, 2 provincias) y
si lo que se está cargando sirve (matriz cruda producto × formato ×
provincia, rango minorista). No es el dashboard público de la Fase 1.

## Medición de H0

```bash
.venv/bin/python -m saimc.extraction.measure
```

Vuelve a correr el motor de reglas sobre cada anuncio de texto ya curado y lo
compara contra lo que confirmaste, métrica por métrica (spec.md §12.5).
Por debajo de 300 anuncios de texto curados el número no es la medición
oficial de H0 (§3) — el script lo avisa — pero sirve para ver la tendencia
mientras se junta volumen.

---

*Esta sección cubre Fase 0, la única con código hoy. Cuando arranque una fase
nueva, su flujo operativo se suma acá — no antes.*
