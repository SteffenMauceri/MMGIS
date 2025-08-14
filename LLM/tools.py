import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, cast

import requests
from langchain_core.tools import tool

from LLM.browser import get_mmgis_page
from LLM.state import get_memory_store, get_current_thread


@tool
async def run_api(command: str) -> str:
    """
    Executes a command against the MMGIS Frontend JavaScript API in the connected browser.
    Example: 'window.mmgisAPI.map.panTo([-5.4, 137.8], 8);'
    """
    print(f"---TOOL CALLED: run_api with command: {command}---")
    try:
        page = await get_mmgis_page()
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
                    // Prefer human-readable layer names via getLayerConfigs
                    let layerNames = null;
                    try {
                        if (api.getLayerConfigs) {
                            const cfgs = api.getLayerConfigs();
                            if (cfgs && typeof cfgs === 'object') layerNames = Object.keys(cfgs);
                        }
                    } catch (_) {}
                    const visible = api.getVisibleLayers ? api.getVisibleLayers() : null;
                    const time = api.getTime ? api.getTime() : null;
                    const startTime = api.getStartTime ? api.getStartTime() : null;
                    const endTime = api.getEndTime ? api.getEndTime() : null;
                    const activeTool = api.getActiveTool ? api.getActiveTool() : null;
                    const activeFeature = api.getActiveFeature ? api.getActiveFeature() : null;
                    return {
                        visibleLayers: visible,
                        layerNames: layerNames,
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
        try:
            if isinstance(data, dict):
                get_memory_store().set_last_ui_state(get_current_thread(), data)
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
                missions = data if isinstance(data, list) else data.get("missions", [])
                return json.dumps({"source": "backend", "missions": missions})
            except Exception:
                pass
    except Exception:
        pass

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
        get_memory_store().upsert_fact(get_current_thread(), key, value)
        return json.dumps({"ok": True})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool
def rag_search(query: str, k: int = 5, api_kind: Optional[str] = None) -> str:
    """Semantic search the local MMGIS docs index. Returns JSON with results including title and source_url.
    Optional api_kind filters: 'javascript_api', 'backend_api', 'configure_rest_api', 'docs'."""
    print(f"---TOOL CALLED: rag_search q='{query}' k={k} kind={api_kind}---")
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

        def _slug(s: str) -> str:
            return "".join(ch if ch.isalnum() else "_" for ch in s)[:128]

        index_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "rag", "index"))
        collection = os.getenv("EMBED_COLLECTION")
        if not collection:
            jsapi_col = f"mmgis_jsapi__{_slug(backend)}__{_slug(model)}"
            collection = jsapi_col
        if not os.path.isdir(index_dir):
            return json.dumps({"ok": False, "error": f"Index directory not found: {index_dir}"})

        vs = Chroma(persist_directory=index_dir, embedding_function=embeddings, collection_name=collection)

        query = f"{method} parameters mmgisAPI.{method}"
        docs = vs.similarity_search(query, k=max(3, int(k)))

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


