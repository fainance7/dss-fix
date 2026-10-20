"""
Журнал ошибок модуля для страницы мониторинга (ТЗ ФFE.7).

Записи уровня ERROR и выше складываются в список Redis «errlog:<модуль>» (последние
500), откуда их читает backend для раздела «Мониторинг». Запись не должна ронять
приложение: при недоступном Redis она молча пропускается.
"""
import json
import logging
import traceback
from datetime import datetime, timezone

import redis

MAX_RECORDS = 500


class RedisErrorHandler(logging.Handler):
    def __init__(self, redis_url: str, module: str):
        super().__init__(level=logging.ERROR)
        self.key = f"errlog:{module}"
        self._redis = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:2000],
            }
            if record.exc_info:
                entry["trace"] = "".join(traceback.format_exception(*record.exc_info))[-4000:]
            pipe = self._redis.pipeline()
            pipe.lpush(self.key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(self.key, 0, MAX_RECORDS - 1)
            pipe.execute()
        except Exception:  # журнал ошибок не должен порождать новые ошибки
            pass


def install(redis_url: str, module: str) -> None:
    logging.getLogger().addHandler(RedisErrorHandler(redis_url, module))
