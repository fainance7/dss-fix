"""ЭЦП-верификация через NCANode (CR-2).

NCANode проверяет CMS-подпись НУЦ РК криптографически (включая ГОСТ), строит
цепочку до корня, проверяет отзыв (OCSP/CRL) и извлекает данные сертификата.
Здесь мы дополнительно связываем подпись с одноразовым nonce (anti-replay):
из вложенного CMS извлекается подписанное содержимое и сверяется с выданным nonce.
"""
import base64

import httpx

from app.config import settings
from app.core.ecp import ECPError


def _b64_eq(a: str, b: str) -> bool:
    """Сравнение двух base64-строк по декодированным байтам (устойчиво к padding)."""
    try:
        return base64.b64decode(a) == base64.b64decode(b)
    except Exception:
        return False


async def verify_cms_via_ncanode(cms_b64: str, expected_nonce_b64: str) -> str:
    """Проверяет CMS через NCANode и возвращает ИИН подписанта.

    Шаги:
      1. /cms/verify — криптопроверка подписи + цепочка + отзыв;
      2. проверка валидности/неотозванности сертификата;
      3. /cms/extract — сверка подписанного содержимого с выданным nonce;
      4. извлечение ИИН из subject сертификата.
    Бросает ECPError (с .error_code) при любой неудаче.
    """
    revocation = [x.strip().upper() for x in settings.NCANODE_REVOCATION_CHECK.split(",") if x.strip()]

    async with httpx.AsyncClient(timeout=settings.NCANODE_TIMEOUT) as client:
        # 1. Криптопроверка подписи + цепочка + отзыв
        try:
            resp = await client.post(
                f"{settings.NCANODE_URL}/cms/verify",
                json={"cms": cms_b64, "revocationCheck": revocation},
            )
        except httpx.HTTPError as exc:
            raise ECPError(f"NCANode недоступен: {exc}", "ncanode_unavailable")

        data = resp.json() if resp.content else {}
        if not data.get("valid"):
            raise ECPError(
                data.get("message") or "Подпись ЭЦП недействительна",
                "ecp_invalid_signature",
            )

        signers = data.get("signers") or []
        certs = (signers[0].get("certificates") if signers else None) or []
        if not certs:
            raise ECPError("В CMS нет сертификата подписанта", "ecp_no_cert")
        cert = certs[0]

        if cert.get("valid") is False:
            raise ECPError("Сертификат недействителен", "cert_invalid")
        for rev in cert.get("revocations") or []:
            if str(rev.get("status", "")).upper() not in ("ACTIVE", "GOOD", "OK", ""):
                raise ECPError("Сертификат отозван", "cert_revoked")

        iin = (cert.get("subject") or {}).get("iin")
        if not iin:
            raise ECPError("ИИН не найден в сертификате ЭЦП", "iin_not_in_cert")

        # 2. Anti-replay: извлечь подписанное содержимое и сверить с nonce
        try:
            ex = await client.post(f"{settings.NCANODE_URL}/cms/extract", json={"cms": cms_b64})
        except httpx.HTTPError as exc:
            raise ECPError(f"NCANode недоступен: {exc}", "ncanode_unavailable")
        signed_data = (ex.json() if ex.content else {}).get("data")
        if not signed_data or not _b64_eq(signed_data, expected_nonce_b64):
            raise ECPError("Подписан неверный запрос (nonce не совпал)", "ecp_nonce_mismatch")

        return iin
