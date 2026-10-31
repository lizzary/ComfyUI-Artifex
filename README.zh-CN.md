[Artifex 独立部署版 / Standalone Artifex](https://github.com/lizzary/Artifex)

# ComfyUI-Artifex

美观易用的图像与工作流管理终端

[English](README.md) · [开发维护文档](docs/开发维护文档.md) · [工作流示例](examples/save-in-artifex.json)

## 安装

在你的 ComfyUI 安装目录下运行，然后重启 ComfyUI、刷新网页：

```sh
cd custom_nodes
git clone https://github.com/lizzary/ComfyUI-Artifex.git
```

支持 Windows x64、Linux x64、macOS Apple Silicon。仓库内含真实二进制，无需安装 Go、Node.js 或单独构建 Artifex。Python 依赖由 ComfyUI 自身提供。自动打标模型不会下载。

## 使用

1. 添加 **Artifex → Save in Artifex**，将 VAE Decode 等节点的 `IMAGE` 输出接到 `images`。
2. 选择目标分组和可选的颜色组。默认保存到名为 **ComfyUI** 的分组，不存在时自动创建。
3. 运行工作流。节点会预览本次生成的全部图像；拖动节点边缘调整预览区域。
4. 选择预览图片后，使用节点的删除菜单立即从 Artifex 和本次预览中删除该图片。


| 选项 / 输入 | 行为 |
| --- | --- |
| 分组 | 必选下拉菜单；重名时每一项均显示 `名称(id:编号)` |
| 颜色组 | 可选下拉菜单；默认“无”，不额外指定颜色组 |
| 自动打标 | 仅开关，无连接点；可以操作，默认关闭；能力校验暂留空并默认放行 |
| `group_name` · STRING | 插口与分组下拉菜单位于同一行；连接后优先使用输入名称，对应下拉菜单变灰 |
| `color_group_name` · STRING | 插口与颜色组下拉菜单位于同一行；连接后优先使用输入名称，对应下拉菜单变灰 |
| 输出 | 无输出端口，是工作流终端节点 |

名称输入不存在时自动创建；存在同名项时选择图片最多的项。输入无效或解析失败时，回退到节点选项，再回退到 **ComfyUI / 无**。

开启自动标签时，如果有可用的标签模型，那么会在上传时使用标签模型自动反推标签

## 打开 Artifex

- 顶部菜单 **Extensions → Artifex → 打开图库**。
- ComfyUI 顶部工具栏的 Artifex 按钮。
- 节点中的 **打开当前分组 ↗**。

## 保存与数据

| 路径 | 内容 |
| --- | --- |
| `bin/` | 已校验的 Artifex v2.6.1 三平台二进制、动态库和原版 React 前端 |
| `runtime/bin/` | 当前平台的运行副本与 `config.toml` |
| `data/` | 插件图库数据库、原图、缩略图等，备份时保留整个目录 |
| `staging/` | 临时批次和失败时保留的图片 |
| `logs/plugin.log` | 插件日志，包含重试、跳过、回退、暂存失败等信息 |
| `logs/artifex-backend.log` | 子进程输出，独立于插件日志 |
| ComfyUI 的 `temp/artifex/` | 原生节点预览缓存，可随 ComfyUI 临时文件清理 |
