"""
Патч для /opt/aldispetro/load_to_raw_historical.py

Та же проблема, что и в convert_to_dss.py: локальный KATO_MAP
не содержит ключей для "аккайынский" и "г.мусрепова".

Запуск на сервере:
    cd /opt/aldispetro
    python3 patches/fix_load_to_raw_historical.py
"""
from pathlib import Path

TARGET = Path("/opt/aldispetro/load_to_raw_historical.py")

OLD_BLOCK_START = "KATO_MAP = {"
OLD_BLOCK_END_MARKER = '"район магжана жумабаева":       593600000,\n}'

NEW_IMPORT_AND_LOOKUP = '''import sys
sys.path.insert(0, "/opt/aldispetro/etl")
sys.path.insert(0, "/opt/airflow/etl")
from kato_map import KATO_MAP as _KATO_MAP_CANON

# Эталонный словарь имеет формат {ключ: (код, имя)} — берём только код
KATO_MAP = {k: v[0] for k, v in _KATO_MAP_CANON.items()}'''


def main():
    text = TARGET.read_text(encoding="utf-8")

    start_idx = text.find(OLD_BLOCK_START)
    end_idx = text.find(OLD_BLOCK_END_MARKER)

    if start_idx == -1 or end_idx == -1:
        print("❌ Не нашёл блок KATO_MAP для замены. "
              "Возможно файл уже пропатчен или изменён вручную.")
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
          "from load_to_raw_historical import lookup_kato; "
          "print('Аккайынский ->', lookup_kato('Аккайынский')); "
          "print('Г.Мусрепова ->', lookup_kato('Г.Мусрепова'))\"")


if __name__ == "__main__":
    main()
