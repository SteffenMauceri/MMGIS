import asyncio
import json
import os
import sys
from typing import Tuple, Optional

from LLM.state import get_memory_store, set_current_thread
from LLM.tools import mmgis_get_state
from LLM.browser import get_mmgis_page


async def get_center_and_zoom() -> Tuple[Optional[float], Optional[float], Optional[int]]:
    try:
        state_json = await mmgis_get_state()
        ui = json.loads(state_json) if state_json else {}
        m = (ui.get("map") or {})
        c = (m.get("center") or {})
        lat = c.get("lat")
        lng = c.get("lng")
        zoom = m.get("zoom")
        return (float(lat) if lat is not None else None,
                float(lng) if lng is not None else None,
                int(zoom) if zoom is not None else None)
    except Exception:
        return (None, None, None)


async def run_nav(agent, store, thread, prompt: str) -> bool:
    before = await get_center_and_zoom()
    print(f"Before {prompt}:", before)

    resp = await agent.ainvoke({
        "messages": [("user", prompt)],
        "facts": store.get_facts(thread),
        "brief": store.get_brief(thread),
        "last_ui_state": store.get_last_ui_state(thread),
        "run_id": store.new_run_id(),
        "thread": thread,
        "scratch": {},
        "step_count": 0,
    })
    print("Agent response:", json.dumps(resp, default=str)[:1000])

    after = await get_center_and_zoom()
    print(f"After {prompt}:", after)

    b_lat, b_lng, b_zoom = before
    a_lat, a_lng, a_zoom = after
    if b_lat is None or b_lng is None or a_lat is None or a_lng is None:
        return False

    cmd = prompt.strip().lower()
    # thresholds (degrees) for minimal pan detection; this is small and depends on zoom
    pan_thresh = 0.001

    if "zoom in" in cmd or cmd == "zoom":
        return (b_zoom is not None and a_zoom is not None and a_zoom > b_zoom)
    if "zoom out" in cmd:
        return (b_zoom is not None and a_zoom is not None and a_zoom < b_zoom)
    if "right" in cmd:
        return (a_lng - b_lng) > pan_thresh
    if "left" in cmd:
        return (b_lng - a_lng) > pan_thresh
    if "up" in cmd or "north" in cmd:
        return (a_lat - b_lat) > pan_thresh
    if "down" in cmd or "south" in cmd:
        return (b_lat - a_lat) > pan_thresh

    # Unknown prompt, consider failure
    return False


async def main():
    # Ensure MMGIS page is available and ready; otherwise skip gracefully
    try:
        page = await get_mmgis_page()
    except Exception as e:
        print("SKIP: Could not connect to a debuggable Chrome or find MMGIS tab.")
        print("Hint: Launch Chrome with remote debugging and open MMGIS, e.g.:")
        print('  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$(mktemp -d)"')
        print("  Then open http://localhost:8888 in that browser instance.")
        print(f"Details: {e}")
        return 0
    ready = await page.evaluate("() => !!(window.mmgisAPI && window.mmgisAPI.map)")
    if not ready:
        print("SKIP: MMGIS page connected but window.mmgisAPI.map is not ready.")
        print("Open MMGIS fully and ensure the map has initialized.")
        return 0

    # Lazy import of agent to avoid hard failure if optional deps missing
    try:
        from LLM.agent_graph import create_agent  # type: ignore
    except Exception as e:
        print("SKIP: agent dependencies not available (e.g., langgraph).")
        print(f"Details: {e}")
        return 0
    thread = {"user_id": os.getenv("MMGIS_USER_ID", os.getenv("USER", "anon")), "session_id": os.getenv("MMGIS_SESSION_ID", "local"), "mission_id": os.getenv("MMGIS_MISSION_ID")}
    set_current_thread(thread)
    store = get_memory_store()

    agent = create_agent(thread)

    tests = [
        "zoom in",
        "zoom out",
        "move the map to the right",
        "move the map to the left",
        "pan up",
        "pan down",
    ]

    results = []
    for t in tests:
        print("\n=== Test:", t, "===")
        ok = await run_nav(agent, store, thread, t)
        print("RESULT:", "PASS" if ok else "SOFT-FAIL")
        results.append((t, ok))

    passed = sum(1 for _, ok in results if ok)
    print(f"\nSummary: {passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


