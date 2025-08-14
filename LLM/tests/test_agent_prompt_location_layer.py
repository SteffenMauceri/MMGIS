import asyncio
import json
import os
import sys

from LLM.agent_graph import create_agent
from LLM.state import get_memory_store, set_current_thread
from LLM.tools import mmgis_get_state


PROMPT = "show me the elevation map for pasadena"
# PROMPT = "show me satellite imagery of san francisco and zoom in as much as you can"


async def main():
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
    print("Map near Pasadena:", ok_view)

    ok = has_elev and ok_view
    print("RESULT:", "PASS" if ok else "SOFT-FAIL")
    # We mark soft-fail because the agent may need an extra turn; this test primarily exercises the flow
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


