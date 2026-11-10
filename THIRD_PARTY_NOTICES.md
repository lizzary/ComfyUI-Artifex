# Bundled software

- **Artifex v2.7.0** — [lizzary/Artifex](https://github.com/lizzary/Artifex/releases/tag/v2.7.0), MIT. The original license is in `bin/ARTIFEX-LICENSE`. The Go binaries and React frontend are unmodified official release assets.
- **ONNX Runtime 1.26.0** — [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime/tree/v1.26.0), MIT. License and dependency notices are in `bin/ONNXRUNTIME-LICENSE` and `bin/ONNXRUNTIME-ThirdPartyNotices.txt`.
- **Artifex frontend dependencies** — retain the generated `*.LICENSE.txt` files under `bin/frontend/static/js/`. The upstream `frontend/package-lock.json` pins their versions.

Official download URLs and archive SHA-256 values are pinned in `bin/upstream.json`; the exact bundled files are recorded in `bin/SHA256SUMS.json`. No tagging model weights are bundled or downloaded automatically.
