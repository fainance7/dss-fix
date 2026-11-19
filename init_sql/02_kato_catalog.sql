-- Эталонный справочник КАТО районов СКО (Задача 2.1)
CREATE TABLE IF NOT EXISTS kato_catalog (
    kato_code  BIGINT PRIMARY KEY,
    name_ru    TEXT NOT NULL,
    name_kz    TEXT,
    level      TEXT,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO kato_catalog (kato_code, name_ru, name_kz, level)
SELECT
    kato_code,
    name_ru,
    NULLIF(name_kz, ''),
    CASE
        WHEN kato_code = 590000000 THEN 'region'
        WHEN kato_code = 591000000 THEN 'city'
        ELSE 'district'
    END AS level
FROM kato_cache
WHERE kato_code::text LIKE '59%00000'
ON CONFLICT (kato_code) DO NOTHING;
