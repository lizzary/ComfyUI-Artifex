"""Stage a batch before making any network request, then drain it in memory."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import logging
from pathlib import Path
import shutil


logger = logging.getLogger("comfyui.artifex")
_sequence = itertools.count(1)
MARKER = "you_are_free_to_delete_all_file_here"


@dataclass
class PendingImage:
    filename: str
    data: bytes


class SaveBatch:
    def __init__(self, images, staging_dir: Path):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.name = f"{stamp}_{next(_sequence):06d}"
        self.pending = deque(
            PendingImage(f"{self.name}_{index:05d}.png", data)
            for index, data in enumerate(images, 1)
        )
        self.directory = None
        directory = staging_dir / self.name
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / MARKER).touch(exist_ok=True)
            directory.mkdir()
            for item in self.pending:
                (directory / item.filename).write_bytes(item.data)
            self.directory = directory
        except OSError:
            logger.exception(
                "Could not stage batch %s; continuing from memory. Any partial files may be deleted.",
                self.name,
            )

    def finish(self):
        if self.pending:
            raise RuntimeError("Cannot finish an Artifex batch with pending images")
        directory, self.directory = self.directory, None
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError:
                logger.exception("Could not remove completed staging directory %s", directory)
