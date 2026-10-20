#!/usr/bin/env python3
"""Opens a Taldau indicator page with a headless browser, waits for the
real GetIndexTreeData request (built by the site's own ExtJS logic) and
prints its parameters - same thing we used to capture manually via
DevTools -> Copy as cURL.

Usage:
    python3 capture_taldau_params.py <index_id> [<index_id> ...]
"""
import sys
import json
from urllib.parse import parse_qsl
from playwright.sync_api import sync_playwright


def capture_one(page, index_id):
    captured = {}

    def on_request(request):
        if "GetIndexTreeData" in request.url and request.method == "POST":
            post_data = request.post_data
            if post_data:
                captured["post_data"] = post_data
                captured["url"] = request.url

    page.on("request", on_request)
    url = "https://taldau.stat.gov.kz/ru/NewIndex/GetIndex/" + str(index_id)
    try:
        page.goto(url, timeout=20000, wait_until="networkidle")
    except Exception as e:
        return {"index_id": index_id, "error": "page.goto failed: " + str(e)}

    page.wait_for_timeout(2000)

    if not captured:
        return {"index_id": index_id, "error": "GetIndexTreeData not captured"}

    parsed = dict(parse_qsl(captured["post_data"]))
    return {
        "index_id": index_id,
        "p_dicIds": parsed.get("p_dicIds"),
        "p_terms": parsed.get("p_terms"),
        "p_measure_id": parsed.get("p_measure_id"),
        "p_period_id": parsed.get("p_period_id"),
        "p_parent_id": parsed.get("p_parent_id"),
        "p_term_id": parsed.get("p_term_id"),
        "id": parsed.get("id"),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 capture_taldau_params.py <index_id> [...]")
        sys.exit(1)

    index_ids = sys.argv[1:]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            for idx in index_ids:
                page = browser.new_page()
                try:
                    result = capture_one(page, idx)
                finally:
                    page.close()
                print(json.dumps(result, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
