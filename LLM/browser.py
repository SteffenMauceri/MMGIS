import os
import asyncio
import subprocess
from typing import Any, Dict, Optional
from LLM.config import get_config_value

from playwright.async_api import async_playwright


_pw: Dict[str, Any] = {"p": None, "browser": None, "context": None, "page": None}


async def get_mmgis_page():
    """Get or create a persistent Playwright page connected to the running MMGIS tab."""
    if _pw.get("page") is not None:
        try:
            if not _pw["page"].is_closed():
                return _pw["page"]
        except Exception:
            pass

    mode = str(get_config_value("browser.mode", "MMGIS_BROWSER_MODE", "managed", str)).strip().lower()
    keep_open = bool(
        get_config_value(
            "browser.keep_open",
            "MMGIS_BROWSER_KEEP_OPEN",
            False,
            lambda v: str(v).lower() in ["1", "true", "yes"],
        )
    )
    ui_url = get_config_value("browser.ui_url", "MMGIS_UI_URL", "http://localhost:8888", str)
    page_timeout_ms = get_config_value("browser.page_timeout_ms", "MMGIS_PAGE_TIMEOUT_MS", 15000, int)
    wait_ms = get_config_value("browser.wait_ms", "MMGIS_WAIT_MS", 15000, int)
    post_nav_wait_ms = get_config_value("browser.post_nav_wait_ms", "MMGIS_POST_NAV_WAIT_MS", 3000, int)
    wait_for_networkidle = bool(
        get_config_value(
            "browser.wait_for_networkidle",
            "MMGIS_WAIT_FOR_NETWORKIDLE",
            False,
            lambda v: str(v).lower() in ["1", "true", "yes"],
        )
    )
    networkidle_timeout_ms = get_config_value(
        "browser.networkidle_timeout_ms", "MMGIS_NETWORKIDLE_TIMEOUT_MS", 15000, int
    )

    if _pw.get("p") is None:
        _pw["p"] = await async_playwright().start()

    page: Optional[Any] = None

    # If the caller wants the window to stay open between runs, prefer attach-mode.
    if keep_open and mode != "attach":
        mode = "attach"

    if mode == "attach":
        # Attach to an already running Chrome over CDP
        cdp_url = get_config_value("browser.cdp_url", "MMGIS_CDP_URL", "http://localhost:9222", str)
        target_substr = get_config_value("browser.url_substr", "MMGIS_URL_SUBSTR", "localhost:8888", str)

        # Try to connect. If it fails and keep_open is requested, spawn Chrome detached and retry.
        try:
            _pw["browser"] = await _pw["p"].chromium.connect_over_cdp(cdp_url)
        except Exception:
            if keep_open:
                # Launch Chrome detached with remote debugging and a persistent profile
                chrome_exec = get_config_value(
                    "browser.chrome_executable",
                    "MMGIS_CHROME_EXECUTABLE",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    str,
                )
                user_data_dir = get_config_value("browser.user_data_dir", "MMGIS_BROWSER_USER_DATA_DIR", "", str)
                if not user_data_dir:
                    # default to a repo-local profile directory so it persists
                    here = os.path.abspath(os.path.dirname(__file__))
                    repo_root = os.path.dirname(here)
                    user_data_dir = os.path.join(repo_root, ".mmgis_chrome_profile")
                try:
                    os.makedirs(user_data_dir, exist_ok=True)
                except Exception:
                    pass
                # Extract port from cdp_url if provided
                port = "9222"
                try:
                    import urllib.parse as up

                    pr = up.urlparse(cdp_url)
                    if pr.port:
                        port = str(pr.port)
                except Exception:
                    pass
                args = [
                    chrome_exec,
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={user_data_dir}",
                    "--new-window",
                    ui_url,
                ]
                try:
                    if os.name == "posix":
                        subprocess.Popen(
                            args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                    else:
                        DETACHED_PROCESS = 0x00000008
                        CREATE_NEW_PROCESS_GROUP = 0x00000200
                        subprocess.Popen(
                            args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                        )
                except Exception:
                    pass
                # Give Chrome a moment to start, then retry connect
                try:
                    await asyncio.sleep(1.5)
                except Exception:
                    pass
                try:
                    _pw["browser"] = await _pw["p"].chromium.connect_over_cdp(cdp_url)
                except Exception as exc:
                    raise RuntimeError(
                        "Could not connect to Chrome over CDP. Launch Chrome with remote debugging or set keep_open=false."
                    ) from exc
            else:
                raise

        for context in _pw["browser"].contexts:
            for pg in context.pages:
                if target_substr in (pg.url or ""):
                    page = pg
                    _pw["context"] = context
                    break
            if page is not None:
                break

        if page is None:
            # Try to open a new tab to the configured MMGIS UI URL
            try:
                if len(_pw["browser"].contexts) == 0:
                    context = await _pw["browser"].new_context()
                else:
                    context = _pw["browser"].contexts[0]
                _pw["context"] = context
                page = await context.new_page()
                await page.goto(ui_url, wait_until="domcontentloaded")
            except Exception:
                # Fall back to any first available page if opening failed
                if (
                    len(_pw["browser"].contexts) == 0
                    or len(_pw["browser"].contexts[0].pages) == 0
                ):
                    raise RuntimeError(
                        "Could not find or open any pages in the connected browser. Ensure MMGIS is accessible."
                    )
                _pw["context"] = _pw["browser"].contexts[0]
                page = _pw["context"].pages[0]
    else:
        # Managed mode: Playwright launches the browser (persistent or ephemeral)
        headless = bool(get_config_value("browser.headless", "MMGIS_BROWSER_HEADLESS", False, lambda v: str(v).lower() in ["1", "true", "yes"]))
        user_data_dir = get_config_value("browser.user_data_dir", "MMGIS_BROWSER_USER_DATA_DIR", "", str)

        if user_data_dir:
            context = await _pw["p"].chromium.launch_persistent_context(user_data_dir, headless=headless)
            _pw["context"] = context
        else:
            browser = await _pw["p"].chromium.launch(headless=headless)
            _pw["browser"] = browser
            _pw["context"] = await browser.new_context()

        context = _pw["context"]
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(ui_url, wait_until="domcontentloaded")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open MMGIS UI at {ui_url}. Ensure the server is running."
            ) from exc
        # Optional additional waits after navigation
        try:
            if wait_for_networkidle:
                await page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
            if post_nav_wait_ms and post_nav_wait_ms > 0:
                await page.wait_for_timeout(post_nav_wait_ms)
        except Exception:
            pass

    # Set default timeouts to avoid hangs
    try:
        if _pw.get("context") is not None:
            await _pw["context"].set_default_timeout(page_timeout_ms)
        await page.set_default_timeout(page_timeout_ms)
    except Exception:
        pass

    # Optionally wait for mmgisAPI to be available
    try:
        # Final readiness checks
        if wait_for_networkidle:
            try:
                await page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
            except Exception:
                pass
        if post_nav_wait_ms and post_nav_wait_ms > 0:
            try:
                await page.wait_for_timeout(post_nav_wait_ms)
            except Exception:
                pass
        await page.wait_for_function(
            "() => !!(window.mmgisAPI && window.mmgisAPI.map)", timeout=wait_ms
        )
    except Exception:
        # Continue anyway; callers may handle missing API
        pass

    _pw["page"] = page
    return page


