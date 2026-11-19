import os
"""
dag_extract_taldau.py

Реализует Задачу 3.1 плана:
  - index_ids теперь принимается через conf при триггере (не захардкоженный
    список TALDAU_INDICATORS) — фронт передаёт массив ID из динамического
    селектора (см. GET /api/taldau-indicators).
  - split_by_kato=True заставляет DAG запрашивать данные ОТДЕЛЬНО для каждого
    из 15 районов СКО (terminalId района), а не только для области целиком
    (terminalId=590000000). Без этого флага поведение как раньше — один
    запрос на всю область.

conf, ожидаемый при триггере:
{
    "index_ids": [702840, 2929761, 704207],   # обязательно, список int
    "split_by_kato": false,                    # опционально, default false
    "indicator_names": {                       # опционально — имена для
        "702840": "Занятое население..."       # человекочитаемого лога;
    }                                           # если не передано, для
}                                               # неизвестных ID используется
                                                  # "Показатель {id}"

Обратная совместимость: если conf пуст или index_ids не передан, DAG
использует старый захардкоженный список TALDAU_INDICATORS — это защищает
от поломки уже существующих внешних вызовов (например, если где-то на
фронте/в другом DAG этот DAG триггерится без conf).
"""
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.api.common.trigger_dag import trigger_dag

DB_CONN         = os.environ.get("REPORTS_DB_URL", "postgresql+psycopg2://aldis:CHANGE_ME@postgres/reports_db")
TALDAU_BASE_URL = "https://taldau.stat.gov.kz/ru/NewIndex/GetIndexTreeData"
# Разрезы (p_dicIds) для показателей. Занятость 702841 = "67,749,576".
# Для остальных узнаются через DevTools; по умолчанию берётся набор занятости.
DIC_IDS_MAP = {
    # index_id: (p_dicIds, p_terms) — свои для каждого показателя, пойманы через DevTools
    702841:  ("67,749,576",     "741880,741917,741935"),         # Уровень занятости
    704203:  ("67,749,206,576", "741880,741917,567124,741935"),  # Охват начальным образованием
    704207:  ("67,749,206,576", "741880,741917,567123,741935"),  # Валовый охват средним образованием
    18951814: ("67,206,576",     "741880,567123,741935"),          # Валовый охват высшим образованием
    18951815: ("67,749,206,576", "741880,741917,567123,741935"),  # Охват дошкольным воспитанием
    702840:  ("67,749,576,1773,1793,3028", "741880,741917,741935,3805694,4197331,741885", "23"),  # Занятое население по группам ВЭД (measure=23 Человек)
    2929761: ("67,327", "741880,3764106", "23"),  # Международная миграция по уровню образования (measure=23 Человек)
    2929761: ("67,327", "741880,3764106", "23"),  # Международная миграция по уровню образования (measure=23 Человек)
    702840:  ("67,749,576,1773,1793,3028", "741880,741917,741935,3805694,4197331,741885", "23"),  # Занятое население по группам ВЭД (measure=23 Человек)
    702944:  ("67,749,576,1773,1793", "741880,741917,741935,3805694,4197331"),  # Уровень безработицы (measure=7, дефолт, DevTools 22.07.2026)
    702184:  ("68,1214,90,59,230,2984", "741880,741335,741927,741907,741906,17901749", "1"),  # Доходы от перевозки пассажиров (measure=1 Млн тенге, DevTools 23.07.2026)
    703834:  ("67,749,576,1433", "741880,741917,741935,3699122", "23"),  # Среднегодовая численность населения (measure=23 Человек, DevTools 23.07.2026)
    703839:  ("67,749,576", "741880,741917,741935", "23"),  # Число родившихся (живыми) (measure=23 Человек, DevTools 23.07.2026)
    77703362: ("67,749,576,1433", "741880,741917,741935,3699122", "23"),  # Численность населения на конец года (Talдау: "Численность и состав населения на конец периода"), DevTools 23.07.2026
    703857:  ("67,749,576", "741880,741917,741935", "23"),  # Естественный прирост населения (measure=23 Человек), DevTools 23.07.2026
    702972:  ("68,859,776,2813,576", "741880,741885,741917,3629946,741935", "1"),  # Среднемесячная номинальная заработная плата одного работника (measure=1 Тенге), DevTools 23.07.2026
    702372:  ("68,64", "741880,741917", "23"),  # Число почтальонов (measure=23 Человек), автоматически 23.07.2026
    701758:  ("68,776,663", "741880,741917,533867", "5"),  # Число насосных станций (measure=5 Единица), автоматически 23.07.2026
    2972339: ("68,1214", "741880,448527", "5"),  # Число международных сообщений (measure=5 Единица), автоматически 23.07.2026
    701647:  ("68,4303", "741880,3079117", "5"),  # Число предприятий и производств (measure=5 Единица), автоматически 23.07.2026
    703847:  ("67,749,576", "741880,741917,741935", "23"),  # Число умерших (measure=23 Человек), автоматически 23.07.2026
    702935:  ("67,749,576,1773,1793,3028", "741880,741917,741935,3805694,4197331,741885", "23"),  # Самостоятельно занятые работники (measure=23 Человек), автоматически 23.07.2026
    703831:  ("67,749,576,1433", "741880,741917,741935,3699122", "23"),  # Численность населения на начало периода (measure=23 Человек), автоматически 23.07.2026
    703076:  ("67,848,2753", "741880,2695730,4772381", "7"),  # Индекс потребительских цен (measure=7 %) - ЗАХВАЧЕН с period_id=5 (квартал), у нас код шлёт 7 (год), проверить результат, автоматически 23.07.2026
    703872:  ("67,749,576,1433", "741880,741917,741935,3699122", "23"),  # Сальдо миграции по всем потокам (measure=23 Человек), автоматически 23.07.2026
    2929752: ("67,749,76,576", "741880,741917,39360,741935", "23"),  # Внешняя миграция по всем потокам - прибытие (measure=23 Человек), автоматически 23.07.2026
    2929753: ("67,749,76,576", "741880,741917,39360,741935", "23"),  # Внешняя миграция по всем потокам - выбытие (measure=23 Человек), автоматически 23.07.2026
    2929754: ("67,749,576,1433", "741880,741917,741935,3699122", "23"),  # Сальдо внешней миграции по всем потокам (measure=23 Человек), автоматически 23.07.2026
    701592:  ("68,90,4303", "741880,741927,3079117", "1"),  # Объем производства промышленной продукции (measure=1 Тенге), автоматически 23.07.2026
    701323:  ("67,2979", "741880,808123", "748"),  # Общее поголовье КРС в сельхозпредприятиях (measure=748 Голова), автоматически 23.07.2026
    705084:  ("67,1212", "741880,741885", "5"),  # Стационарные источники выбросов, увеличившие выбросы (measure=5 Единица), автоматически 23.07.2026
    702183:  ("68,1214,2258", "741880,741335,4421292", "1"),  # Доходы от перевозки грузов (measure=1 Тенге), автоматически 23.07.2026
    701766:  ("68,1212", "741880,741885", "71"),  # Израсходовано воды на собственные производственные нужды (measure=71 Метр кубический), автоматически 23.07.2026
    703239:  ("68,915,90,524", "741880,741885,741927,679491", "33"),  # Общая площадь земельных участков (measure=33 Метр квадратный), автоматически 23.07.2026
    701885:  ("68,60,71,2987", "741880,741908,741919,18753820", "1"),  # Объем выполненных строительных работ (услуг) (measure=1 Тенге), автоматически 23.07.2026
    19197226: ("68", "741880", "596"),  # Производство электроэнергии в натуральном выражении (measure=596 Ватт-час), автоматически 23.07.2026
    701324:  ("67,2979", "741880,808123", "748"),  # Общее поголовье овец и коз в сельхозпредприятиях (measure=748 Голова), автоматически 23.07.2026
    701361:  ("68,488", "741880,450122", "11"),  # Средний надой молока на одну дойную корову (measure=11 Килограмм), автоматически 23.07.2026
    701245:  ("68,488,3136", "741880,450122,18946612", "33"),  # Посевная площадь сельскохозяйственных культур
    7012571: ("68,4153", "741880,19722801", "33", 701257),  # Посевные зерновые площади (уточнённая)
    7012572: ("68,4153", "741880,19722810", "33", 701257),  # Масличные площади (уточнённая) (measure=33 Метр квадратный), автоматически 23.07.2026
    701449:  ("68,853", "741880,2658135", "33"),  # лесовосстановление (measure=33 Метр квадратный), автоматически 23.07.2026
    702689:  ("67,915,90,59,61,565,681,853", "741880,741885,741927,741907,741926,808204,808076,2658135", "5"),  # Количество мест размещения (measure=5 Единица), автоматически 23.07.2026
    702177:  ("68,1214,59,230,2984", "741880,741335,741907,741906,17901749", "41"),  # Пассажирооборот (measure=41 Пассажиро-километр), автоматически 23.07.2026
    703906:  ("67,64,576", "741880,741917,741935", "154"),  # Ожидаемая продолжительность жизни населения при рождении (measure=154 Месяц - проверить значения!), автоматически 23.07.2026
    701625:  ("68,4303,848", "741880,3079117,2695730", "7"),  # Индексы промышленного производства по промышленной продукции (measure=7 %), автоматически 23.07.2026
    702249:  ("67,59,230", "741880,741907,741906", "5"),  # Количество автобусов, закрепленных за маршрутами (measure=5 Единица), автоматически 23.07.2026
    704447:  ("67", "741880", "1"),  # Среднедушевые номинальные денежные доходы населения (measure=1 Тенге), автоматически 23.07.2026
    704465:  ("67,749,735", "741880,741917,545138", "1"),  # Денежные расходы населения на душу (measure=1 Тенге), автоматически 23.07.2026
    704466:  ("67,749,735", "741880,741917,545139", "1"),  # Потребительские расходы населения на душу (measure=1 Тенге), автоматически 23.07.2026
    702575:  ("67", "741880", "5"),  # Число кинотеатров (measure=5 Единица), автоматически 23.07.2026
    702599:  ("68,59,64,133", "741880,741907,741917,741938", "5"),  # Число музеев (measure=5 Единица), автоматически 23.07.2026
    2709798: ("67,90,59,35,64", "741880,741927,741907,741889,741917", "5"),  # Число библиотек с учетом учебных библиотек (measure=5 Единица), автоматически 23.07.2026
    701263:  ("67,488,3136", "741880,450122,18932638", "33"),  # Используемая площадь теплиц (measure=33 Метр квадратный), автоматически 23.07.2026
    701421:  ("67,488", "741880,450123", "11"),  # Наличие кормов в сельхозформированиях (measure=11 Килограмм) - ЗАХВАЧЕН с period_id=4, проверить результат, автоматически 23.07.2026
    701494:  ("68,116,488", "741880,741920,450122", "1"),  # Затраты на рыболовство и аквакультуру (measure=1 Тенге), автоматически 23.07.2026
    701516:  ("68,853", "741880,2658135", "33"),  # Площадь закрепленных охотничьих угодий (measure=33 Метр квадратный), автоматически 23.07.2026
    702843:  ("67,576", "741880,741935", "23"),  # Занятое население по группе сельское/лесное/рыбное хозяйство (measure=23 Человек), автоматически 23.07.2026
    701436:  ("68,481,853", "741880,474063,2658135", "71"),  # Заготовка древесины необработанной (measure=71 Метр кубический), автоматически 23.07.2026
    702443:  ("68,64", "741880,741917", "7"),  # Процент охвата территории эфирным цифровым телевещанием (measure=7 %), автоматически 23.07.2026
    703245:  ("67,127,563,1200", "741880,808378,808262,2658135", "1"),  # Сумма выданных кредитов (measure=1 Тенге), автоматически 23.07.2026
    701783:  ("68,776", "741880,741917", "71"),  # Передано сточных вод другим канализациям (measure=71 Метр кубический), автоматически 23.07.2026
    701763:  ("68", "741880", "71"),  # Пропущено воды через очистные сооружения (measure=71 Метр кубический), автоматически 23.07.2026
    701238:  ("68,485,488,2945", "741880,808161,450122,18951468", "1"),  # Стоимость 1 центнера реализованной сельскохозяйственной продукции (measure=1 Тенге), автоматически 23.07.2026
    701415:  ("67,479,488", "741880,676233,450122", "1"),  # Расходы на ремонт и технические уходы за сельхозтехникой (measure=1 Тенге), автоматически 23.07.2026
    701752:  ("67,749", "741880,741917", "5"),  # Число отдельных сетей системы водоснабжения (measure=5 Единица), автоматически 23.07.2026
    701952:  ("68,848", "741880,2695730", "7"),  # Индекс физического объема жилых зданий (measure=7 %), автоматически 23.07.2026
    702189:  ("68,1214,59,848", "741880,741335,741907,2695732", "7"),  # Индексы физического объема перевозок пассажиров (measure=7 %), автоматически 23.07.2026
    702243:  ("67,59,230", "741880,741907,741906", "5"),  # Количество автобусных маршрутов (measure=5 Единица), автоматически 23.07.2026
    702407:  ("68,64,573", "741880,741917,808208", "5"),  # Плотность абонентов фиксированного Интернета на 100 человек (measure=5 Единица), автоматически 23.07.2026
    702618:  ("67,1443,90,59,64", "741880,807851,741927,741907,741917", "5"),  # Число спектаклей (представлений) (measure=5 Единица), автоматически 23.07.2026
    702692:  ("67,915,90,59,61,298,478,681,853", "741880,741885,741927,741907,741926,808329,808242,808076,2658135", "23"),  # Количество обслуженных посетителей в местах размещения (measure=23 Человек), автоматически 23.07.2026
    702878:  ("67,749,576", "741880,741917,741935", "23"),  # Занятое население (в трудоспособном возрасте) (measure=23 Человек), автоматически 23.07.2026
    702948:  ("67,749,576", "741880,741917,741935", "23"),  # Безработное население (в трудоспособном возрасте) (measure=23 Человек), автоматически 23.07.2026
    703916:  ("67,749", "741880,741917", "23"),  # Численность детей в дошкольных организациях (measure=23 Человек), автоматически 23.07.2026
}
FILTER_JSON = '[{"property":null,"value":null}]'
SKO_TERM_ID     = 264023  # terminalId всей области — используется, если
                          # split_by_kato=False (поведение как раньше)
SKO_REAL_KATO   = 590000000  # НАСТОЯЩИЙ код КАТО всей области. НЕ равен
                          # SKO_TERM_ID! Для районов term_id и kato_code
                          # совпадают (см. SKO_DISTRICT_TERM_IDS), но для
                          # области Talдау использует свой внутренний ID
                          # (264023), который не существует ни в одном
                          # справочнике КАТО — при записи в БД его нужно
                          # подменять на SKO_REAL_KATO.

# 15 районов СКО — terminalId для каждого совпадает с kato_code (см.
# kato_cache, отфильтрованный по записям уровня "район"/"город областного
# значения": kato_code оканчивается на 5 нулей). Используется только когда
# split_by_kato=True.
SKO_DISTRICT_TERM_IDS = {
    591000000: "Петропавловск Г.А.",
    593200000: "Айыртауский район",
    593400000: "Акжарский район",
    593600000: "Район Магжана Жумабаева",
    594200000: "Есильский район",
    594600000: "Жамбылский район",
    595000000: "Кызылжарский район",
    595200000: "Мамлютский район",
    595600000: "Район Шал акына",
    595800000: "Аккайынский район",
    596000000: "Тайыншинский район",
    596200000: "Тимирязевский район",
    596400000: "Уалихановский район",
    596600000: "Район им.Габита Мусрепова",
}

# Старый захардкоженный список — используется только как fallback, если
# conf не передан (обратная совместимость, см. docstring).
TALDAU_INDICATORS_FALLBACK = [
    (2709380, "ВРП на душу населения"),
    (2709379, "Валовой региональный продукт"),
    (2709382, "Уровень безработицы"),
    (704447,  "Среднедушевые номинальные денежные доходы"),
    (704448,  "Индекс физического объёма промышленного производства"),
    (704449,  "Индекс физического объёма сельскохозяйственной продукции"),
    (704451,  "Среднемесячная заработная плата"),
]

def _resolve_indicators(conf: dict) -> list[tuple[int, str]]:
    """
    Определяет список (index_id, indicator_name) для запроса — либо из
    conf['index_ids'] (+ conf['indicator_names'] для подписей), либо
    fallback на старый захардкоженный список, если conf пуст.
    """
    index_ids = conf.get("index_ids")
    if not index_ids:
        return TALDAU_INDICATORS_FALLBACK

    names = conf.get("indicator_names", {})
    # Если имя не передано явно — берём из базы taldau_indicators_catalog
    # по taldau_index_id (там же правильное direction_code для DSS).
    db_names = {}
    existing_names = {}
    try:
        _eng = create_engine(DB_CONN)
        _cat = pd.read_sql("SELECT taldau_index_id, name_ru FROM taldau_indicators_catalog", _eng)
        db_names = dict(zip(_cat["taldau_index_id"], _cat["name_ru"]))
    except Exception as _e:
        print(f"[warn] не удалось прочитать taldau_indicators_catalog: {_e}")
        _eng = create_engine(DB_CONN)
    # Канонизация по index_id: если этот показатель уже когда-то запрашивался,
    # используем ИМЕННО ТО имя, что уже лежит в raw_taldau (самое раннее по
    # fetched_at), а не то, что передал текущий вызов — иначе разное
    # написание (регистр, пробелы) одного и того же index_id плодит дубли
    # под "разными" именами при сборке (см. docstring модуля).
    try:
        _raw = pd.read_sql(
            "SELECT DISTINCT ON (indicator_code) indicator_code, indicator_name "
            "FROM raw_taldau ORDER BY indicator_code, fetched_at ASC",
            _eng
        )
        existing_names = dict(zip(_raw["indicator_code"].astype(str), _raw["indicator_name"]))
    except Exception as _e:
        print(f"[warn] не удалось прочитать существующие имена из raw_taldau: {_e}")
    result = []
    for idx in index_ids:
        canonical = existing_names.get(str(idx))
        name = canonical or (names.get(str(idx)) or names.get(idx)
                              or db_names.get(int(idx)) or f"Показатель {idx}")
        result.append((int(idx), name))
    return result


def fetch_taldau(**context):
    """
    Запрашивает Talдау API для каждого показателя из conf['index_ids'].

    Если conf['split_by_kato']=True — делает ОДИН запрос за "детьми" узла
    СКО (p_parent_id=id=SKO_TERM_ID), и Talдау возвращает сразу все 14
    районов с их годовыми данными внутри одного ответа — раскрыто через
    DevTools 12.08.2026. Иначе (обычный режим) — один запрос на всю
    область целиком (terminalId=590000000, как и раньше).

    У Talдау своя внутренняя нумерация id районов, не совпадающая с
    кодами КАТО — соответствие (проверено по названиям, все 14 совпали)
    зашито в TALDAU_DISTRICT_ID_TO_KATO ниже.
    """
    conf = context["dag_run"].conf or {}
    split_by_kato = bool(conf.get("split_by_kato", False))
    year_from = int(conf.get("year_from") or 2000)
    year_to = int(conf.get("year_to") or datetime.now().year)
    indicators = _resolve_indicators(conf)

    engine = create_engine(DB_CONN)
    rows   = []
    errors = []
    _ind_names = [n for _, n in _resolve_indicators(conf)]
    if _ind_names:
        _names_sql = ",".join(f"'{n}'" for n in _ind_names)
        with engine.begin() as _conn:
            _conn.execute(text(f"DELETE FROM raw_taldau WHERE indicator_name IN ({_names_sql})"))
        print(f"[CLEANUP] raw_taldau: удалены старые строки для {len(_ind_names)} показателей")

    # id районов у Talдау — своя внутренняя нумерация, НЕ коды КАТО.
    # Раскрыто через DevTools 12.08.2026, сопоставлено по названиям
    # районов (все 14 совпали однозначно).
    TALDAU_DISTRICT_ID_TO_KATO = {
        264024: 591000000,  # Петропавловск Г.А.
        264031: 593200000,  # Айыртауский район
        264140: 593400000,  # Акжарский район
        264178: 593600000,  # Район Магжана Жумабаева
        264274: 594200000,  # Есильский район
        264351: 594600000,  # Жамбылский район
        264430: 595000000,  # Кызылжарский район
        264529: 595200000,  # Мамлютский район
        264587: 595600000,  # Район Шал акына
        264646: 595800000,  # Аккайынский район
        264693: 596000000,  # Тайыншинский район
        264811: 596200000,  # Тимирязевский район
        264854: 596400000,  # Уалихановский район
        264901: 596600000,  # Район им.Габита Мусрепова
    }

    for index_id, ind_name in indicators:
        try:
            _cfg = DIC_IDS_MAP.get(int(index_id), ("67,749,576", "741880,741917,741935"))
            dic_ids   = _cfg[0]
            p_terms_val = _cfg[1]
            measure_id = _cfg[2] if len(_cfg) > 2 else "7"

            if split_by_kato:
                # ОДИН запрос за "детьми" узла СКО — Talдау отдаёт сразу все
                # районы с их годовыми данными внутри. p_parent_id и id оба
                # равны id родителя, остальное — как в обычном запросе.
                payload = {
                    "p_parent_id": str(SKO_TERM_ID), "p_index_id": str(_cfg[3] if len(_cfg) > 3 else index_id),
                    "p_period_id": "7", "p_measure_id": measure_id, "p_term_id": "741880",
                    "p_terms": p_terms_val, "p_dicIds": dic_ids,
                    "idx": "0", "filter": FILTER_JSON, "id": str(SKO_TERM_ID),
                }
                resp = requests.post(TALDAU_BASE_URL, data=payload, timeout=30)
                resp.raise_for_status()
                api_rows = resp.json()

                found_any = False
                for _entry in api_rows:
                    try:
                        _taldau_id = int(_entry.get("id"))
                    except (TypeError, ValueError):
                        continue
                    real_kato = TALDAU_DISTRICT_ID_TO_KATO.get(_taldau_id)
                    if real_kato is None:
                        continue
                    district_label = _entry.get("text", str(real_kato))

                    _periods = []
                    for _k, _v in _entry.items():
                        if _k.startswith("y122") and _v not in (None, ""):
                            try:
                                _yr = 2000 + int(_k[4:])
                                if year_from <= _yr <= year_to:
                                    _periods.append({"date": "31.12." + str(_yr), "value": _v})
                            except Exception:
                                pass

                    if not _periods:
                        print(f"[ПУСТО] {ind_name} ({index_id}) — нет данных по "
                              f"{district_label} (id={_taldau_id})")
                        continue

                    found_any = True
                    term_records = [{"terms": [real_kato], "periods": _periods}]
                    rows.append({
                        "indicator_code": str(index_id),
                        "indicator_name": ind_name,
                        "kato_code":      real_kato,
                        "raw_json":       json.dumps(term_records, ensure_ascii=False),
                        "fetched_at":     datetime.now().isoformat(),
                        "source_file":    "taldau_api",
                    })
                    print(f"[OK] {ind_name} / {district_label}: {len(_periods)} периодов")

                if not found_any:
                    print(f"[ПУСТО] {ind_name} ({index_id}) — ни один район не вернул данных")

            else:
                payload = {
                    "p_parent_id": "741880", "p_index_id": str(_cfg[3] if len(_cfg) > 3 else index_id),
                    "p_period_id": "7", "p_measure_id": measure_id, "p_term_id": "741880",
                    "p_terms": p_terms_val, "p_dicIds": dic_ids,
                    "idx": "0", "filter": FILTER_JSON, "id": "741880",
                }
                resp = requests.post(TALDAU_BASE_URL, data=payload, timeout=30)
                resp.raise_for_status()
                api_rows = resp.json()
                _region = next((x for x in api_rows if str(x.get("id")) == str(SKO_TERM_ID)), None)
                _periods = []
                if _region:
                    for _k, _v in _region.items():
                        if _k.startswith("y122") and _v not in (None, ""):
                            try:
                                _yr = 2000 + int(_k[4:])
                                if year_from <= _yr <= year_to:
                                    _periods.append({"date": "31.12." + str(_yr), "value": _v})
                            except Exception:
                                pass
                term_records = [{"terms": [SKO_REAL_KATO], "periods": _periods}] if _periods else []

                if not term_records:
                    print(f"[ПУСТО] {ind_name} ({index_id}) — нет данных по области целиком")
                else:
                    rows.append({
                        "indicator_code": str(index_id),
                        "indicator_name": ind_name,
                        "kato_code":      SKO_REAL_KATO,
                        "raw_json":       json.dumps(term_records, ensure_ascii=False),
                        "fetched_at":     datetime.now().isoformat(),
                        "source_file":    "taldau_api",
                    })
                    print(f"[OK] {ind_name} / Северо-Казахстанская область (целиком): "
                          f"{len(term_records[0]['periods'])} периодов")

        except requests.exceptions.HTTPError as e:
            msg = f"[HTTP ERROR] {ind_name} ({index_id}): {e}"
            print(msg)
            errors.append(msg)
        except Exception as e:
            msg = f"[ERROR] {ind_name} ({index_id}): {e}"
            print(msg)
            errors.append(msg)

    if rows:
        pd.DataFrame(rows).to_sql(
            "raw_taldau", engine, if_exists="append", index=False
        )
        print(f"Сохранено в raw_taldau: {len(rows)} записей "
              f"({'по районам' if split_by_kato else 'по области'})")
    else:
        # Все показатели вернули пустой ответ — это не ошибка API
        # (errors пуст), а просто отсутствие данных по данному terminalId.
        # Роняем DAG только если были реальные HTTP-ошибки.
        msg = f"Talдау API не вернул ни одного показателя. Ошибки: {errors}"
        if errors:
            raise ValueError(msg)
        else:
            print(f"[WARN] {msg} — все ответы пусты, но HTTP-ошибок не было. "
                  "Возможно, для выбранных показателей нет данных по этому terminalId.")


def transform_taldau(**context):
    """
    Не изменилась по сути относительно старой версии, кроме того, что
    kato_code теперь читается из raw_taldau (записан в fetch_taldau), а не
    жёстко прописан как 590000000 — это и даёт детализацию по районам,
    когда fetch_taldau был запущен с split_by_kato=True.
    """
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    requested_names = set(name for _, name in _resolve_indicators(conf))

    engine = create_engine(DB_CONN)

    raw = pd.read_sql("SELECT * FROM raw_taldau", engine)
    if raw.empty:
        print("raw_taldau пуст — нечего трансформировать")
        return

    try:
        ind_cache = pd.read_sql("SELECT * FROM indicator_cache", engine)
        ind_map   = dict(zip(
            ind_cache["indicator_name"],
            zip(ind_cache["direction_code"], ind_cache["unit_name"])
        ))
    except Exception:
        ind_map = {}
    # Приоритетный источник направления/единицы — taldau_indicators_catalog
    # (там правильные direction_code: labor, social_demog — не other).
    taldau_map = {}
    try:
        _tc = pd.read_sql("SELECT name_ru, direction_code, unit_name FROM taldau_indicators_catalog", engine)
        taldau_map = dict(zip(_tc["name_ru"], zip(_tc["direction_code"], _tc["unit_name"])))
    except Exception as _e:
        print(f"[warn] taldau_indicators_catalog недоступен: {_e}")

    rows = []

    for _, rec in raw.iterrows():
        try:
            data = json.loads(rec["raw_json"])
        except Exception as e:
            print(f"Ошибка парсинга JSON для {rec['indicator_name']}: {e}")
            continue

        ind_name = rec["indicator_name"]
        # kato_code теперь приходит из самой записи raw_taldau (новая
        # колонка, добавленная в fetch_taldau) — fallback на 590000000
        # для старых строк, сохранённых до этого изменения.
        row_kato = int(rec["kato_code"]) if "kato_code" in rec and pd.notna(rec.get("kato_code")) else 590000000
        direction_code, unit_name = taldau_map.get(ind_name) or ind_map.get(ind_name, ("other", "единиц"))

        for region in data:
            if row_kato not in region.get("terms", []):
                continue

            for period in region.get("periods", []):
                try:
                    date_str = period.get("date", "")
                    if not date_str:
                        continue
                    dt   = datetime.strptime(date_str, "%d.%m.%Y")
                    year = dt.year
                    value = float(
                        str(period.get("value", "")).replace(" ", "").replace(",", ".")
                    )
                    rows.append({
                        "indicator_name": ind_name,
                        "direction_code": direction_code,
                        "unit_name":      unit_name,
                        "kato_code":      row_kato,
                        "period_type":    "year",
                        "period_start":   f"{year}-01-01",
                        "period_end":     f"{year}-12-31",
                        "value":          value,
                        "source_file":    "taldau_api",
                    })
                except Exception as e:
                    print(f"  Пропуск периода {period}: {e}")
                    continue

    if not rows:
        print("После трансформации нет данных")
        return []

    df_out = pd.DataFrame(rows)
    df_out = df_out.drop_duplicates(
        subset=["kato_code", "indicator_name", "period_start"]
    ).reset_index(drop=True)

    _names_to_del = df_out["indicator_name"].unique().tolist()
    if _names_to_del:
        _del_sql = ",".join(f"'{n}'" for n in _names_to_del)
        with engine.begin() as _conn:
            _conn.execute(text(f"DELETE FROM transformed_taldau WHERE indicator_name IN ({_del_sql})"))
        print(f"[CLEANUP] transformed_taldau: удалены старые строки для {len(_names_to_del)} показателей")
    df_out.to_sql(
        "transformed_taldau", engine, if_exists="append", index=False
    )
    print(
        f"transformed_taldau: {len(df_out)} строк, "
        f"{df_out['indicator_name'].nunique()} показателей, "
        f"{df_out['kato_code'].nunique()} районов"
    )
    return sorted(
        n for n in df_out["indicator_name"].unique().tolist()
        if n in requested_names
    )


def trigger_build_excel_taldau(**context):
    """
    Заменяет TriggerDagRunOperator: тот передаёт conf через Jinja-шаблон
    внутри строки, а Jinja всегда рендерит {{ }} в текст — список
    показателей приходил в dag_build_excel как строковое представление
    списка, а не настоящий Python-список, из-за чего len()/tuple()
    работали посимвольно и WHERE indicator_name IN (...) ничего не
    находил. trigger_dag() передаёт conf напрямую как Python-объект,
    без прохода через Jinja — типы сохраняются корректно.
    """
    ti = context["ti"]
    names = ti.xcom_pull(task_ids="transform_taldau") or []
    trigger_dag(
        dag_id="dag_build_excel",
        conf={"taldau_only": True, "indicator_names": names},
        run_id=f"taldau_only__{context['ts_nodash']}",
    )


with DAG(
    "dag_extract_taldau",
    default_args={
        "owner":       "airflow",
        "retries":     1,
        "retry_delay": timedelta(minutes=5),
    },
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["extract"],
) as dag:

    t1 = PythonOperator(task_id="fetch_taldau",     python_callable=fetch_taldau)
    t2 = PythonOperator(task_id="transform_taldau", python_callable=transform_taldau)
    t3 = PythonOperator(
        task_id="trigger_build_excel",
        python_callable=trigger_build_excel_taldau,
    )
    t1 >> t2 >> t3
