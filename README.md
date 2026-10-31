[Standalone Artifex](https://github.com/lizzary/Artifex)

# ComfyUI-Artifex

An intuitive, polished workspace for managing images and workflows.

[中文说明](README.zh-CN.md) · [Development and maintenance guide (Chinese)](docs/开发维护文档.md) · [Example workflow](examples/save-in-artifex.json)

## Installation

Run these commands from your ComfyUI installation directory, then restart ComfyUI and refresh the page:

```sh
cd custom_nodes
git clone https://github.com/lizzary/ComfyUI-Artifex.git
```

Supports Windows x64, Linux x64, and macOS Apple Silicon. The repository includes compiled binaries, so you do not need Go, Node.js, or a separate Artifex build. ComfyUI provides the Python dependencies. Tagging models are not downloaded automatically.

## Usage

1. Add **Artifex → Save in Artifex** and connect the `IMAGE` output of VAE Decode or another node to `images`.
2. Choose a target group and an optional color group. Images go to the **ComfyUI** group by default, which is created if it does not exist.
3. Run the workflow. The node previews all images generated in the current run; drag its edges to resize the preview area.
4. Select a preview image and use the node's delete menu to remove it immediately from Artifex and the preview.

| Option / Input | Behavior |
| --- | --- |
| Group | Required dropdown; every entry with a duplicate name displays `Name(id:ID)` |
| Color group | Optional dropdown; defaults to **None**, with no color group assigned |
| Auto-tag | Toggle only, with no input socket; available and off by default; capability validation is left unimplemented and currently allows the option |
| `group_name` · STRING | The socket sits on the same row as the group dropdown; a connected name takes priority and disables the dropdown |
| `color_group_name` · STRING | The socket sits on the same row as the color-group dropdown; a connected name takes priority and disables the dropdown |
| Outputs | No output ports; this is a terminal node in the workflow |

Name inputs create missing groups. If multiple groups share a name, the one containing the most images is selected. Invalid inputs or resolution errors fall back to the dropdown selection, then to **ComfyUI / None**.

When auto-tagging is enabled and a tagging model is available, the model infers tags for uploaded images.

## Open Artifex

- Top menu: **Extensions → Artifex → Open gallery**.
- The Artifex button in ComfyUI's top toolbar.
- **Open current group ↗** inside the node.

## Storage and data

| Path | Contents |
| --- | --- |
| `bin/` | Verified Artifex v2.6.1 binaries for all three platforms, shared libraries, and the original React frontend |
| `runtime/bin/` | Runtime copies for the current platform and `config.toml` |
| `data/` | Gallery database, original images, thumbnails, and other data; back up the entire directory |
| `staging/` | Temporary batches and images retained after failures |
| `logs/plugin.log` | Plugin diagnostics, including retries, skipped files, fallbacks, and staging failures |
| `logs/artifex-backend.log` | Subprocess output, separate from the plugin log |
| ComfyUI's `temp/artifex/` | Native node preview cache; can be cleared with ComfyUI's temporary files |
