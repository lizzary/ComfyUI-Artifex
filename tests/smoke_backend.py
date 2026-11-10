"""Explicit real-binary smoke test: python -m tests.smoke_backend (or runpy).

Creates and removes an isolated library, never the user's plugin data directory.
"""

from contextlib import contextmanager
from pathlib import Path
import socket
import struct
from tempfile import TemporaryDirectory
import zlib

from artifex_bridge.client import ArtifexClient, ArtifexError, ArtifexTaggingError
from artifex_bridge.config import Config
from artifex_bridge.process import ArtifexProcess


@contextmanager
def occupy_starting_port():
    for _ in range(20):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            port = blocker.getsockname()[1]
            if not 10000 <= port <= 65435:
                continue
            try:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port + 1))
            except OSError:
                continue
            blocker.listen()
            yield port
            return
    raise RuntimeError("Could not reserve a test port with an available neighbor")


def main():
    source = Config()

    class TestConfig(Config):
        @property
        def binary_path(self):
            return source.binary_path

        @property
        def frontend_dir(self):
            return source.frontend_dir

    with TemporaryDirectory(prefix=".artifex-integration-", dir=source.root) as temp:
        config = TestConfig(root=Path(temp), retry_delay=0.05)
        config.runtime_dir.mkdir()
        port_file = config.runtime_dir / "port.txt"
        with occupy_starting_port() as port:
            port_file.write_text(f"{port}\n", encoding="utf-8")
            process = ArtifexProcess(config)
            try:
                assert process.ensure_running() == f"http://127.0.0.1:{port + 1}"
                assert int(port_file.read_text(encoding="utf-8")) == port, "Fallback changed the starting port"
            finally:
                process.stop()

        process = ArtifexProcess(config)
        try:
            url = process.ensure_running()
            assert url == f"http://127.0.0.1:{port}", "Restart did not return to the original port"
            process.stop()
            process = ArtifexProcess(config)
            assert process.ensure_running() == url, "A fresh ComfyUI process would change the browser origin"
            client = ArtifexClient(process)
            assert process._busy_backend_alive(process._process), "Static frontend probe is unsupported"
            group = client.resolve_group(name="integration")
            # TruncateText can deliver a one-item list through its STRING socket.
            color = client.resolve_color(group["id"], name=["1234"])
            assert color is not None and color["customName"] == "1234", color
            assert client.resolve_color(group["id"], name=["1234"])["value"] == color["value"]
            library = process.library_id
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
            result = client.upload(group["id"], "20261031_1.png", png, library_id=library)
            assert result.status == "saved", result
            client.assign_color(group["id"], color, result.illustration["id"], library_id=library)
            colors = client._color_config(group["id"])
            assert len(colors["sets"]) == 1, colors
            board = colors["sets"][0]
            assert colors["active_id"] == board["id"] == color["set_id"], colors
            assert len(board["pairs"]) == 1 and board["pairs"][0]["id"] == color["id"], board
            assert board["manual_assignments"] == {str(result.illustration["id"]): color["id"]}, board
            assert client.image_bytes(result.illustration["id"], library_id=library)[0] == png
            process._process.kill()
            process._process.wait()
            assert client.image_bytes(result.illustration["id"], library_id=library)[0] == png
            assert client._color_config(group["id"]) == colors, "Color membership was not persisted"
            assert process.expected_library(library) == process.library_id
            client.delete_image(result.illustration["id"], library_id=library)
            settings_before = client._request("GET", "/api/settings")
            assert settings_before["auto_tag"] is False
            try:
                client.upload(group["id"], "20261031_2.png", png, auto_tag=True, library_id=library)
            except ArtifexTaggingError as error:
                assert error.status == 503, error
                image_id = error.illustration["id"]
                assert client.image_bytes(image_id, library_id=library)[0] == png
                client.delete_image(image_id, library_id=library)
            else:
                raise AssertionError("Missing model was silently accepted for automatic tagging")
            assert client._request("GET", "/api/settings")["auto_tag"] is True
            client._request("PUT", "/api/settings", payload={"database_dir": str(Path(temp) / "second-library")})
            client.resolve_group(name="second")
            try:
                client.delete_image(1, library_id=library)
            except ArtifexError as error:
                assert error.status == 409, error
            else:
                raise AssertionError("Stale library token was accepted")
            print("PASS real Artifex: port fallback/reuse, group/color, upload, assign, preview, crash/restart, delete, tagging error, library isolation")
        finally:
            process.stop()


if __name__ == "__main__":
    main()
