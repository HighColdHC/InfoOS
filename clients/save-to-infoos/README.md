# Save to InfoOS

Chrome/Chromium Manifest V3 浏览器扩展。它把用户明确选择的当前链接、选区或可读正文提交到 InfoOS `/api/capture/v1`，不写入 Obsidian，也不访问 InfoOS 内部端口。

## 开发

```bash
npm install
npm run verify
```

构建产物位于 `dist/`。在 Chrome 的“管理扩展程序”中打开开发者模式，选择“加载已解压的扩展程序”，然后选择该目录。

## 本地契约验收

```bash
npm run mock-server
```

使用以下配置：

- InfoOS 地址：`http://127.0.0.1:19123`
- 捕获 Token：`test-capture-token`
- 测试页面：`http://127.0.0.1:19123/fixture`

模拟服务只用于本机开发，不写入 InfoOS 数据。

## 权限说明

| 权限 | 用途 |
|---|---|
| `activeTab` | 仅在用户点击工具栏、菜单或快捷键时读取当前页元数据 |
| `scripting` | URL 模式注入小型元数据读取函数；正文模式才注入 Readability |
| `contextMenus` | 提供“交给 InfoOS”和“把选中内容交给 InfoOS” |
| `storage` | 保存 InfoOS 地址、捕获 Token、UI 设置及安全回执 |
| optional host permission | 保存设置时，仅向用户配置的 InfoOS origin 请求连接权限 |

扩展没有常驻 content script，不读取 Cookie、localStorage、sessionStorage、密码、MFA 或表单值。正文提取从克隆 DOM 中移除表单、脚本、iframe、事件处理器与可执行 URL。
