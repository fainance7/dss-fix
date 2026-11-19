"""Р-10: next-параметр страницы входа ETL — без XSS и без открытого редиректа."""
import os

os.environ.setdefault("API_SECRET_KEY", "test")

from routers.dss_login import _page, _safe_next  # noqa: E402


def test_safe_next_keeps_local_upload_paths():
    assert _safe_next("/upload/files?x=1") == "/upload/files?x=1"


def test_safe_next_rejects_foreign_targets():
    for bad in ["https://evil.example", "//evil.example/upload", "/\\evil.example", "/other",
                "", None, "/upload/\r\nSet-Cookie: x=1", "javascript:alert(1)"]:
        assert _safe_next(bad) == "/upload/"


def test_page_escapes_next_path():
    html = _page("", '/upload/"><script>alert(1)</script>')
    assert "<script>alert(1)" not in html
    assert "&quot;&gt;&lt;script&gt;" in html
