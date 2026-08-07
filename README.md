# SAIMC

Sistema Abierto de Inteligencia de Mercado para Cuba.

Documento rector: `docs/whitepaper.md`. Para consultarlo sin leerlo entero,
`docs/whitepaper-map.md`. Instrucciones de trabajo: `CLAUDE.md`.

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
sistema propone y el operador confirma (whitepaper §6.1, líneas 452-456).

`inbox/` y `data/` no van a git: contienen datos personales de terceros.
