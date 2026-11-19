"""НТ-4: кеш истории запусков — один запрос к Airflow на ключ при одновременных обращениях."""
import os
import threading
import time

os.environ.setdefault("API_SECRET_KEY", "test")
os.environ.setdefault("DB_CONN", "postgresql://u:p@localhost/db")

import routers.upload as up  # noqa: E402


class _Resp:
    status_code = 200

    def json(self):
        return {"dag_runs": [{"dag_run_id": "r1"}]}


def test_single_flight_under_concurrency(monkeypatch):
    up._runs_cache.clear()
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        time.sleep(0.2)
        return _Resp()

    monkeypatch.setattr(up.httpx, "get", fake_get)
    threads = [threading.Thread(target=up.get_recent_dag_runs, args=("d", 5)) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert up.get_recent_dag_runs("d", 5) == [{"dag_run_id": "r1"}]


def test_stale_value_kept_when_airflow_fails(monkeypatch):
    up._runs_cache.clear()
    up._runs_cache[("d", 5)] = (0.0, [{"dag_run_id": "old"}])
    calls = []

    def failing_get(url, **kw):
        calls.append(url)
        raise up.httpx.ConnectError("down")

    monkeypatch.setattr(up.httpx, "get", failing_get)
    assert up.get_recent_dag_runs("d", 5) == [{"dag_run_id": "old"}]
    assert up.get_recent_dag_runs("d", 5) == [{"dag_run_id": "old"}]
    assert len(calls) == 1


def test_no_value_and_airflow_fails_returns_empty(monkeypatch):
    up._runs_cache.clear()
    monkeypatch.setattr(up.httpx, "get", lambda url, **kw: (_ for _ in ()).throw(up.httpx.ConnectError("x")))
    assert up.get_recent_dag_runs("x", 5) == []
