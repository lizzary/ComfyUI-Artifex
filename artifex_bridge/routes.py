import asyncio
import ipaddress
import logging
import os
import subprocess
import sys

from aiohttp import web
from server import PromptServer

from . import runtime
from .client import ArtifexError, DEFAULT_GROUP
from .process import ArtifexUnavailable
from .queue import MARKER


logger = logging.getLogger("comfyui.artifex")


def options(group="default", group_name=None):
    client = runtime.client
    groups = client.groups()
    candidates = [item for item in groups if group_name and item["name"] == group_name.strip()]
    if not candidates:
        candidates = [item for item in groups if item["value"] == group]
    if not candidates and group == "default":
        candidates = [item for item in groups if item["name"] == DEFAULT_GROUP]
    selected = max(candidates, key=lambda item: (item["illustration_count"], -item["id"])) if candidates else None
    return {
        "groups": groups,
        "color_groups": client.color_groups(selected["id"]) if selected else [],
        "auto_tag_available": client.auto_tag_available(),
    }


def open_target(body):
    target = body.get("target")
    if target not in {"gallery", "group", "staging"}:
        raise ValueError("Unknown Artifex destination")
    if target == "staging":
        directory = runtime.config.staging_dir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / MARKER).touch(exist_ok=True)
        destination = str(directory)
        result = {"opened": True}
    else:
        destination = runtime.client.gallery_url()
        result = {"opened": True, "url": destination}
    if os.name == "nt":
        os.startfile(destination)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", destination],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result


async def respond(operation, *args):
    try:
        result = await asyncio.to_thread(operation, *args)
        return web.json_response(result or {"ok": True})
    except KeyError as error:
        return web.json_response({"error": str(error)}, status=404)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ArtifexError, ArtifexUnavailable, OSError) as error:
        logger.exception("Artifex bridge request failed")
        return web.json_response({"error": str(error)}, status=503)


def register_routes():
    routes = PromptServer.instance.routes

    @routes.get("/artifex/options")
    async def get_options(request):
        return await respond(options, request.query.get("group", "default"), request.query.get("group_name"))

    @routes.post("/artifex/open")
    async def open_artifex(request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "Expected a JSON object"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Expected a JSON object"}, status=400)
        try:
            local = ipaddress.ip_address(request.remote).is_loopback
        except ValueError:
            local = False
        if not local:
            return web.json_response({"error": "Open Artifex from the local ComfyUI computer"}, status=403)
        return await respond(open_target, body)

    @routes.delete("/artifex/images/{token}")
    async def delete_image(request):
        return await respond(runtime.previews.delete, request.match_info["token"], runtime.client)
