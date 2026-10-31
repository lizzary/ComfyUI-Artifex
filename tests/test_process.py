from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from artifex_bridge.config import Config
from artifex_bridge.process import ArtifexProcess, ArtifexUnavailable


class ProcessTests(unittest.TestCase):
    def test_model_loading_timeout_uses_static_probe_without_restarting(self):
        process = ArtifexProcess(Config())
        process._process = Mock()
        process._process.poll.return_value = None
        process._base_url = "http://127.0.0.1:1234"
        process.library_id = "original-library"
        response = MagicMock()
        response.__enter__.return_value.status = 200
        process._opener.open = Mock(side_effect=[TimeoutError("model loading"), response])
        process._spawn = Mock()
        self.assertEqual(process.ensure_running(), process._base_url)
        process._spawn.assert_not_called()
        self.assertEqual(process.library_id, "original-library")
        self.assertEqual(process._opener.open.call_args.args[0].get_method(), "GET")
        process.stop()

    def test_static_probe_cannot_mark_first_start_or_restart_ready(self):
        for library, restarting in ((None, False), ("old-library", True)):
            with self.subTest(library=library, restarting=restarting):
                process = ArtifexProcess(Config())
                process._process = Mock()
                process._process.poll.return_value = None
                process._base_url = "http://127.0.0.1:1234"
                process.library_id = library
                process._restart_pending = restarting
                process._opener.open = Mock(side_effect=TimeoutError())
                self.assertFalse(process._ping())
                process._opener.open.assert_called_once()
                process.stop()

    def test_non_timeout_api_error_does_not_use_static_fallback(self):
        process = ArtifexProcess(Config())
        process._process = Mock()
        process._process.poll.return_value = None
        process._base_url = "http://127.0.0.1:1234"
        process.library_id = "library"
        process._opener.open = Mock(side_effect=HTTPError(process._base_url, 409, "Conflict", None, None))
        self.assertFalse(process._ping())
        process._opener.open.assert_called_once()
        process.stop()

    def test_exited_process_still_restarts_with_an_old_library_token(self):
        process = ArtifexProcess(Config())
        child = Mock()
        child.poll.return_value = 1
        process._process = child
        process.library_id = "old-library"
        process._opener.open = Mock()
        self.assertFalse(process._ping())
        process._opener.open.assert_not_called()
        process._spawn = Mock(side_effect=lambda: setattr(process, "_ping", lambda: True))
        process.ensure_running()
        process._spawn.assert_called_once()

    def test_failed_start_retries_three_times_with_exponential_delay(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            process = ArtifexProcess(Config(root=Path(directory)))
            process._spawn = Mock(side_effect=OSError("missing binary"))
            with patch("artifex_bridge.process.time.sleep") as sleep:
                with self.assertRaisesRegex(ArtifexUnavailable, "3 attempts: missing binary"):
                    process.ensure_running()
            self.assertEqual(process._spawn.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_concurrent_start_requests_create_only_one_process(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            process = ArtifexProcess(Config(root=Path(directory)))
            process._ping = lambda: process._process is not None
            def spawn():
                process._process = Mock()
                process._process.poll.return_value = None
                process._base_url = "http://127.0.0.1:1234"
            process._spawn = Mock(side_effect=spawn)
            with ThreadPoolExecutor(max_workers=8) as pool:
                urls = list(pool.map(lambda _: process.ensure_running(), range(8)))
            self.assertEqual(process._spawn.call_count, 1)
            self.assertEqual(len(set(urls)), 1)
            process.stop()

    def test_prepare_creates_isolated_settings_and_does_not_rewrite_them(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            config = Config(root=Path(directory))
            config.binary_path.parent.mkdir(parents=True)
            config.binary_path.write_bytes(b"fake executable")
            config.frontend_dir.mkdir(parents=True)
            (config.frontend_dir / "index.html").write_text("frontend")
            process = ArtifexProcess(config)
            executable = process._prepare()
            self.assertEqual(executable.parent, config.runtime_dir / "bin")
            settings = executable.parent / "config.toml"
            self.assertIn("auto_tag = false", settings.read_text())
            settings.write_text("user preferences")
            process._prepare()
            self.assertEqual(settings.read_text(), "user preferences")
            self.assertFalse((config.binary_path.parent / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()
