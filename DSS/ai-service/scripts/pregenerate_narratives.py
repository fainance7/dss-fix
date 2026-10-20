#!/usr/bin/env python3
"""
Пакетная предгенерация нарративов сценариев на GPU-сервере.

Зачем: прод не должен ждать LLM. Скрипт прогоняет все пары
(сценарий × направление), пишет готовые тексты и отпечаток входа в
`development_scenarios.narratives`, после чего прод отдаёт текст мгновенно и
не зависит ни от канала к GPU, ни от наличия аренды.

Промпты собираются ТЕМ ЖЕ кодом, что и на проде (`app.services.narrative`), —
иначе отпечаток не совпадёт и кэш будет считаться устаревшим.

Запуск на GPU-сервере (БД прода — через обратный SSH-туннель):

    DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:15432/dss \\
    LLM_BASE_URL=http://127.0.0.1:8000/v1 LLM_MODEL=dss-narrative \\
    LLM_API_KEY=... LLM_SECTION_MAX_TOKENS=800 \\
    python scripts/pregenerate_narratives.py --concurrency 8

Параллелизм имеет смысл только на GPU: vLLM обрабатывает пачку запросов
непрерывным батчингом почти за время одного. На CPU-Ollama параллелизм лишь
дробит и без того скудный ресурс — там оставляйте --concurrency 1.
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.ai_models import DevelopmentScenario  # noqa: E402
from app.services import narrative  # noqa: E402
from app.services.geo_context import build_geo_context, render_context_as_text  # noqa: E402
from app.services.llm_client import LLMUnavailableError  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pregenerate")


async def _directions(db, only: list[str] | None) -> list[str]:
    rows = (await db.execute(text("SELECT code FROM directions ORDER BY code"))).fetchall()
    codes = [r[0] for r in rows]
    if only:
        missing = sorted(set(only) - set(codes))
        if missing:
            raise SystemExit(f"Неизвестные направления: {', '.join(missing)}")
        return [c for c in codes if c in only]
    return codes


async def _scenarios(db, only_ids: list[str] | None) -> list[DevelopmentScenario]:
    q = select(DevelopmentScenario).order_by(DevelopmentScenario.created_at)
    rows = list((await db.execute(q)).scalars().all())
    if only_ids:
        wanted = {i.lower() for i in only_ids}
        rows = [s for s in rows if str(s.id).lower() in wanted]
    return rows


async def _build_jobs(force: bool, only_dirs, only_scenarios) -> list[dict]:
    """Собирает список заданий: что именно нужно (пере)генерировать."""
    jobs: list[dict] = []
    async with AsyncSessionLocal() as db:
        directions = await _directions(db, only_dirs)
        scenarios = await _scenarios(db, only_scenarios)
        logger.info("Сценариев: %d, направлений: %d", len(scenarios), len(directions))

        for sc in scenarios:
            for dc in directions:
                try:
                    ctx = await build_geo_context(db, direction_code=dc, region_id=sc.region_id)
                except ValueError as exc:
                    logger.warning("Пропуск %s/%s: %s", sc.id, dc, exc)
                    continue
                prompt = narrative.build_user_prompt_for(sc, render_context_as_text(ctx))
                digest = narrative.input_hash(prompt)

                entry = (sc.narratives or {}).get(dc) or {}
                if not force and entry.get("status") == "done" and entry.get("input_hash") == digest:
                    continue  # актуально — заново не считаем
                jobs.append({
                    "scenario_id": sc.id, "direction": dc,
                    "prompt": prompt, "hash": digest,
                })
    return jobs


async def _save(scenario_id, direction: str, text_: str, digest: str) -> None:
    async with AsyncSessionLocal() as db:
        sc = (await db.execute(
            select(DevelopmentScenario).where(DevelopmentScenario.id == scenario_id)
        )).scalar_one_or_none()
        if sc is None:
            return
        narratives = dict(sc.narratives or {})
        narratives[direction] = {
            "status": "done",
            "text": text_,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
            "input_hash": digest,
            "model": settings.LLM_MODEL,
            "source": "pregenerated",
        }
        sc.narratives = narratives
        flag_modified(sc, "narratives")
        await db.commit()


async def _run_job(job: dict, sem: asyncio.Semaphore, dry_run: bool, stats: dict) -> None:
    async with sem:
        label = f"{job['scenario_id']}/{job['direction']}"
        if dry_run:
            logger.info("[dry-run] %s", label)
            stats["planned"] += 1
            return
        started = asyncio.get_event_loop().time()
        try:
            text_ = await narrative.generate_sections(job["prompt"])
        except LLMUnavailableError as exc:
            logger.error("LLM недоступна на %s: %s", label, exc)
            stats["failed"] += 1
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка на %s: %s", label, exc)
            stats["failed"] += 1
            return
        await _save(job["scenario_id"], job["direction"], text_, job["hash"])
        elapsed = asyncio.get_event_loop().time() - started
        stats["done"] += 1
        stats["seconds"] += elapsed
        logger.info("Готово %s за %.1f c (%d симв.)", label, elapsed, len(text_))


async def main_async(args: argparse.Namespace) -> int:
    jobs = await _build_jobs(args.force, args.directions, args.scenarios)
    if not jobs:
        logger.info("Нечего генерировать — все нарративы актуальны")
        return 0

    logger.info(
        "К генерации: %d (параллельно %d, модель %s, %d ток./секция)",
        len(jobs), args.concurrency, settings.LLM_MODEL, settings.LLM_SECTION_MAX_TOKENS,
    )
    stats = {"done": 0, "failed": 0, "planned": 0, "seconds": 0.0}
    sem = asyncio.Semaphore(args.concurrency)
    started = asyncio.get_event_loop().time()
    await asyncio.gather(*(_run_job(j, sem, args.dry_run, stats) for j in jobs))
    total = asyncio.get_event_loop().time() - started

    if args.dry_run:
        logger.info("Запланировано: %d", stats["planned"])
    else:
        avg = stats["seconds"] / stats["done"] if stats["done"] else 0.0
        logger.info(
            "Итог: готово %d, ошибок %d, суммарно %.1f c (среднее на нарратив %.1f c)",
            stats["done"], stats["failed"], total, avg,
        )
    return 1 if stats["failed"] else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Предгенерация нарративов сценариев на GPU")
    p.add_argument("--concurrency", type=int, default=8,
                   help="Параллельных запросов к LLM (GPU: 8-16, CPU: 1)")
    p.add_argument("--force", action="store_true",
                   help="Перегенерировать даже актуальные нарративы")
    p.add_argument("--directions", nargs="+", help="Только указанные направления")
    p.add_argument("--scenarios", nargs="+", help="Только указанные id сценариев")
    p.add_argument("--dry-run", action="store_true", help="Показать план без генерации")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
