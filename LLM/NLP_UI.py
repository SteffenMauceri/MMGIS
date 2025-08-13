import os
import json
import asyncio
import argparse
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated, Optional, Any, Dict, List, Tuple
from playwright.async_api import async_playwright
import requests
from LLM.memory_store import MemoryStore
from typing import cast
import re

base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
api_key = os.getenv("OLLAMA_API_KEY", "ollama")
model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")

# Durable memory store
_db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "memory", "agent_memory.sqlite3"))
_memory_store = MemoryStore(_db_path)
_CURRENT_THREAD: Dict[str, Optional[str]] = {}

# Persistent Playwright connection cache
_pw: Dict[str, Any] = {"p": None, "browser": None, "page": None}


async def get_mmgis_page():
    """Get or create a persistent Playwright page connected to the running MMGIS tab."""
    # Reuse existing page if available and valid
    if _pw.get("page") is not None:
        try:
            if not _pw["page"].is_closed():
                return _pw["page"]
        except Exception:
            pass

    # (Re)connect
    cdp_url = os.getenv("MMGIS_CDP_URL", "http://localhost:9222")
    target_substr = os.getenv("MMGIS_URL_SUBSTR", "localhost:8888")

    # Ensure playwright singleton
    if _pw.get("p") is None:
        _pw["p"] = await async_playwright().start()

    _pw["browser"] = await _pw["p"].chromium.connect_over_cdp(cdp_url)

    # Try to find the correct page by URL match
    page = None
    for context in _pw["browser"].contexts:
        for pg in context.pages:
            if target_substr in (pg.url or ""):
                page = pg
                break
        if page is not None:
            break

    if page is None:
        # Fall back to the first available page
        if len(_pw["browser"].contexts) == 0 or len(_pw["browser"].contexts[0].pages) == 0:
            raise RuntimeError(
                "Could not find any open pages in the connected browser. Open MMGIS first."
            )
        page = _pw["browser"].contexts[0].pages[0]

    _pw["page"] = page
    return page

# 1. Define Tools
@tool
async def run_api(command: str) -> str:
    """
    Executes a command against the MMGIS Frontend JavaScript API in the connected browser.
    Example: 'window.mmgisAPI.map.panTo([-5.4, 137.8], 8);'
    """
    print(f"---TOOL CALLED: run_api with command: {command}---")
    try:
        page = await get_mmgis_page()
        # Execute the command but do not return the massive map object.
        await page.evaluate(command)
        return "Success: The command was executed in the browser."
    except Exception as e:
        return f"Error executing command with Playwright: {str(e)}"

@tool
async def mmgis_eval(expr: str, max_kb: int = 256) -> str:
    """
    Evaluate a JavaScript expression in the MMGIS page and return a JSON-stringified result (truncated).
    Example: 'window.mmgisAPI.getVisibleLayers()'
    """
    print(f"---TOOL CALLED: mmgis_eval with expr: {expr}---")
    try:
        page = await get_mmgis_page()
        js = f"""
        () => {{
            try {{
                const value = (0, eval)({json.dumps(expr)});
                const replacer = (key, val) => {{
                    if (typeof val === 'function') return undefined;
                    if (val && typeof val === 'object') {{
                        const proto = Object.prototype.toString.call(val);
                        if (proto !== '[object Array]' && proto !== '[object Object]') return undefined;
                    }}
                    return val;
                }};
                return JSON.stringify(value, replacer);
            }} catch (e) {{
                return JSON.stringify({{'error': String(e)}});
            }}
        }}
        """
        result: str = await page.evaluate(js)
        if result is None:
            return "null"
        encoded = result.encode("utf-8")
        limit = max(1, int(max_kb)) * 1024
        if len(encoded) > limit:
            truncated = encoded[: limit - 3].decode("utf-8", errors="ignore") + "..."
            return truncated
        return result
    except Exception as e:
        return f"Error in mmgis_eval: {str(e)}"

@tool
async def mmgis_get_state() -> str:
    """Return a compact snapshot of MMGIS UI/map state as JSON."""
    print("---TOOL CALLED: mmgis_get_state---")
    try:
        page = await get_mmgis_page()
        data = await page.evaluate(
            """
            () => {
                try {
                    const api = window.mmgisAPI;
                    if (!api) return { error: 'mmgisAPI not found' };
                    const map = api.map;
                    const center = map ? map.getCenter() : null;
                    const zoom = map ? map.getZoom() : null;
                    const b = map ? map.getBounds() : null;
                    const bounds = b ? {
                        north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest()
                    } : null;
                    const visible = api.getVisibleLayers ? api.getVisibleLayers() : null;
                    const time = api.getTime ? api.getTime() : null;
                    const startTime = api.getStartTime ? api.getStartTime() : null;
                    const endTime = api.getEndTime ? api.getEndTime() : null;
                    const activeTool = api.getActiveTool ? api.getActiveTool() : null;
                    const activeFeature = api.getActiveFeature ? api.getActiveFeature() : null;
                    return {
                        visibleLayers: visible,
                        time, startTime, endTime,
                        activeTool,
                        activeFeature,
                        map: { center, zoom, bounds }
                    };
                } catch (e) {
                    return { error: String(e) };
                }
            }
            """
        )
        # Persist last known UI state for the current thread if available
        try:
            if isinstance(data, dict):
                _memory_store.set_last_ui_state(_CURRENT_THREAD, data)
        except Exception:
            pass
        return json.dumps(data)
    except Exception as e:
        return json.dumps({"error": f"Error in mmgis_get_state: {str(e)}"})


@tool
async def mmgis_set_zoom(zoom: int) -> str:
    """Set the Leaflet map zoom level (integer)."""
    print(f"---TOOL CALLED: mmgis_set_zoom zoom={zoom}---")
    try:
        page = await get_mmgis_page()
        await page.evaluate("(z) => window.mmgisAPI && window.mmgisAPI.map && window.mmgisAPI.map.setZoom(z)", zoom)
        # Confirm
        current = await page.evaluate("() => window.mmgisAPI && window.mmgisAPI.map ? window.mmgisAPI.map.getZoom() : null")
        return json.dumps({"ok": True, "zoom": current})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool
async def mmgis_set_view(lat: float, lng: float, zoom: Optional[int] = None) -> str:
    """Set the map view to [lat, lng] and optional zoom."""
    print(f"---TOOL CALLED: mmgis_set_view lat={lat} lng={lng} zoom={zoom}---")
    try:
        page = await get_mmgis_page()
        await page.evaluate(
            "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom ?? m.getZoom()); }",
            {"lat": lat, "lng": lng, "zoom": zoom},
        )
        current = await page.evaluate(
            "() => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return null; const c = m.getCenter(); return { lat: c.lat, lng: c.lng, zoom: m.getZoom() }; }"
        )
        return json.dumps({"ok": True, "view": current})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@tool
async def mmgis_toggle_layer(layer_name: str, on: Optional[bool] = None) -> str:
    """Toggle a layer on/off by name. If `on` is omitted, it toggles current state."""
    print(f"---TOOL CALLED: mmgis_toggle_layer layer={layer_name} on={on}---")
    try:
        page = await get_mmgis_page()
        result = await page.evaluate(
            """
            ({layer, on}) => {
                try {
                    const api = window.mmgisAPI;
                    if (!api || !api.toggleLayer) return 'Error: mmgisAPI.toggleLayer not found';
                    return Promise.resolve(api.toggleLayer(layer, on)).then(() => 'ok');
                } catch (e) { return 'Error: ' + String(e); }
            }
            """,
            {"layer": layer_name, "on": on},
        )
        return str(result)
    except Exception as e:
        return f"Error in mmgis_toggle_layer: {str(e)}"

@tool
def mmgis_http(method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None, headers: Optional[dict] = None) -> str:
    """
    Low-level HTTP caller for MMGIS backend. Uses env MMGIS_API_BASE (default http://localhost:8889) and MMGIS_API_TOKEN.
    Returns raw text (JSON most of the time).
    """
    print(f"---TOOL CALLED: mmgis_http {method} {path}---")
    try:
        base = os.getenv("MMGIS_API_BASE", "http://localhost:8889")
        url = base.rstrip("/") + "/" + path.lstrip("/")
        hdrs = dict(headers or {})
        token = os.getenv("MMGIS_API_TOKEN")
        if token and "Authorization" not in hdrs:
            hdrs["Authorization"] = f"Bearer {token}"
        r = requests.request(method.upper(), url, params=params, json=json_body, headers=hdrs, timeout=60)
        return r.text
    except Exception as e:
        return f"Error in mmgis_http: {str(e)}"


@tool
def mmgis_get_missions() -> str:
    """Return the list of missions, preferring backend /api/configure/missions, falling back to scanning the Missions/ directory."""
    print("---TOOL CALLED: mmgis_get_missions---")
    # Try backend first
    try:
        base = os.getenv("MMGIS_API_BASE", "http://localhost:8889")
        url = base.rstrip("/") + "/api/configure/missions"
        hdrs = {}
        token = os.getenv("MMGIS_API_TOKEN")
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        r = requests.get(url, headers=hdrs, timeout=30)
        if r.ok:
            try:
                data = r.json()
                # If API returns array or object with 'missions'
                missions = data if isinstance(data, list) else data.get("missions", [])
                return json.dumps({"source": "backend", "missions": missions})
            except Exception:
                pass
    except Exception:
        pass

    # Fallback to filesystem scan
    try:
        root = os.getcwd()
        missions_dir = os.path.join(root, "Missions")
        if not os.path.isdir(missions_dir):
            missions_dir = os.path.join(root, "missions")
        if not os.path.isdir(missions_dir):
            return json.dumps({"source": "filesystem", "missions": []})
        entries = []
        for name in os.listdir(missions_dir):
            path = os.path.join(missions_dir, name)
            if os.path.isdir(path) and not name.startswith('.') and not name.endswith('_deleted_'):
                entries.append(name)
        return json.dumps({"source": "filesystem", "missions": sorted(entries)})
    except Exception as e:
        return json.dumps({"source": "filesystem", "error": str(e), "missions": []})

@tool
def read_file(file_path: str) -> str:
    """Read contents of a file"""
    print(f"---TOOL CALLED: read_file on {file_path}---")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"

@tool
def list_directory(directory_path: str) -> str:
    """List contents of a directory"""
    print(f"---TOOL CALLED: list_directory on {directory_path}---")
    try:
        items = os.listdir(directory_path)
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory {directory_path}: {str(e)}"

@tool
def find_files_by_pattern(pattern: str, directory: str = ".") -> str:
    """Find files matching a glob pattern"""
    print(f"---TOOL CALLED: find_files_by_pattern with pattern {pattern}---")
    try:
        import glob
        search_pattern = os.path.join(directory, pattern)
        matches = glob.glob(search_pattern, recursive=True)
        return "\n".join(matches) if matches else "No files found matching pattern"
    except Exception as e:
        return f"Error searching for pattern {pattern}: {str(e)}"

@tool
def memory_set_fact(key: str, value_json: str) -> str:
    """Store or update a JSON-serializable fact for this conversation thread."""
    print(f"---TOOL CALLED: memory_set_fact key={key}---")
    try:
        value = json.loads(value_json)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Invalid JSON for value: {str(e)}"})
    try:
        _memory_store.upsert_fact(_CURRENT_THREAD, key, value)
        return json.dumps({"ok": True})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

 # RAG search tool (built on Chroma index under LLM/rag/index)
@tool
def rag_search(query: str, k: int = 5, api_kind: Optional[str] = None) -> str:
    """Semantic search the local MMGIS docs index. Returns JSON with results including title and source_url.
    Optional api_kind filters: 'javascript_api', 'backend_api', 'configure_rest_api', 'docs'."""
    print(f"---TOOL CALLED: rag_search q='{query}' k={k} kind={api_kind}---")
    try:
        # Lazy imports to avoid hard dependency if index isn't used
        from langchain_community.vectorstores import Chroma

        # Configure embeddings backend
        backend = os.getenv("EMBED_BACKEND")
        # Treat OpenAI and Ollama as distinct backends
        openai_base = os.getenv("OPENAI_BASE_URL")
        openai_key = os.getenv("OPENAI_API_KEY")
        ollama_base = os.getenv("OLLAMA_BASE_URL")
        if backend is None:
            if openai_base or openai_key:
                backend = "openai"
            elif ollama_base:
                backend = "ollama"
            else:
                backend = "hf"

        if backend == "openai":
            from langchain_openai import OpenAIEmbeddings
            model = os.getenv("EMBED_MODEL", "text-embedding-3-small")
            if openai_base:
                os.environ["OPENAI_BASE_URL"] = openai_base
            if openai_key:
                os.environ["OPENAI_API_KEY"] = openai_key
            embeddings = OpenAIEmbeddings(model=model)
        elif backend == "ollama":
            from langchain_community.embeddings import OllamaEmbeddings
            model = os.getenv("EMBED_MODEL", "nomic-embed-text")
            base = ollama_base or "http://localhost:11434"
            embeddings = OllamaEmbeddings(model=model, base_url=base)
        else:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            model = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
            embeddings = HuggingFaceEmbeddings(model_name=model)
        index_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "rag", "index"))
        # Match the collection naming used at embed time
        def _slug(s: str) -> str:
            return "".join(ch if ch.isalnum() else "_" for ch in s)[:128]
        collection_name = os.getenv("EMBED_COLLECTION") or f"mmgis_docs__{_slug(backend)}__{_slug(model)}"
        if not os.path.isdir(index_dir):
            return json.dumps({"ok": False, "error": f"Index directory not found: {index_dir}"})

        vs = Chroma(persist_directory=index_dir, embedding_function=embeddings, collection_name=collection_name)
        k = max(1, int(k))
        docs = vs.similarity_search(query, k=k)

        results = []
        for d in docs:
            md = cast(dict, getattr(d, "metadata", {}))
            if api_kind and md.get("api_kind") != api_kind:
                continue
            results.append({
                "title": md.get("title", ""),
                "source_url": md.get("source_url", ""),
                "api_kind": md.get("api_kind", ""),
                "snippet": d.page_content[:500]
            })
        return json.dumps({"ok": True, "results": results})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@tool
def rag_jsapi_help(method: str, k: int = 8) -> str:
    """Return structured guidance for a JavaScript API method from the local index.
    Extracts the method section, parameters, and example code fences where possible.
    """
    try:
        from langchain_community.vectorstores import Chroma

        backend = os.getenv("EMBED_BACKEND")
        openai_base = os.getenv("OPENAI_BASE_URL")
        openai_key = os.getenv("OPENAI_API_KEY")
        ollama_base = os.getenv("OLLAMA_BASE_URL")
        if backend is None:
            if openai_base or openai_key:
                backend = "openai"
            elif ollama_base:
                backend = "ollama"
            else:
                backend = "hf"

        # Select embeddings
        if backend == "openai":
            from langchain_openai import OpenAIEmbeddings
            model = os.getenv("EMBED_MODEL", "text-embedding-3-small")
            if openai_base:
                os.environ["OPENAI_BASE_URL"] = openai_base
            if openai_key:
                os.environ["OPENAI_API_KEY"] = openai_key
            embeddings = OpenAIEmbeddings(model=model)
        elif backend == "ollama":
            from langchain_community.embeddings import OllamaEmbeddings
            model = os.getenv("EMBED_MODEL", "nomic-embed-text")
            base = ollama_base or "http://localhost:11434"
            embeddings = OllamaEmbeddings(model=model, base_url=base)
        else:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            model = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
            embeddings = HuggingFaceEmbeddings(model_name=model)

        # Prefer a dedicated JS API collection if present
        def _slug(s: str) -> str:
            return "".join(ch if ch.isalnum() else "_" for ch in s)[:128]

        index_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "rag", "index"))
        collection = os.getenv("EMBED_COLLECTION")
        if not collection:
            # try JS API name first, else default
            jsapi_col = f"mmgis_jsapi__{_slug(backend)}__{_slug(model)}"
            collection = jsapi_col
        if not os.path.isdir(index_dir):
            return json.dumps({"ok": False, "error": f"Index directory not found: {index_dir}"})

        vs = Chroma(persist_directory=index_dir, embedding_function=embeddings, collection_name=collection)

        # Retrieve candidates
        query = f"{method} parameters mmgisAPI.{method}"
        docs = vs.similarity_search(query, k=max(3, int(k)))

        # Heuristics to extract method section
        def extract_section(text: str, method_name: str) -> str:
            lines = text.splitlines()
            pattern = re.compile(rf"^\s*#{2,4}\s+.*\b{re.escape(method_name)}\b.*", re.IGNORECASE)
            start = None
            for i, ln in enumerate(lines):
                if pattern.match(ln):
                    start = i
                    break
            if start is None:
                return text[:1200]
            # find end at next heading of same or higher level
            for j in range(start + 1, len(lines)):
                if re.match(r"^\s*#{1,4}\s+", lines[j]):
                    return "\n".join(lines[start:j]).strip()
            return "\n".join(lines[start:]).strip()

        def extract_params(block: str) -> List[str]:
            params: List[str] = []
            in_params = False
            for ln in block.splitlines():
                if ln.strip().lower().startswith("####  function parameters"):
                    in_params = True
                    continue
                if in_params and re.match(r"^\s*#{1,4}\s+", ln):
                    break
                if in_params and re.match(r"^\s*[*\-]\s+", ln):
                    params.append(re.sub(r"^\s*[*\-]\s+", "", ln).strip())
            return params

        def extract_examples(block: str) -> List[str]:
            examples: List[str] = []
            fence = "```"
            cur: List[str] = []
            inside = False
            for ln in block.splitlines():
                if ln.strip().startswith(fence):
                    if inside:
                        examples.append("\n".join(cur).strip())
                        cur = []
                        inside = False
                    else:
                        inside = True
                        continue
                elif inside:
                    cur.append(ln)
            return examples

        best = None
        for d in docs:
            section = extract_section(d.page_content, method)
            if section:
                best = section
                break
        if best is None and docs:
            best = docs[0].page_content
        if best is None:
            return json.dumps({"ok": False, "error": "No documentation found"})

        params = extract_params(best)
        examples = extract_examples(best)
        title_match = re.search(r"^\s*#{2,4}\s+(.*)$", best, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else method

        return json.dumps({
            "ok": True,
            "method": method,
            "title": title,
            "parameters": params,
            "examples": examples[:3],
            "excerpt": best[:1200],
        })
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

tools = [
    run_api,
    mmgis_eval,
    mmgis_get_state,
    mmgis_set_zoom,
    mmgis_set_view,
    mmgis_toggle_layer,
    mmgis_http,
    mmgis_get_missions,
    read_file,
    list_directory,
    find_files_by_pattern,
    memory_set_fact,
    rag_search,
    rag_jsapi_help,
]

# 2. Define Agent State and Agents
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    facts: Dict[str, Any]
    brief: Optional[str]
    last_ui_state: Optional[Dict[str, Any]]
    run_id: str
    thread: Dict[str, Optional[str]]
    scratch: Dict[str, Any]
    step_count: int

def create_planner_model():
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0
    )

def create_api_agent_model():
    llm = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0
    )
    return llm.bind_tools(tools)

async def _summarize_history(model: ChatOpenAI, messages: List[Tuple[str, str]], current_brief: Optional[str]) -> str:
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

def _planner_context(memory: MemoryStore, thread: Dict[str, Optional[str]]) -> List[Tuple[str, str]]:
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

# 3. Assemble the graph
def create_agent(thread: Dict[str, Optional[str]]):
    
    planner_prompt = """You are the orchestrator for MMGIS. Your job is to output EXACTLY ONE of the following per turn, then stop:

- STEP: <a single, clear instruction for the api_agent to execute now>
- FINAL: <a concise final answer for the user>

Constraints:
- You do NOT have tools; only the api_agent can execute tools.
- Keep each STEP minimal (one coherent action). Examples: "get the UI state", "list missions", "set zoom to 4".
- Prefer known tools when phrasing the STEP: mmgis_get_state, mmgis_get_missions, mmgis_set_zoom, mmgis_set_view, mmgis_toggle_layer, mmgis_eval, mmgis_http, read_file, list_directory, find_files_by_pattern, rag_search, rag_jsapi_help.
- If the user requests the current UI/map state, the FIRST STEP should be to get it: "STEP: get the UI state".
- If the user is likely finished, emit FINAL.
"""
    
    api_agent_prompt = """You are an API and file system specialist for MMGIS with tool access.

Your input includes the latest STEP from the planner (or FINAL).

Do exactly this:
1) If the latest planner message starts with FINAL:, do nothing and return immediately (no tools).
2) If it starts with STEP:, interpret the instruction and EXECUTE it, using as many tool calls as needed.
   - Filesystem: list_directory, read_file, find_files_by_pattern
   - UI/map: mmgis_get_state, mmgis_toggle_layer, mmgis_set_zoom, mmgis_set_view, mmgis_eval
   - Backend: mmgis_http (inject token from env)
   - Prefer parameterized wrappers over run_api
3) When the step is successfully completed, reply with a concise summary prefixed with: STEP_DONE:
   - Include only essential results (e.g., missions list, new zoom level)
   - Do not request more tools in this reply

State updates:
- When you obtain the MMGIS UI state, it is automatically persisted. You may also persist key facts using memory_set_fact(key, value_json) when appropriate (e.g., user preferences).

Limits and halting:
- Avoid repeating the same tool calls.
- Perform at most 5 tool calls in this single step; if you hit the limit, summarize progress and say what to do next.

Documentation: Consult 'LLM/APIs.md' via read_file if needed before using APIs.
"""

    planner_model = create_planner_model()
    api_agent_model = create_api_agent_model()

    async def planner_node(state: AgentState):
        # Include durable memory context ahead of the planner prompt
        mem_ctx = _planner_context(_memory_store, state.get("thread", {}))
        messages = [("system", planner_prompt)] + mem_ctx + state["messages"]
        result = await planner_model.ainvoke(messages)
        # Increment planner step count
        step_count = state.get("step_count", 0) + 1
        return {"messages": [result], "step_count": step_count}

    async def api_agent_node(state: AgentState):
        # Prepend guidance; the model should then call tools directly for this single step
        preface = [("system", api_agent_prompt)]
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
        # If planner emitted FINAL or exceeded step budget, end
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
        # No tool calls pending; hand back to planner
        return "planner"

    graph.add_conditional_edges("api_agent", continue_from_api_agent, {"tools": "tools", "planner": "planner"})
    
    graph.add_edge("tools", "api_agent")

    agent = graph.compile()
    return agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MMGIS LLM Agent runner")
    parser.add_argument("-m", "--message", type=str, help="Send a single message to the agent and print the response")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start an interactive prompt loop")
    parser.add_argument("--user-id", type=str, default=os.getenv("MMGIS_USER_ID", os.getenv("USER", "anon")))
    parser.add_argument("--session-id", type=str, default=os.getenv("MMGIS_SESSION_ID", "local"))
    parser.add_argument("--mission-id", type=str, default=os.getenv("MMGIS_MISSION_ID"))
    args = parser.parse_args()

    thread = {"user_id": args.user_id, "session_id": args.session_id, "mission_id": args.mission_id}
    _CURRENT_THREAD = thread
    run_id = _memory_store.new_run_id()

    # Load prior thread messages (short-term memory)
    prior: List[Tuple[str, str]] = _memory_store.load_recent_messages(thread, limit=50)

    agent = create_agent(thread)

    async def run_single(msg: str):
        # Persist user turn
        _memory_store.append_messages(thread, [("user", msg)], run_id)
        initial_messages: List[Tuple[str, str]] = prior + [("user", msg)]
        resp = await agent.ainvoke({
            "messages": initial_messages,
            "facts": _memory_store.get_facts(thread),
            "brief": _memory_store.get_brief(thread),
            "last_ui_state": _memory_store.get_last_ui_state(thread),
            "run_id": run_id,
            "thread": thread,
            "scratch": {},
            "step_count": 0,
        })
        # Persist assistant turn (compact)
        _memory_store.append_messages(thread, [("assistant", json.dumps(resp, default=str)[:4000])], run_id)
        # Optional summarization
        try:
            flat_messages = prior + [("assistant", json.dumps(resp, default=str)[:1000])]
            if len(flat_messages) >= 40:
                new_brief = await _summarize_history(create_planner_model(), flat_messages, _memory_store.get_brief(thread))
                _memory_store.set_brief(thread, new_brief)
        except Exception:
            pass
        print(resp)

    async def run_interactive():
        print("Interactive mode. Type 'exit' or Ctrl-D to quit.")
        # Seed in-memory view from durable store
        messages: List[Tuple[str, str]] = prior.copy()
        while True:
            try:
                user_in = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user_in.lower() in {"exit", "quit"}:
                break
            if not user_in:
                continue

            messages.append(("user", user_in))
            _memory_store.append_messages(thread, [("user", user_in)], run_id)

            resp = await agent.ainvoke({
                "messages": messages,
                "facts": _memory_store.get_facts(thread),
                "brief": _memory_store.get_brief(thread),
                "last_ui_state": _memory_store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })

            # Persist assistant turn and keep local context concise
            rsp_text = json.dumps(resp, default=str)[:4000]
            messages.append(("assistant", rsp_text))
            _memory_store.append_messages(thread, [("assistant", rsp_text)], run_id)

            # Periodically summarize history into brief
            try:
                if len(messages) >= 40:
                    new_brief = await _summarize_history(create_planner_model(), messages, _memory_store.get_brief(thread))
                    _memory_store.set_brief(thread, new_brief)
                    # Prune to last 10 visible turns to keep loop fast
                    messages = messages[-10:]
            except Exception:
                pass

            print(resp)

    if args.interactive:
        asyncio.run(run_interactive())
    elif args.message:
        asyncio.run(run_single(args.message))
    else:
        async def main():
            # Smoke test 1: list files
            response1 = await agent.ainvoke({
                "messages": prior + [("user", "List all the files in the current directory.")],
                "facts": _memory_store.get_facts(thread),
                "brief": _memory_store.get_brief(thread),
                "last_ui_state": _memory_store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })
            print(response1)
            # Smoke test 2: try MMGIS state (works only if MMGIS page is connected over CDP)
            response2 = await agent.ainvoke({
                "messages": prior + [("user", "Get the MMGIS UI state.")],
                "facts": _memory_store.get_facts(thread),
                "brief": _memory_store.get_brief(thread),
                "last_ui_state": _memory_store.get_last_ui_state(thread),
                "run_id": run_id,
                "thread": thread,
                "scratch": {},
                "step_count": 0,
            })
            print(response2)
        asyncio.run(main())
