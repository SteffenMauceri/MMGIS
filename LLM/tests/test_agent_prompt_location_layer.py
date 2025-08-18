import asyncio
import json
import os
import sys

from LLM.state import get_memory_store, set_current_thread
from LLM.tools import mmgis_get_state
from LLM.browser import get_mmgis_page


PROMPT = "show me the elevation map for pasadena"
# PROMPT = "show me satellite imagery of san francisco and zoom in as much as you can"


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

    # Import agent lazily to allow skipping when optional deps are missing
    try:
        from LLM.agent_graph import create_agent  # type: ignore
    except Exception as e:
        print("SKIP: agent dependencies not available (e.g., langgraph).")
        print(f"Details: {e}")
        return 0

    # Thread context
    thread = {"user_id": os.getenv("MMGIS_USER_ID", os.getenv("USER", "anon")), "session_id": os.getenv("MMGIS_SESSION_ID", "local"), "mission_id": os.getenv("MMGIS_MISSION_ID")}
    set_current_thread(thread)
    store = get_memory_store()
    run_id = store.new_run_id()

    agent = create_agent(thread)

    messages = [("user", PROMPT)]
    resp = await agent.ainvoke({
        "messages": messages,
        "facts": store.get_facts(thread),
        "brief": store.get_brief(thread),
        "last_ui_state": store.get_last_ui_state(thread),
        "run_id": run_id,
        "thread": thread,
        "scratch": {},
        "step_count": 0,
    })
    print(json.dumps(resp, default=str)[:4000])

    # Fetch live UI state after agent actions
    try:
        state_json = await mmgis_get_state()
        ui = json.loads(state_json) if state_json else {}
    except Exception:
        ui = {}

    layers = ui.get("layers") or []
    names = [ (l.get("display_name") or l.get("name") or "") for l in layers ]
    print("Detected layers:", json.dumps(names))
    has_elev = any("elevation" in n.lower() for n in names)

    ok_view = False
    try:
        m = (ui.get("map") or {})
        c = (m.get("center") or {})
        if c:
            lat = float(c.get("lat"))
            lng = float(c.get("lng"))
            ok_view = (abs(lat - 34.1478) < 0.6) and (abs(lng + 118.1445) < 0.6)
    except Exception:
        pass

    # Fallback: read directly from the live page if state was empty
    if not has_elev or not ok_view:
        try:
            direct = await page.evaluate(
                """
                () => {
                    try {
                        const api = window.mmgisAPI; if (!api) return {};
                        const m = api.map; if (!m) return {};
                        const c = m.getCenter();
                        const visible = api.getVisibleLayers ? api.getVisibleLayers() : {};
                        let elev_on = false;
                        if (api.getLayerConfigs) {
                            const cfgs = api.getLayerConfigs() || {};
                            for (const [key, cfg] of Object.entries(cfgs)) {
                                const nm = (cfg && (cfg.display_name || cfg.name)) || key;
                                const id = (cfg && cfg.uuid) || key;
                                const isVis = visible ? (visible[id] ?? visible[nm] ?? false) : false;
                                if (isVis && String(nm || '').toLowerCase().includes('elevation')) {
                                    elev_on = true;
                                }
                            }
                        }
                        return { center: { lat: c.lat, lng: c.lng }, elev_on };
                    } catch(e) { return {}; }
                }
                """
            )
            if isinstance(direct, dict):
                c = direct.get("center") or {}
                lat = float(c.get("lat", 0))
                lng = float(c.get("lng", 0))
                ok_view = (abs(lat - 34.1478) < 0.6) and (abs(lng + 118.1445) < 0.6)
                has_elev = bool(direct.get("elev_on", False))
        except Exception:
            pass
    print("Map near Pasadena:", ok_view)

    ok = has_elev and ok_view
    print("RESULT:", "PASS" if ok else "SOFT-FAIL")
    # We mark soft-fail because the agent may need an extra turn; this test primarily exercises the flow
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


