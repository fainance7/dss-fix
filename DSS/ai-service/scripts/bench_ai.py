#!/usr/bin/env python3
"""
Замеры производительности inference Модуля ИИ: «до» (CPU) и «после» (GPU).

Скрипт намеренно написан на стандартной библиотеке — он должен запускаться и на
прод-сервере внутри контейнера, и на GPU-узле без установки зависимостей.

Что меряется:
  * скорость генерации (токенов в секунду) на одиночном запросе;
  * время одной секции нарратива и полного отчёта из трёх секций;
  * пропускная способность при параллельных запросах (для предгенерации);
  * время первого токена (latency прогрева).

Пример:

    python scripts/bench_ai.py \\
        --base-url http://127.0.0.1:8000/v1 --model dss-narrative --api-key $KEY \\
        --label "GPU RTX PRO 6000 / Qwen3-32B-FP8" \\
        --out /srv/ai/artifacts/bench-gpu.json
"""
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

RUSSIAN_DIRECTIVE = (
    "Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. Весь ответ — только по-русски, "
    "без слов, фраз и предложений на других языках (в т.ч. английском и китайском).\n\n"
)

SYSTEM = (
    "Вы — аналитик системы поддержки принятия решений (DSS) Северо-Казахстанской "
    "области. По данным сценария развития и геопространственному контексту готовите "
    "отчёт для акимата на русском языке. Не выдумывайте цифры — используйте только "
    "то, что есть в контексте."
)

SECTIONS = [
    "Напишите ТОЛЬКО раздел «Текущая ситуация»: 2 коротких абзаца с опорой на "
    "агрегаты по районам из контекста. Без заголовка — только текст раздела.",
    "Напишите ТОЛЬКО раздел «Прогнозный нарратив»: как сценарий и его параметры "
    "влияют на регион до целевого года. Без заголовка — только текст раздела.",
    "Напишите ТОЛЬКО раздел «Рекомендации»: 3 конкретных действия для акимата "
    "нумерованным списком. Без заголовка — только текст раздела.",
]

USER = """Сценарий: Оптимальный сценарий развития СКО
Тип: optimal
Целевой год: 2030
Параметры (рычаги влияния и computed KPI):
```json
{
  "investment_multiplier": 1.5,
  "kpis": {
    "agri_output": 1284500.0,
    "population": 528300.0,
    "natural_increase": -1402.0,
    "agri_investment": 87600.0
  }
}
```

Геопространственный контекст (Сельское хозяйство, Северо-Казахстанская область):
- Айыртауский район: посевная площадь 125400 га, валовой сбор 187500 тонн, население 42300 человек
- Кызылжарский район: посевная площадь 143200 га, валовой сбор 201300 тонн, население 47100 человек
- Тайыншинский район: посевная площадь 168900 га, валовой сбор 223700 тонн, население 51800 человек
- г. Петропавловск: население 218400 человек
"""


def call(base_url, model, api_key, instruction, max_tokens, timeout, extra_body=None):
    """Один запрос к OpenAI-совместимому серверу. Возвращает (текст, секунды, usage)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": RUSSIAN_DIRECTIVE + SYSTEM + "\n\n" + instruction},
            {"role": "user", "content": USER},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra_body:
        payload.update(extra_body)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    elapsed = time.monotonic() - started
    return data["choices"][0]["message"]["content"], elapsed, data.get("usage") or {}


def bench_report(args, extra_body):
    """Полный отчёт: три секции подряд, как это делает ai-service."""
    sections, times, tokens = [], [], 0
    for instruction in SECTIONS:
        text, elapsed, usage = call(
            args.base_url, args.model, args.api_key, instruction,
            args.max_tokens, args.timeout, extra_body,
        )
        sections.append(text)
        times.append(elapsed)
        tokens += int(usage.get("completion_tokens") or 0)
    return {
        "секций": len(SECTIONS),
        "время_секций_с": [round(t, 2) for t in times],
        "время_отчёта_с": round(sum(times), 2),
        "токенов_сгенерировано": tokens,
        "токенов_в_секунду": round(tokens / sum(times), 2) if tokens and sum(times) else None,
        "символов": sum(len(s) for s in sections),
        "есть_think_блок": any("<think>" in s.lower() for s in sections),
        "есть_иероглифы": any(any("　" <= ch <= "鿿" for ch in s) for s in sections),
    }


def bench_parallel(args, extra_body, n):
    """Пропускная способность: n одновременных запросов (сценарий предгенерации)."""
    results, errors = [], []
    lock = threading.Lock()

    def worker():
        try:
            _, elapsed, usage = call(
                args.base_url, args.model, args.api_key, SECTIONS[0],
                args.max_tokens, args.timeout, extra_body,
            )
            with lock:
                results.append((elapsed, int(usage.get("completion_tokens") or 0)))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - started

    if not results:
        return {"параллельных": n, "ошибок": len(errors), "примеры_ошибок": errors[:3]}
    total_tokens = sum(tok for _, tok in results)
    return {
        "параллельных": n,
        "успешных": len(results),
        "ошибок": len(errors),
        "общее_время_с": round(wall, 2),
        "среднее_на_запрос_с": round(statistics.mean(e for e, _ in results), 2),
        "суммарно_токенов_в_секунду": round(total_tokens / wall, 2) if wall else None,
    }


def main():
    p = argparse.ArgumentParser(description="Замеры inference Модуля ИИ DSS")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--api-key")
    p.add_argument("--label", default="", help="Подпись конфигурации в отчёте")
    p.add_argument("--max-tokens", type=int, default=800)
    p.add_argument("--timeout", type=float, default=7200)
    p.add_argument("--repeats", type=int, default=1, help="Прогонов полного отчёта")
    p.add_argument("--parallel", type=int, default=0, help="Проверить N параллельных запросов")
    p.add_argument("--no-thinking", action="store_true",
                   help="Отключить режим рассуждений (Qwen3)")
    p.add_argument("--out")
    args = p.parse_args()

    extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if args.no_thinking else None

    report = {
        "метка": args.label or args.model,
        "модель": args.model,
        "сервер": args.base_url,
        "бюджет_токенов_на_секцию": args.max_tokens,
        "замер_выполнен": datetime.now(timezone.utc).isoformat(),
        "прогоны": [],
    }
    for i in range(args.repeats):
        run = bench_report(args, extra_body)
        report["прогоны"].append(run)
        print(f"Прогон {i + 1}: отчёт за {run['время_отчёта_с']} с, "
              f"{run['токенов_в_секунду']} ток/с", flush=True)

    speeds = [r["токенов_в_секунду"] for r in report["прогоны"] if r["токенов_в_секунду"]]
    totals = [r["время_отчёта_с"] for r in report["прогоны"]]
    report["итог"] = {
        "среднее_ток_в_секунду": round(statistics.mean(speeds), 2) if speeds else None,
        "среднее_время_отчёта_с": round(statistics.mean(totals), 2) if totals else None,
        "среднее_время_секции_с": round(statistics.mean(totals) / len(SECTIONS), 2) if totals else None,
    }

    if args.parallel:
        report["параллельно"] = bench_parallel(args, extra_body, args.parallel)
        print(f"Параллельно {args.parallel}: {report['параллельно']}", flush=True)

    print("\n" + json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nОтчёт сохранён: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
