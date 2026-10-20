#!/usr/bin/env python3
"""
Оценка качества нарративной модели: сравнение конфигураций A/B/C.

Сравниваются:
  A — прод «как было» (малая модель на CPU-Ollama);
  B — крупная модель на GPU с улучшенным промптом, без дообучения;
  C — дообученная модель.

Правило внедрения (зафиксировано заранее, чтобы не подгонять его под результат):
дообученная модель уходит в прод, только если она бьёт B по доле верифицируемых
чисел и по слепому парному сравнению с запасом не меньше `--win-threshold`.
Отрицательный результат — тоже результат: он документируется, а в прод остаётся B.

Метрики на hold-out:
  * доля верифицируемых чисел (числовой гейт из build_narrative_corpus);
  * чистота языка и соблюдение структуры;
  * win-rate в слепом парном сравнении (позиции кандидатов переставляются);
  * латентность и скорость генерации.

## Два режима

Одновременно поднять две модели по 30 ГБ на одной карте не выходит: vLLM
требует, чтобы вся заданная доля помещалась в СВОБОДНУЮ память, и при этом
вычитает из бюджета память, занятую другими процессами. Эти два условия для
пары крупных моделей на 95 ГиБ несовместимы. Поэтому предусмотрен
последовательный режим — модели работают по очереди, тексты сохраняются, а
сравнение выполняется отдельным шагом:

    # 1) при поднятой дообученной модели
    python scripts/eval_narrative.py --config C=http://127.0.0.1:8001/v1,dss-narrative-ft,$KEY \\
        --dataset .../test.jsonl --no-judge --save-generations /srv/ai/artifacts/gen_C.json

    # 2) при поднятой базовой
    python scripts/eval_narrative.py --config B=http://127.0.0.1:8000/v1,dss-narrative,$KEY \\
        --dataset .../test.jsonl --no-judge --save-generations /srv/ai/artifacts/gen_B.json

    # 3) слепое сравнение сохранённых текстов (нужна только модель-судья)
    python scripts/eval_narrative.py --compare /srv/ai/artifacts/gen_B.json /srv/ai/artifacts/gen_C.json \\
        --judge-url http://127.0.0.1:8000/v1 --judge-model dss-narrative --judge-key $KEY \\
        --out /srv/ai/artifacts/eval.json

Если памяти хватает, обе конфигурации можно передать одним запуском — тогда
генерация и сравнение идут за один проход.
"""
import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from build_narrative_corpus import language_clean, numbers_verified, structure_ok  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("eval")

_RUSSIAN_DIRECTIVE = (
    "Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. Весь ответ — только по-русски, "
    "без слов, фраз и предложений на других языках (в т.ч. английском и китайском). "
    "Если в контексте встречаются иноязычные термины — передавай их по-русски.\n\n"
)

_JUDGE_SYSTEM = (
    "Вы — строгий рецензент аналитических заключений для акимата Северо-Казахстанской "
    "области. Вам даны КОНТЕКСТ и два варианта заключения — А и Б. Выберите лучший по "
    "точности чисел, конкретности рекомендаций, деловому регистру и связи с контекстом. "
    'Ответьте ТОЛЬКО JSON: {"winner":"А"} или {"winner":"Б"} или {"winner":"ничья"}.'
)


async def generate(
    client: httpx.AsyncClient, cfg: dict, system: str, user: str, max_tokens: int,
) -> tuple[str, float]:
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg.get("key") else {}
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _RUSSIAN_DIRECTIVE + system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if cfg.get("extra"):
        payload.update(cfg["extra"])
    started = time.monotonic()
    resp = await client.post(
        f"{cfg['base_url'].rstrip('/')}/chat/completions", json=payload, headers=headers,
    )
    resp.raise_for_status()
    elapsed = time.monotonic() - started
    return resp.json()["choices"][0]["message"]["content"], elapsed


def score_text(text_: str, context: str) -> dict:
    nums_ok, bad = numbers_verified(text_, context)
    lang_ok, lang_why = language_clean(text_)
    struct_ok, struct_why = structure_ok(text_, min_chars=300, max_chars=8000)
    has_think = "<think>" in text_.lower()
    return {
        "numbers_ok": nums_ok,
        "unverified_numbers": bad[:10],
        "language_ok": lang_ok,
        "language_issue": lang_why,
        "structure_ok": struct_ok,
        "structure_issue": struct_why,
        "has_think_block": has_think,
        "chars": len(text_),
    }


async def judge_pair(
    client: httpx.AsyncClient, judge: dict, context: str, a: str, b: str,
) -> str | None:
    """Слепое сравнение: какой из двух текстов лучше. Позиции уже перемешаны."""
    headers = {"Authorization": f"Bearer {judge['key']}"} if judge.get("key") else {}
    payload = {
        "model": judge["model"],
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content":
                f"КОНТЕКСТ:\n{context[:4000]}\n\nВАРИАНТ А:\n{a}\n\nВАРИАНТ Б:\n{b}"},
        ],
        "temperature": 0.0,
        "max_tokens": 40,
        "stream": False,
        **DEFAULT_EXTRA_BODY,
    }
    resp = await client.post(
        f"{judge['base_url'].rstrip('/')}/chat/completions", json=payload, headers=headers,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0)).get("winner")
    except json.JSONDecodeError:
        return None


async def compare_saved(args) -> dict:
    """Слепое сравнение ранее сохранённых текстов двух конфигураций.

    Нужен только сервер модели-судьи: сами заключения уже сгенерированы. Это и
    позволяет обойтись одной моделью в памяти за раз.
    """
    left = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
    right = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
    name_left = next(k for k in left if not k.startswith("_"))
    name_right = next(k for k in right if not k.startswith("_"))
    inputs = left.get("_inputs") or right.get("_inputs") or {}

    common = sorted(set(left[name_left]) & set(right[name_right]), key=int)
    logger.info(
        "Сравнение %s против %s: общих примеров %d",
        name_left, name_right, len(common),
    )

    judge = {"base_url": args.judge_url, "model": args.judge_model}
    if args.judge_key:
        judge["key"] = args.judge_key

    pairwise = {"C_wins": 0, "B_wins": 0, "ties": 0, "failed": 0}
    per_pair = []
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    async def _one(client, key):
        async with sem:
            ctx = (inputs.get(key) or {}).get("user", "")
            a_text, b_text = left[name_left][key], right[name_right][key]
            swap = random.Random(7 + int(key)).random() < 0.5
            first, second = (b_text, a_text) if swap else (a_text, b_text)
            try:
                winner = await judge_pair(client, judge, ctx, first, second)
            except Exception as exc:  # noqa: BLE001
                logger.warning("судья: %s", exc)
                winner = None
        async with lock:
            if winner is None:
                pairwise["failed"] += 1
                verdict = "failed"
            elif winner == "ничья":
                pairwise["ties"] += 1
                verdict = "ties"
            else:
                first_won = winner == "А"
                right_won = (not first_won) if not swap else first_won
                verdict = "C_wins" if right_won else "B_wins"
                pairwise[verdict] += 1
            per_pair.append({"key": key, "verdict": verdict, "swapped": swap})

    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout, connect=10.0)) as client:
        await asyncio.gather(*(_one(client, k) for k in common))

    return {"pairwise": pairwise, "per_pair": per_pair,
            "left": name_left, "right": name_right, "samples": len(common)}


# Режим рассуждений Qwen3 включён по умолчанию, и тогда в ответ попадает блок
# <think>…</think>. В бою он отключается через LLM_EXTRA_BODY; при оценке его
# тоже нужно отключать, иначе замеряется не та конфигурация, что работает в
# Системе. Первый прогон без этого дал «чистый язык 0 %» на всех 96 ответах —
# дефект измерения, а не модели.
DEFAULT_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

# Подсказка формата для моделей, которые его не знают из обучения. Без неё
# сравнение нечестное: дообученная воспроизводит структуру по памяти, а базовой
# про структуру никто не говорил, и метрика формата у неё обнуляется. С этой
# подсказкой обе стороны получают одинаковые условия, и сравнение идёт по
# содержанию, а не по знанию формата.
FORMAT_HINT = (
    "\n\nОформите заключение ровно тремя разделами с заголовками Markdown:\n"
    "## 1. Текущая ситуация\n"
    "## 2. Прогнозный нарратив (до целевого года)\n"
    "## 3. Рекомендации\n"
    "В третьем разделе — ровно три рекомендации нумерованным списком."
)


def parse_config(spec: str, extra: dict | None) -> tuple[str, dict]:
    """`ИМЯ=base_url,model[,api_key]`"""
    name, _, rest = spec.partition("=")
    parts = rest.split(",")
    if len(parts) < 2:
        raise SystemExit(f"Неверная конфигурация: {spec}")
    cfg = {"base_url": parts[0], "model": parts[1]}
    if len(parts) > 2 and parts[2]:
        cfg["key"] = parts[2]
    if extra:
        cfg["extra"] = extra
    return name, cfg


async def _process_sample(
    idx, sample, configs, judge, client, args, rnd_seed, results, pairwise, lock, sem,
    generations=None,
):
    """Генерация всеми конфигурациями на одном входе + парное сравнение."""
    async with sem:
        texts: dict[str, str] = {}
        scored: dict[str, dict] = {}
        for name, cfg in configs.items():
            system = sample["system"] + (FORMAT_HINT if args.format_hint else "")
            try:
                text_, elapsed = await generate(
                    client, cfg, system, sample["user"], args.max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: ошибка генерации (%s)", name, exc)
                continue
            texts[name] = text_
            entry = score_text(text_, sample["user"])
            entry["seconds"] = round(elapsed, 2)
            scored[name] = entry

        if generations is not None:
            async with lock:
                for name, text_ in texts.items():
                    generations.setdefault(name, {})[str(idx)] = text_
                generations.setdefault("_inputs", {})[str(idx)] = {
                    "system": sample["system"], "user": sample["user"],
                    "meta": sample.get("meta", {}),
                }

        verdict = None
        if "B" in texts and "C" in texts and not args.no_judge:
            # Порядок вариантов переставляется псевдослучайно, но детерминированно
            # по номеру примера: прогон воспроизводим, а позиционное предпочтение
            # судьи не смещает результат.
            swap = random.Random(rnd_seed + idx).random() < 0.5
            first, second = (texts["C"], texts["B"]) if swap else (texts["B"], texts["C"])
            winner = await judge_pair(client, judge, sample["user"], first, second)
            if winner is None:
                verdict = "failed"
            elif winner == "ничья":
                verdict = "ties"
            else:
                first_won = winner == "А"
                verdict = "C_wins" if (first_won if swap else not first_won) else "B_wins"

    async with lock:
        for name, entry in scored.items():
            results[name].append(entry)
        if verdict:
            pairwise[verdict] += 1
        done = sum(len(v) for v in results.values())
        if done % 20 == 0:
            logger.info("Сгенерировано ответов: %d", done)


async def main_async(args) -> int:
    if args.compare:
        result = await compare_saved(args)
        pw = result["pairwise"]
        decided = pw["C_wins"] + pw["B_wins"]
        if decided:
            result["win_rate_C"] = round(pw["C_wins"] / decided, 3)
        print("\n" + json.dumps(
            {k: v for k, v in result.items() if k != "per_pair"},
            ensure_ascii=False, indent=2,
        ))
        if args.out:
            Path(args.out).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            logger.info("Отчёт сохранён: %s", args.out)
        return 0

    samples = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    random.Random(42).shuffle(samples)
    samples = samples[:args.limit]
    extra = json.loads(args.extra_body) if args.extra_body else None
    configs = dict(parse_config(c, extra) for c in args.config)
    judge = {"base_url": args.judge_url, "model": args.judge_model}
    if args.judge_key:
        judge["key"] = args.judge_key
    logger.info(
        "Примеров: %d, конфигураций: %s, параллельно: %d",
        len(samples), ", ".join(configs), args.concurrency,
    )

    results: dict[str, list[dict]] = {name: [] for name in configs}
    pairwise = {"C_wins": 0, "B_wins": 0, "ties": 0, "failed": 0}
    generations: dict | None = {} if args.save_generations else None
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout, connect=10.0)) as client:
        await asyncio.gather(*(
            _process_sample(i, s_, configs, judge, client, args, 7, results, pairwise,
                            lock, sem, generations)
            for i, s_ in enumerate(samples)
        ))

    summary: dict = {
        "dataset": args.dataset, "samples": len(samples),
        "подсказка_формата": bool(args.format_hint),
        "configs": {},
    }
    for name, rows in results.items():
        if not rows:
            continue
        n = len(rows)
        summary["configs"][name] = {
            "model": configs[name]["model"],
            "n": n,
            "числа_верифицированы_%": round(100 * sum(r["numbers_ok"] for r in rows) / n, 1),
            "язык_чистый_%": round(100 * sum(r["language_ok"] for r in rows) / n, 1),
            "структура_соблюдена_%": round(100 * sum(r["structure_ok"] for r in rows) / n, 1),
            "блок_think_%": round(100 * sum(r["has_think_block"] for r in rows) / n, 1),
            "среднее_время_с": round(sum(r["seconds"] for r in rows) / n, 2),
            "средняя_длина": round(sum(r["chars"] for r in rows) / n),
        }

    decided = pairwise["C_wins"] + pairwise["B_wins"]
    if decided:
        win_rate = pairwise["C_wins"] / decided
        summary["pairwise"] = {**pairwise, "win_rate_C": round(win_rate, 3)}
        c = summary["configs"].get("C", {})
        b = summary["configs"].get("B", {})
        numbers_better = c.get("числа_верифицированы_%", 0) >= b.get("числа_верифицированы_%", 0)
        summary["решение"] = (
            "внедрять дообученную модель"
            if win_rate >= args.win_threshold and numbers_better
            else "оставить недообученную (B): порог не достигнут"
        )

    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"summary": summary, "per_sample": results}, ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        logger.info("Отчёт сохранён: %s", args.out)
    if generations is not None:
        Path(args.save_generations).write_text(
            json.dumps(generations, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("Тексты сохранены: %s", args.save_generations)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Сравнение конфигураций нарративной модели")
    p.add_argument("--config", action="append",
                   help="ИМЯ=base_url,model[,api_key] — повторяется для каждой конфигурации")
    p.add_argument("--dataset", help="JSONL hold-out выборки")
    p.add_argument("--save-generations", help="Куда сохранить сгенерированные тексты")
    p.add_argument("--compare", nargs=2, metavar=("ФАЙЛ_B", "ФАЙЛ_C"),
                   help="Сравнить ранее сохранённые тексты (нужен только судья)")
    p.add_argument("--judge-url", help="Сервер модели-судьи")
    p.add_argument("--judge-model", help="Модель-судья")
    p.add_argument("--judge-key")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--format-hint", action="store_true",
                   help="Добавить в системный промпт описание формата разделов — "
                        "для моделей, не знающих его из обучения")
    p.add_argument("--extra-body", default=json.dumps(DEFAULT_EXTRA_BODY),
                   help="Доп. поля тела запроса (JSON). По умолчанию отключает "
                        "режим рассуждений Qwen3 — как в рабочей конфигурации")
    p.add_argument("--concurrency", type=int, default=6,
                   help="Параллельных примеров (на GPU ускоряет прогон многократно)")
    p.add_argument("--max-tokens", type=int, default=2400)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--win-threshold", type=float, default=0.6,
                   help="Минимальный win-rate дообученной модели для внедрения")
    p.add_argument("--out")
    args = p.parse_args()
    if args.compare:
        if not (args.judge_url and args.judge_model):
            raise SystemExit("Для сравнения сохранённых текстов нужны --judge-url и --judge-model")
    else:
        if not args.config or not args.dataset:
            raise SystemExit("Нужны --config и --dataset (или --compare)")
        if not args.no_judge and not (args.judge_url and args.judge_model):
            raise SystemExit("Нужны --judge-url и --judge-model (или --no-judge)")
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
