"""Use ComfyUI's native temporary-image previews, with scoped deletion handles."""

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import uuid


logger = logging.getLogger("comfyui.artifex")


@dataclass(frozen=True)
class Preview:
    image_id: int | None
    library_id: str
    path: Path


class PreviewStore:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def add(self, directory, item, image_id, library_id):
        path = Path(directory) / "artifex" / item.filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.data)
        except OSError:
            logger.exception("Could not write ComfyUI preview %s", path)
            return None
        token = uuid.uuid4().hex
        with self._lock:
            self._items[token] = Preview(image_id, library_id, path)
        return (
            {"filename": path.name, "subfolder": "artifex", "type": "temp"},
            {"id": token, "image_id": image_id, "filename": path.name},
        )

    def delete(self, token, client):
        with self._lock:
            item = self._items.get(token)
            if item is None:
                raise KeyError("This preview has expired; open Artifex to manage the image")
            if item.image_id is not None:
                client.delete_image(item.image_id, library_id=item.library_id)
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Image deleted, but preview cleanup failed: %s", item.path)
            del self._items[token]
