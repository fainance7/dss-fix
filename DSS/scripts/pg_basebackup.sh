#!/bin/sh
# DSS — PITR base backup (G14)
# Снимает физический базовый бэкап (pg_basebackup) — точку отсчёта для
# восстановления на момент времени (point-in-time recovery) вместе с
# непрерывно архивируемым WAL (postgres archive_command → /wal_archive).
#
# Запускается ВНУТРИ контейнера dss-postgres (там разрешён `local replication
# trust`, а сетевая репликация pg_hba по умолчанию закрыта). Ежедневно по cron:
#   docker exec dss-postgres sh /pg_basebackup.sh
#
# Env (берутся из окружения dss-postgres): POSTGRES_USER; ретеншн — см. ниже.
# Инвариант ретеншна: WAL хранится ДОЛЬШЕ базовых бэкапов, иначе самый старый
# базовый бэкап останется без нужного ему WAL и PITR по нему сломается.
set -e

BASE_DIR="/basebackups"
WAL_DIR="/wal_archive"
BASE_RETENTION_DAYS="${PITR_BASE_RETENTION_DAYS:-7}"
WAL_RETENTION_DAYS="${PITR_WAL_RETENTION_DAYS:-8}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DEST="${BASE_DIR}/base_${TIMESTAMP}"

echo "[pitr] Базовый бэкап старт: $(date -u)"
mkdir -p "$DEST"

# Локальное подключение через unix-сокет (без -h) → pg_hba `local replication trust`.
# -Ft -z: tar+gzip; -Xs: стримить WAL в бэкап (самодостаточная точка);
# -c fast: немедленный checkpoint; -P: прогресс.
pg_basebackup \
  -U "${POSTGRES_USER:-dss_user}" \
  -D "$DEST" \
  -Ft -z -Xs -c fast -P

echo "[pitr] Базовый бэкап готов: $DEST ($(du -sh "$DEST" | cut -f1))"

# ── Ротация базовых бэкапов ──────────────────────────────────
echo "[pitr] Удаляю базовые бэкапы старше ${BASE_RETENTION_DAYS} дн..."
find "$BASE_DIR" -maxdepth 1 -type d -name 'base_*' -mtime "+${BASE_RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true

# ── Ротация WAL-архива (строго дольше базовых бэкапов) ───────
if [ -d "$WAL_DIR" ]; then
  echo "[pitr] Удаляю архив WAL старше ${WAL_RETENTION_DAYS} дн..."
  find "$WAL_DIR" -type f -mtime "+${WAL_RETENTION_DAYS}" -delete 2>/dev/null || true
  echo "[pitr] WAL-файлов в архиве: $(find "$WAL_DIR" -type f | wc -l), объём: $(du -sh "$WAL_DIR" 2>/dev/null | cut -f1)"
fi

# ── Опциональная выгрузка базового бэкапа в S3 ───────────────
if [ -n "$S3_BUCKET" ] && [ -n "$AWS_ACCESS_KEY_ID" ] && command -v aws >/dev/null 2>&1; then
  AWS_ENDPOINT_ARGS=""
  [ -n "$S3_ENDPOINT" ] && AWS_ENDPOINT_ARGS="--endpoint-url ${S3_ENDPOINT}"
  echo "[pitr] Выгрузка в S3: s3://${S3_BUCKET}/dss-basebackups/base_${TIMESTAMP}/"
  aws s3 cp "$DEST" "s3://${S3_BUCKET}/dss-basebackups/base_${TIMESTAMP}/" --recursive $AWS_ENDPOINT_ARGS || echo "[pitr] WARNING: S3 upload failed"
fi

echo "[pitr] Готово: $(date -u)"
