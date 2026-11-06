"""Exercise a running, isolated ComfyUI: python tests/smoke_comfy.py [base URL]."""

import io
import json
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image


base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"
opener = build_opener(ProxyHandler({}))


def request(path, method="GET", body=None, origin=base, raw=False):
    data = None if body is None else json.dumps(body).encode()
    with opener.open(Request(origin + path, data=data, method=method,
                             headers={"Content-Type": "application/json"}), timeout=90) as response:
        value = response.read()
        return value if raw else json.loads(value) if value else None


def main():
    assert request("/object_info/SaveInArtifex")["SaveInArtifex"]["output"] == []
    group_name = "Plugin smoke " + str(time.time_ns())
    prompt = {
        "1": {"class_type": "EmptyImage", "inputs": {"width": 32, "height": 24, "batch_size": 3, "color": 2848554}},
        "2": {"class_type": "SaveInArtifex", "inputs": {
            "images": ["1", 0], "group": "deleted-group", "color_group": "deleted-color", "auto_tag": False,
            "group_name": group_name, "color_group_name": ["3", 0],
        }},
        "3": {"class_type": "TruncateText", "inputs": {"texts": "smoke-color", "max_length": 77}},
    }
    queued = request("/prompt", "POST", {"prompt": prompt, "extra_data": {"extra_pnginfo": {"workflow": {"nodes": []}}}})
    prompt_id = queued["prompt_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        history = request("/history/" + prompt_id)
        if prompt_id in history:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Workflow did not finish")
    history = history[prompt_id]
    assert history["status"]["status_str"] == "success", history["status"]
    output = history["outputs"]["2"]
    assert len(output["images"]) == len(output["artifex_images"]) == 3
    gallery = request("/artifex/open", "POST", {"target": "gallery"})["url"].rstrip("/")
    assert request("/artifex/open", "POST", {"target": "group"})["url"].rstrip("/") == gallery
    groups = request("/api/groups", origin=gallery)
    group = next(item for item in groups if item["name"] == group_name)
    group_id = group["id"]
    try:
        assert group["illustration_count"] == 3
        config = request("/api/group-config", origin=gallery)
        colors = config["groups"][str(group_id)]["colors"]["sets"][0]
        assert len(colors["manual_assignments"]) == 3
        assert len(colors["pairs"]) == 1 and colors["pairs"][0]["customName"] == "smoke-color", colors
        assert set(colors["manual_assignments"].values()) == {colors["pairs"][0]["id"]}, colors
        first = output["artifex_images"][0]
        data = request(f"/api/illustrations/{first['image_id']}/file", origin=gallery, raw=True)
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (32, 24)
            assert "prompt" in image.info and "workflow" in image.info
        for record in output["artifex_images"]:
            request("/artifex/images/" + record["id"], "DELETE")
        remaining = request(f"/api/groups/{group_id}/illustrations", origin=gallery)
        assert remaining["total"] == 0
        assert not list((Path(__file__).resolve().parents[1] / "staging").glob("*/*.png"))
        print("ComfyUI smoke passed: schema, stale selections, batch upload, metadata, color assignment, native previews, deletion, home links, staging cleanup")
    finally:
        # This uniquely named group was created by this smoke only.
        request(f"/api/groups/{group_id}", "DELETE", origin=gallery)


if __name__ == "__main__":
    main()
