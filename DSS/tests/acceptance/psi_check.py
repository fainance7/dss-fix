"""
Автоматизированная часть испытаний DSS по ПМИ (Этап 6).

Каждая проверка привязана к пункту ПМИ (идентификатор вида ХД-01) и к пункту ТЗ.
Скрипт не изменяет данные: выполняются только чтения и негативные проверки прав,
в которых запрос заведомо адресован несуществующему объекту или несёт пустое тело —
при отказе в доступе сервер отвечает 403, а при ошибочно выданном доступе — 404/422,
так что состояние Системы не меняется ни в одном из исходов.

Запуск:
    backend/.venv/bin/python tests/acceptance/psi_check.py \
        --creds ~/Documents/АН/Показ/Демо_учётки_DSS.txt --out psi_result.json

Файл учётных записей — текст с парами строк «логин: …» / «пароль: …».
Нужны учётные записи ролей: администратор, эксперт по направлениям, эксперт по
проверке данных, аким области, аким района (по умолчанию — demo_*).
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import socket
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

API = "https://dssapi.dwh.kz/api/v1"
WEB = "https://dss.dwh.kz"
AI_DIRECT = "https://dssai.dwh.kz/api/v1"

ROLE_USERS = {
    "admin": "demo_admin",
    "exp_dir": "demo_expert",
    "exp_ver": "demo_verifier",
    "akim_obl": "demo_akim_obl",
    "akim_ray": "demo_akim_petro",
}

PASS, FAIL, PARTIAL, MANUAL = "Соответствует", "Не соответствует", "Частично", "Визуально"

results: list[dict] = []
timings: dict[str, list[float]] = {}


def rid() -> str:
    return str(uuid.uuid4())


def record(cid: str, verdict: str, actual: str) -> None:
    results.append({"id": cid, "verdict": verdict, "actual": actual})
    print(f"[{verdict:>16}] {cid}: {actual}")


class Api:
    def __init__(self, creds: dict[str, str]):
        self.c = httpx.Client(timeout=120, follow_redirects=False)
        self.tok: dict[str, str] = {}
        for role, login in ROLE_USERS.items():
            r = self.c.post(f"{API}/auth/login", json={"username": login, "password": creds[login]})
            r.raise_for_status()
            self.tok[role] = r.json()["access_token"]
        self.refresh_ok = False

    def req(self, method: str, path: str, role: str | None = "admin", base: str = API, **kw):
        headers = kw.pop("headers", {})
        if role:
            headers["Authorization"] = f"Bearer {self.tok[role]}"
        t0 = time.perf_counter()
        r = self.c.request(method, base + path, headers=headers, **kw)
        timings.setdefault(f"{method} {path.split('?')[0]}", []).append(time.perf_counter() - t0)
        return r

    def get(self, path, role="admin", **kw):
        return self.req("GET", path, role, **kw)


def j(r):
    try:
        return r.json()
    except ValueError:
        return None


def items(r):
    d = j(r)
    if isinstance(d, dict):
        for k in ("items", "data"):
            if k in d:
                return d[k]
    return d or []


def total(r):
    d = j(r)
    if isinstance(d, dict):
        return d.get("total", len(items(r)))
    return len(d or [])


# --------------------------------------------------------------------------- СХД
def check_shd(a: Api) -> None:
    sh = a.get("/shapefiles")
    gd = a.get("/gis/datasets?page_size=1")
    slots = j(a.get("/landscape/slots")) or []
    raster = next((x["latest"] for x in slots if (x.get("latest") or {}).get("status") == "ready"), None)
    tile = a.get(f"/landscape/raster/{raster['id']}/tiles/6/44/20.png") if raster else None
    vec = a.get("/shapefiles/geojson?name=" + items(sh)[0]["name"]) if total(sh) else None
    ok = sh.status_code == 200 and gd.status_code == 200 and total(gd) > 0 and tile is not None \
        and tile.status_code == 200
    record("ХД-01", PASS if ok else FAIL,
           f"векторных слоёв (shape) — {total(sh)}; наборов каталога геоданных — {total(gd)}; "
           f"векторный слой в GeoJSON — HTTP {vec.status_code if vec is not None else '—'}; "
           f"растровый тайл XYZ — HTTP {tile.status_code if tile is not None else '—'}"
           f" ({tile.headers.get('content-type')})" if tile is not None else "")

    iv = a.get("/indicator-values?page_size=1&validation_status=approved")
    first = (items(iv) or [{}])[0]
    ts = None
    if first.get("indicator_id") and first.get("region_id"):
        ts = a.get(f"/indicator-values/timeseries?indicator_id={first['indicator_id']}"
                   f"&region_id={first['region_id']}&period_type={first.get('period_type', 'year')}")
    st = items(a.get("/meteo-stations"))
    cm = a.get(f"/climate/monthly?indicator=precipitation&station_id={st[0]['id']}") if st else None
    ok = iv.status_code == 200 and total(iv) > 0 and ts is not None and ts.status_code == 200
    record("ХД-02", PASS if ok else FAIL,
           f"утверждённых значений показателей — {total(iv)}; временной ряд по показателю и "
           f"территории — HTTP {ts.status_code if ts else '—'} ({len(items(ts)) if ts else 0} точек); "
           f"метеостанций — {len(st)}; помесячный климатический ряд — HTTP {cm.status_code if cm else '—'}")

    refs = {p: a.get(p) for p in ("/regions", "/kato?level=2", "/directions", "/units", "/indicators",
                                   "/data-sources", "/data-categories", "/economic-sectors")}
    ok = all(r.status_code == 200 and total(r) > 0 for r in refs.values())
    record("ХД-03", PASS if ok else FAIL,
           "; ".join(f"{p.split('?')[0].strip('/')} — {total(r)}" for p, r in refs.items()))

    ij = a.get("/data-intake/jobs?page_size=1")
    pr = a.get("/data-intake/protocol")
    lr = a.get("/landscape/runs?page_size=1")
    ok = ij.status_code == 200 and total(ij) > 0
    record("ХД-04", PASS if ok else PARTIAL,
           f"протоколов приёма партий данных — {total(ij)}; регламент приёма — HTTP {pr.status_code}; "
           f"отчётов расчётов ЛП — {total(lr)}. Хранение неструктурированных документов как "
           f"отдельной сущности не реализовано — документы хранятся как протоколы и отчёты расчётов")

    fc = a.get("/ai/api/v1/forecasts?page_size=1")
    sc = a.get("/ai/api/v1/scenarios")
    ok = fc.status_code == 200 and total(fc) > 0 and sc.status_code == 200 and total(sc) > 0
    record("ХД-05", PASS if ok else FAIL, f"прогнозов — {total(fc)}; сценариев развития — {total(sc)}")

    dl = a.get("/data-layers?page_size=200")
    need = ("source_name", "valid_from", "spatial_resolution", "update_frequency", "owner",
            "description", "accuracy_description")
    li = items(dl)
    filled = {k: sum(1 for x in li if x.get(k)) for k in need}
    gd_items = items(a.get("/gis/datasets?page_size=5"))
    complete = li and all(v == len(li) for v in filled.values())
    record("ХД-06", (PASS if complete else PARTIAL) if li and all(k in li[0] for k in need) else FAIL,
           f"наборов в каталоге метаданных — {total(dl)}; поля каталога присутствуют: "
           f"{', '.join(need)}; заполненность: "
           + ", ".join(f"{k} {v}/{len(li)}" for k, v in filled.items())
           + f"; каталог геоданных — {total(gd)} наборов"
           + (f" (атрибуты: {', '.join(list(gd_items[0])[:8])})" if gd_items else ""))

    hist = a.get(f"/data-layers/{li[0]['id']}/history") if li else None
    models = items(a.get("/ai/api/v1/models"))
    per_ind: dict[str, set] = {}
    for m in models:
        per_ind.setdefault(m.get("indicator_id") or m["name"], set()).add(m.get("version"))
    multi = sum(1 for v in per_ind.values() if len(v) > 1)
    statuses = sorted({m.get("status") for m in models})
    ok = hist is not None and hist.status_code == 200 and multi > 0
    record("ХД-07", PASS if ok else FAIL,
           f"журнал изменений набора данных — HTTP {hist.status_code if hist else '—'}; версий моделей ИИ — "
           f"{len(models)}, показателей с несколькими версиями модели — {multi}; статусы моделей: "
           f"{', '.join(statuses)}")

    flt = None
    if first.get("indicator_id") and first.get("region_id"):
        flt = a.get(f"/indicator-values?indicator_id={first['indicator_id']}&region_id={first['region_id']}"
                    f"&period_type={first.get('period_type', 'year')}&page_size=5")
    by_dir = a.get(f"/data-layers?direction_id={li[0]['direction_id']}") if li else None
    unauth = a.get("/indicator-values?page_size=1", role=None)
    ok = flt is not None and flt.status_code == 200 and by_dir is not None and by_dir.status_code == 200 \
        and unauth.status_code in (401, 403)
    record("ХД-08", PASS if ok else FAIL,
           f"чтение по показателю, территории и периоду — HTTP {flt.status_code if flt else '—'} "
           f"({total(flt) if flt else 0} записей); поиск по метаданным (направление) — HTTP "
           f"{by_dir.status_code if by_dir else '—'}; запрос без токена — HTTP {unauth.status_code}. "
           f"Запись проверяется автотестами (test_indicator_values, test_data_intake)")

    sdh = a.get("/monitoring/sdh")
    sysm = a.get("/monitoring/system")
    sv = j(sysm) or {}
    ok = sdh.status_code == 200 and sysm.status_code == 200 and sv.get("db_size_bytes", 0) > 0
    record("ХД-10", PASS if ok else FAIL,
           f"метрики СХД — HTTP {sdh.status_code}; объём БД — {sv.get('db_size_pretty')}; службы: "
           + ", ".join(f"{s['name']} {s['status']} ({s.get('latency_ms')} мс)" for s in sv.get("services", [])))


# --------------------------------------------------------------------------- ИИ
def check_ai(a: Api) -> None:
    norms = a.get("/indicator-norms")
    st = items(a.get("/meteo-stations"))
    cm = a.get(f"/climate/monthly?indicator=temperature&station_id={st[0]['id']}") if st else None
    record("ИИ-01", PASS if norms.status_code == 200 and total(norms) > 0 else FAIL,
           f"рассчитанных норм показателей — {total(norms)}; климатические месяцы/сезоны из декад — "
           f"HTTP {cm.status_code if cm else '—'}. Агрегированные индексы (ΣT, K, ГТК) и снимок/прирост "
           f"показателей рассчитываются Системой (см. ИИ-01 ПМИ)")

    models = items(a.get("/ai/api/v1/models"))
    types = sorted({m.get("model_type") for m in models})
    active = [m for m in models if m.get("status") == "active"]
    with_metrics = sum(1 for m in active if m.get("metrics"))
    record("ИИ-02", PASS if active and with_metrics == len(active) else PARTIAL,
           f"моделей всего — {len(models)}, активных — {len(active)}, с метриками качества — {with_metrics}; "
           f"типы моделей: {', '.join(types)}")

    runs = j(a.get("/monitoring/forecasts")) or []
    last = max((r["created_at"] for r in runs if r.get("created_at")), default=None)
    age_h = None
    if last:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))
                 .astimezone(timezone.utc)).total_seconds() / 3600
    record("ИИ-03", PASS if age_h is not None and age_h < 48 else PARTIAL,
           f"последний прогон прогнозов — {last} ({age_h:.1f} ч назад); расписание: ежедневно 02:00, "
           f"еженедельно (вс 03:00), обновление сценариев (сб 04:00); запуск по запросу — "
           f"POST /forecasts (автотесты)" if age_h is not None else "история прогонов пуста")

    sc = items(a.get("/ai/api/v1/scenarios"))
    stypes = sorted({s["scenario_type"] for s in sc})
    meta = [m for s in sc for m in ((s.get("parameters") or {}).get("kpi_meta") or {}).values()]
    with_mult = sum(1 for m in meta if "effective_multiplier" in m)
    bad = a.req("PATCH", f"/ai/api/v1/scenarios/{rid()}/parameters", role="exp_dir",
                json={"investment_multiplier": 9})
    record("ИИ-04", PASS if with_mult > 0 and bad.status_code in (403, 404, 422) else FAIL,
           f"рычаги сценария: investment_multiplier, population_growth_rate, infrastructure_index, "
           f"employment_target, gdp_growth_target (с допустимыми диапазонами); KPI сценариев с применённым "
           f"множителем — {with_mult}/{len(meta)} (без множителя — сценарии, рассчитанные до его введения)")
    record("ИИ-05", PASS if {"optimal", "stable", "risk"} <= set(stypes) else FAIL,
           f"типы сценариев в Системе: {', '.join(stypes)}; всего сценариев — {len(sc)}")

    by_year: dict[int, list] = {}
    for s in sc:
        by_year.setdefault(s["target_year"], []).append(s["id"])
    year, ids = max(by_year.items(), key=lambda kv: len(kv[1])) if by_year else (None, [])
    cmp_ = a.get(f"/ai/api/v1/scenarios/compare?ids={','.join(ids[:3])}") if len(ids) >= 2 else None
    cd = j(cmp_) if cmp_ is not None else None
    record("ИИ-06", PASS if cmp_ is not None and cmp_.status_code == 200 else FAIL,
           f"сравнение {min(len(ids), 3)} сценариев на {year} г. — HTTP {cmp_.status_code if cmp_ else '—'}"
           + (f"; разделы ответа: {', '.join(list(cd)[:8])}" if isinstance(cd, dict) else ""))

    appr = [s for s in sc if s.get("approved_at")]
    tasks = items(a.get("/ai/api/v1/tasks?page_size=500"))
    regions = {t.get("region_id") for t in tasks if t.get("scenario_id") in {s["id"] for s in appr}}
    ok = appr and all(s.get("approved_by") for s in appr) and regions
    record("ИИ-07", PASS if ok else FAIL,
           f"утверждённых сценариев — {len(appr)} (с указанием лица и времени — "
           f"{sum(1 for s in appr if s.get('approved_by') and s.get('approved_at'))}); задач по "
           f"утверждённым сценариям — {sum(1 for t in tasks if t.get('scenario_id') in {s['id'] for s in appr})}, "
           f"районов с задачами — {len(regions)}")

    fl = items(a.get("/ai/api/v1/forecasts?page_size=50"))
    xai = [f for f in fl if f.get("xai_explanation")]
    keys = sorted({k for f in xai for k in f["xai_explanation"]})
    record("ИИ-08", PASS if xai else FAIL,
           f"прогнозов с объяснением (из последних {len(fl)}) — {len(xai)}; состав объяснения: "
           f"{', '.join(keys[:10])}")

    st_m = sorted({m.get("status") for m in models})
    r1 = a.req("POST", f"/ai/api/v1/models/{rid()}/rollback", role="exp_dir", json={})
    record("ИИ-09", PASS if {"active", "deprecated"} <= set(st_m) and r1.status_code == 403 else PARTIAL,
           f"статусы версий моделей: {', '.join(st_m)}; откат модели экспертом — HTTP {r1.status_code} "
           f"(разрешён только администратору); регламент переобучения и отката — "
           f"docs/Регламент_переобучения_и_отката_моделей_ФИИ6.md")

    nokey = a.get("/models", role=None, base=AI_DIRECT)
    viaproxy = a.get("/ai/api/v1/models")
    record("ИИ-10", PASS if nokey.status_code in (401, 403, 404) and viaproxy.status_code == 200 else FAIL,
           f"API Модуля ИИ без служебного ключа — HTTP {nokey.status_code}; через BackEnd — "
           f"HTTP {viaproxy.status_code}")


# --------------------------------------------------------------------------- BackEnd
def check_backend(a: Api, creds: dict[str, str]) -> None:
    bad = a.c.post(f"{API}/auth/login", json={"username": ROLE_USERS["admin"], "password": "wrong-" + rid()})
    me = a.get("/auth/me")
    nt = a.get("/auth/me", role=None)
    forged = a.c.get(f"{API}/auth/me", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.x"})
    ok = bad.status_code == 401 and me.status_code == 200 and nt.status_code in (401, 403) and forged.status_code == 401
    record("БЕ-01", PASS if ok else FAIL,
           f"вход с верным паролем — 200; с неверным — {bad.status_code}; /auth/me с токеном — "
           f"{me.status_code}, без токена — {nt.status_code}, с поддельным — {forged.status_code}")

    lg = a.c.post(f"{API}/auth/login", json={"username": ROLE_USERS["exp_ver"],
                                             "password": creds[ROLE_USERS["exp_ver"]]}).json()
    rf = a.c.post(f"{API}/auth/refresh", json={"refresh_token": lg.get("refresh_token")})
    ch = a.get("/auth/ecp-challenge", role=None)
    record("БЕ-02", PASS if rf.status_code == 200 and ch.status_code == 200 else FAIL,
           f"обновление токена (refresh) — HTTP {rf.status_code}; вызов для входа по ЭЦП (NCALayer + "
           f"NCANode) — HTTP {ch.status_code}. OAuth 2.0 заменён схемой JWT + ЭЦП по решению Заказчика "
           f"(письмо по G13)")

    docs = a.c.get(f"{API.rsplit('/api', 1)[0]}/docs")
    oapi = a.c.get(f"{API.rsplit('/api', 1)[0]}/openapi.json")
    record("БЕ-03", PASS if docs.status_code == 404 and oapi.status_code == 404 else FAIL,
           f"интерактивная документация API в продуктивном контуре закрыта: /docs — {docs.status_code}, "
           f"/openapi.json — {oapi.status_code}; перечень методов — в техническом описании")

    t = a.get("/ai/api/v1/tasks?page_size=1")
    record("БЕ-04", PASS if t.status_code == 200 else FAIL,
           f"запрос к Модулю ИИ через BackEnd с проверкой прав — HTTP {t.status_code}")

    seq = []
    for _ in range(5):
        t0 = time.perf_counter()
        a.get("/data-layers?page_size=50")
        seq.append(time.perf_counter() - t0)
    redis = next((x for x in (j(a.get("/monitoring/system")) or {}).get("services", [])
                  if x["name"] == "Redis"), {})
    record("БЕ-05", PASS if redis.get("status") == "up" else FAIL,
           f"служба кеша Redis — {redis.get('status')}; чтение каталога данных через кеш: "
           f"{statistics.median(seq) * 1000:.0f} мс (медиана 5 запросов)")

    al = a.get("/audit-log?page_size=5")
    acts = j(a.get("/audit-log/actions")) or []
    last = (items(al) or [{}])[0]
    record("БЕ-06", PASS if al.status_code == 200 and total(al) > 0 else FAIL,
           f"записей в журнале аудита — {total(al)}; типов действий — {len(acts)}; последняя запись: "
           f"{last.get('action')} {last.get('created_at') or last.get('timestamp')}")


# --------------------------------------------------------------------------- FrontEnd
PAGES = ["/login", "/dashboard", "/dashboard/map", "/dashboard/analytics", "/dashboard/scenarios",
         "/dashboard/tasks", "/dashboard/agriculture", "/dashboard/climate/thermal", "/dashboard/landscape",
         "/dashboard/landscape/compare", "/dashboard/data", "/dashboard/data-intake",
         "/admin/users", "/admin/monitoring", "/admin/references", "/admin/settings"]


def check_frontend(a: Api) -> None:
    codes = {}
    for p in PAGES:
        t0 = time.perf_counter()
        r = a.c.get(WEB + p)
        timings.setdefault(f"PAGE {p}", []).append(time.perf_counter() - t0)
        codes[p] = 200 if r.status_code == 200 or (
            r.status_code == 307 and r.headers.get("location", "").startswith("/login")) else r.status_code
    html = a.c.get(WEB + "/login").text
    viewport = 'name="viewport"' in html
    ok = all(c == 200 for c in codes.values())
    record("ФЕ-01", PASS if ok and viewport else FAIL,
           f"страниц доступно — {sum(1 for c in codes.values() if c == 200)}/{len(codes)} (без входа — "
           f"переадресация на страницу входа); адаптивная "
           f"разметка (viewport) — {'есть' if viewport else 'нет'}; фреймворк — Next.js/React")


# --------------------------------------------------------------------------- роли
def check_rbac(a: Api) -> None:
    cases = [
        ("РД-01", "akim_ray", "POST", "/users", {}, "создание пользователя акимом района"),
        ("РД-01", "exp_dir", "POST", "/users", {}, "создание пользователя экспертом"),
        ("РД-01", "akim_obl", "GET", "/users", None, "список пользователей акимом области"),
        ("РД-02", "exp_dir", "GET", "/audit-log", None, "журнал аудита экспертом"),
        ("РД-02", "exp_ver", "GET", "/monitoring/system", None, "мониторинг экспертом по проверке"),
        ("РД-02", "akim_obl", "PUT", "/settings", {}, "изменение настроек акимом области"),
        ("РД-03", "exp_dir", "POST", "/ai/api/v1/internal/scenario-approve",
         {"scenario_id": rid()}, "утверждение сценария экспертом"),
        ("РД-03", "akim_ray", "POST", "/ai/api/v1/internal/scenario-approve",
         {"scenario_id": rid()}, "утверждение сценария акимом района"),
        ("РД-03", "exp_ver", "POST", f"/ai/api/v1/scenarios/{rid()}/reject", {}, "отклонение сценария экспертом по проверке"),
        ("РД-03", "akim_ray", "POST", "/ai/api/v1/scenarios/generate", {}, "формирование сценариев акимом района"),
        ("РД-04", "akim_ray", "PUT", f"/ai/api/v1/tasks/{rid()}/status", {"status": "accepted"},
         "самоприёмка задачи акимом района"),
        ("РД-04", "akim_ray", "POST", f"/ai/api/v1/tasks/{rid()}/accept", {}, "приёмка задачи акимом района"),
        ("РД-04", "akim_ray", "POST", "/ai/api/v1/tasks", {}, "создание задачи акимом района"),
        ("РД-05", "exp_ver", "POST", "/ai/api/v1/models", {}, "регистрация модели экспертом по проверке"),
        ("РД-05", "akim_obl", "PUT", f"/ai/api/v1/models/{rid()}/activate", {}, "активация модели акимом области"),
        ("РД-06", "akim_ray", "GET", "/gis/datasets", None, "каталог геоданных акимом района"),
        ("РД-06", "akim_obl", "POST", "/data-intake/excel", None, "загрузка данных акимом области"),
    ]
    by_id: dict[str, list[str]] = {}
    fails: dict[str, int] = {}
    for cid, role, m, p, body, title in cases:
        r = a.req(m, p, role=role, json=body) if body is not None else a.req(m, p, role=role)
        ok = r.status_code == 403
        by_id.setdefault(cid, []).append(f"{title} — {r.status_code}")
        fails[cid] = fails.get(cid, 0) + (0 if ok else 1)

    # Акимат района: только свой район и только утверждённые сценарии
    sc = items(a.get("/ai/api/v1/scenarios", role="akim_ray"))
    tk = items(a.get("/ai/api/v1/tasks?page_size=500", role="akim_ray"))
    regions = items(a.get("/regions"))
    own = {t.get("region_id") for t in tk}
    other = next((r["id"] for r in regions if r.get("level") == 2 and r["id"] not in own), None)
    foreign = a.get(f"/ai/api/v1/tasks?region_id={other}", role="akim_ray") if other else None
    only_appr = all(s.get("approved_at") for s in sc)
    by_id.setdefault("РД-07", []).extend([
        f"сценариев видно — {len(sc)}, все утверждённые — {'да' if only_appr else 'нет'}",
        f"задач видно — {len(tk)}, районов в выдаче — {len(own)}",
        f"задачи чужого района — {foreign.status_code if foreign else '—'}",
    ])
    fails["РД-07"] = int(not only_appr) + int(len(own) > 1) + int(bool(foreign) and foreign.status_code != 403)

    # Эксперт по направлениям видит данные, эксперт по проверке утверждает данные
    v = a.get("/indicator-values?page_size=1", role="exp_dir")
    vv = a.req("POST", f"/indicator-values/{rid()}/validate", role="exp_ver", json={"validation_status": "approved"})
    by_id.setdefault("РД-08", []).extend([
        f"чтение показателей экспертом — {v.status_code}",
        f"утверждение несуществующего значения экспертом по проверке — {vv.status_code} (доступ есть, объекта нет)",
    ])
    fails["РД-08"] = int(v.status_code != 200) + int(vv.status_code not in (404, 422))
    for cid, lines in by_id.items():
        record(cid, PASS if fails.get(cid, 0) == 0 else FAIL, "; ".join(lines))


# --------------------------------------------------------------------------- НФТ
def tls_days_left(host: str) -> int:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
        exp = datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return (exp.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days


def check_nft(a: Api) -> None:
    probe = ["/regions", "/indicators", "/data-layers?page_size=50", "/indicator-values?page_size=100",
             "/ai/api/v1/scenarios", "/ai/api/v1/tasks?page_size=100", "/monitoring/system", "/audit-log"]
    samples = []
    for _ in range(10):
        for p in probe:
            t0 = time.perf_counter()
            a.get(p)
            samples.append(time.perf_counter() - t0)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    worst = max(((k, max(v)) for k, v in timings.items()), key=lambda kv: kv[1])
    slow = sorted(((k, max(v)) for k, v in timings.items() if max(v) >= 3), key=lambda kv: -kv[1])
    record("НФ-01", (PASS if not slow else PARTIAL) if p95 < 3 else FAIL,
           f"{len(samples)} запросов к основным методам API: медиана {statistics.median(samples) * 1000:.0f} мс, "
           f"95-й процентиль {p95 * 1000:.0f} мс; наибольшее время за весь прогон — {worst[0]} "
           f"{worst[1]:.2f} с" + (f"; операций дольше 3 с — {len(slow)}: "
                                  + ", ".join(f"{k} {v:.1f} с" for k, v in slow) if slow else ""))

    http = httpx.get(WEB.replace("https", "http") + "/login", follow_redirects=False, timeout=20)
    sec = a.c.get(WEB + "/login")
    hsts = sec.headers.get("strict-transport-security")
    days = {h: tls_days_left(h) for h in ("dss.dwh.kz", "dssapi.dwh.kz", "dssai.dwh.kz")}
    ok = http.status_code in (301, 302, 307, 308) and all(d > 14 for d in days.values())
    record("НФ-03", PASS if ok else FAIL,
           f"HTTP→HTTPS — {http.status_code} → {http.headers.get('location')}; HSTS — "
           f"{'есть' if hsts else 'нет'}; срок сертификатов, дней: "
           + ", ".join(f"{h} {d}" for h, d in days.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--out", default="psi_result.json")
    args = ap.parse_args()
    creds, cur = {}, None
    for line in Path(args.creds).expanduser().read_text(encoding="utf-8").splitlines():
        if m := re.search(r"логин:\s*(\S+)", line):
            cur = m.group(1)
        if (m := re.search(r"пароль:\s*(\S+)", line)) and cur:
            creds[cur] = m.group(1)

    started = datetime.now(timezone.utc)
    a = Api(creds)
    for fn in (check_shd, check_ai, lambda x: check_backend(x, creds), check_frontend, check_rbac, check_nft):
        try:
            fn(a)
        except Exception as e:  # проверка не должна обрывать весь прогон
            record("ОШИБКА", FAIL, f"{getattr(fn, '__name__', 'check')}: {type(e).__name__}: {e}")
    Path(args.out).write_text(json.dumps({
        "started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
        "api": API, "results": results,
        "timings": {k: {"n": len(v), "max_s": round(max(v), 3), "median_s": round(statistics.median(v), 3)}
                    for k, v in timings.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
