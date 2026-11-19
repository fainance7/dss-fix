"""
Патч для /opt/aldispetro/convert_to_dss.py

Проблема: локальный словарь KATO_MAP внутри файла не содержит ключей
для "аккайынский" и "г.мусрепова" — из-за этого эти 2 района
тихо пропускались при конвертации (видно по логу "[ПРОПУСК] нет КАТО для: ...").

Решение: убираем локальный KATO_MAP, импортируем эталонный из etl/kato_map.py,
где оба района присутствуют со всеми вариантами написания.

Запуск на сервере:
    cd /opt/aldispetro
    python3 patches/fix_convert_to_dss.py
"""
import re
from pathlib import Path

TARGET = Path("/opt/aldispetro/convert_to_dss.py")

OLD_BLOCK_START = 'KATO_MAP = {'
OLD_BLOCK_END_MARKER = '"петропавловск": 591000000,\n}'

NEW_IMPORT_AND_LOOKUP = '''import sys
sys.path.insert(0, "/opt/aldispetro/etl")
sys.path.insert(0, "/opt/airflow/etl")
from kato_map import KATO_MAP as _KATO_MAP_CANON, normalize as _normalize

# Доп. варианты написания, специфичные для исходных файлов этого скрипта
# (не входят в эталонный справочник, но встречаются в конкретных xlsx)
_EXTRA_KATO_ALIASES = {
    "северо-казахстанская обасть": 590000000,
    "итого по области":            590000000,
    "г. петропавл":                591000000,
    "айыртауский район (ско)":     593200000,
    "акжарский район (ско)":       593400000,
    "м.жумабаева район (ско)":     593600000,
    "есильский район (ско)":       594200000,
    "жамбылский район (ско)":      594600000,
    "кызылжарский район (ско)":    595000000,
    "мамлютский район (ско)":      595200000,
    "шал акына район (ско)":       595600000,
    "тайыншинский район (ско)":    596000000,
    "тимирязевский район (ско)":   596200000,
    "уалихановский район (ско)":   596400000,
    "район им.габита мусрепова":   596600000,
    "им.габита мусрепова":         596600000,
    "новоишимский":                596600000,
    "новоишимское":                596600000,
    "благовещенка":  594600000,
    "булаево":       593600000,
    "пресновка":     594600000,
    "явленка":       594200000,
    "мамлютка":      595200000,
    "сергеевка":     595600000,
    "смирново":      596200000,
    "тайынша":       596000000,
    "тимирязево":    596200000,
    "кишкенеколь":   596400000,
}

# Собираем финальный словарь: эталонный (kato_map.py) + доп. алиасы.
# Эталонный словарь имеет формат {ключ: (код, имя)}, тут нужен только код.
KATO_MAP = {k: v[0] for k, v in _KATO_MAP_CANON.items()}
KATO_MAP.update(_EXTRA_KATO_ALIASES)'''


def main():
    text = TARGET.read_text(encoding="utf-8")

    start_idx = text.find(OLD_BLOCK_START)
    end_idx = text.find(OLD_BLOCK_END_MARKER)

    if start_idx == -1 or end_idx == -1:
        print("❌ Не нашёл блок KATO_MAP для замены. "
              "Возможно файл уже пропатчен или изменён вручную.")
        print("   Проверь файл руками — ищи 'KATO_MAP = {' в начале.")
        return

    end_idx += len(OLD_BLOCK_END_MARKER)

    backup = TARGET.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    print(f"✅ Бэкап сохранён: {backup}")

    new_text = text[:start_idx] + NEW_IMPORT_AND_LOOKUP + text[end_idx:]
    TARGET.write_text(new_text, encoding="utf-8")
    print(f"✅ Патч применён: {TARGET}")
    print()
    print("Проверка:")
    print("  python3 -c \"import sys; sys.path.insert(0,'/opt/aldispetro'); "
          "from convert_to_dss import lookup_kato; "
          "print('Аккайынский ->', lookup_kato('Аккайынский')); "
          "print('Г.Мусрепова ->', lookup_kato('Г.Мусрепова'))\"")


if __name__ == "__main__":
    main()
