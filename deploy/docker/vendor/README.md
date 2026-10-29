# 固定的 OpenCLI Bridge 构建输入

`opencli-1.0.22.crx` 是既有 Chrome Web Store OpenCLI 扩展的原始 CRX3 包，没有修改。

- 扩展 ID：`ildkmabpimmkaediidaifkhjpohdnifk`
- 包内 manifest.version：`1.0.22`
- 文件大小：47,680 bytes
- SHA-256：`31e9e7238c68f83a76d30b3fbde2bb0d76106588bb070d3fc5be6576422b9990`
- 取自已部署镜像 `sha256:88a0ded0a30beb4cc7b68380a97a6234ad9adbc58654e2a11a58f26ac32aaac9` 内的 `/opt/infoos/extensions/opencli-1.0.22.crx`。

2026-09-05 完整镜像构建实际发现：Google 更新服务的动态下载地址返回了不同内容，而旧 Dockerfile 仍校验此固定版本的哈希。构建因此失败。现改用这份与旧 Dockerfile 校验值完全一致的已有构建输入；Dockerfile 继续检查 SHA-256。

升级扩展时同时更换版本文件、版本号和校验值，再完成对应浏览器运行验证。此文件只固定镜像中的安装包，不代表持久 Chrome Profile 中自动更新后的安装版本；运行版本仍应按现场证据确认。
