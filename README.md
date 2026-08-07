# SAIMC

Sistema Abierto de Inteligencia de Mercado para Cuba.

Documento rector: `docs/whitepaper.md`. Para consultarlo sin leerlo entero,
`docs/whitepaper-map.md`. Instrucciones de trabajo: `CLAUDE.md`. Qué falta y
por qué, en orden de prioridad: `docs/backlog.md`.

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

### Tests

```bash
.venv/bin/pytest
```

Corren sobre SQLite en memoria: no hace falta PostgreSQL para ejecutarlos.

---

## Rutina de captura

La captura y la curaduría son actos separados en el tiempo. Al capturar no se
estructura nada: alcanza con que el archivo llegue a la bandeja.

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

El OCR **no** corre en la ingesta. Asiste a la cola de curaduría, donde el
sistema propone y el operador confirma (whitepaper §6.1, «Rol del OCR y la
extracción asistida»).

`inbox/` y `data/` no van a git: contienen datos personales de terceros.

---

## Rutina de curaduría

Con la bandeja ya ingerida (paso anterior), levantá el servidor:

```bash
.venv/bin/uvicorn saimc.web.app:app --reload
```

1. Abrí `http://127.0.0.1:8000/curation`. Muestra el elemento `pending` más
   antiguo, uno por vez.
2. Si es texto, el motor de reglas ya propuso producto, precio, moneda,
   cantidad y provincia: confirmá o corregí cada campo. Si es imagen, no hay
   propuesta — no hay OCR en este slice — y se completa todo a mano mirando
   la evidencia.
3. **Curar** guarda el registro en `prices_history` y pasa al siguiente.
   **Descartar** lo marca `discarded` cuando el anuncio no trae información
   económica útil.
4. La API completa (Swagger) queda en `http://127.0.0.1:8000/docs`.

Cada confirmación o corrección es, al mismo tiempo, el dato que después mide
H0 (whitepaper §6.1: «la cola de curaduría es simultáneamente la herramienta
de trabajo y el instrumento de medición»).

## Medición de H0

```bash
.venv/bin/python -m saimc.extraction.measure
```

Vuelve a correr el motor de reglas sobre cada anuncio de texto ya curado y lo
compara contra lo que confirmaste, métrica por métrica (whitepaper §12.5).
Por debajo de 300 anuncios de texto curados el número no es la medición
oficial de H0 (§3) — el script lo avisa — pero sirve para ver la tendencia
mientras se junta volumen.

---

*Esta sección cubre Fase 0, la única con código hoy. Cuando arranque una fase
nueva, su flujo operativo se suma acá — no antes.*
