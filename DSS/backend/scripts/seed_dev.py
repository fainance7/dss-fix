#!/usr/bin/env python3
"""
Seed-скрипт для dev-базы данных DSS.
Наполняет БД тестовыми данными для СКО (Северо-Казахстанская область):
  - Регионы (область + 11 районов + г. Петропавловск)
  - Источники данных и категории
  - 6 слоёв данных (ВРП, Население, Занятость, Инвестиции, Зарплата, Посевные площади)
  - Исторические данные 2015–2024 для каждого слоя × каждого района
  - Тестовые пользователи для всех 5 ролей
"""
import asyncio
import json
import random
import uuid
from datetime import date, datetime, timezone

import asyncpg
from passlib.context import CryptContext

import os
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"postgresql://dss_user:{os.environ.get('POSTGRES_PASSWORD', 'dss_local_password')}@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}/{os.environ.get('POSTGRES_DB', 'dss_db')}"
)
ADMIN_USER_ID = "36f02ac7-c768-4de8-93ff-19c163a0c0a5"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Регионы СКО
# ---------------------------------------------------------------------------
# Уровень 1 — область
SKO_OBLAST = {
    "code": "SKO",
    "name": "Северо-Казахстанская область",
    "level": 1,
    "lon": 69.14, "lat": 54.0,
}

# Уровень 2 — районы и г. Петропавловск
SKO_DISTRICTS = [
    {"code": "SKO-PETRO",  "name": "г. Петропавловск",               "lon": 69.14, "lat": 54.87, "pop_base": 240_000, "gdp_share": 0.38},
    {"code": "SKO-KYZYL",  "name": "Кызылжарский район",             "lon": 69.50, "lat": 54.50, "pop_base":  60_000, "gdp_share": 0.12},
    {"code": "SKO-AIYR",   "name": "Айыртауский район",              "lon": 68.50, "lat": 53.60, "pop_base":  45_000, "gdp_share": 0.07},
    {"code": "SKO-ZHUMB",  "name": "Район Магжана Жумабаева", "lon": 70.70, "lat": 54.90, "pop_base":  45_000, "gdp_share": 0.08},
    {"code": "SKO-TIMUR",  "name": "Тимирязевский район",            "lon": 71.50, "lat": 54.10, "pop_base":  40_000, "gdp_share": 0.06},
    {"code": "SKO-MAMLY",  "name": "Мамлютский район",               "lon": 68.80, "lat": 54.50, "pop_base":  35_000, "gdp_share": 0.06},
    {"code": "SKO-AKKAY",  "name": "Аккайынский район",              "lon": 71.00, "lat": 53.60, "pop_base":  30_000, "gdp_share": 0.05},
    {"code": "SKO-ZHAMB",  "name": "Жамбылский район",               "lon": 70.00, "lat": 54.50, "pop_base":  28_000, "gdp_share": 0.05},
    {"code": "SKO-SHALA",  "name": "Район Шал акына",               "lon": 69.00, "lat": 53.70, "pop_base":  30_000, "gdp_share": 0.05},
    {"code": "SKO-AKZHAR", "name": "Акжарский район",                "lon": 67.50, "lat": 54.20, "pop_base":  25_000, "gdp_share": 0.04},
    {"code": "SKO-UALI",   "name": "Уалихановский район",            "lon": 70.20, "lat": 53.20, "pop_base":  20_000, "gdp_share": 0.04},
    {"code": "SKO-ESIL",   "name": "Есильский район",                "lon": 66.40, "lat": 51.96, "pop_base":  25_000, "gdp_share": 0.04},
    {"code": "SKO-TAYIN",  "name": "Тайыншинский район",             "lon": 69.75, "lat": 53.85, "pop_base":  35_000, "gdp_share": 0.05},
    {"code": "SKO-MUSREP", "name": "Район им.Габита Мусрепова",      "lon": 70.80, "lat": 53.50, "pop_base":  22_000, "gdp_share": 0.04},
]

# ---------------------------------------------------------------------------
# Тестовые пользователи
# ---------------------------------------------------------------------------
TEST_USERS = [
    {"username": "exp_ver",        "email": "exp_ver@dss.kz",        "full_name": "Верификатор Ибрагимов А.",     "role": "exp_ver",  "district": None},
    {"username": "exp_dir",        "email": "exp_dir@dss.kz",        "full_name": "Директор Сейткали М.",         "role": "exp_dir",  "district": None},
    {"username": "akim_obl",       "email": "akim_obl@dss.kz",       "full_name": "Аким СКО Мухамедов К.",        "role": "akim_obl", "district": None},
    {"username": "akim_petro",     "email": "akim_petro@dss.kz",     "full_name": "Аким г. Петропавловск",        "role": "akim_ray", "district": "SKO-PETRO"},
    {"username": "akim_kyzyl",     "email": "akim_kyzyl@dss.kz",     "full_name": "Аким Кызылжарского р-на",      "role": "akim_ray", "district": "SKO-KYZYL"},
    {"username": "akim_aiyr",      "email": "akim_aiyr@dss.kz",      "full_name": "Аким Айыртауского р-на",       "role": "akim_ray", "district": "SKO-AIYR"},
    {"username": "akim_zhumb",     "email": "akim_zhumb@dss.kz",     "full_name": "Аким р-на им. М. Жумабаева",   "role": "akim_ray", "district": "SKO-ZHUMB"},
]
TEST_PASSWORD = "Test2026"

# ---------------------------------------------------------------------------
# Базовые значения ВРП СКО по годам (млн тг, вся область)
# ---------------------------------------------------------------------------
OBLAST_GDP = {
    2015: 1_420_000, 2016: 1_580_000, 2017: 1_780_000, 2018: 1_950_000,
    2019: 2_050_000, 2020: 1_980_000, 2021: 2_200_000, 2022: 2_480_000,
    2023: 2_680_000, 2024: 2_850_000,
}
OBLAST_POP = {
    2015: 575_000, 2016: 570_000, 2017: 567_000, 2018: 563_000,
    2019: 558_000, 2020: 555_000, 2021: 553_000, 2022: 550_000,
    2023: 549_000, 2024: 548_000,
}
OBLAST_EMPLOY = {
    2015: 91.2, 2016: 91.5, 2017: 92.0, 2018: 92.4, 2019: 92.8,
    2020: 91.3, 2021: 92.6, 2022: 93.1, 2023: 93.6, 2024: 94.5,
}
OBLAST_INVEST = {
    2015: 95_000, 2016: 105_000, 2017: 115_000, 2018: 130_000,
    2019: 145_000, 2020: 132_000, 2021: 148_000, 2022: 160_000,
    2023: 172_000, 2024: 180_000,
}
OBLAST_SALARY = {
    2015: 118_000, 2016: 128_000, 2017: 142_000, 2018: 158_000,
    2019: 172_000, 2020: 180_000, 2021: 198_000, 2022: 225_000,
    2023: 255_000, 2024: 280_000,
}
OBLAST_SOWING = {
    2015: 3_950_000, 2016: 4_020_000, 2017: 4_100_000, 2018: 4_050_000,
    2019: 4_120_000, 2020: 4_180_000, 2021: 4_230_000, 2022: 4_250_000,
    2023: 4_270_000, 2024: 4_300_000,
}


def jitter(value: float, pct: float = 0.04) -> float:
    """Добавляет случайный шум ±pct% к значению."""
    return value * (1 + random.uniform(-pct, pct))


async def seed(conn: asyncpg.Connection) -> None:
    random.seed(42)

    # -----------------------------------------------------------------------
    # 1. Регионы
    # -----------------------------------------------------------------------
    print("📍 Создание регионов...")

    oblast_id = await conn.fetchval("""
        INSERT INTO regions (id, code, name, parent_id, level)
        VALUES ($1, $2, $3, NULL, 1)
        ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
    """, uuid.uuid4(), SKO_OBLAST["code"], SKO_OBLAST["name"])

    district_ids: dict[str, uuid.UUID] = {}
    for d in SKO_DISTRICTS:
        did = await conn.fetchval("""
            INSERT INTO regions (id, code, name, parent_id, level)
            VALUES ($1, $2, $3, $4, 2)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """, uuid.uuid4(), d["code"], d["name"], oblast_id)
        district_ids[d["code"]] = did

    print(f"   ✓ Область + {len(district_ids)} районов/городов")

    # -----------------------------------------------------------------------
    # 2. Источник данных
    # -----------------------------------------------------------------------
    print("🗄️  Создание источника данных...")

    source_id = await conn.fetchval("""
        INSERT INTO data_sources (id, name, description, url, is_active)
        VALUES ($1, $2, $3, $4, true)
        ON CONFLICT DO NOTHING
        RETURNING id
    """,
        uuid.uuid4(),
        "Бюро национальной статистики РК",
        "Официальная статистика по регионам Казахстана",
        "https://stat.gov.kz",
    )

    if source_id is None:
        source_id = await conn.fetchval(
            "SELECT id FROM data_sources WHERE name = $1 LIMIT 1",
            "Бюро национальной статистики РК",
        )
    print(f"   ✓ Источник: {source_id}")

    # -----------------------------------------------------------------------
    # 3. Категории данных
    # -----------------------------------------------------------------------
    print("📂 Создание категорий...")

    categories = {
        "economy":   ("Экономика",         "Макроэкономические показатели региона"),
        "demograph": ("Демография",         "Численность и состав населения"),
        "employ":    ("Занятость",          "Рынок труда и занятость населения"),
        "agro":      ("Сельское хозяйство", "Аграрный сектор и землепользование"),
    }
    cat_ids: dict[str, uuid.UUID] = {}
    for key, (name, desc) in categories.items():
        cid = await conn.fetchval("""
            INSERT INTO data_categories (id, name, description)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            RETURNING id
        """, uuid.uuid4(), name, desc)
        if cid is None:
            cid = await conn.fetchval("SELECT id FROM data_categories WHERE name = $1", name)
        cat_ids[key] = cid
    print(f"   ✓ {len(cat_ids)} категорий")

    # -----------------------------------------------------------------------
    # 4. Слои данных
    # -----------------------------------------------------------------------
    print("📊 Создание слоёв данных...")

    # -----------------------------------------------------------------------
    # 3.5. Направления (directions) — seed if not exist
    # -----------------------------------------------------------------------
    print("🧭 Привязка направлений...")
    dir_ids: dict[str, uuid.UUID] = {}
    for row in await conn.fetch("SELECT id, code FROM directions"):
        dir_ids[row["code"]] = row["id"]
    print(f"   ✓ {len(dir_ids)} направлений найдено")

    # Маппинг слоёв к направлениям
    LAYER_DIRECTION_MAP = {
        "ВРП региона":                   "economy",
        "Численность населения":         "social_demog",
        "Уровень занятости":             "labor",
        "Инвестиции в основной капитал": "economy",
        "Среднемесячная зарплата":       "labor",
        "Посевные площади":              "economy",
    }

    admin_uid = uuid.UUID(ADMIN_USER_ID)
    layers_def = [
        ("ВРП региона",                    "economy",   "Валовый региональный продукт (ВРП) в млн тг",                   "млн тг"),
        ("Численность населения",          "demograph", "Среднегодовая численность постоянного населения",               "чел."),
        ("Уровень занятости",              "employ",    "Доля занятых в общей численности экономически активного нас.",  "%"),
        ("Инвестиции в основной капитал",  "economy",   "Объём инвестиций в основной капитал в млн тг",                  "млн тг"),
        ("Среднемесячная зарплата",        "economy",   "Номинальная начисленная среднемесячная заработная плата",        "тг"),
        ("Посевные площади",               "agro",      "Площадь посевов сельскохозяйственных культур",                  "га"),
    ]

    layer_ids: dict[str, uuid.UUID] = {}
    for name, cat_key, desc, unit in layers_def:
        direction_code = LAYER_DIRECTION_MAP.get(name, "economy")
        direction_id = dir_ids.get(direction_code)
        lid = await conn.fetchval("""
            INSERT INTO data_layers
              (id, name, description, category_id, source_id, direction_id, version,
               spatial_resolution, update_frequency, owner,
               valid_from, status, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,'1.0','district','annual',
                    'Бюро нацстатистики РК','2015-01-01','active',$7)
            ON CONFLICT DO NOTHING
            RETURNING id
        """,
            uuid.uuid4(), name, desc,
            cat_ids[cat_key], source_id, direction_id, admin_uid,
        )
        if lid is None:
            lid = await conn.fetchval("SELECT id FROM data_layers WHERE name = $1", name)
        layer_ids[name] = lid

    print(f"   ✓ {len(layer_ids)} слоёв данных")

    # -----------------------------------------------------------------------
    # 5. Исторические данные 2015–2024
    # -----------------------------------------------------------------------
    print("📈 Генерация исторических данных 2015–2024...")

    oblast_data = {
        "ВРП региона":                   OBLAST_GDP,
        "Численность населения":         OBLAST_POP,
        "Уровень занятости":             OBLAST_EMPLOY,
        "Инвестиции в основной капитал": OBLAST_INVEST,
        "Среднемесячная зарплата":       OBLAST_SALARY,
        "Посевные площади":              OBLAST_SOWING,
    }

    # Словарь с долями по районам для каждого слоя
    def district_share(district: dict, layer_name: str, oblast_val: float, year: int) -> float:
        """Вычисляет значение показателя для района."""
        if layer_name == "ВРП региона":
            return jitter(oblast_val * district["gdp_share"], 0.05)
        if layer_name == "Численность населения":
            base = district["pop_base"]
            # Петропавловск стабилен, сельские убывают ~0.4%/год
            years_from_2024 = year - 2024
            if district["code"] == "SKO-PETRO":
                factor = 1.0 + years_from_2024 * 0.003
            else:
                factor = 1.0 + years_from_2024 * (-0.004)
            return jitter(base * factor, 0.02)
        if layer_name == "Уровень занятости":
            # Небольшая вариация по районам от областного
            return jitter(oblast_val + random.uniform(-1.5, 1.5), 0.01)
        if layer_name == "Инвестиции в основной капитал":
            # Петропавловск получает ~40%, остальные пропорционально gdp_share
            return jitter(oblast_val * district["gdp_share"] * 1.05, 0.08)
        if layer_name == "Среднемесячная зарплата":
            # Петропавловск на ~15% выше, сельские на 10-20% ниже
            if district["code"] == "SKO-PETRO":
                return jitter(oblast_val * 1.15, 0.03)
            else:
                return jitter(oblast_val * random.uniform(0.80, 0.92), 0.02)
        if layer_name == "Посевные площади":
            # Пашня только в районах, не в городе
            if district["code"] == "SKO-PETRO":
                return jitter(oblast_val * 0.01, 0.05)
            # Пропорционально, кроме горожан
            rural_share = district["gdp_share"] / (1 - 0.38) * 0.99
            return jitter(oblast_val * rural_share, 0.06)
        return 0.0

    units_map = {
        "ВРП региона": "млн тг",
        "Численность населения": "чел.",
        "Уровень занятости": "%",
        "Инвестиции в основной капитал": "млн тг",
        "Среднемесячная зарплата": "тг",
        "Посевные площади": "га",
    }

    rows = []
    for layer_name, year_vals in oblast_data.items():
        lid = layer_ids[layer_name]
        unit = units_map[layer_name]

        for year, oblast_val in year_vals.items():
            data_date = date(year, 12, 31)

            # Запись для всей области
            rows.append((
                uuid.uuid4(), lid, oblast_id, data_date,
                f"POINT({SKO_OBLAST['lon']} {SKO_OBLAST['lat']})",
                json.dumps({"value": round(oblast_val, 2), "unit": unit, "year": year}),
            ))

            # Записи для каждого района
            for d in SKO_DISTRICTS:
                did = district_ids[d["code"]]
                val = district_share(d, layer_name, oblast_val, year)
                rows.append((
                    uuid.uuid4(), lid, did, data_date,
                    f"POINT({d['lon']} {d['lat']})",
                    json.dumps({"value": round(val, 2), "unit": unit, "year": year}),
                ))

    # Batch insert
    await conn.executemany("""
        INSERT INTO spatial_data
          (id, layer_id, region_id, data_date, geom, properties, validation_status)
        VALUES ($1, $2, $3, $4, ST_GeomFromText($5, 4326), $6, 'approved')
        ON CONFLICT DO NOTHING
    """, rows)

    total_regions = 1 + len(SKO_DISTRICTS)  # область + районы
    print(f"   ✓ {len(rows)} записей ({len(oblast_data)} слоя × {len(year_vals)} лет × {total_regions} регионов)")

    # -----------------------------------------------------------------------
    # 6. Тестовые пользователи
    # -----------------------------------------------------------------------
    print("👥 Создание тестовых пользователей...")

    password_hash = pwd_context.hash(TEST_PASSWORD)

    role_ids: dict[str, uuid.UUID] = {}
    for role_name in ("admin", "exp_ver", "exp_dir", "akim_obl", "akim_ray"):
        rid = await conn.fetchval("SELECT id FROM roles WHERE name = $1", role_name)
        if rid:
            role_ids[role_name] = rid

    for user_def in TEST_USERS:
        uid = await conn.fetchval("""
            INSERT INTO users (id, username, email, password_hash, is_active)
            VALUES ($1, $2, $3, $4, true)
            ON CONFLICT (username) DO UPDATE SET
                email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash
            RETURNING id
        """,
            uuid.uuid4(),
            user_def["username"],
            user_def["email"],
            password_hash,
        )

        role_id = role_ids.get(user_def["role"])
        if not role_id:
            print(f"   ⚠ Роль {user_def['role']} не найдена")
            continue

        region_id = None
        if user_def["district"]:
            region_id = district_ids.get(user_def["district"])

        # Назначаем роль
        await conn.execute("""
            INSERT INTO user_roles (user_id, role_id, region_id)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
        """, uid, role_id, region_id)

        region_label = user_def["district"] or "вся область"
        print(f"   ✓ {user_def['username']:15s} [{user_def['role']:8s}] — {region_label}")

    # -----------------------------------------------------------------------
    # 6.5. Назначение направлений для exp_dir (пример ограничения)
    # -----------------------------------------------------------------------
    print("🧭 Назначение направлений для exp_dir...")
    exp_dir_uid = await conn.fetchval("SELECT id FROM users WHERE username = 'exp_dir'")
    if exp_dir_uid and dir_ids:
        # Ограничим exp_dir тремя направлениями как демо
        demo_dirs = ["economy", "labor", "social_demog"]
        for dcode in demo_dirs:
            did = dir_ids.get(dcode)
            if did:
                await conn.execute("""
                    INSERT INTO user_directions (user_id, direction_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                """, exp_dir_uid, did)
        print(f"   ✓ exp_dir ограничен: {', '.join(demo_dirs)}")

    # -----------------------------------------------------------------------
    # 7. Итог
    # -----------------------------------------------------------------------
    region_count = await conn.fetchval("SELECT COUNT(*) FROM regions")
    layer_count  = await conn.fetchval("SELECT COUNT(*) FROM data_layers")
    data_count   = await conn.fetchval("SELECT COUNT(*) FROM spatial_data")
    user_count   = await conn.fetchval("SELECT COUNT(*) FROM users")

    print()
    print("✅ Seed завершён:")
    print(f"   Регионов:              {region_count}")
    print(f"   Слоёв данных:          {layer_count}")
    print(f"   Записей spatial_data:  {data_count}")
    print(f"   Пользователей:         {user_count}")
    print()
    print("🔑 Учётные данные:")
    print(f"   admin      / CHANGE_ME  (уже существовал)")
    for u in TEST_USERS:
        print(f"   {u['username']:15s} / {TEST_PASSWORD}  [{u['role']}]")


async def main() -> None:
    print("🚀 DSS Dev Seed — СКО")
    print("=" * 40)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
