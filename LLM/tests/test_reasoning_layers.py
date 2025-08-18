import asyncio
import json
import os
import sys

from typing import List, Tuple

from LLM.state import get_memory_store, set_current_thread
from LLM.tools import mmgis_get_state
from LLM.browser import get_mmgis_page


def has_wind_layer_visible(ui_state: dict) -> bool:
    try:
        layers = ui_state.get("layers") or []
        for l in layers:
            name = (l.get("display_name") or l.get("name") or "").lower()
            vis = l.get("visible")
            if vis and any(k in name for k in ["wind", "hrrr", "gfs"]):
                return True
    except Exception:
        pass
    return False


async def ensure_page_ready() -> bool:
    try:
        page = await get_mmgis_page()
        ready = await page.evaluate("() => !!(window.mmgisAPI && window.mmgisAPI.map)")
        return bool(ready)
    except Exception:
        return False


async def run_prompt(agent, store, thread, prompt: str) -> Tuple[bool, dict]:
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
    try:
        state_json = await mmgis_get_state()
        ui = json.loads(state_json) if state_json else {}
    except Exception:
        ui = {}
    ok = has_wind_layer_visible(ui)
    return ok, ui


async def main():
    if not await ensure_page_ready():
        print("SKIP: MMGIS page not ready. Start Chrome with remote debugging and open MMGIS.")
        return 0

    thread = {"user_id": os.getenv("MMGIS_USER_ID", os.getenv("USER", "anon")), "session_id": os.getenv("MMGIS_SESSION_ID", "local"), "mission_id": os.getenv("MMGIS_MISSION_ID")}
    set_current_thread(thread)
    store = get_memory_store()
    try:
        from LLM.agent_graph import create_agent  # type: ignore
    except Exception as e:
        print("SKIP: agent dependencies not available (e.g., langgraph).")
        print(f"Details: {e}")
        return 0
    agent = create_agent(thread)

    prompts: List[str] = [
        "show air flow here",
        "is there a storm?",
        "environmental conditions",
    ]

    results = []
    for p in prompts:
        print("\n=== Test:", p, "===")
        ok, ui = await run_prompt(agent, store, thread, p)
        print("Wind layer visible:", ok)
        results.append((p, ok))

    passed = sum(1 for _, ok in results if ok)
    print(f"\nSummary: {passed}/{len(prompts)} prompts resulted in a wind layer being visible")
    # Soft requirement: at least one prompt should succeed to pass
    return 0 if passed >= 1 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


