"""Этап 7: некорректный JSON в теле запроса — 400, а не 500."""
import asyncio
import os

import pytest

os.environ.setdefault("API_SECRET_KEY", "test")
os.environ.setdefault("DB_CONN", "postgresql://u:p@localhost/db")

from fastapi import HTTPException  # noqa: E402

import routers.upload as up  # noqa: E402


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        import json
        return json.loads(self._body)


@pytest.mark.parametrize("body", ["{bad json", "[1, 2]", "\"text\""])
def test_bad_json_is_400(body):
    with pytest.raises(HTTPException) as e:
        asyncio.run(up._json_body(_Req(body)))
    assert e.value.status_code == 400


def test_good_json_passes():
    assert asyncio.run(up._json_body(_Req('{"a": 1}'))) == {"a": 1}
