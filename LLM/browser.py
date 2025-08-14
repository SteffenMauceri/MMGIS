import os
from typing import Any, Dict

from playwright.async_api import async_playwright


_pw: Dict[str, Any] = {"p": None, "browser": None, "page": None}


async def get_mmgis_page():
    """Get or create a persistent Playwright page connected to the running MMGIS tab."""
    if _pw.get("page") is not None:
        try:
            if not _pw["page"].is_closed():
                return _pw["page"]
        except Exception:
            pass

    cdp_url = os.getenv("MMGIS_CDP_URL", "http://localhost:9222")
    target_substr = os.getenv("MMGIS_URL_SUBSTR", "localhost:8888")

    if _pw.get("p") is None:
        _pw["p"] = await async_playwright().start()

    _pw["browser"] = await _pw["p"].chromium.connect_over_cdp(cdp_url)

    page = None
    for context in _pw["browser"].contexts:
        for pg in context.pages:
            if target_substr in (pg.url or ""):
                page = pg
                break
        if page is not None:
            break

    if page is None:
        # Try to open a new tab to the configured MMGIS UI URL
        ui_url = os.getenv("MMGIS_UI_URL", "http://localhost:8888")
        try:
            if len(_pw["browser"].contexts) == 0:
                context = await _pw["browser"].new_context()
            else:
                context = _pw["browser"].contexts[0]
            page = await context.new_page()
            await page.goto(ui_url, wait_until="domcontentloaded")
        except Exception:
            # Fall back to any first available page if opening failed
            if len(_pw["browser"].contexts) == 0 or len(_pw["browser"].contexts[0].pages) == 0:
                raise RuntimeError(
                    "Could not find or open any pages in the connected browser. Ensure MMGIS is accessible."
                )
            page = _pw["browser"].contexts[0].pages[0]

    # Optionally wait for mmgisAPI to be available
    try:
        wait_ms = int(os.getenv("MMGIS_WAIT_MS", "15000"))
        await page.wait_for_function(
            "() => !!(window.mmgisAPI && window.mmgisAPI.map)", timeout=wait_ms
        )
    except Exception:
        # Continue anyway; callers may handle missing API
        pass

    _pw["page"] = page
    return page


