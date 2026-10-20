# Эмпирическая цепочка «земельный баланс → показатели → индекс УР → выбор варианта ЛП» (07–08.10.2026)

Отчёт: `docs/Отчёт_ЗБ_УР_эмпирические_связи_и_применимость_2026-10-07.md`.

Данные — выгрузка всех утверждённых годовых значений области и районов с прода (только чтение):

    ssh … 'docker exec dss-postgres psql -U dss_user -d dss_db -c "\copy (SELECT i.name AS indicator, u.name AS unit,
      d.name AS direction, r.name AS region, r.level, extract(year from v.period_start)::int AS year, v.value
      FROM indicator_values v JOIN indicators i ON i.id=v.indicator_id JOIN regions r ON r.id=v.region_id
      LEFT JOIN units u ON u.id=i.unit_id LEFT JOIN directions d ON d.id=i.direction_id
      WHERE v.period_type='year' AND v.validation_status='approved' AND r.level IN (1,2)) TO STDOUT WITH CSV HEADER"' > panel.csv

Индекс УР берётся из справочника `ai-service/app/services/landscape_impact/reference/ur_official.json`
(воспроизводит книгу заказчика с MAE 0,000). Запуск — в контейнере ai-service:

    docker exec dss-ai-service mkdir -p /wlb
    docker cp panel.csv dss-ai-service:/wlb/ && docker cp scripts/research/lb_ur_link/. dss-ai-service:/wlb/
    docker cp "docs/отчет НИР_оценка УР СКО_испр.docx" dss-ai-service:/wlb/nir.docx
    docker exec -w /wlb dss-ai-service python -W ignore b1_oblast_time.py   # и т. д. по порядку

| Скрипт | Что делает |
|---|---|
| common.py | загрузка панели, ключи территорий, приведённые значения 14 индикаторов, пересчёт индекса с подменой значений (п. 12 методики), перестановочные тесты, двусторонние ФЭ |
| b0_methodology_check.py | сверка таблиц 3 (границы) и 4 (веса) новой редакции отчёта НИР со справочником |
| b1_oblast_time.py | область, первые разности: 8 земельных рядов × показатели (2004–2023 и 2014–2023), FDR, jackknife, MASE, подокна |
| b2_district_cross.py | срез 13 районов: доли угодий × уровни 14 индикаторов и индексов; контроль масштаба, нормы ГТК, доли казахов |
| b3_district_panel.py | районная панель 2014–2024: посевы и поголовье (прокси использования земель) × 14 индикаторов, двусторонние ФЭ, кластерный бутстрэп |
| b4_variants.py | применение к V1–V3 (`lp_variant_presets.json`): канал конструкции (ВП СХ), связи [С], «наивный» срез; Δ индекса и сравнение с MDE |
| b5_checks_summary.py | сверка с книгой, калибровка теста на нулевых данных, сводка |

Порядок: b0 → b1 → b2 → b3 (≈2 мин) → b4 (читает CSV b2, b3) → b5.
