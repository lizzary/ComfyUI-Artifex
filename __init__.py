from .artifex_bridge.node import SaveInArtifex
from .artifex_bridge.routes import register_routes
from .artifex_bridge.runtime import start

NODE_CLASS_MAPPINGS = {"SaveInArtifex": SaveInArtifex}
NODE_DISPLAY_NAME_MAPPINGS = {"SaveInArtifex": "Save in Artifex"}
WEB_DIRECTORY = "./web"

register_routes()
start()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
