"""
Seed-скрипт: заполняет таблицу kato_reference данными КАТО для СКО
и связывает записи с таблицей regions.

Запуск:
    docker exec dss-backend python scripts/seed_kato.py
или локально:
    POSTGRES_HOST=localhost python scripts/seed_kato.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('POSTGRES_USER', 'dss_user')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'password')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/"
    f"{os.getenv('POSTGRES_DB', 'dss_db')}"
)

# ---------------------------------------------------------------------------
# Данные КАТО для Северо-Казахстанской области (актуальная схема после реформы
# 2022 г.: область 590000000, районы 59xxxxxxx). Зеркалит продовый справочник
# kato_reference / docs/data/KATO_16.03.2026_ru.csv (сверено с БД 2026-07-01).
# Источник: Классификатор административно-территориальных объектов РК
# ---------------------------------------------------------------------------
KATO_SKO = [
    # kato_code, name_ru, name_kz, type_code, type_name_ru, parent_code, level
    (
        "590000000",
        "Северо-Казахстанская область",
        "Солтүстік Қазақстан облысы",
        "oblys",
        "Область",
        None,
        1,
    ),
    (
        "591000000",
        "Петропавловск Г.А.",
        "Петропавловск Қ.А.",
        "qala",
        "Городская администрация",
        "590000000",
        2,
    ),
    (
        "593200000",
        "Айыртауский район",
        "Айыртау ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "593400000",
        "Акжарский район",
        "Ақжар ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "593600000",
        "Район Магжана Жумабаева",
        "М. Жұмабаев ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "594200000",
        "Есильский район",
        "Есиль ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "594600000",
        "Жамбылский район",
        "Жамбыл ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "595000000",
        "Кызылжарский район",
        "Қызылжар ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "595200000",
        "Мамлютский район",
        "Мамлют ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "595600000",
        "Район Шал акына",
        "Шал ақыны ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "595800000",
        "Аккайынский район",
        "Аққайың ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "596000000",
        "Тайыншинский район",
        "Тайыншин ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "596200000",
        "Тимирязевский район",
        "Тимирязев ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "596400000",
        "Уалихановский район",
        "Уәлиханов ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
    (
        "596600000",
        "Район им.Габита Мусрепова",
        "Ғабит Мусрепов ауданы",
        "rayon",
        "Район",
        "590000000",
        2,
    ),
]

# Сопоставление кодов КАТО с кодами regions в DSS
# Ключ — код DSS (column `code` в таблице regions), значение — код КАТО
REGION_CODE_TO_KATO: dict[str, str] = {
    "SKO":        "590000000",
    "SKO-PETRO":  "591000000",
    "SKO-AIYR":   "593200000",
    "SKO-AKZHAR": "593400000",
    "SKO-ZHUMB":  "593600000",
    "SKO-ESIL":   "594200000",
    "SKO-ZHAMB":  "594600000",
    "SKO-KYZYL":  "595000000",
    "SKO-MAMLY":  "595200000",
    "SKO-SHALA":  "595600000",
    "SKO-AKKAY":  "595800000",
    "SKO-TAYIN":  "596000000",
    "SKO-TIMUR":  "596200000",
    "SKO-UALI":   "596400000",
    "SKO-MUSREP": "596600000",
}


async def seed():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # 1. Вставляем записи КАТО (upsert)
        print("Inserting KATO records...")
        for row in KATO_SKO:
            kato_code, name_ru, name_kz, type_code, type_name_ru, parent_code, level = row
            await session.execute(
                text("""
                    INSERT INTO kato_reference
                        (kato_code, name_ru, name_kz, type_code, type_name_ru, parent_code, level, is_active)
                    VALUES
                        (:kato_code, :name_ru, :name_kz, :type_code, :type_name_ru, :parent_code, :level, TRUE)
                    ON CONFLICT (kato_code) DO UPDATE SET
                        name_ru      = EXCLUDED.name_ru,
                        name_kz      = EXCLUDED.name_kz,
                        type_code    = EXCLUDED.type_code,
                        type_name_ru = EXCLUDED.type_name_ru,
                        parent_code  = EXCLUDED.parent_code,
                        level        = EXCLUDED.level
                """),
                {
                    "kato_code": kato_code,
                    "name_ru": name_ru,
                    "name_kz": name_kz,
                    "type_code": type_code,
                    "type_name_ru": type_name_ru,
                    "parent_code": parent_code,
                    "level": level,
                },
            )
            print(f"  ✓ {kato_code}  {name_ru}")

        await session.commit()

        # 2. Связываем regions.kato_code с kato_reference
        print("\nLinking regions to KATO...")
        for region_code, kato_code in REGION_CODE_TO_KATO.items():
            result = await session.execute(
                text("UPDATE regions SET kato_code = :kato WHERE code = :code RETURNING name"),
                {"kato": kato_code, "code": region_code},
            )
            row = result.fetchone()
            if row:
                print(f"  ✓ regions.code={region_code} → kato_code={kato_code}  ({row[0]})")
            else:
                print(f"  ⚠ regions.code={region_code} не найден в БД")

        # 3. Связываем kato_reference.region_id обратно
        print("\nLinking KATO back to region UUIDs...")
        await session.execute(
            text("""
                UPDATE kato_reference k
                SET region_id = r.id
                FROM regions r
                WHERE r.kato_code = k.kato_code
                  AND k.region_id IS DISTINCT FROM r.id
            """)
        )

        await session.commit()

        # 4. Привязка ролей к регионам через КАТО
        print("\nLinking user_roles to regions via KATO...")
        # admin, akim_obl, exp_dir, exp_ver → SKO (область)
        r1 = await session.execute(
            text("""
                UPDATE user_roles ur
                SET region_id = (SELECT id FROM regions WHERE code = 'SKO')
                FROM roles r
                WHERE ur.role_id = r.id
                  AND ur.region_id IS NULL
                  AND r.name IN ('admin', 'akim_obl', 'exp_dir', 'exp_ver')
            """)
        )
        print(f"  ✓ Привязано к SKO: {r1.rowcount} записей")

        await session.commit()

        # 5. Итоговая сводка
        count = await session.execute(text("SELECT COUNT(*) FROM kato_reference"))
        linked = await session.execute(
            text("SELECT COUNT(*) FROM kato_reference WHERE region_id IS NOT NULL")
        )
        print(f"\nДобавлено записей КАТО:  {count.scalar()}")
        print(f"Связано с regions:        {linked.scalar()}")
        print("\nГотово.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
