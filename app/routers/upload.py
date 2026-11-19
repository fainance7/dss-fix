# app/routers/upload.py

import os
import httpx
import psycopg2
from datetime import datetime
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from routers.upload_parser import upload_file, detect_structure, upload_zip

router = APIRouter(prefix="/upload", tags=["Upload Interface"])
templates = Jinja2Templates(directory="templates")

# H-5: Airflow pass — only from env, fail-fast. No insecure defaults.
AIRFLOW_URL  = os.environ.get("AIRFLOW_URL", "http://airflow-webserver:8080")
AIRFLOW_USER = os.environ.get("AIRFLOW_USER", "admin")
AIRFLOW_PASS = os.environ.get("AIRFLOW_ADMIN_PASSWORD")
if not AIRFLOW_PASS:
    raise RuntimeError("AIRFLOW_ADMIN_PASSWORD must be set (audit H-5: no insecure default)")

ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", "/app/artifacts")

DB_CONN = os.environ.get("DB_CONN") or os.environ.get("REPORTS_DB_DSN")
if not DB_CONN:
    raise RuntimeError("DB_CONN or REPORTS_DB_DSN must be set (audit H-5: no insecure default)")


def trigger_dag(dag_id: str, conf: dict = None) -> dict:
    """Триггерит DAG в Airflow и возвращает dag_run_id."""
    if conf is None:
        conf = {}
    url  = f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns"
    resp = httpx.post(url, json={"conf": conf},
                      auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Airflow error: {resp.text}")
    return resp.json()


def get_dag_run_status(dag_id: str, dag_run_id: str) -> dict:
    """Возвращает статус конкретного DAG Run."""
    url = f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
    try:
        resp = httpx.get(url, auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10)
        if resp.status_code != 200:
            return {"state": "unknown"}
        return resp.json()
    except httpx.HTTPError:
        return {"state": "unknown"}


def get_recent_dag_runs(dag_id: str, limit: int = 5) -> list:
    """Возвращает последние запуски DAG."""
    url = f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns?limit={limit}&order_by=-execution_date"
    try:
        resp = httpx.get(url, auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10)
        if resp.status_code != 200:
            return []
        return resp.json().get("dag_runs", [])
    except httpx.HTTPError:
        return []


def get_task_instances(dag_id: str, dag_run_id: str) -> list:
    """Возвращает список тасков конкретного DAG run."""
    import urllib.parse
    encoded_run_id = urllib.parse.quote(dag_run_id, safe="")
    url = f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns/{encoded_run_id}/taskInstances"
    try:
        resp = httpx.get(url, auth=(AIRFLOW_USER, AIRFLOW_PASS), timeout=10)
        if resp.status_code != 200:
            return []
        return resp.json().get("task_instances", [])
    except httpx.HTTPError:
        return []


def get_task_log(dag_id: str, dag_run_id: str, task_id: str, try_number: int) -> str:
    """Скачивает текстовый лог конкретной попытки выполнения таска."""
    import urllib.parse
    encoded_run_id = urllib.parse.quote(dag_run_id, safe="")
    url = (f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns/{encoded_run_id}"
          f"/taskInstances/{task_id}/logs/{try_number}")
    try:
        resp = httpx.get(url, auth=(AIRFLOW_USER, AIRFLOW_PASS),
                         params={"full_content": "true"}, timeout=10)
        if resp.status_code != 200:
            return ""
        return resp.text
    except httpx.HTTPError:
        return ""


def extract_error_message(log_text: str) -> str | None:
    """
    Извлекает человекочитаемое сообщение об ошибке из текстового лога Airflow.

    Логика: находит последний блок "Traceback (most recent call last):" в
    логе (если их несколько, последний — финальная причина падения), затем
    первую строку после него, которая выглядит как "ИмяИсключения: текст"
    (т.е. не начинается с пробела и не является строкой "File ..." самого
    traceback-а). Собирает эту строку и все последующие строки сообщения
    исключения (до следующей служебной строки лога airflow, начинающейся
    с временной метки "[") — это покрывает многострочные сообщения вроде
    кастомных ValueError с собранным списком ошибок валидации.

    Возвращает None, если в логе не нашлось traceback-а — например, если
    таск был помечен failed из-за таймаута/убийства процесса, а не
    необработанного исключения; в этом случае вызывающий код должен
    показать пользователю запасной текст, а не None как есть.
    """
    if not log_text:
        return None

    lines = log_text.split("\n")

    traceback_idx = None
    for i, line in enumerate(lines):
        if "Traceback (most recent call last):" in line:
            traceback_idx = i

    if traceback_idx is None:
        return None

    exception_line_idx = None
    for i in range(traceback_idx + 1, len(lines)):
        line = lines[i]
        if line.startswith("["):
            break
        if line and not line.startswith(" ") and not line.startswith("File "):
            exception_line_idx = i
            break

    if exception_line_idx is None:
        return None

    msg_lines = []
    for i in range(exception_line_idx, len(lines)):
        line = lines[i]
        if line.startswith("["):
            break
        msg_lines.append(line)

    result = "\n".join(msg_lines).strip()
    return result or None


@router.get("/", response_class=HTMLResponse)
async def upload_index(request: Request):
    """Главная страница Upload Interface."""
    history = []
    for dag_id in ["dag_extract_weather", "dag_extract_taldau",
                   "dag_extract_historical", "dag_build_excel", "dag_auto_convert"]:
        # limit=10, не 3 — иначе при нескольких одновременных запусках
        # карточка "В процессе" может недосчитать активные DAG, если они
        # не попадают в последние 3 по каждому dag_id
        runs = get_recent_dag_runs(dag_id, limit=10)
        for run in runs:
            history.append({
                "dag_id":       dag_id,
                "dag_run_id":   run.get("dag_run_id"),
                "state":        run.get("state", "unknown"),
                "start_date":   run.get("start_date"),
                "end_date":     run.get("end_date"),
            })
    history.sort(key=lambda x: x.get("start_date") or "", reverse=True)

    stats = {
        "total":   len(history),
        "success": sum(1 for r in history if r["state"] == "success"),
        "failed":  sum(1 for r in history if r["state"] == "failed"),
        "running": sum(1 for r in history if r["state"] in ("running", "queued")),
    }
    return templates.TemplateResponse(request, "upload/index.html", { "history": history[:10], "stats": stats})


@router.get("/manual", response_class=HTMLResponse)
async def upload_manual(request: Request):
    """Страница ручной загрузки файлов."""
    return templates.TemplateResponse(request, "upload/manual.html", {})


@router.get("/taldau", response_class=HTMLResponse)
async def upload_taldau(request: Request):
    """Страница запуска Taldau + Гос. API."""
    return templates.TemplateResponse(request, "upload/taldau.html", {})


@router.get("/climate", response_class=HTMLResponse)
async def upload_climate(request: Request):
    """Страница запуска Climate API (Open-Meteo)."""
    return templates.TemplateResponse(request, "upload/climate.html", {})


@router.get("/files", response_class=HTMLResponse)
async def upload_files(request: Request):
    """Страница со списком готовых DSS Excel файлов."""
    return templates.TemplateResponse(request, "upload/files.html", {})


@router.get("/agromap", response_class=HTMLResponse)
async def agromap_page(request: Request):
    return templates.TemplateResponse(request, "upload/agromap.html", {})

@router.get("/legal", response_class=HTMLResponse)
async def legal_page(request: Request):
    return templates.TemplateResponse(request, "upload/legal.html", {})


@router.post("/api/manual/upload")
async def api_manual_upload(
    file: UploadFile = File(...),
    direction_code: str = Form("economy"),
    indicator_name: str = Form(""),
    period_type:    str = Form("annual"),
):
    """
    Принимает файл, сохраняет в artifacts/, триггерит dag_auto_convert.

    dag_auto_convert парсит файл через универсальный детектор структуры
    (etl/universal_detector.py) и сам, по цепочке, запускает
    dag_extract_historical -> dag_build_excel — пользователю достаточно
    одной загрузки на фронте.

    direction_code и indicator_name с фронта используются только как
    fallback для показателей, которые автодетект не смог сопоставить
    с каталогом (etl/indicator_catalog.csv) — выбор пользователя не
    переопределяет правильно найденные показатели.

    ВНИМАНИЕ: этот эндпоинт уже однажды откатывался обратно на
    dag_build_excel при правках в неродственной задаче (добавление
    /upload/files). Если меняешь этот файл — проверь после сохранения,
    что вызывается именно dag_auto_convert, не dag_build_excel.
    """
    # Список должен совпадать с ALLOWED_EXTENSIONS в upload_parser.py и с
    # форматами, которые реально умеет extract_sheets() в
    # etl/document_adapters.py — иначе пользователь может загрузить файл,
    # который успешно сохранится, но dag_auto_convert не сможет
    # обработать (или наоборот, как было здесь: фронт уже мог принять
    # .docx, но бэкенд тут отвергал его до того, как файл попадал в
    # universal_detector, который .docx/.doc/.pdf поддерживает).
    allowed = {"xlsx", "xls", "csv", "doc", "docx", "pdf"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        raise HTTPException(400, f"Недопустимый формат. Разрешены: {', '.join(sorted(allowed))}")

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    savepath = os.path.join(ARTIFACTS_DIR, f"{ts}_{file.filename}")

    content = await file.read()
    with open(savepath, "wb") as f:
        f.write(content)

    # ARTIFACTS_DIR внутри fastapi-контейнера смонтирован как /app/artifacts,
    # а в airflow-контейнерах та же папка смонтирована как /opt/airflow/artifacts.
    # DAG выполняется в airflow-worker, поэтому путь нужно пересчитать.
    worker_path = savepath.replace(ARTIFACTS_DIR, "/opt/airflow/artifacts")

    run = trigger_dag("dag_auto_convert", conf={
        "source_file":             worker_path,
        "fallback_direction_code": direction_code,
        "fallback_indicator_name": indicator_name,
    })

    return JSONResponse({"success": True, "dag_run_id": run.get("dag_run_id"),
                         "dag_id": "dag_auto_convert",
                         "message": "Файл сохранён, запущен dag_auto_convert "
                                    "(автоматическое распознавание структуры + загрузка в DSS)"})


@router.post("/api/taldau/run")
async def api_taldau_run(request: Request):
    """
    Триггерит dag_extract_taldau.

    split_by_kato передаётся с фронта (тоггл "Детализировать по районам
    СКО") напрямую в conf DAG-а — это и есть правка backend, без которой
    тоггл на фронте визуально работал бы, но не давал бы реального эффекта
    (см. Задача 1.4, пункт C списка противоречий).

    kato_region_code принимается и пробрасывается в conf (Задача B из
    Backend_zadachi_napartnik.md), НО dag_extract_taldau.py сейчас
    игнорирует это поле — внутри DAG регион СКО зашит явно
    (SKO_TERM_ID=264023 для всей области, SKO_DISTRICT_TERM_IDS для
    районов). Это осознанное временное ограничение: расширение DAG на
    произвольные регионы (не только СКО) — отдельная задача, не входившая
    в текущий список. Поле принимается уже сейчас, чтобы фронт не
    блокировался ожиданием этой доработки — когда регион появится в
    реальных данных, нужно будет доработать DAG, не трогая фронт.
    """
    data            = await request.json()
    index_ids       = data.get("index_ids", [])
    year_from       = int(data.get("year_from", 2010))
    year_to         = int(data.get("year_to", datetime.now().year))
    split_by_kato   = bool(data.get("split_by_kato", False))
    kato_region_code = str(data.get("kato_region_code", "590000000"))  # принимается, см. docstring
    indicator_names = data.get("indicator_names", {})  # {id: name} - для показателей из
                                                          # живого поиска Talдау, которых ещё
                                                          # нет в taldau_indicators_catalog

    if not index_ids:
        raise HTTPException(400, "Не указаны index_ids")

    run = trigger_dag("dag_extract_taldau", conf={
        "index_ids":        index_ids,
        "year_from":        year_from,
        "year_to":          year_to,
        "split_by_kato":    split_by_kato,
        "kato_region_code": kato_region_code,
        "indicator_names":  indicator_names,
    })

    return JSONResponse({"success": True, "dag_run_id": run.get("dag_run_id"),
                         "dag_id": "dag_extract_taldau",
                         "message": f"Запущен dag_extract_taldau для {len(index_ids)} индикаторов"
                                    f"{' (по районам СКО)' if split_by_kato else ''}"})


@router.get("/api/taldau-search")
async def api_taldau_search(q: str = ""):
    """
    Живой поиск по ПОЛНОМУ каталогу показателей Talдау (не только по 7
    строкам в taldau_indicators_catalog) — через недокументированный
    внутренний эндпоинт /Search/getSearchPageGridData самого сайта
    taldau.stat.gov.kz (используется его собственным ExtJS-гридом поиска;
    формат запроса подсмотрен через DevTools браузера).

    ВАЖНО: это только КАТАЛОГ (id + название + код), не данные. Чтобы
    реально получить данные по показателю, которого нет в DIC_IDS_MAP (см.
    dag_extract_taldau.py), нужно так же вручную через DevTools поймать его
    p_dicIds/p_terms — отдельная задача, этот эндпоинт её не решает.
    Запуск DAG для "непроверенного" ID не ошибка — DIC_IDS_MAP отдаёт
    дефолтные параметры, и Talдау просто вернёт [ПУСТО] (уже обрабатывается
    в fetch_taldau как некритичный случай).

    Живой поиск - необязательная надстройка: если сайт Talдау недоступен
    или поменял вёрстку, эндпоинт не роняет страницу, а возвращает
    success=False с пустым results.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return JSONResponse({"success": True, "results": []})

    headers = {
        "accept": "*/*",
        "accept-language": "ru,en-US;q=0.9,en;q=0.8",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://taldau.stat.gov.kz",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "x-requested-with": "XMLHttpRequest",
    }
    search_page_url = "https://taldau.stat.gov.kz/ru/Search/SearchByKeyWord"
    grid_data_url = "https://taldau.stat.gov.kz/ru/Search/getSearchPageGridData"

    try:
        with httpx.Client(timeout=15, headers=headers) as client:
            r1 = client.get(search_page_url, params={"keyword": q})
            r2 = client.post(
                grid_data_url,
                data={
                    "pid": -1, "keyword": q, "tree": "",
                    "periods": "", "page": 1, "start": 0, "limit": 50,
                },
                headers={"referer": str(r1.url)},
            )
            r2.raise_for_status()
            payload = r2.json()
    except Exception as e:
        return JSONResponse({"success": False, "results": [],
                             "error": f"Talдау недоступен: {str(e)[:200]}"})

    results = [
        {"id": item.get("id"), "name": item.get("Name"), "code": item.get("Code")}
        for item in payload.get("results", [])
        if item.get("id") is not None and item.get("Name")
    ]
    return JSONResponse({"success": True, "total": payload.get("total"), "results": results})


@router.get("/api/taldau-indicators")
async def api_taldau_indicators():
    """
    Возвращает список показателей Talдау из taldau_indicators_catalog —
    реализует Задачу 1.4 плана (динамический селектор) + доработку
    "Задача A" из Backend_zadachi_napartnik.md (поле category для тегов
    в мультиселекте).

    Раньше список показателей был захардкожен в JS-массиве INDICATORS прямо
    внутри upload/taldau.html — добавление нового показателя требовало
    правки фронтенд-кода. Теперь источник истины — таблица в БД, фронт
    запрашивает актуальный список при каждой загрузке страницы /upload/taldau.

    Возвращает только is_active=true показатели — деактивированные (например,
    те, для которых Talдау API стабильно отдаёт 404) не показываются в
    селекторе, но остаются в таблице для истории.

    КАЖДЫЙ элемент содержит ОБА набора имён полей одновременно:
      - старые (name, direction, unit, id=taldau_index_id) — для
        обратной совместимости, если где-то ещё используется прежний формат
      - новые (display_name, taldau_id, category, direction_code) — точно
        по контракту из Backend_zadachi_napartnik.md (Задача A), который
        ожидает функция resolveCategory() и остальной код в taldau.html

    Ответ обёрнут в {"success": true, "indicators": [...]} как и раньше —
    НЕ плоский массив. Фронт должен брать response.indicators, а не сам
    JSON-корень как массив (так было задумано до этой правки, менять
    обёртку сейчас означало бы риск сломать что-то ещё, использующее
    текущий ответ).
    """
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, taldau_index_id, name_ru, direction_code, unit_name, category
            FROM taldau_indicators_catalog
            WHERE is_active = TRUE
            ORDER BY direction_code, name_ru
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Не удалось прочитать каталог показателей Talдау: {e}")

    indicators = [
        {
            # старые поля — обратная совместимость
            "id":        r[1],   # было: taldau_index_id как "id"
            "name":      r[2],
            "direction": r[3],
            "unit":      r[4],
            # новые поля — контракт из Backend_zadachi_napartnik.md
            "display_name":   r[2],
            "taldau_id":      str(r[1]),
            "direction_code": r[3],
            "category":       r[5],   # NULL, если категория ещё не назначена
            "internal_id":    r[0],   # serial PK таблицы, если когда-нибудь
                                       # понадобится отличить запись от
                                       # taldau_index_id (они могут не совпадать
                                       # 1:1 в будущем, если один показатель
                                       # Talдау будет представлен несколькими
                                       # строками с разными unit/direction)
        }
        for r in rows
    ]
    return JSONResponse({"success": True, "indicators": indicators})


@router.post("/api/climate/run")
async def api_climate_run(request: Request):
    """Триггерит dag_extract_weather."""
    data      = await request.json()
    year_from = int(data.get("year_from", 2020))
    year_to   = int(data.get("year_to", datetime.now().year))
    districts = data.get("districts", ["all"])

    run = trigger_dag("dag_extract_weather", conf={
        "year_from": year_from,
        "year_to":   year_to,
        "districts": districts,
    })

    return JSONResponse({"success": True, "dag_run_id": run.get("dag_run_id"),
                         "dag_id": "dag_extract_weather",
                         "message": f"Запущен dag_extract_weather за {year_from}–{year_to}"})


@router.get("/api/dag-status/{dag_id}/{dag_run_id}")
async def api_dag_status(dag_id: str, dag_run_id: str):
    """Возвращает статус конкретного DAG Run."""
    status = get_dag_run_status(dag_id, dag_run_id)
    return JSONResponse({"success": True, "state": status.get("state", "unknown"),
                         "start_date": status.get("start_date"),
                         "end_date":   status.get("end_date")})


@router.get("/api/history")
async def api_history():
    """Возвращает историю последних запусков всех DAG-ов."""
    history = []
    for dag_id in ["dag_extract_weather", "dag_extract_taldau",
                   "dag_extract_historical", "dag_build_excel", "dag_auto_convert"]:
        runs = get_recent_dag_runs(dag_id, limit=5)
        for run in runs:
            history.append({
                "dag_id":     dag_id,
                "dag_run_id": run.get("dag_run_id"),
                "state":      run.get("state", "unknown"),
                "start_date": run.get("start_date"),
                "end_date":   run.get("end_date"),
            })
    history.sort(key=lambda x: x.get("start_date") or "", reverse=True)
    return JSONResponse({"success": True, "runs": history[:20]})


@router.get("/api/failed-logs")
async def api_failed_logs(limit: int = 20):
    """
    Реализует Задачу 1.5 плана: счётчик "С ошибками" на дашборде — кликабельная
    ссылка, открывающая таблицу человекочитаемых причин падения по каждому
    failed DAG run.

    Для каждого найденного failed-запуска (по всем известным DAG-ам):
      1. Получает список тасков этого run через taskInstances
      2. Находит таск(и) со state='failed'
      3. Скачивает лог именно той попытки (try_number), что завершилась
         неудачей — не последней попытки вообще, если retry успел сработать
      4. Извлекает текст исключения (extract_error_message) вместо
         технического трейсбэка целиком

    Если у failed-таска лог не содержит распознаваемого traceback (например,
    таск был убит по таймауту/нехватке памяти — мы видели такое с airflow
    worker'ом ранее в этом проекте), message будет общим текстом-заглушкой,
    а не None — фронту не нужно отдельно обрабатывать null.
    """
    results = []

    for dag_id in ["dag_extract_weather", "dag_extract_taldau",
                   "dag_extract_historical", "dag_build_excel", "dag_auto_convert"]:
        runs = get_recent_dag_runs(dag_id, limit=limit)
        failed_runs = [r for r in runs if r.get("state") == "failed"]

        for run in failed_runs:
            dag_run_id = run.get("dag_run_id")
            tasks = get_task_instances(dag_id, dag_run_id)
            failed_tasks = [t for t in tasks if t.get("state") == "failed"]

            if not failed_tasks:
                # DAG помечен failed, но ни один таск не в состоянии failed —
                # бывает при upstream_failed на родительском DAG; всё равно
                # показываем запись, чтобы счётчик "С ошибками" совпадал
                # со списком, который видит пользователь
                results.append({
                    "dag_id":     dag_id,
                    "dag_run_id": dag_run_id,
                    "task_id":    None,
                    "start_date": run.get("start_date"),
                    "end_date":   run.get("end_date"),
                    "message":    "DAG помечен failed, но ни один отдельный таск "
                                  "не завершился ошибкой (возможна проблема в "
                                  "зависимом DAG или прерывание выполнения).",
                })
                continue

            for task in failed_tasks:
                task_id = task.get("task_id")
                try_number = task.get("try_number", 1)
                log_text = get_task_log(dag_id, dag_run_id, task_id, try_number)
                message = extract_error_message(log_text)

                results.append({
                    "dag_id":     dag_id,
                    "dag_run_id": dag_run_id,
                    "task_id":    task_id,
                    "start_date": run.get("start_date"),
                    "end_date":   run.get("end_date"),
                    "message":    message or (
                        "Не удалось извлечь точный текст ошибки из лога "
                        "(таск может быть прерван таймаутом или нехваткой "
                        "памяти, а не необработанным исключением)."
                    ),
                })

    results.sort(key=lambda x: x.get("end_date") or "", reverse=True)
    return JSONResponse({"success": True, "total": len(results), "errors": results})


# ════════════════════════════════════════════════════════════════════════════
# РОУТЫ-ФАСАД к upload_parser.py (решение пункта A списка противоречий)
#
# upload_parser.py защищён verify_api_key на уровне main.py — это правильно
# для прямого программного доступа к API, но фронтенд (браузер) не может
# безопасно хранить и отправлять секретный ключ. Эти 3 роута — публичный,
# browser-facing фасад: они принимают запрос от фронта БЕЗ ключа (как и
# остальные роуты в этом файле).
#
# ВАЖНО: раньше здесь был self-HTTP вызов на localhost:8000 с передачей
# INTERNAL_API_KEY в заголовке. При однопоточном uvicorn (1 worker, см.
# `ps aux` — запуск без --workers) это гарантированно дедлочило event loop:
# обрабатывая входящий запрос, процесс делал синхронный httpx.post сам на
# себя и ждал ответа от самого себя, который физически не мог прийти,
# потому что единственный worker был занят этим же ожиданием. Через 60-300
# секунд (в зависимости от роута) httpx падал по таймауту → 502.
#
# Теперь функции upload_parser.py (upload_file, detect_structure, upload_zip)
# вызываются напрямую как обычные Python-функции, в одном процессе, без
# HTTP и без сети. Ключ для этого не нужен: Depends(verify_api_key) в
# main.py — это зависимость FastAPI-роутера, она проверяется только когда
# запрос идёт через реальный HTTP-роутинг Starlette. При прямом вызове
# функции мы не проходим через этот роутинг и не нарушаем защиту — мы просто
# выполняем код. Внешний пользователь всё ещё не может попасть на
# /upload/detect, /upload/file, /upload/zip без ключа напрямую; единственный
# способ выполнить этот код без ключа — это публичные /api/* роуты ниже,
# что и есть их явное предназначение.
#
# Фронту нужно звать именно ЭТИ пути, не /upload/detect, /upload/file,
# /upload/zip напрямую — те остаются доступны только с валидным ключом.
# ════════════════════════════════════════════════════════════════════════════

@router.post("/api/detect")
async def api_proxy_detect(file: UploadFile = File(...)):
    """
    Вызывает detect_structure() из upload_parser.py напрямую (в одном
    процессе) — реализует Задачу 1.3 (Data Preview) без необходимости
    передавать API-ключ из браузера и без сетевого self-вызова.
    """
    return await detect_structure(file=file)


@router.post("/api/file")
async def api_proxy_file(
    file:           UploadFile = File(...),
    indicator_name: str = Form(""),
    direction:      str = Form("economy"),
    unit:           str = Form(""),
    sheet_name:     str = Form(""),
    mode:           str = Form("dag"),
):
    """
    Вызывает upload_file() из upload_parser.py напрямую — финальный шаг
    Задачи 1.3: вызывается фронтом только после нажатия «Подтвердить и
    сгенерировать DSS Excel» в модальном окне превью, на том же файле,
    что уже был показан через /api/detect.
    """
    return await upload_file(
        file=file,
        indicator_name=indicator_name or None,
        direction=direction,
        unit=unit or None,
        sheet_name=sheet_name or None,
        mode=mode,
    )


@router.post("/api/zip")
async def api_proxy_zip(
    file:      UploadFile = File(...),
    direction: str = Form("economy"),
    mode:      str = Form("dag"),
):
    """
    Вызывает upload_zip() из upload_parser.py напрямую — реализует Задачу 1.2
    (массовая загрузка ZIP) без передачи ключа в браузер и без self-HTTP.
    Отвечает на пункт D списка противоречий: фронт должен слать .zip именно
    сюда, не на /upload/api/manual/upload (тот эндпоинт принимает только
    xlsx/xls/csv/doc/docx/pdf по отдельности, не архивы).
    """
    return await upload_zip(file=file, direction=direction, mode=mode)
