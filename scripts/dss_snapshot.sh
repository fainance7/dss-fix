#!/bin/bash
# dss_snapshot.sh — снимок значений DSS для ежемесячной задачи ETL (dag_monthly_update):
# по нему задача понимает, какие значения уже есть в DSS. Только чтение базы DSS.
#
# Запуск по cron на сервере от пользователя из группы docker, каждую ночь:
#   0 1 * * * /opt/dssetl/scripts/dss_snapshot.sh >> /opt/dssetl/artifacts/dss_snapshot/snapshot.log 2>&1
#
# Файл заменяется только целиком и только удачной выгрузкой: при ошибке или пустом
# результате остаётся прошлый снимок (а задача не отправит партию по старому снимку).
set -euo pipefail

OUT=${DSS_SNAPSHOT_OUT:-/opt/dssetl/artifacts/dss_snapshot/dss_values.csv.gz}
TMP="$OUT.tmp"
MIN_ROWS=1000
trap 'rm -f "$TMP"' EXIT

SQL="SELECT to_jsonb(d)->>'code' AS dir, i.name, u.name AS unit, i.is_active,
       to_jsonb(r)->>'kato_code' AS kato, v.period_type, v.period_start, v.value,
       v.validation_status AS st, ds.name AS source
FROM indicator_values v
JOIN indicators i ON i.id = v.indicator_id
JOIN directions d ON d.id = i.direction_id
JOIN units u ON u.id = i.unit_id
JOIN regions r ON r.id = v.region_id
LEFT JOIN data_sources ds ON ds.id = v.source_id"
SQL=$(echo "$SQL" | tr '\n' ' ')

docker exec dss-postgres psql -X -q -v ON_ERROR_STOP=1 -U dss_user -d dss_db \
  -c "\copy ($SQL) TO STDOUT WITH (FORMAT csv, HEADER true)" | gzip -9 > "$TMP"

ROWS=$(zcat "$TMP" | wc -l)
if [ "$ROWS" -lt "$MIN_ROWS" ]; then
  echo "$(date '+%F %T') ОШИБКА: в выгрузке $ROWS строк, снимок не заменён"
  exit 1
fi
chmod 644 "$TMP"
mv -f "$TMP" "$OUT"
echo "$(date '+%F %T') снимок DSS обновлён: $((ROWS - 1)) значений"
