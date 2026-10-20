#!/usr/bin/env python3
"""
Сборка обучающего корпуса для дообучения языковой модели нарратива.

Размеченного корпуса аналитических заключений по СКО не существует, поэтому он
строится дистилляцией: крупная open-weight модель (teacher) на GPU-сервере
генерирует по несколько кандидатов на каждый контекст, после чего кандидаты
проходят пять фильтров. Всё локально — ни один запрос не уходит во внешние
сервисы (требование автономности ИИ в DSS).

## Пять гейтов

1. **Числовой** (главный). Из текста вынимаются все числа и сверяются с числами
   контекста. Допуск на округление, отдельно разрешены годы, порядковые номера
   списка и величины, выводимые из контекста делением (доли и проценты). Любое
   число «из воздуха» бракует кандидата целиком. Это прямой ответ на главную
   претензию к LLM-текстам и на директиву промпта «Не выдумывайте цифры».
2. **Языковой** — ни иероглифов, ни латиницы вне списка допустимых терминов.
3. **Структурный** — длина в заданном коридоре, в разделе рекомендаций ровно три
   пункта нумерованного списка.
4. **Судейский** — тот же teacher оценивает кандидата по рубрике 1–5; проходят
   только оценки не ниже порога.
5. **Дедупликация** — кандидаты, слишком похожие по n-граммам на уже принятые,
   отбрасываются, иначе модель выучит один шаблон.

## Разбиение

Hold-out делается ПО СУЩНОСТЯМ, а не случайно: указанные районы и направления
целиком уходят в test. При случайном разбиении почти идентичные тексты по
одному району попадут и в train, и в test, и метрика соврёт.

Запуск на GPU-сервере:

    DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:15432/dss \\
    LLM_BASE_URL=http://127.0.0.1:8000/v1 LLM_MODEL=dss-narrative LLM_API_KEY=... \\
    python scripts/build_narrative_corpus.py --candidates 6 --out /srv/ai/datasets/narrative
"""
import argparse
import asyncio
import json
import logging
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.ai_models import DevelopmentScenario  # noqa: E402
from app.services import narrative  # noqa: E402
from app.services.geo_context import build_geo_context, render_context_as_text  # noqa: E402
from app.services.llm_client import LLMUnavailableError, chat_completion  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("corpus")

# Латиница допустима только в общепринятых сокращениях и единицах.
_ALLOWED_LATIN = {
    "DSS", "GIS", "ГИС", "KPI", "SWOT", "IT", "ha", "km", "kg", "t", "AI",
}
_CJK_RE = re.compile(r"[　-鿿가-힯]")
_LATIN_RE = re.compile(r"[A-Za-z]{2,}")
# Первая альтернатива — число с разделителями тысяч (1 234,5), и она требует
# хотя бы одну группу из трёх цифр: иначе «2030» разбиралось как 203 и 0, а
# «-1402» — как -140 и 2.
# Параметры числового гейта подобраны замером на самом корпусе: в реальные
# тексты подставлялось выдуманное число, и считалась доля пойманных подделок
# против доли сохранённых настоящих текстов (300 испытаний на вариант).
#
#   допуск   производные            ловит выдумку   сохраняет настоящие
#   1 %      сумма+разность, ≤1000       54 %              100 %
#   0,2 %    сумма+разность, ≤1000       80 %               88 %
#   0,2 %    только разность, ≤200       86 %               86 %   ← выбрано
#   0,05 %   только разность, ≤200       96 %               67 %
#   0,2 %    без производных, ≤200       94 %               68 %
#
# Допуск 1 % выглядел безобидно, но на числах в сотни тысяч он покрывает тысячи
# значений и пропускал почти половину подделок. Суммы пар исключены: выигрыш в
# сохранности настоящих текстов (+2 п.п.) не стоит потери 5 п.п. в поимке
# выдумки — гейт существует ради достоверности.
MAX_DERIVED_RATIO = 200.0
NUMBER_REL_TOL = 0.002

_NUM_RE = re.compile(r"-?\d{1,3}(?:[\u00a0\u202f  ]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")


def parse_numbers(text_: str) -> list[float]:
    """Числа из текста с учётом русского форматирования (1 234,5)."""
    out: list[float] = []
    for raw in _NUM_RE.findall(text_):
        cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def numbers_verified(
    generated: str, context: str, *, rel_tol: float = NUMBER_REL_TOL,
) -> tuple[bool, list[float]]:
    """Проверяет, что каждое число текста обосновано контекстом.

    Разрешены: числа контекста (с допуском на округление), годы, порядковые
    номера и малые целые (нумерация списков, кварталы), а также величины,
    получаемые делением двух чисел контекста — так модель может корректно
    посчитать долю или процент.
    """
    ctx_nums = parse_numbers(context)
    # Величины, выводимые из контекста элементарной арифметикой: это обычная
    # аналитическая работа, а не выдумка. Разрешены:
    #   * отношения (доля, процент, кратность) — с ограничением диапазона, иначе
    #     произвольное частное двух больших чисел «оправдает» что угодно;
    #   * разности пар — ими считают убыль и прирост; по величине они
    #     ограничены самими числами контекста.
    # Суммы пар не разрешены: замер показал, что они заметно ослабляют гейт.
    derived: set[float] = set()
    for a in ctx_nums:
        for b in ctx_nums:
            derived.add(round(a - b, 4))
            if not b:
                continue
            r = a / b
            if abs(r) <= MAX_DERIVED_RATIO:
                derived.add(round(r, 4))
            r100 = r * 100
            if abs(r100) <= MAX_DERIVED_RATIO:
                derived.add(round(r100, 2))

    unverified: list[float] = []
    for n in parse_numbers(generated):
        if 1900 <= n <= 2100 and float(n).is_integer():
            continue                      # год
        if float(n).is_integer() and abs(n) <= 12:
            continue                      # нумерация, кварталы, месяцы
        if any(abs(n - c) <= max(abs(c) * rel_tol, 1e-9) for c in ctx_nums):
            continue                      # число из контекста
        if any(abs(n - d) <= max(abs(d) * rel_tol, 1e-9) for d in derived):
            continue                      # доля, разность или сумма чисел контекста
        unverified.append(n)
    return (not unverified), unverified


def language_clean(text_: str) -> tuple[bool, str]:
    if _CJK_RE.search(text_):
        return False, "иероглифы"
    for token in _LATIN_RE.findall(text_):
        if token.upper() not in {t.upper() for t in _ALLOWED_LATIN}:
            return False, f"латиница: {token}"
    return True, ""


def structure_ok(text_: str, *, min_chars: int, max_chars: int) -> tuple[bool, str]:
    if not (min_chars <= len(text_) <= max_chars):
        return False, f"длина {len(text_)} вне [{min_chars}, {max_chars}]"
    if text_.count("##") != len(narrative.NARRATIVE_SECTIONS):
        return False, "нарушена разбивка на разделы"
    tail = text_.split("## 3.")[-1]
    numbered = re.findall(r"^\s*(\d)[.)]\s+", tail, flags=re.MULTILINE)
    if len(numbered) != 3:
        return False, f"рекомендаций {len(numbered)}, нужно 3"
    return True, ""


_JUDGE_SYSTEM = (
    "Вы — строгий рецензент аналитических заключений для акимата. Оцените текст "
    "по пяти осям от 1 до 5: точность чисел, деловой регистр, конкретность "
    "рекомендаций, отсутствие «воды», связь с приведённым контекстом. "
    "Ответьте ТОЛЬКО JSON вида "
    '{"числа":5,"регистр":4,"конкретность":4,"без_воды":4,"связь":5} без пояснений.'
)


async def judge(candidate: str, context: str) -> dict | None:
    """Оценка кандидата teacher-моделью по рубрике."""
    try:
        raw = await chat_completion(
            system_prompt=_JUDGE_SYSTEM,
            user_prompt=f"КОНТЕКСТ:\n{context}\n\nТЕКСТ НА ОЦЕНКУ:\n{candidate}",
            temperature=0.0, max_tokens=120,
        )
    except LLMUnavailableError as exc:
        logger.warning("Судья недоступен: %s", exc)
        return None
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _ngrams(text_: str, n: int = 8) -> set[str]:
    words = re.findall(r"\w+", text_.lower())
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 0))}


def too_similar(candidate: str, accepted_ngrams: list[set[str]], threshold: float) -> bool:
    cand = _ngrams(candidate)
    if not cand:
        return True
    for prev in accepted_ngrams:
        if not prev:
            continue
        overlap = len(cand & prev) / len(cand)
        if overlap >= threshold:
            return True
    return False


async def build_contexts(db, args) -> list[dict]:
    """Матрица контекстов: сценарии × районы × направления."""
    scenarios = list((await db.execute(select(DevelopmentScenario))).scalars().all())
    directions = [r[0] for r in (await db.execute(
        text("SELECT code FROM directions ORDER BY code")
    )).fetchall()]
    regions = [
        (r[0], r[1]) for r in (await db.execute(text(
            "SELECT id::text, name FROM regions ORDER BY level, name"
        ))).fetchall()
    ]

    contexts: list[dict] = []
    for sc in scenarios:
        for region_id, region_name in regions:
            for dc in directions:
                try:
                    ctx = await build_geo_context(db, direction_code=dc, region_id=region_id)
                except ValueError:
                    continue
                geo_text = render_context_as_text(ctx)
                if len(geo_text) < args.min_context_chars:
                    continue  # пустой контекст — учить не на чем
                contexts.append({
                    "scenario_id": str(sc.id),
                    "scenario_name": sc.name,
                    "scenario_type": sc.scenario_type,
                    "target_year": sc.target_year,
                    "parameters": sc.parameters,
                    "region_id": region_id,
                    "region_name": region_name,
                    "direction": dc,
                    "geo_text": geo_text,
                })
    random.Random(42).shuffle(contexts)
    return contexts[:args.max_contexts]


async def process_context(ctx: dict, args, sem, state: dict) -> list[dict]:
    """Генерация кандидатов на один контекст и прогон через гейты."""
    user_prompt = narrative.build_user_prompt(
        ctx["scenario_name"], ctx["scenario_type"], ctx["target_year"],
        ctx["parameters"], ctx["geo_text"],
    )
    accepted: list[dict] = []
    async with sem:
        for _ in range(args.candidates):
            try:
                text_ = await narrative.generate_sections(
                    user_prompt, max_tokens=args.max_tokens,
                )
            except LLMUnavailableError as exc:
                logger.error("LLM недоступна: %s", exc)
                state["errors"] += 1
                return []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ошибка генерации: %s", exc)
                state["errors"] += 1
                continue

            # Сверяем с ПОЛНЫМ промптом, а не только с гео-контекстом: KPI
            # сценария (валовая продукция, инвестиции, естественный прирост)
            # лежат в блоке параметров, и модель вправе их использовать —
            # именно это ей и предписано инструкцией «не выдумывайте цифры».
            ok, bad_numbers = numbers_verified(text_, user_prompt)
            if not ok:
                state["rejected_numbers"] += 1
                logger.debug("Брак по числам %s: %s", ctx["direction"], bad_numbers[:5])
                continue
            ok, why = language_clean(text_)
            if not ok:
                state["rejected_language"] += 1
                continue
            ok, why = structure_ok(
                text_, min_chars=args.min_chars, max_chars=args.max_chars,
            )
            if not ok:
                state["rejected_structure"] += 1
                continue
            if too_similar(text_, state["ngrams"], args.dedup_threshold):
                state["rejected_duplicate"] += 1
                continue

            scores = await judge(text_, user_prompt) if not args.no_judge else {}
            if scores:
                if min(int(v) for v in scores.values()) < args.min_score:
                    state["rejected_judge"] += 1
                    continue

            state["ngrams"].append(_ngrams(text_))
            accepted.append({
                "system": narrative.NARRATIVE_BASE_SYSTEM,
                "user": user_prompt,
                "assistant": text_,
                "meta": {
                    "region": ctx["region_name"], "region_id": ctx["region_id"],
                    "direction": ctx["direction"], "scenario_type": ctx["scenario_type"],
                    "target_year": ctx["target_year"], "scores": scores,
                    "teacher_model": settings.LLM_MODEL,
                },
            })
            state["accepted"] += 1
            break  # одного принятого кандидата на контекст достаточно
    return accepted


def split_by_entities(samples: list[dict], test_regions: set[str], test_direction: str):
    """Hold-out по сущностям: указанные районы и направление целиком — в test."""
    train, test = [], []
    for s in samples:
        meta = s["meta"]
        if meta["region"] in test_regions or meta["direction"] == test_direction:
            test.append(s)
        else:
            train.append(s)
    return train, test


def revalidate(out_dir: Path) -> int:
    """Перепроверка готового корпуса текущими фильтрами, без обращения к LLM.

    Нужна, когда фильтры ужесточились после сборки: пересобирать корпус заново
    дорого (это часы работы GPU), а прогнать уже сгенерированные тексты через
    новые проверки — секунды. Отбракованные примеры сохраняются отдельно, чтобы
    решение можно было проверить, а не принимать на веру.
    """
    total = kept = 0
    report: dict[str, int] = {"числа": 0, "язык": 0, "структура": 0}
    for name in ("train", "test"):
        path = out_dir / f"{name}.jsonl"
        if not path.exists():
            continue
        good, bad = [], []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            item = json.loads(line)
            text_, ctx = item["assistant"], item["user"]
            ok, unverified = numbers_verified(text_, ctx)
            if not ok:
                report["числа"] += 1
                item["_rejected"] = {"gate": "числа", "values": unverified[:10]}
                bad.append(item)
                continue
            ok, why = language_clean(text_)
            if not ok:
                report["язык"] += 1
                item["_rejected"] = {"gate": "язык", "reason": why}
                bad.append(item)
                continue
            ok, why = structure_ok(text_, min_chars=800, max_chars=6000)
            if not ok:
                report["структура"] += 1
                item["_rejected"] = {"gate": "структура", "reason": why}
                bad.append(item)
                continue
            good.append(item)
            kept += 1

        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in good), encoding="utf-8",
        )
        if bad:
            (out_dir / f"{name}.rejected.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in bad), encoding="utf-8",
            )
        logger.info("%s: оставлено %d, отбраковано %d", name, len(good), len(bad))

    print(json.dumps(
        {"проверено": total, "оставлено": kept, "отбраковано_по": report},
        ensure_ascii=False, indent=2,
    ))
    return 0


async def main_async(args) -> int:
    if args.revalidate:
        return revalidate(Path(args.out))

    async with AsyncSessionLocal() as db:
        contexts = await build_contexts(db, args)
    logger.info("Контекстов к обработке: %d (по %d кандидатов)", len(contexts), args.candidates)
    if not contexts:
        logger.error("Нет пригодных контекстов — проверьте данные и направления")
        return 1

    state = {
        "accepted": 0, "errors": 0, "rejected_numbers": 0, "rejected_language": 0,
        "rejected_structure": 0, "rejected_duplicate": 0, "rejected_judge": 0,
        "ngrams": [],
    }
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*(process_context(c, args, sem, state) for c in contexts))
    samples = [s for batch in results for s in batch]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    test_regions = set(args.test_regions or [])
    train, test = split_by_entities(samples, test_regions, args.test_direction)

    for name, rows in (("train", train), ("test", test)):
        path = out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        logger.info("%s: %d примеров → %s", name, len(rows), path)

    stats = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "teacher_model": settings.LLM_MODEL,
        "contexts": len(contexts),
        "candidates_per_context": args.candidates,
        "accepted": state["accepted"],
        "train": len(train), "test": len(test),
        "rejected": {
            "числа": state["rejected_numbers"],
            "язык": state["rejected_language"],
            "структура": state["rejected_structure"],
            "дубликаты": state["rejected_duplicate"],
            "судья": state["rejected_judge"],
        },
        "errors": state["errors"],
        "test_regions": sorted(test_regions),
        "test_direction": args.test_direction,
    }
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("\n" + json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if samples else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Сборка корпуса для дообучения нарративной модели")
    p.add_argument("--out", default="/srv/ai/datasets/narrative")
    p.add_argument("--candidates", type=int, default=6, help="Кандидатов на контекст")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--max-contexts", type=int, default=1500)
    p.add_argument("--max-tokens", type=int, default=800, help="Токенов на секцию")
    p.add_argument("--min-chars", type=int, default=800)
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--min-context-chars", type=int, default=200)
    p.add_argument("--min-score", type=int, default=4, help="Порог оценки судьи (1-5)")
    p.add_argument("--no-judge", action="store_true", help="Без судейского гейта")
    p.add_argument("--dedup-threshold", type=float, default=0.6)
    p.add_argument("--test-regions", nargs="+",
                   default=["Айыртауский район", "Тимирязевский район"],
                   help="Районы, целиком уходящие в test")
    p.add_argument("--test-direction", default="tourism",
                   help="Направление, целиком уходящее в test")
    p.add_argument("--revalidate", action="store_true",
                   help="Перепроверить готовый корпус текущими фильтрами (без LLM)")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
