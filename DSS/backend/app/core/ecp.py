"""
ECP (ЭЦП) — Kazakhstan Electronic Digital Signature utilities.

Parses CMS SignedData returned by NCALayer WebSocket API,
extracts and validates the signer certificate, and reads the IIN.

Dependencies: asn1crypto (pure Python, already a transitive dep of cryptography).
"""
import base64
import os
import re
from datetime import datetime, timezone

from asn1crypto import cms, pem, x509

# OIDs for ИИН (individual) and БИН (legal entity) from НУЦ РК
_IIN_OID = "1.2.398.3.18.101.1.2.2"
_BIN_OID = "1.2.398.3.18.101.1.2.1"

# Directory with НУЦ РК root PEM files.
# Empty directory → issuer verification is skipped (dev mode).
_NCA_CERTS_DIR = os.path.join(os.path.dirname(__file__), "nca_certs")


class ECPError(Exception):
    """ЭЦП verification error. Carries an error_code for structured HTTP responses."""

    def __init__(self, message: str, error_code: str = "ecp_error") -> None:
        self.error_code = error_code
        super().__init__(message)


def _parse_cms_and_extract_cert(cms_b64: str) -> x509.Certificate:
    """
    Decode base64 CMS SignedData (PKCS#7), return the signer certificate.
    Raises ECPError on structural problems.
    """
    try:
        raw = base64.b64decode(cms_b64)
    except Exception:
        raise ECPError("Invalid base64 in CMS signature", "cms_parse_error")

    try:
        content_info = cms.ContentInfo.load(raw)
        signed_data = content_info["content"]
        certs = signed_data["certificates"]
        if not certs or len(certs) == 0:
            raise ECPError("No certificates embedded in CMS", "cms_no_cert")
        # First certificate is the signer's; CertificateChoices.chosen → Certificate
        return certs[0].chosen
    except ECPError:
        raise
    except Exception as exc:
        raise ECPError(f"CMS parse failed: {exc}", "cms_parse_error")


def _check_cert_validity(cert: x509.Certificate) -> None:
    """Raise ECPError if the certificate is expired or not yet valid."""
    now = datetime.now(timezone.utc)
    validity = cert["tbs_certificate"]["validity"]
    not_before = validity["not_before"].native
    not_after = validity["not_after"].native

    if now < not_before:
        raise ECPError("Certificate is not yet valid", "cert_not_yet_valid")
    if now > not_after:
        raise ECPError("Certificate has expired", "cert_expired")


def _verify_issuer(cert: x509.Certificate) -> None:
    """
    Lightweight issuer check: verify the cert was signed by one of the
    НУЦ РК root certificates stored in _NCA_CERTS_DIR.

    If the directory is absent or empty (development), verification is skipped.
    Production: place nca_root.pem (and nca_gost.pem if needed) from pki.gov.kz
    into backend/app/core/nca_certs/.
    """
    if not os.path.isdir(_NCA_CERTS_DIR):
        return

    pem_files = [f for f in os.listdir(_NCA_CERTS_DIR) if f.endswith((".pem", ".cer", ".crt"))]
    if not pem_files:
        return  # dev mode — no root certs loaded

    cert_issuer = cert["tbs_certificate"]["issuer"].human_friendly

    for pem_file in pem_files:
        with open(os.path.join(_NCA_CERTS_DIR, pem_file), "rb") as fh:
            pem_data = fh.read()
        try:
            if pem.detect(pem_data):
                _, _, der = pem.unarmor(pem_data)
            else:
                der = pem_data
            root_cert = x509.Certificate.load(der)
            root_subject = root_cert["tbs_certificate"]["subject"].human_friendly
            # Accept if issuer DN contains or matches the root subject DN
            if root_subject in cert_issuer or cert_issuer in root_subject:
                return
        except Exception:
            continue

    raise ECPError(
        "Certificate not issued by a trusted НУЦ РК authority",
        "cert_untrusted_issuer",
    )


def _extract_iin_from_cert(cert: x509.Certificate) -> str:
    """
    Extract ИИН (individual) or БИН (legal entity) from the certificate Subject.

    Strategies tried in order:
    1. Dedicated НУЦ РК OID 1.2.398.3.18.101.1.2.2 (ИИН) or 1.2.398.3.18.101.1.2.1 (БИН)
    2. Serial Number (OID 2.5.4.5) with prefix "IIN<12digits>" or "BIN<12digits>"
    3. Last whitespace-delimited token of CN matching 12 digits (older certs)

    Returns the 12-digit identifier string on success.
    Raises ECPError if not found.
    """
    subject = cert["tbs_certificate"]["subject"]

    # Strategy 1: dedicated НУЦ РК OIDs for ИИН and БИН
    for rdn in subject.chosen:
        for attr in rdn:
            oid = attr["type"].dotted
            if oid in (_IIN_OID, _BIN_OID):
                value = attr["value"].native
                if isinstance(value, str) and re.fullmatch(r"\d{12}", value):
                    return value

    # Strategy 2: Serial Number field — "IIN<12digits>" or "BIN<12digits>"
    for rdn in subject.chosen:
        for attr in rdn:
            if attr["type"].human_friendly == "Serial Number":
                value = attr["value"].native or ""
                m = re.search(r"\d{12}", value)
                if m:
                    return m.group(0)

    # Strategy 3: last token of CN matches 12 digits
    for rdn in subject.chosen:
        for attr in rdn:
            if attr["type"].human_friendly == "Common Name":
                cn = attr["value"].native or ""
                parts = cn.strip().split()
                if parts and re.fullmatch(r"\d{12}", parts[-1]):
                    return parts[-1]

    raise ECPError("ИИН не найден в сертификате ЭЦП", "iin_not_in_cert")


def validate_cms_and_get_iin(cms_b64: str) -> str:
    """
    Full pipeline:
      parse CMS → extract cert → check expiry → verify issuer → extract ИИН.

    Returns the 12-digit ИИН string on success.
    Raises ECPError (with .error_code) on any failure.
    """
    cert = _parse_cms_and_extract_cert(cms_b64)
    _check_cert_validity(cert)
    _verify_issuer(cert)
    return _extract_iin_from_cert(cert)
