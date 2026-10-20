# Повторный анализ: алгоритм выбора варианта ЛП для устойчивого развития (06.10.2026)

Отчёт: `docs/Отчёт_выбор_варианта_ЛП_для_УР_2026-10-06.md`.

Данные — выгрузка утверждённых годовых значений с прода (направления «Сельское хозяйство»,
«Климат», «Земельные ресурсы», ВП СХ, численность населения):

    ssh … 'docker exec dss-postgres psql -U dss_user -d dss_db -c "\copy (SELECT i.name AS indicator, u.name AS unit,
      r.name AS region, r.level, extract(year from v.period_start)::int AS year, v.value, s.name AS source
      FROM indicator_values v … WHERE v.period_type='year' AND v.validation_status='approved' AND …) TO STDOUT WITH CSV HEADER"' > panel.csv

Скрипты читают `/w/panel.csv`, `/w/lp_compare_crosstab.json`, `/w/lp_compare.json` и запускаются в
контейнере ai-service (нужны pandas, scipy и модуль `app.services.landscape_impact.ur_official`):

    docker cp . dss-ai-service:/w && docker exec dss-ai-service python /w/a2_yield_climate.py

| Скрипт | Что делает |
|---|---|
| a1_reconcile.py | сверка урожайности, сбора и посевной площади зерновых по районам |
| a2_yield_climate.py | панель «урожайность ~ климат метеостанций»: эффекты района и тренд / район и год, перестановочный тест, jackknife, прогноз на отложенных годах |
| a3_chain_inputs.py | пространственные проверки (урожайность и её вариабельность ~ норма ГТК, ~ пригодность), отдача 1 га посевов |
| a4_chain.py | цепочка «вариант → пашня по районам → продукция СХ → официальный индекс УР», точка безубыточности «цена природы» |
| a5_checks.py | климат ~ стоимость продукции с 1 га, ВП СХ района; эффект климатических прогнозов 2030; сверка ВП СХ районы/область |
