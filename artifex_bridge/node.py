import io
import json
import logging

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
from comfy.cli_args import args

from . import runtime
from .queue import SaveBatch


logger = logging.getLogger("comfyui.artifex")


def encode_images(images, prompt, extra_pnginfo):
    metadata = PngInfo()
    if not args.disable_metadata:
        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt, ensure_ascii=False))
        for key, value in (extra_pnginfo or {}).items():
            metadata.add_text(key, json.dumps(value, ensure_ascii=False))
    for index, tensor in enumerate(images):
        try:
            pixels = np.clip(255.0 * tensor.cpu().numpy(), 0, 255).astype(np.uint8)
            if pixels.shape[-1] == 1:
                pixels = pixels[..., 0]
            image = Image.fromarray(pixels)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", pnginfo=metadata, compress_level=4)
            yield buffer.getvalue()
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Skipping unencodable image %s: %s", index + 1, error)


class SaveInArtifex:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "group": (["default"], {"default": "default"}),
                "color_group": ([""], {"default": ""}),
                "auto_tag": ("BOOLEAN", {"default": False, "socketless": True}),
            },
            "optional": {
                "group_name": ("STRING", {"forceInput": True}),
                "color_group_name": ("STRING", {"forceInput": True}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    @classmethod
    def VALIDATE_INPUTS(cls, group, color_group):
        # Options come from a live library. Deleted/renamed choices fall back at execution.
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "Artifex"
    DESCRIPTION = "Save generated images to your local Artifex library."

    def save_images(self, images, group="default", color_group="", auto_tag=False,
                    group_name=None, color_group_name=None, prompt=None, extra_pnginfo=None):
        batch = SaveBatch(encode_images(images, prompt, extra_pnginfo), runtime.config.staging_dir)
        previews, handles = [], []
        actual_group = []
        try:
            if batch.pending:
                client = runtime.client
                with client.library_session() as library_id:
                    target = client.resolve_group(name=group_name, selected=group)
                    color = client.resolve_color(target["id"], name=color_group_name, selected=color_group)
                    actual_group = [f"id:{target['id']}"]
                    tagging = bool(auto_tag and client.auto_tag_available())
                    if auto_tag and not tagging:
                        logger.warning("Automatic tagging is temporarily unavailable; saving without tagging")
                    while batch.pending:
                        item = batch.pending[0]
                        outcome = client.upload(target["id"], item.filename, item.data,
                                                auto_tag=tagging, library_id=library_id)
                        illustration = outcome.illustration
                        image_id = illustration["id"] if illustration else None
                        if image_id is not None and color is not None:
                            client.assign_color(target["id"], color, image_id, library_id=library_id)
                        if outcome.status == "skipped":
                            logger.warning("Skipped %s: %s", item.filename, outcome.reason)
                        preview = runtime.previews.add(folder_paths.get_temp_directory(), item, image_id, library_id)
                        if preview is not None:
                            descriptor, handle = preview
                            previews.append(descriptor)
                            handles.append(handle)
                        batch.pending.popleft()
            batch.finish()
        except Exception:
            logger.exception("Artifex batch %s failed with %s pending images; staging: %s",
                             batch.name, len(batch.pending), batch.directory or "memory only")
            raise
        return {"ui": {"images": previews, "artifex_images": handles, "artifex_group": actual_group}, "result": ()}
