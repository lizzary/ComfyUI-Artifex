import importlib
import io
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from artifex_bridge.client import UploadOutcome
from artifex_bridge.preview import PreviewStore
from artifex_bridge.queue import MARKER, SaveBatch


class Tensor:
    def cpu(self):
        return self

    def numpy(self):
        return np.full((4, 6, 3), 0.5, dtype=np.float32)


class NodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The actual Comfy integration is exercised separately. Only its two
        # environment globals are replaced in these failure-path unit tests.
        modules = {
            "folder_paths": SimpleNamespace(get_temp_directory=lambda: "unused"),
            "comfy": SimpleNamespace(),
            "comfy.cli_args": SimpleNamespace(args=SimpleNamespace(disable_metadata=False)),
        }
        with patch.dict(sys.modules, modules):
            cls.node = importlib.import_module("artifex_bridge.node")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.client = Mock()
        self.client.resolve_group.return_value = {"id": 8}
        self.client.resolve_color.return_value = {"id": "pair", "set_id": "set"}
        self.client.upload.return_value = UploadOutcome("saved", {"id": 19})
        self.client.auto_tag_available.return_value = True
        self.client.library_session.side_effect = lambda: nullcontext("test-library")
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(staging_dir=self.root / "staging"),
            client=self.client,
            process=SimpleNamespace(library_id="test-library"),
            previews=PreviewStore(),
        )
        self.patches = [
            patch.object(self.node, "runtime", self.runtime),
            patch.object(self.node.folder_paths, "get_temp_directory", return_value=str(self.root / "temp")),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_entire_batch_staged_before_backend_and_native_previews_remain(self):
        def resolve(**kwargs):
            staged = list((self.root / "staging").glob("*/*.png"))
            self.assertEqual(len(staged), 2)
            with Image.open(staged[0]) as image:
                self.assertIn('"node"', image.info["prompt"])
                self.assertIn('"nodes"', image.info["workflow"])
            return {"id": 8}
        self.client.resolve_group.side_effect = resolve
        result = self.node.SaveInArtifex().save_images(
            [Tensor(), Tensor()], group_name="海边", color_group_name=["双手比v"],
            prompt={"node": {}}, extra_pnginfo={"workflow": {"nodes": []}},
        )
        self.assertEqual(list((self.root / "staging").iterdir()), [self.root / "staging" / MARKER])
        self.assertEqual(result["result"], ())
        self.assertEqual(len(result["ui"]["images"]), 2)
        self.assertEqual(len(list((self.root / "temp" / "artifex").glob("*.png"))), 2)
        self.assertEqual(self.client.assign_color.call_count, 2)
        self.client.resolve_group.assert_called_once_with(name="海边", selected="default")
        self.client.resolve_color.assert_called_once_with(8, name=["双手比v"], selected="")

    def test_staging_failure_still_uploads_memory(self):
        self.runtime.config.staging_dir.write_bytes(b"not a directory")
        with self.assertLogs("comfyui.artifex", "ERROR"):
            result = self.node.SaveInArtifex().save_images([Tensor()])
        self.client.upload.assert_called_once()
        self.assertEqual(len(result["ui"]["images"]), 1)

    def test_failed_upload_preserves_staging_and_raises(self):
        self.client.upload.side_effect = RuntimeError("backend unavailable")
        with self.assertLogs("comfyui.artifex", "ERROR"), self.assertRaisesRegex(RuntimeError, "backend unavailable"):
            self.node.SaveInArtifex().save_images([Tensor(), Tensor()])
        self.assertEqual(len(list((self.root / "staging").glob("*/*.png"))), 2)

    def test_skipped_file_drains_without_color_assignment(self):
        self.client.upload.return_value = UploadOutcome("skipped", reason="unsupported")
        with self.assertLogs("comfyui.artifex", "WARNING"):
            result = self.node.SaveInArtifex().save_images([Tensor()])
        self.client.assign_color.assert_not_called()
        self.assertIsNone(result["ui"]["artifex_images"][0]["image_id"])
        self.assertEqual(len(list((self.root / "staging").glob("*/"))), 0)

    def test_delete_removes_only_recorded_image_and_preview(self):
        result = self.node.SaveInArtifex().save_images([Tensor()])
        token = result["ui"]["artifex_images"][0]["id"]
        self.runtime.previews.delete(token, self.client)
        self.client.delete_image.assert_called_once_with(19, library_id="test-library")
        self.assertEqual(list((self.root / "temp" / "artifex").iterdir()), [])
        with self.assertRaises(KeyError):
            self.runtime.previews.delete("../other", self.client)

    def test_metadata_disable_and_auto_tag_enabled(self):
        with patch.object(self.node.args, "disable_metadata", True):
            data = next(self.node.encode_images([Tensor()], {"secret": "prompt"}, {"workflow": {}}))
        with Image.open(io.BytesIO(data)) as image:
            self.assertNotIn("prompt", image.info)
            self.assertNotIn("workflow", image.info)
        self.node.SaveInArtifex().save_images([Tensor()], auto_tag=True)
        self.assertTrue(self.client.upload.call_args.kwargs["auto_tag"])

    def test_auto_tag_defaults_off_and_honors_explicit_unavailable(self):
        self.node.SaveInArtifex().save_images([Tensor()])
        self.assertFalse(self.client.upload.call_args.kwargs["auto_tag"])
        self.client.auto_tag_available.return_value = False
        with self.assertLogs("comfyui.artifex", "WARNING"):
            self.node.SaveInArtifex().save_images([Tensor()], auto_tag=True)
        self.assertFalse(self.client.upload.call_args.kwargs["auto_tag"])

    def test_directory_cleanup_attempted_once(self):
        batch = SaveBatch([b"png"], self.root / "staging")
        batch.pending.clear()
        with patch("artifex_bridge.queue.shutil.rmtree", side_effect=PermissionError) as remove:
            with self.assertLogs("comfyui.artifex", "ERROR"):
                batch.finish()
            batch.finish()
        remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
