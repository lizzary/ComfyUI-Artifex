"""Explicit real-binary smoke test: python -m tests.smoke_backend (or runpy).

Creates and removes an isolated library, never the user's plugin data directory.
"""

from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import zlib

from artifex_bridge.client import ArtifexClient, ArtifexError, ArtifexTaggingError
from artifex_bridge.config import Config
from artifex_bridge.process import ArtifexProcess


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
        process = ArtifexProcess(TestConfig(root=Path(temp), retry_delay=0.05))
        client = ArtifexClient(process)
        try:
            process.ensure_running()
            assert process._busy_backend_alive(process._process), "Static frontend probe is unsupported"
            group = client.resolve_group(name="integration")
            color = client.resolve_color(group["id"], name="blue")
            library = process.library_id
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
            result = client.upload(group["id"], "20261031_1.png", png, library_id=library)
            assert result.status == "saved", result
            client.assign_color(group["id"], color, result.illustration["id"], library_id=library)
            assert client.image_bytes(result.illustration["id"], library_id=library)[0] == png
            process._process.kill()
            process._process.wait()
            assert client.image_bytes(result.illustration["id"], library_id=library)[0] == png
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
            print("PASS real Artifex: start, group/color, upload, assign, preview, crash/restart, delete, tagging error, library isolation")
        finally:
            process.stop()


if __name__ == "__main__":
    main()
