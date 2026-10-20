#!/bin/sh
# Регистрирует cron-задачу для ежедневного бэкапа в 03:00 (UTC+5 = 22:00 UTC)
# Запускать один раз на хост-машине: sh scripts/schedule-backup.sh

CRON_JOB="0 22 * * * cd $(pwd) && docker compose --profile backup run --rm backup >> /var/log/dss-backup.log 2>&1"

# Add to crontab if not already present
(crontab -l 2>/dev/null | grep -v "dss-backup"; echo "$CRON_JOB") | crontab -
echo "Cron job registered: $CRON_JOB"
crontab -l | grep dss
