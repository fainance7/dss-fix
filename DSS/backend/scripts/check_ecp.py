"""
Диагностика ЭЦП: извлечь ИИН из CMS SignedData.

Использование:
  python scripts/check_ecp.py <base64_cms_file_or_string>

Как получить CMS для теста:
  1. Откройте браузер → DevTools (F12) → Network
  2. На странице /login выберите вкладку "ЭЦП" → нажмите "Войти по ЭЦП"
  3. Подпишите в NCALayer
  4. В DevTools найдите запрос POST /auth/login-ecp
  5. Скопируйте поле "cms_signature" из Request Body
  6. Сохраните в файл cms.txt
  7. Запустите: python scripts/check_ecp.py cms.txt
"""

import sys
import os
import base64

# Добавляем корневую папку проекта в PYTHONPATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.ecp import (
    _parse_cms_and_extract_cert,
    _check_cert_validity,
    _extract_iin_from_cert,
    _verify_issuer,
    ECPError,
)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    # Аргумент — файл или строка base64
    if os.path.isfile(arg):
        with open(arg, 'r') as f:
            cms_b64 = f.read().strip()
    else:
        cms_b64 = arg.strip()

    print("=" * 60)
    print("Диагностика ЭЦП сертификата")
    print("=" * 60)

    # 1. Парсинг CMS
    print("\n[1] Парсинг CMS SignedData...")
    try:
        cert = _parse_cms_and_extract_cert(cms_b64)
        print("    OK")
    except ECPError as e:
        print(f"    ОШИБКА: {e} (code={e.error_code})")
        sys.exit(1)

    # 2. Информация о сертификате
    print("\n[2] Сертификат:")
    subject = cert["tbs_certificate"]["subject"]
    issuer = cert["tbs_certificate"]["issuer"]
    validity = cert["tbs_certificate"]["validity"]
    print(f"    Subject : {subject.human_friendly}")
    print(f"    Issuer  : {issuer.human_friendly}")
    print(f"    Valid from : {validity['not_before'].native}")
    print(f"    Valid until: {validity['not_after'].native}")

    # 3. Проверка срока действия
    print("\n[3] Срок действия...")
    try:
        _check_cert_validity(cert)
        print("    OK — сертификат действителен")
    except ECPError as e:
        print(f"    ОШИБКА: {e} (code={e.error_code})")

    # 4. Проверка issuer (НУЦ РК)
    print("\n[4] Проверка НУЦ РК issuer...")
    try:
        _verify_issuer(cert)
        print("    OK")
    except ECPError as e:
        print(f"    ОШИБКА: {e} (code={e.error_code})")
        print("    → Проверьте файлы в backend/app/core/nca_certs/")

    # 5. Извлечение ИИН
    print("\n[5] Извлечение ИИН...")
    try:
        iin = _extract_iin_from_cert(cert)
        print(f"    ИИН: {iin}")
        print()
        print("=" * 60)
        print(f"  Добавьте ИИН {iin} в профиль пользователя")
        print("  через /admin/users → Изменить → поле ИИН")
        print("=" * 60)
    except ECPError as e:
        print(f"    ОШИБКА: {e} (code={e.error_code})")
        print()
        print("    Все атрибуты Subject для ручного поиска ИИН:")
        for rdn in subject.chosen:
            for attr in rdn:
                print(f"      OID={attr['type'].dotted}  value={attr['value'].native!r}")


if __name__ == "__main__":
    main()
