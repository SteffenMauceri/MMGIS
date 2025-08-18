import os
import shlex
import subprocess
import sys
from typing import Optional

from LLM.config import get_config_value


def _default_profile_dir() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    # Keep profile inside the repo to respect workspace constraints
    return os.path.join(os.path.dirname(here), ".mmgis_chrome_profile")


def launch_demo_browser() -> int:
    ui_url = get_config_value("browser.ui_url", "MMGIS_UI_URL", "http://localhost:8888", str)
    cdp_url = get_config_value("browser.cdp_url", "MMGIS_CDP_URL", "http://localhost:9222", str)
    chrome_exec = get_config_value(
        "browser.chrome_executable",
        "MMGIS_CHROME_EXECUTABLE",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        str,
    )
    user_data_dir = get_config_value(
        "browser.user_data_dir", "MMGIS_BROWSER_USER_DATA_DIR", _default_profile_dir(), str
    )

    # Extract port from cdp_url if possible
    port: Optional[str] = None
    try:
        import urllib.parse as up

        pr = up.urlparse(cdp_url)
        if pr.port:
            port = str(pr.port)
    except Exception:
        pass
    if not port:
        port = "9222"

    os.makedirs(user_data_dir, exist_ok=True)

    args = [
        chrome_exec,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--new-window",
        ui_url,
    ]

    try:
        # Launch detached so the browser survives after this script exits
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            # Windows
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            )
    except FileNotFoundError:
        print("Chrome executable not found. Set 'browser.chrome_executable' in LLM/config.json or MMGIS_CHROME_EXECUTABLE env.")
        return 1
    except Exception as e:
        print(f"Failed to launch Chrome: {e}")
        return 1

    print("Launched Chrome for demo:")
    print(f"  URL: {ui_url}")
    print(f"  CDP: {cdp_url}")
    print(f"  Profile: {user_data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(launch_demo_browser())


