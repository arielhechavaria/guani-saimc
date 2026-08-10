#!/usr/bin/env bash
# Backup diario de PostgreSQL. Lo corre systemd (ver
# systemd/saimc-backup.service + .timer, instalados a mano en el VPS —
# no hay CI/CD que los despliegue, decisión 4 de CLAUDE.md).
#
# Retiene los últimos 7 días. Local únicamente, sin copia fuera del VPS: si
# el disco del VPS se pierde entero, se pierde el backup también. Aceptado
# como límite conocido por ahora — ver docs/decisions.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$REPO_DIR/backups"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

DATABASE_URL="$(grep -E '^SAIMC_DATABASE_URL=' "$REPO_DIR/.env" | cut -d= -f2-)"
# pg_dump no reconoce el sufijo +psycopg del driver SQLAlchemy.
PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/saimc_${TIMESTAMP}.sql.gz"

pg_dump "$PG_URL" | gzip > "$OUT_FILE"

find "$BACKUP_DIR" -name 'saimc_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
