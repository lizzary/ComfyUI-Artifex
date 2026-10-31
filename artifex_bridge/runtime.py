import logging
from logging.handlers import RotatingFileHandler

from .client import ArtifexClient
from .config import Config
from .preview import PreviewStore
from .process import ArtifexProcess
from .queue import MARKER


config = Config()
process = ArtifexProcess(config)
client = ArtifexClient(process)
previews = PreviewStore()


def start():
    logger = logging.getLogger("comfyui.artifex")
    logger.setLevel(logging.INFO)
    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            config.log_dir / "plugin.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.exception("Cannot create Artifex plugin log; using ComfyUI logging")
    try:
        config.staging_dir.mkdir(parents=True, exist_ok=True)
        (config.staging_dir / MARKER).touch(exist_ok=True)
    except OSError:
        logger.exception("Cannot prepare Artifex staging folder; saves will continue from memory")
    process.start_background()
