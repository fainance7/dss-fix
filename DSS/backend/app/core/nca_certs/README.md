# НУЦ РК Root Certificates

Place root certificate PEM files here for production issuer verification.

Download from: https://pki.gov.kz/

Files expected:
- `nca_root.pem`   — RSA root (НУЦ РК Root CA)
- `nca_gost.pem`   — GOST root (if applicable)

In **development** (empty directory): issuer verification is skipped automatically.
