import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple, cast

import requests
from langchain_core.tools import tool

from LLM.browser import get_mmgis_page
from LLM.state import get_memory_store, get_current_thread
from LLM.config import get_config_value


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
        # If caller used default, allow config to override
        if int(max_kb) == 256:
            try:
                max_kb = int(get_config_value("tools.mmgis_eval_max_kb", None, 256, int))
            except Exception:
                max_kb = 256
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
                    // Prefer human-readable layer names via getLayerConfigs using display_name/name
                    let layerNames = null;
                    let layers = [];
                    let visible = api.getVisibleLayers ? api.getVisibleLayers() : null;
                    try {
                        if (api.getLayerConfigs) {
                            const cfgs = api.getLayerConfigs();
                            if (cfgs && typeof cfgs === 'object') {
                                layerNames = Object.values(cfgs).map(cfg => (cfg && (cfg.display_name || cfg.name)) || null).filter(Boolean);
                                const entries = Object.entries(cfgs);
                                for (const [key, cfg] of entries) {
                                    const uuid = (cfg && cfg.uuid) ? cfg.uuid : key;
                                    const name = (cfg && cfg.name) ? cfg.name : key;
                                    const display_name = (cfg && (cfg.display_name || cfg.name)) ? (cfg.display_name || cfg.name) : name;
                                    const isVisible = visible ? (visible[uuid] ?? visible[name] ?? false) : null;
                                    layers.push({ uuid, name, display_name, visible: isVisible });
                                }
                            }
                        }
                    } catch (_) {}
                    const time = api.getTime ? api.getTime() : null;
                    const startTime = api.getStartTime ? api.getStartTime() : null;
                    const endTime = api.getEndTime ? api.getEndTime() : null;
                    const activeTool = api.getActiveTool ? api.getActiveTool() : null;
                    const activeFeature = api.getActiveFeature ? api.getActiveFeature() : null;
                    return {
                        visibleLayers: visible,
                        layerNames: layerNames,
                        layers: layers,
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
async def mmgis_list_layers() -> str:
    """Return a detailed list of available layers from mmgisAPI.getLayerConfigs().
    Each entry includes at least { uuid, name, display_name } and may include
    { type, kind, category, group, description, tags } when available.
    """
    print("---TOOL CALLED: mmgis_list_layers---")
    try:
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
        layers = []
        if isinstance(cfgs, dict):
            for key, cfg in cfgs.items():
                cfg = cfg or {}
                entry = {
                    "uuid": cfg.get("uuid") or key,
                    "name": cfg.get("name") or key,
                    "display_name": cfg.get("display_name") or cfg.get("name") or key,
                }
                # Optional metadata if present
                for k in ["type", "kind", "category", "group", "description", "tags"]:
                    if k in cfg:
                        entry[k] = cfg.get(k)
                layers.append(entry)
        return json.dumps({"ok": True, "layers": layers})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool
def geocode_place(place: str) -> str:
    """
    Resolve a place name to lat/lng coordinates and a reasonable zoom.
    Offline-first: looks for LLM/places_local.json; falls back to a small built-in dictionary.
    Returns JSON { ok, lat, lng, zoom, source } or { ok: false, error }.
    """
    try:
        place_norm = (place or "").strip().lower()
        # 1) local file override
        try:
            here = os.path.dirname(__file__)
            p = os.path.join(here, "places_local.json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if place_norm == str(k).lower():
                            lat = float(v.get("lat"))
                            lng = float(v.get("lng"))
                            zoom = int(v.get("zoom", 12))
                            return json.dumps({"ok": True, "lat": lat, "lng": lng, "zoom": zoom, "source": "local_file"})
        except Exception:
            pass

        # 2) minimal built-in examples for tests
        builtins = {
            "pasadena": {"lat": 34.1478, "lng": -118.1445, "zoom": 12},
            "los angeles": {"lat": 34.0522, "lng": -118.2437, "zoom": 11},
            "paris": {"lat": 48.8566, "lng": 2.3522, "zoom": 12},
            "san francisco": {"lat": 37.7749, "lng": -122.4194, "zoom": 13},
        }
        # allow city with suffix like ", ca" or ", california"
        for k, v in builtins.items():
            if place_norm == k or place_norm.startswith(k + ","):
                out = {"ok": True, "lat": v["lat"], "lng": v["lng"], "zoom": v["zoom"], "source": "builtin"}
                return json.dumps(out)

        return json.dumps({"ok": False, "error": f"Unknown place: {place}"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool
def mmgis_geocode_place(place: str) -> str:
    """Alias for geocode_place to accommodate models that prefix with mmgis_."""
    return geocode_place(place)


@tool
async def mmgis_find_layer(query: str) -> str:
    """
    Find layers by human-readable name using mmgisAPI.getLayerConfigs() live from the page.
    Performs case-insensitive substring matching with simple synonyms.
    Returns JSON { ok, matches: [ { name, display_name, uuid } ] }.
    """
    try:
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
        matches: List[Dict[str, str]] = []
        q = (query or "").strip().lower()
        synonyms = {
            "elevation": ["elevation", "terrain", "hillshade"],
            "wind": ["wind", "hrrr", "gfs"],
            "imagery": ["imagery", "satellite", "world imagery", "esri", "firefly"],
            "satellite": ["satellite", "imagery", "world imagery", "esri", "firefly"],
        }
        tokens = set(q.split())
        expanded: List[str] = []
        for t in tokens:
            expanded.append(t)
            for syn in synonyms.get(t, []):
                expanded.append(syn)

        def _score(name: str) -> int:
            n = (name or "").lower()
            return sum(1 for t in expanded if t and t in n)

        if isinstance(cfgs, dict):
            for key, cfg in cfgs.items():
                name = (cfg or {}).get("name") or key
                disp = (cfg or {}).get("display_name") or name
                uid = (cfg or {}).get("uuid") or key
                s = _score(disp) or _score(name)
                if s > 0:
                    matches.append({"name": name, "display_name": disp, "uuid": uid})

        matches.sort(key=lambda m: _score(m.get("display_name") or m.get("name") or ""), reverse=True)
        return json.dumps({"ok": True, "matches": matches})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


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
        # Persist updated UI state after changing view
        try:
            state = await page.evaluate(
                """
                () => {
                    try {
                        const api = window.mmgisAPI; if (!api) return null;
                        const map = api.map;
                        const center = map ? map.getCenter() : null;
                        const zoom = map ? map.getZoom() : null;
                        const b = map ? map.getBounds() : null;
                        const bounds = b ? { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() } : null;
                        const visible = api.getVisibleLayers ? api.getVisibleLayers() : null;
                        let layerNames = null; let layers = [];
                        try {
                            if (api.getLayerConfigs) {
                                const cfgs = api.getLayerConfigs();
                                if (cfgs && typeof cfgs === 'object') {
                                    layerNames = Object.values(cfgs).map(cfg => (cfg && (cfg.display_name || cfg.name)) || null).filter(Boolean);
                                    const entries = Object.entries(cfgs);
                                    for (const [key, cfg] of entries) {
                                        const uuid = (cfg && cfg.uuid) ? cfg.uuid : key;
                                        const name = (cfg && cfg.name) ? cfg.name : key;
                                        const display_name = (cfg && (cfg.display_name || cfg.name)) ? (cfg.display_name || cfg.name) : name;
                                        const isVisible = visible ? (visible[uuid] ?? visible[name] ?? false) : null;
                                        layers.push({ uuid, name, display_name, visible: isVisible });
                                    }
                                }
                            }
                        } catch (_) {}
                        return { visibleLayers: visible, layerNames, layers, map: { center, zoom, bounds } };
                    } catch (e) { return null; }
                }
                """
            )
            if isinstance(state, dict):
                get_memory_store().set_last_ui_state(get_current_thread(), state)
        except Exception:
            pass
        return json.dumps({"ok": True, "view": current})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


@tool
async def mmgis_set_view_center(center: Dict[str, float], zoom: Optional[int] = None) -> str:
    """Set the map view given a center object {lat, lng} plus optional zoom.
    This is a convenience alias for models that pass a 'center' dict.
    """
    try:
        lat = float(center.get("lat"))
        lng = float(center.get("lng"))
    except Exception:
        return json.dumps({"ok": False, "error": "center must include numeric lat and lng"})
    return await mmgis_set_view(lat, lng, zoom)


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
                    // Accept either a UUID or a human-readable name by using asLayerUUID if available
                    let id = (typeof api.asLayerUUID === 'function') ? api.asLayerUUID(layer) : layer;
                    if (!id || id === layer) {
                        try {
                            if (typeof api.getLayerConfigs === 'function') {
                                const cfgs = api.getLayerConfigs() || {};
                                const target = String(layer || '').toLowerCase();
                                // fuzzy match by display_name or name
                                for (const [key, cfg] of Object.entries(cfgs)) {
                                    const nm = (cfg && (cfg.display_name || cfg.name)) || key;
                                    if (String(nm).toLowerCase().includes(target)) {
                                        id = (cfg && cfg.uuid) ? cfg.uuid : key;
                                        break;
                                    }
                                }
                            }
                        } catch (_) {}
                    }
                    return Promise.resolve(api.toggleLayer(id || layer, on)).then(() => 'ok');
                } catch (e) { return 'Error: ' + String(e); }
            }
            """,
            {"layer": layer_name, "on": on},
        )
        # Persist updated UI state after toggling
        try:
            state = await page.evaluate(
                """
                () => {
                    try {
                        const api = window.mmgisAPI; if (!api) return null;
                        const map = api.map;
                        const center = map ? map.getCenter() : null;
                        const zoom = map ? map.getZoom() : null;
                        const b = map ? map.getBounds() : null;
                        const bounds = b ? { north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() } : null;
                        const visible = api.getVisibleLayers ? api.getVisibleLayers() : null;
                        let layerNames = null; let layers = [];
                        try {
                            if (api.getLayerConfigs) {
                                const cfgs = api.getLayerConfigs();
                                if (cfgs && typeof cfgs === 'object') {
                                    layerNames = Object.values(cfgs).map(cfg => (cfg && (cfg.display_name || cfg.name)) || null).filter(Boolean);
                                    const entries = Object.entries(cfgs);
                                    for (const [key, cfg] of entries) {
                                        const uuid = (cfg && cfg.uuid) ? cfg.uuid : key;
                                        const name = (cfg && cfg.name) ? cfg.name : key;
                                        const display_name = (cfg && (cfg.display_name || cfg.name)) ? (cfg.display_name || cfg.name) : name;
                                        const isVisible = visible ? (visible[uuid] ?? visible[name] ?? false) : null;
                                        layers.push({ uuid, name, display_name, visible: isVisible });
                                    }
                                }
                            }
                        } catch (_) {}
                        return { visibleLayers: visible, layerNames, layers, map: { center, zoom, bounds } };
                    } catch (e) { return null; }
                }
                """
            )
            if isinstance(state, dict):
                get_memory_store().set_last_ui_state(get_current_thread(), state)
        except Exception:
            pass
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
        base = get_config_value("mmgis_api.base_url", "MMGIS_API_BASE", "http://localhost:8889", str)
        url = base.rstrip("/") + "/" + path.lstrip("/")
        hdrs = dict(headers or {})
        token = get_config_value("mmgis_api.token", "MMGIS_API_TOKEN", None)
        if token and "Authorization" not in hdrs:
            hdrs["Authorization"] = f"Bearer {token}"
        http_timeout = get_config_value("mmgis_api.http_timeout_s", None, 60, int)
        r = requests.request(method.upper(), url, params=params, json=json_body, headers=hdrs, timeout=http_timeout)
        return r.text
    except Exception as e:
        return f"Error in mmgis_http: {str(e)}"


@tool
def mmgis_get_missions() -> str:
    """Return the list of missions, preferring backend /api/configure/missions, falling back to scanning the Missions/ directory."""
    print("---TOOL CALLED: mmgis_get_missions---")
    try:
        base = get_config_value("mmgis_api.base_url", "MMGIS_API_BASE", "http://localhost:8889", str)
        url = base.rstrip("/") + "/api/configure/missions"
        hdrs = {}
        token = get_config_value("mmgis_api.token", "MMGIS_API_TOKEN", None)
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        http_timeout = get_config_value("mmgis_api.http_timeout_s", None, 60, int)
        r = requests.get(url, headers=hdrs, timeout=http_timeout)
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
async def execute_ui_intent(intent: str, args: Optional[dict] = None) -> str:
    """
    Execute a UI intent on the MMGIS page.
    Args:
        intent: The name of the intent (e.g., "panTo", "zoomTo", "toggleLayer", "setView").
        args: A dictionary of arguments for the intent.
    Returns:
        A JSON string indicating success or failure.
    """
    print(f"---TOOL CALLED: execute_ui_intent intent={intent} args={args}---")
    try:
        page = await get_mmgis_page()
        args = args or {}
        if intent == "panTo":
            lat = args.get("lat")
            lng = args.get("lng")
            zoom = args.get("zoom")
            if lat is not None and lng is not None:
                await page.evaluate(
                    "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.panTo([lat, lng], zoom ?? m.getZoom()); }",
                    {"lat": lat, "lng": lng, "zoom": zoom},
                )
                return json.dumps({"ok": True, "message": f"Panned to {lat}, {lng} with zoom {zoom}"})
            return json.dumps({"ok": False, "error": "Missing lat or lng in panTo args"})
        if intent == "zoomTo":
            lat = args.get("lat")
            lng = args.get("lng")
            zoom = args.get("zoom")
            if lat is not None and lng is not None and zoom is not None:
                await page.evaluate(
                    "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom); }",
                    {"lat": lat, "lng": lng, "zoom": zoom},
                )
                return json.dumps({"ok": True, "message": f"Zoomed to {lat}, {lng} with zoom {zoom}"})
            return json.dumps({"ok": False, "error": "Missing lat, lng, or zoom in zoomTo args"})
        if intent == "toggleLayer":
            layer_name = args.get("layer_name")
            on = args.get("on")
            if layer_name is not None:
                await page.evaluate(
                    "({name, on}) => { const api = window.mmgisAPI; if(!api || !api.toggleLayer) return; return Promise.resolve(api.toggleLayer(name, on)); }",
                    {"name": layer_name, "on": on},
                )
                return json.dumps({"ok": True, "message": f"Toggled layer '{layer_name}' to {on}"})
            return json.dumps({"ok": False, "error": "Missing layer_name in toggleLayer args"})
        if intent == "setView":
            lat = args.get("lat")
            lng = args.get("lng")
            zoom = args.get("zoom")
            if lat is not None and lng is not None:
                await page.evaluate(
                    "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom ?? m.getZoom()); }",
                    {"lat": lat, "lng": lng, "zoom": zoom},
                )
                return json.dumps({"ok": True, "message": f"Set view to {lat}, {lng} with zoom {zoom}"})
            return json.dumps({"ok": False, "error": "Missing lat or lng in setView args"})
        if intent == "setZoom":
            zoom = args.get("zoom")
            if zoom is not None:
                await page.evaluate(
                    "(z) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setZoom(z); }",
                    int(args.get("zoom")),
                )
                return json.dumps({"ok": True, "message": f"Set zoom to {zoom}"})
            return json.dumps({"ok": False, "error": "Missing zoom in setZoom args"})
        return json.dumps({"ok": False, "error": f"Unknown intent: {intent}"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Error executing UI intent: {str(e)}"})


tools = [
    mmgis_eval,
    mmgis_get_state,
    mmgis_list_layers,
    mmgis_set_zoom,
    mmgis_set_view,
    mmgis_set_view_center,
    mmgis_toggle_layer,
    geocode_place,
    mmgis_geocode_place,
    mmgis_find_layer,
    mmgis_http,
    mmgis_get_missions,
    execute_ui_intent,
    read_file,
    list_directory,
    find_files_by_pattern,
    memory_set_fact,
]


