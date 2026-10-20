# ЗБ-УР на районной панели (11.10.2026)

Перепроверка алгоритма ЗБ-УР на партии `DSS_import_nedostayushchie_dannye_2026-10-09.xlsx`.
Итоги — в разделе 14 `docs/Алгоритм_ЗБ_УР_2026-09-09.md`.

Данные — выгрузка утверждённых годовых значений с прода в `/w/panel.csv`
(столбцы indicator, direction, unit, region, level, year, value):

    ssh … "docker exec dss-postgres psql -U dss_user -d dss_db -c \"\copy (SELECT i.name AS indicator,
      d.code AS direction, u.name AS unit, r.name AS region, r.level,
      extract(year from v.period_start)::int AS year, v.value FROM indicator_values v
      JOIN indicators i ON i.id=v.indicator_id JOIN directions d ON d.id=i.direction_id
      LEFT JOIN units u ON u.id=i.unit_id JOIN regions r ON r.id=v.region_id
      WHERE v.period_type='year' AND v.validation_status='approved') TO STDOUT WITH CSV HEADER\"" > panel.csv

Запуск в контейнере ai-service: `docker cp . dss-ai-service:/w && docker exec dss-ai-service python /w/p1_panel_links.py`.

| Скрипт | Что делает |
|---|---|
| p1_panel_links.py | панель «районы × годы», эффекты района и года, кластерные SE, перестановки, jackknife: ВП СХ ~ посевы, урожайность ~ площадь зерновых + ГТК, сбор ~ площадь, урожайность ~ ГТК |
| p2_variant_district_ranges.py | сдвиг пашни V1–V3 по районам против размаха посевной площади района × 1,5 (`lp_variant_presets.json` нужно положить в `/w`) |

Справочник модуля строится отдельно: `ai-service/scripts/build_district_panel.py --from-csv /w/panel.csv --write`.
