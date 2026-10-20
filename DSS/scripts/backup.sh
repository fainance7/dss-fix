#!/bin/sh
# DSS — Backup script (задача 2.10)
# Запускается ежедневно в 03:00 через cron / docker compose --profile backup run backup
# Хранение: локально /backups + опционально S3 (30 дней retention)

set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups"
FILENAME="dss_backup_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

echo "[backup] Starting DSS database backup at $(date)"

# Create backup dir
mkdir -p "$BACKUP_DIR"

# Dump database
pg_dump \
  -h "${POSTGRES_HOST:-postgres}" \
  -U "${POSTGRES_USER:-dss_user}" \
  -d "${POSTGRES_DB:-dss_db}" \
  --format=plain \
  --no-owner \
  --no-acl \
  | gzip > "$FILEPATH"

echo "[backup] Dump completed: $FILEPATH ($(du -sh "$FILEPATH" | cut -f1))"

# ── Upload to S3 (optional) ────────────────────────────────────
if [ -n "$S3_BUCKET" ] && [ -n "$AWS_ACCESS_KEY_ID" ]; then
  echo "[backup] Uploading to S3: s3://${S3_BUCKET}/dss-backups/${FILENAME}"
  # Use aws cli if available
  if command -v aws >/dev/null 2>&1; then
    AWS_ENDPOINT_ARGS=""
    if [ -n "$S3_ENDPOINT" ]; then
      AWS_ENDPOINT_ARGS="--endpoint-url ${S3_ENDPOINT}"
    fi
    aws s3 cp "$FILEPATH" "s3://${S3_BUCKET}/dss-backups/${FILENAME}" $AWS_ENDPOINT_ARGS
    echo "[backup] S3 upload done."
  else
    echo "[backup] WARNING: aws cli not found, skipping S3 upload."
  fi
fi

# ── Rotate local backups (keep 30 days) ──────────────────────
echo "[backup] Cleaning backups older than 30 days..."
find "$BACKUP_DIR" -name "dss_backup_*.sql.gz" -mtime +30 -delete
echo "[backup] Local files after cleanup:"
ls -lh "$BACKUP_DIR"

echo "[backup] Backup completed successfully at $(date)"
