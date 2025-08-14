import asyncio
import json
import sys

from LLM.browser import get_mmgis_page


TARGET_LAYER_NAME = "elevation-tiles"


async def get_configs_and_visible(page):
    return await page.evaluate(
        """
        () => {
            try {
                const api = window.mmgisAPI;
                if (!api) return { cfgs: {}, visible: {} };
                const cfgs = (typeof api.getLayerConfigs === 'function') ? (api.getLayerConfigs() || {}) : {};
                const visible = (typeof api.getVisibleLayers === 'function') ? (api.getVisibleLayers() || {}) : {};
                return { cfgs, visible };
            } catch (e) { return { cfgs: {}, visible: {} }; }
        }
        """
    )


async def as_uuid(page, name_or_uuid):
    return await page.evaluate(
        """
        (x) => {
            try {
                const api = window.mmgisAPI;
                if (!api) return x;
                if (typeof api.asLayerUUID === 'function') {
                    return api.asLayerUUID(x) || x;
                }
                return x;
            } catch(e) { return x; }
        }
        """,
        name_or_uuid,
    )


async def toggle_layer(page, name_or_uuid, on):
    return await page.evaluate(
        """
        ({layer, on}) => {
            try {
                const api = window.mmgisAPI;
                if (!api || typeof api.toggleLayer !== 'function') return 'toggleLayer not available';
                const id = (typeof api.asLayerUUID === 'function') ? api.asLayerUUID(layer) : layer;
                return Promise.resolve(api.toggleLayer(id || layer, on)).then(() => 'ok');
            } catch (e) { return String(e); }
        }
        """,
        {"layer": name_or_uuid, "on": on},
    )


async def main():
    try:
        page = await get_mmgis_page()
    except Exception as e:
        print("SKIP: Could not connect to a debuggable Chrome or find MMGIS tab.")
        print(f"Details: {e}")
        return 2

    ready = await page.evaluate("() => !!(window.mmgisAPI && window.mmgisAPI.map)")
    if not ready:
        print("SKIP: MMGIS page connected but window.mmgisAPI.map is not ready.")
        return 2

    data = await get_configs_and_visible(page)
    cfgs = data.get("cfgs", {}) or {}
    visible = data.get("visible", {}) or {}

    # Resolve target elevation layer UUID
    target_uuid = await as_uuid(page, TARGET_LAYER_NAME)
    # Fallback: search cfgs by display_name/name
    if not target_uuid or target_uuid not in visible:
        for key, cfg in (cfgs.items() if isinstance(cfgs, dict) else []):
            name = (cfg or {}).get("name") or (cfg or {}).get("display_name") or key
            if name == TARGET_LAYER_NAME:
                target_uuid = (cfg or {}).get("uuid") or key
                break

    # Ensure target layer is on
    if target_uuid:
        if not visible.get(target_uuid, False):
            res = await toggle_layer(page, target_uuid, True)
            print(f"Turned ON '{TARGET_LAYER_NAME}' ->", res)
    else:
        print(f"WARN: Could not resolve target layer '{TARGET_LAYER_NAME}'")

    # Turn off all other active layers
    turned_off = []
    for uid, is_on in (visible.items() if isinstance(visible, dict) else []):
        if is_on and uid != target_uuid:
            res = await toggle_layer(page, uid, False)
            turned_off.append(uid)
    print("Turned OFF layers (uuids):", json.dumps(turned_off))

    # Verify state
    new_visible = (await get_configs_and_visible(page)).get("visible", {}) or {}
    on_keys = [k for k, v in new_visible.items() if v]
    print("Now ON (uuids):", json.dumps(on_keys))

    ok = True
    if target_uuid and not new_visible.get(target_uuid, False):
        ok = False
        print("FAIL: target elevation-tiles is not ON")
    # All others should be False
    others_on = [k for k in on_keys if k != target_uuid]
    if others_on:
        ok = False
        print("FAIL: other layers are still ON:", json.dumps(others_on))

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


