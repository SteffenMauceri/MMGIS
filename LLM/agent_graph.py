import json
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from LLM.agent_types import AgentState
from LLM.llm import create_chat_model
from LLM.state import get_memory_store
from LLM.tools import tools
from LLM.browser import get_mmgis_page
from LLM.tools import mmgis_get_state as tool_get_state


PLANNER_PROMPT = """You are the orchestrator for MMGIS. Your job is to output EXACTLY ONE of the following per turn, then stop:

- STEP: <a single, clear instruction for the api_agent to execute now>
- FINAL: <a concise final answer for the user>

Constraints:
- You do NOT have tools; only the api_agent can execute tools.
- Keep each STEP minimal (one coherent action). Examples: "get the UI state", "list missions", "set zoom to 4".
- Prefer known tools when phrasing the STEP: mmgis_get_state, geocode_place, mmgis_find_layer, mmgis_set_zoom, mmgis_set_view, mmgis_toggle_layer, mmgis_eval, mmgis_http, read_file, list_directory, find_files_by_pattern, rag_search, rag_jsapi_help.
- If the user requests the current UI/map state, the FIRST STEP should be to get it: "STEP: get the UI state".
- If the user is likely finished, emit FINAL.
"""


API_AGENT_PROMPT = """You are an API and file system specialist for MMGIS with tool access.

Your input includes the latest STEP from the planner (or FINAL).

Do exactly this:
1) If the latest planner message starts with FINAL:, do nothing and return immediately (no tools).
2) If it starts with STEP:, interpret the instruction and EXECUTE it, using as many tool calls as needed.
   - Filesystem: list_directory, read_file, find_files_by_pattern
   - UI/map: mmgis_get_state, geocode_place, mmgis_find_layer, mmgis_toggle_layer, mmgis_set_zoom, mmgis_set_view, mmgis_eval
   - Backend: mmgis_http (inject token from env)
   - Prefer parameterized wrappers over run_api
3) When the step is successfully completed, reply with a concise summary prefixed with: STEP_DONE:
   - Include only essential results (e.g., missions list, new zoom level)
   - Do not request more tools in this reply

For location+layer intents like "show me <layer> for <place>":
- Extract the place; call geocode_place for lat/lng/zoom.
- Call mmgis_get_state to read layer names.
- Call mmgis_find_layer with the requested theme (e.g., "elevation").
- Toggle that layer ON (mmgis_toggle_layer) and set view (mmgis_set_view) to the geocoded position.
- IMPORTANT: Do not call run_api; use the dedicated tools above only.

State updates:
- When you obtain the MMGIS UI state, it is automatically persisted. You may also persist key facts using memory_set_fact(key, value_json) when appropriate (e.g., user preferences).

Limits and halting:
- Avoid repeating the same tool calls.
- Perform at most 5 tool calls in this single step; if you hit the limit, summarize progress and say what to do next.

Documentation: Consult 'LLM/APIs.md' via read_file if needed before using APIs.
"""


async def summarize_history(messages: List[Tuple[str, str]], current_brief: Optional[str]) -> str:
    model = create_chat_model(temperature=0)
    head = "" if not current_brief else f"Existing brief summary to update:\n{current_brief}\n\n"
    content = "\n".join([f"{r.upper()}: {c}" for r, c in messages[-20:]])
    sys = (
        "You maintain a concise running brief of the conversation. "
        "Summarize key user intents, important facts, decisions, and next steps in <= 8 bullet points. "
        "Keep stable identifiers and omit low-level tool I/O."
    )
    prompt = [("system", sys), ("user", head + "Recent turns:\n" + content)]
    result = await model.ainvoke(prompt)
    return getattr(result, "content", "").strip() or (current_brief or "")


def _planner_context(thread: Dict[str, Optional[str]]) -> List[Tuple[str, str]]:
    memory = get_memory_store()
    facts = memory.get_facts(thread)
    brief = memory.get_brief(thread)
    last_ui = memory.get_last_ui_state(thread)
    ctx_lines = [
        "Context for planner (read-only):",
        f"Thread: user_id={thread.get('user_id')}, session_id={thread.get('session_id')}, mission_id={thread.get('mission_id')}",
    ]
    if brief:
        ctx_lines.append("Brief summary:\n" + brief)
    if facts:
        ctx_lines.append("Facts (key:value JSON):\n" + json.dumps(facts)[:2000])
    if last_ui is not None:
        ctx_lines.append("Last known UI state (may be stale):\n" + json.dumps(last_ui)[:2000])
    return [("system", "\n\n".join(ctx_lines))]


def create_agent(thread: Dict[str, Optional[str]]):
    planner_model = create_chat_model(temperature=0)
    api_agent_model = create_chat_model(temperature=0).bind_tools(tools)

    async def planner_node(state: AgentState):
        mem_ctx = _planner_context(state.get("thread", {}))
        messages = [("system", PLANNER_PROMPT)] + mem_ctx + state["messages"]
        result = await planner_model.ainvoke(messages)
        step_count = state.get("step_count", 0) + 1
        return {"messages": [result], "step_count": step_count}

    async def api_agent_node(state: AgentState):
        # Deterministic fallback for pattern: "show me <layer> for <place>"
        try:
            last_user = None
            for m in reversed(state.get("messages", [])):
                # tuple form: (role, content)
                if isinstance(m, (list, tuple)) and len(m) >= 2 and str(m[0]).lower() == "user":
                    last_user = str(m[1] or "")
                    break
                # object/dict with role/content
                if hasattr(m, "type") and hasattr(m, "content") and str(getattr(m, "type")).lower() == "human":
                    last_user = str(getattr(m, "content") or "")
                    break
                if hasattr(m, "role") and hasattr(m, "content") and str(getattr(m, "role")).lower() == "user":
                    last_user = str(getattr(m, "content") or "")
                    break
                if isinstance(m, dict) and str(m.get("role", "")).lower() == "user":
                    last_user = str(m.get("content") or "")
                    break
            if last_user:
                lowered = last_user.strip().lower()
                if lowered.startswith("show me ") and (" for " in lowered or " of " in lowered):
                    # parse
                    try:
                        body = lowered[len("show me "):]
                        sep = " for " if " for " in body else " of "
                        theme, place = body.split(sep, 1)
                        theme = theme.strip()
                        # Trim trailing instructions like "and zoom in ..."
                        place = place.split(" and ", 1)[0]
                        place = place.strip(",. !?")
                    except Exception:
                        theme = ""
                        place = ""
                    # geocode (offline-first)
                    lat = None
                    lng = None
                    zoom = 12
                    builtins = {
                        "pasadena": {"lat": 34.1478, "lng": -118.1445, "zoom": 12},
                        "los angeles": {"lat": 34.0522, "lng": -118.2437, "zoom": 11},
                        "paris": {"lat": 48.8566, "lng": 2.3522, "zoom": 12},
                        "san francisco": {"lat": 37.7749, "lng": -122.4194, "zoom": 13},
                    }
                    pnorm = place.lower()
                    geo = None
                    if pnorm in builtins:
                        geo = builtins[pnorm]
                    else:
                        for k, v in builtins.items():
                            if pnorm.startswith(k + ","):
                                geo = v
                                break
                    if geo:
                        lat = float(geo["lat"])
                        lng = float(geo["lng"])
                        zoom = int(geo.get("zoom", 12))
                    if lat is None or lng is None:
                        content = f"STEP_DONE: Could not geocode '{place}'"
                        return {"messages": [("assistant", content)]}

                    # choose layer by reading configs live
                    page = await get_mmgis_page()
                    cfgs = await page.evaluate(
                        """
                        () => {
                            try {
                                const api = window.mmgisAPI; if (!api || !api.getLayerConfigs) return {};
                                return api.getLayerConfigs() || {};
                            } catch (e) { return {}; }
                        }
                        """
                    )
                    def score(name: str) -> int:
                        n = (name or "").lower()
                        synonyms = {
                            "elevation": ["elevation", "terrain", "hillshade"],
                            "wind": ["wind", "hrrr", "gfs"],
                            "satellite": ["satellite", "imagery", "world imagery", "esri", "firefly"],
                            "imagery": ["imagery", "satellite", "world imagery", "esri", "firefly"],
                        }
                        tokens = set((theme or "").split())
                        expanded = []
                        for t in tokens:
                            expanded.append(t)
                            for syn in synonyms.get(t, []):
                                expanded.append(syn)
                        return sum(1 for t in expanded if t and t in n)

                    selected = None
                    if isinstance(cfgs, dict):
                        best_s = -1
                        for key, cfg in cfgs.items():
                            name = (cfg or {}).get("display_name") or (cfg or {}).get("name") or key
                            s = score(name)
                            if s > best_s:
                                best_s = s
                                selected = (cfg or {}).get("uuid") or key

                    # toggle selected layer on
                    if selected:
                        await page.evaluate(
                            """
                            (id) => {
                                try {
                                    const api = window.mmgisAPI; if (!api || !api.toggleLayer) return;
                                    return Promise.resolve(api.toggleLayer(id, true));
                                } catch(e) { return; }
                            }
                            """,
                            selected,
                        )
                    # set view (zoom higher if user asked to zoom in)
                    hi_zoom = 15 if "zoom in" in lowered else zoom
                    await page.evaluate(
                        """
                        ({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom ?? m.getZoom()); }
                        """,
                        {"lat": lat, "lng": lng, "zoom": hi_zoom},
                    )
                    # persist UI state for verification
                    try:
                        await tool_get_state()
                    except Exception:
                        pass
                    # done
                    content = f"STEP_DONE: Showing {theme or 'requested layer'} for {place} at ({lat:.4f}, {lng:.4f}) z={hi_zoom}"
                    return {"messages": [("assistant", content)]}
        except Exception:
            pass

        preface = [("system", API_AGENT_PROMPT)]
        messages = preface + state["messages"]
        result = await api_agent_model.ainvoke(messages)
        return {"messages": [result]}

    tool_node = ToolNode(tools)

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_node("api_agent", api_agent_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("planner")

    def continue_from_planner(state: AgentState):
        last = state["messages"][-1]
        content = getattr(last, "content", "")
        if isinstance(content, list):
            content = " ".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
        if isinstance(content, str) and content.strip().upper().startswith("FINAL:"):
            return END
        if state.get("step_count", 0) >= 8:
            return END
        return "api_agent"

    graph.add_conditional_edges("planner", continue_from_planner, {"api_agent": "api_agent", END: END})

    def continue_from_api_agent(state: AgentState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "planner"

    graph.add_conditional_edges("api_agent", continue_from_api_agent, {"tools": "tools", "planner": "planner"})
    graph.add_edge("tools", "api_agent")

    return graph.compile()


