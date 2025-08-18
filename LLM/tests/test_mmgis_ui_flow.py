import asyncio
import json
import os
import sys

from LLM.browser import get_mmgis_page


PASADENA = {"lat": 34.1478, "lng": -118.1445, "zoom": 12}


async def list_candidate_layers(page):
    # Prefer the documented getLayerConfigs() and extract display_name or name for humans
    names = await page.evaluate(
        """
        () => {
            try {
                const api = window.mmgisAPI;
                if (!api || typeof api.getLayerConfigs !== 'function') return [];
                const cfgs = api.getLayerConfigs();
                const out = [];
                for (const [key, cfg] of Object.entries(cfgs || {})) {
                    const nm = (cfg && (cfg.display_name || cfg.name)) || key;
                    if (nm) out.push(nm);
                }
                return out;
            } catch (e) { return []; }
        }
        """
    )
    if isinstance(names, list):
        return names
    return []


async def toggle_layer_on(page, layer_name):
    result = await page.evaluate(
        """
        ({ layer }) => {
            try {
                const api = window.mmgisAPI;
                if (!api || !api.toggleLayer) return 'toggleLayer not available';
                return Promise.resolve(api.toggleLayer(layer, true)).then(() => 'ok');
            } catch (e) { return String(e); }
        }
        """,
        {"layer": layer_name},
    )
    return result


async def set_view(page, lat, lng, zoom=None):
    await page.evaluate(
        "({lat, lng, zoom}) => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return; m.setView([lat, lng], zoom ?? m.getZoom()); }",
        {"lat": lat, "lng": lng, "zoom": zoom},
    )
    center = await page.evaluate(
        "() => { const m = window.mmgisAPI && window.mmgisAPI.map; if(!m) return null; const c = m.getCenter(); return { lat: c.lat, lng: c.lng, zoom: m.getZoom() }; }"
    )
    return center


async def main():
    # Preconditions: Chrome started with --remote-debugging-port and MMGIS open
    try:
        page = await get_mmgis_page()
    except Exception as e:
        print("SKIP: Could not connect to a debuggable Chrome or find MMGIS tab.")
        print("Hint: Launch Chrome with remote debugging and open MMGIS, e.g.:")
        print('  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$(mktemp -d)"')
        print("  Then open http://localhost:8888 in that browser instance.")
        print(f"Details: {e}")
        return 0

    # Ensure mmgisAPI readiness
    ready = await page.evaluate("() => !!(window.mmgisAPI && window.mmgisAPI.map)")
    if not ready:
        print("SKIP: MMGIS page connected but window.mmgisAPI.map is not ready.")
        print("Open MMGIS fully and ensure the map has initialized.")
        return 0

    # 1) Tell the user what layers they could visualize
    layers = await list_candidate_layers(page)
    print("Candidate layers:", json.dumps(layers))

    # 2) User specifies a layer they want to see -> pick first available if any
    selected = None
    if layers:
        selected = layers[0]
        result = await toggle_layer_on(page, selected)
        print(f"Toggled layer '{selected}' on ->", result)
    else:
        print("No layers found to toggle. Skipping toggle step.")

    # 3) Center the map over Pasadena, CA
    new_view = await set_view(page, PASADENA["lat"], PASADENA["lng"], PASADENA["zoom"])
    print("New map view:", json.dumps(new_view))

    # Simple assertions
    ok = True
    if new_view is None or abs(new_view.get("lat", 0) - PASADENA["lat"]) > 0.5 or abs(new_view.get("lng", 0) - PASADENA["lng"]) > 0.5:
        ok = False
        print("FAIL: map center not near Pasadena coordinates")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


