import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class OpenTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(sys.modules, {"server": SimpleNamespace(PromptServer=Mock())}):
            cls.routes = importlib.import_module("artifex_bridge.routes")

    def test_gallery_and_group_use_system_default_browser_after_health_check(self):
        client = Mock()
        client.gallery_url.return_value = "http://127.0.0.1:23456/"
        with patch.object(self.routes.runtime, "client", client), \
                patch.object(self.routes.os, "startfile", create=True) as startfile, \
                patch.object(self.routes.subprocess, "Popen") as popen:
            for target in ("gallery", "group"):
                result = self.routes.open_target({"target": target})
                self.assertTrue(result["opened"])
                self.assertEqual(result["url"], client.gallery_url.return_value)
            self.assertEqual(client.gallery_url.call_count, 2)
            if os.name == "nt":
                self.assertEqual(startfile.call_count, 2)
                startfile.assert_called_with("http://127.0.0.1:23456/")
            else:
                self.assertEqual(popen.call_count, 2)
                self.assertEqual(popen.call_args.args[0][-1], "http://127.0.0.1:23456/")

    def test_backend_failure_does_not_launch_a_browser(self):
        with patch.object(self.routes.runtime.client, "gallery_url", side_effect=OSError("backend unavailable")), \
                patch.object(self.routes.os, "startfile", create=True) as startfile, \
                patch.object(self.routes.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(OSError, "backend unavailable"):
                self.routes.open_target({"target": "gallery"})
            startfile.assert_not_called()
            popen.assert_not_called()

    def test_staging_opens_locally_without_backend(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            runtime = SimpleNamespace(config=SimpleNamespace(staging_dir=Path(directory) / "staging"), client=Mock())
            with patch.object(self.routes, "runtime", runtime), \
                    patch.object(self.routes.os, "startfile", create=True), \
                    patch.object(self.routes.subprocess, "Popen"):
                self.assertEqual(self.routes.open_target({"target": "staging"}), {"opened": True})
            runtime.client.gallery_url.assert_not_called()
            self.assertTrue((runtime.config.staging_dir / self.routes.MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
