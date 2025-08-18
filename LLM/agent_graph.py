import json
from typing import Any, Dict, List, Optional, Tuple
import os
import asyncio
from LLM.config import get_config_value

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
- Prefer known tools when phrasing the STEP: mmgis_get_state, mmgis_list_layers, geocode_place, mmgis_find_layer, mmgis_set_zoom, mmgis_set_view, mmgis_toggle_layer, mmgis_eval, mmgis_http, read_file, list_directory, find_files_by_pattern.
- If the user requests the current UI/map state, the FIRST STEP should be to get it: "STEP: get the UI state".
- If the user is likely finished, emit FINAL.
"""


API_AGENT_PROMPT = """You are an API and file system specialist for MMGIS with tool access.

Your input includes the latest STEP from the planner (or FINAL).

Do exactly this:
1) If the latest planner message starts with FINAL:, do nothing and return immediately (no tools).
2) If it starts with STEP:, interpret the instruction and EXECUTE it, using as many tool calls as needed.
   - Filesystem: list_directory, read_file, find_files_by_pattern
   - UI/map: mmgis_get_state, mmgis_list_layers, geocode_place, mmgis_find_layer, mmgis_toggle_layer, mmgis_set_zoom, mmgis_set_view, mmgis_eval
   - Backend: mmgis_http (inject token from env)
   - Prefer parameterized wrappers over run_api
3) When the step is successfully completed, reply with a concise summary prefixed with: STEP_DONE:
   - Include only essential results (e.g., missions list, new zoom level)
   - Do not request more tools in this reply

For requests to “show X” or “what layer should I use for Y?”:
- First call mmgis_list_layers() to get names and descriptions.
- Use your own reasoning over the returned names/descriptions to pick the best match (no synonym gating).
- If a place is mentioned, call geocode_place for lat/lng/zoom.
- Toggle the selected layer ON (mmgis_toggle_layer) and set view (mmgis_set_view) with the geocoded position and an appropriate zoom.
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
    recent = int(get_config_value("summarization.summarizer_recent_turns", None, 20, int))
    bullets = int(get_config_value("summarization.summarizer_bullets_max", None, 8, int))
    content = "\n".join([f"{r.upper()}: {c}" for r, c in messages[-recent:]])
    sys = (
        "You maintain a concise running brief of the conversation. "
        f"Summarize key user intents, important facts, decisions, and next steps in <= {bullets} bullet points. "
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
        trunc = int(get_config_value("summarization.planner_context_trunc_chars", None, 2000, int))
        ctx_lines.append("Facts (key:value JSON):\n" + json.dumps(facts)[:trunc])
    if last_ui is not None:
        trunc = int(get_config_value("summarization.planner_context_trunc_chars", None, 2000, int))
        ctx_lines.append("Last known UI state (may be stale):\n" + json.dumps(last_ui)[:trunc])
    return [("system", "\n\n".join(ctx_lines))]


def create_agent(thread: Dict[str, Optional[str]]):
    # Global limits
    max_steps = get_config_value("agent.max_steps", "MMGIS_MAX_STEPS", 8, int)
    max_tool_calls = get_config_value("agent.max_tool_calls", "MMGIS_MAX_TOOL_CALLS", 10, int)
    overall_timeout_s = get_config_value("agent.overall_timeout_s", "MMGIS_OVERALL_TIMEOUT_S", 60.0, float)
    planner_model = create_chat_model(temperature=0)
    api_agent_model = create_chat_model(temperature=0).bind_tools(tools)

    async def planner_node(state: AgentState):
        mem_ctx = _planner_context(state.get("thread", {}))
        messages = [("system", PLANNER_PROMPT)] + mem_ctx + state["messages"]
        result = await planner_model.ainvoke(messages)
        step_count = state.get("step_count", 0) + 1
        return {"messages": [result], "step_count": step_count}

    async def api_agent_node(state: AgentState):
        # Deterministic fallbacks when the model can't use tools
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

                # 1) Navigation commands: zoom/pan
                nav_cmd = None
                if any(kw in lowered for kw in ["zoom in", "zoom out", "move", "pan", "left", "right", "up", "down", "north", "south"]):
                    nav_cmd = lowered
                if nav_cmd is not None:
                    page = await get_mmgis_page()
                    # Read current center/zoom
                    cur = await page.evaluate(
                        "() => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return null; const c = m.getCenter(); return { lat: c.lat, lng: c.lng, zoom: m.getZoom() }; }"
                    )
                    if isinstance(cur, dict):
                        lat = float(cur.get("lat") or 0.0)
                        lng = float(cur.get("lng") or 0.0)
                        zoom = int(cur.get("zoom") or 2)
                    else:
                        lat, lng, zoom = 0.0, 0.0, 2

                    # Simple degree shifts scaled by zoom (smaller shift at higher zoom)
                    try:
                        scale = max(1, zoom)
                        base_at_zoom2 = float(get_config_value("navigation.pan_base_deg_at_zoom2", None, 0.5, float))
                        base = base_at_zoom2 / (2 ** (scale - 2))
                    except Exception:
                        base = float(get_config_value("navigation.min_pan_deg", None, 0.01, float))

                    if "zoom in" in nav_cmd or nav_cmd == "zoom":
                        await page.evaluate("() => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setZoom(m.getZoom()+1); }")
                        await tool_get_state()
                        return {"messages": [("assistant", "FINAL: zoomed in")]} 
                    if "zoom out" in nav_cmd:
                        await page.evaluate("() => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setZoom(m.getZoom()-1); }")
                        await tool_get_state()
                        return {"messages": [("assistant", "FINAL: zoomed out")]} 

                    # Panning
                    dlat, dlng = 0.0, 0.0
                    if "right" in nav_cmd:
                        dlng += base
                    if "left" in nav_cmd:
                        dlng -= base
                    if "up" in nav_cmd or "north" in nav_cmd:
                        dlat += base
                    if "down" in nav_cmd or "south" in nav_cmd:
                        dlat -= base
                    new_lat = lat + dlat
                    new_lng = lng + dlng
                    await page.evaluate(
                        "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom); }",
                        {"lat": new_lat, "lng": new_lng, "zoom": zoom},
                    )
                    await tool_get_state()
                    return {"messages": [("assistant", f"FINAL: panned to {new_lat:.6f},{new_lng:.6f} z={zoom}")]} 

                # 2) Deterministic fallback for pattern: "show me <layer> for/of <place>"
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
                    zoom = int(get_config_value("navigation.default_place_zoom", None, 12, int))
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
                    # set view (zoom higher by default for clarity; even higher if user asked to zoom in)
                    hi_zoom_default = int(get_config_value("navigation.hi_zoom_default", None, 13, int))
                    hi_zoom_zoom_in = int(get_config_value("navigation.hi_zoom_if_zoom_in", None, 15, int))
                    hi_zoom = hi_zoom_default if "zoom in" not in lowered else hi_zoom_zoom_in
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
                    content = f"FINAL: Showing {theme or 'requested layer'} for {place} at ({lat:.4f}, {lng:.4f}) z={hi_zoom}"
                    return {"messages": [("assistant", content)]}
        except Exception:
            pass

        preface = [("system", API_AGENT_PROMPT)]
        messages = preface + state["messages"]
        result = await api_agent_model.ainvoke(messages)
        return {"messages": [result]}

    class BudgetedToolNode(ToolNode):
        async def aexecute(self, state: AgentState):  # type: ignore[override]
            used = int(state.get("tool_calls_used", 0))
            if used >= max_tool_calls:
                # Convert to a planner-visible message to finalize
                return {"messages": [("assistant", f"FINAL: Stopping after {used} tool calls (budget exhausted).")]}  # type: ignore[return-value]
            out = await super().aexecute(state)
            # Increment budget counter if any tool was actually called
            try:
                last = out.get("messages", [])[-1]
                has_tool = False
                try:
                    tool_calls = getattr(last, "tool_calls", None)
                except Exception:
                    tool_calls = None
                if not tool_calls:
                    ak = getattr(last, "additional_kwargs", {}) or {}
                    tool_calls = ak.get("tool_calls")
                has_tool = bool(tool_calls)
            except Exception:
                has_tool = False
            if has_tool:
                used += 1
            out["tool_calls_used"] = used
            return out

    tool_node = BudgetedToolNode(tools)

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
        # Step budget
        if state.get("step_count", 0) >= max_steps:
            return END
        return "api_agent"

    graph.add_conditional_edges("planner", continue_from_planner, {"api_agent": "api_agent", END: END})

    def continue_from_api_agent(state: AgentState):
        last = state["messages"][-1]
        # LangChain Chat messages store tool calls under additional_kwargs.tool_calls
        try:
            tool_calls = getattr(last, "tool_calls", None)
        except Exception:
            tool_calls = None
        if not tool_calls:
            try:
                ak = getattr(last, "additional_kwargs", {}) or {}
                tool_calls = ak.get("tool_calls")
            except Exception:
                tool_calls = None
        if tool_calls:
            return "tools"
        return "planner"

    graph.add_conditional_edges("api_agent", continue_from_api_agent, {"tools": "tools", "planner": "planner"})
    graph.add_edge("tools", "api_agent")

    app = graph.compile()

    # Wrap ainvoke with an overall timeout
    original_ainvoke = app.ainvoke

    async def ainvoke_with_timeout(input_state: AgentState, *args, **kwargs):
        try:
            return await asyncio.wait_for(original_ainvoke(input_state, *args, **kwargs), timeout=overall_timeout_s)
        except asyncio.TimeoutError:
            return {"messages": [("assistant", f"FINAL: Stopping after {overall_timeout_s:.0f}s (overall timeout).")]}  # type: ignore[return-value]

    app.ainvoke = ainvoke_with_timeout  # type: ignore[attr-defined]
    return app


