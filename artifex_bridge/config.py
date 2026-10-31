"""Filesystem layout for the plugin-owned Artifex instance."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform


@dataclass(frozen=True)
class Config:
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    startup_timeout: float = 120.0
    request_timeout: float = 120.0
    retry_delay: float = 1.0
    port: int = 0

    @property
    def platform_name(self) -> str:
        system = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
        machine = platform.machine().lower()
        arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
        if system is None or arch is None:
            raise RuntimeError(f"Unsupported Artifex platform: {platform.system()} {machine}")
        return f"{system}-{arch}"

    @property
    def binary_path(self) -> Path:
        return self.root / "bin" / self.platform_name / ("artifex.exe" if os.name == "nt" else "artifex")

    @property
    def frontend_dir(self) -> Path:
        return self.root / "bin" / "frontend"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"
