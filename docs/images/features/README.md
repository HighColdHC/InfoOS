# README 功能截图复现

17 张图使用线上 SQLite 的一致性只读备份、真实前端和深色模式。标题、摘要、作者、来源名、账号、链接与内部标识在截图前替换成中性虚构文本，与原文没有可还原的对应关系；业务统计、评分、时间、状态和图表保留。虚构标题与摘要不再附加原文数字括号；独立数字、日期和统计节点另行核对。没有插入虚构业务记录。

通知与采集环境合并为 `collection-environment.webp`，不再使用 `notification-settings.webp`。Token 与账号截图来自清除凭据后的创建前状态；环境页则使用按线上白名单事实重建的会话、账号状态和组件版本快照，不能称为首次安装态。

2026-11-02 仅重拍商机看板与商机统计，源码、容器与前端产物 revision 为 `492e5bf5`（完整值 `492e5bf5097c490836869810a71f3f95da43c29a`）。其余 15 张图保留 2026-10-29 全量批次的像素；下文的组件快照、来源健康与全量库数量均属于该历史批次，不能当作新两图的当前数据。

## 2026-11-02 商机两图补拍

两图来自同一份一致性只读备份，全部商机知识卡 **175** 张；看板默认每列最多加载 50 张，本图在待调研列完整展示前四张，其他列保留空态。统计页完整展示五组图表；每日新增按上海自然日为 10 / 25 / 19 / 18 / 24 / 70 / 9（2026-10-28 至 2026-11-03）。任务日期为 2026-11-02，拍摄时上海日期已为 2026-11-03，未冻结或改写时间。

新规则将 `.opportunities .tile h3` 按标题字段、`.opportunities .tile__next` 按正文整段虚构替换，卡片及状态选择器的辅助标签也整段替换；纯金额文本同样替换。卡片子类型、强度、证据数、平台、状态、时间，以及统计页的总数、图表和日期提示保留。本次新增规则只含选择器与字段类型，不含原库账号、产品、标题或金额叙述。

看板为 **1440×1039、80,050 字节**；统计为 **1440×1432、55,548 字节**。高度在脱敏后按内容确定：看板到第四张卡片底部，统计到最后一组图表底部；保留真实产品布局与深色主题。其余 15 张文件的 SHA-256 与补拍前一致。

本次 `--only` 跳过其他页面和与阅读页无关的采集器健康前置检查，不改写健康状态。Python 源码通过 `PYTHONPATH` 指向当前 checkout，SQLite、浏览器临时区和隔离 HOME 均放在 `/tmp` 私有目录；本机未运行产品测试、TypeScript 检查或 npm 构建。文件大小、分层检查通过。

两张最终 WebP 均已逐张打开检查：虚构标题与摘要为中性文本，无真实账号、产品或金额叙述，卡片与图表完整可见。两图 DOM 原库语料命中均为 **0**；[远端原库复核](../../../scripts/audit-readme-snapshot.py) 使用 `--only opportunity-board opportunity-stats`，OCR 私有标识与完整语料（含正文片段）命中均为 **0**，README、截图说明与脚本的私有标识命中均为 **0**。当前 17 张图总量 **1,897,456 字节**，最大单张 **366,184 字节**。

本机 `readme_screenshot_audit.py` 对全部 17 张图的 OCR 私有标识命中为 **0**，文档与脚本命中为 **0**；无损 WebP 均仅含 VP8L，无 EXIF/XMP/ICC 或文字元数据。标识匹配仍按完整文本与词边界检查，合并公共边界和首字符分组以减少大语料下的重复匹配。

## 准备与导出

阅读 [备份与恢复点](../../../deploy/docker/README.md#独立磁盘备份)。准备 Python 3.12+、匹配 revision 的前端产物、Playwright Chromium、Pillow，以及已核验 SSH 主机身份的私有运维入口。入口支持 `command <远端命令>`，连接配置由入口自行加载，不读取或输出配置内容。OCR 使用 macOS Swift / Vision。

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[web]' 'playwright==1.64.1' 'Pillow==13.0.0'
.venv/bin/python -m playwright install chromium
# 先提供匹配 revision 的 frontend/dist；本次从已核验容器取回，不在本机构建。
.venv/bin/python scripts/export-readme-snapshot.py \
  --transport /path/to/private-command-wrapper \
  --container YOUR_APPLIANCE_CONTAINER
.venv/bin/python scripts/run-readme-demo.py \
  --snapshot /tmp/infoos-shots-XXXXXXXX/snapshot.sqlite3 \
  --only opportunity-board opportunity-stats
```

本次容器产物位于 `/opt/infoos/app/frontend/dist`，经私有入口 `command` 执行 tar + base64，通过 stdout 传回并在本机解码；连接配置由入口自行加载。`frontend/dist` 被 Git 忽略。`--only` 仅支持这两张图，跳过空库首次引导与其他截图；省略它仍运行原来的全量流程。

[导出脚本](../../../scripts/export-readme-snapshot.py) 用 `docker inspect` 识别挂载，以 SQLite URI `mode=ro` 打开源库，用在线备份 API 生成一致性副本并执行 `quick_check`。远端只在宿主机临时目录创建副本，导出后自动删除。本机临时目录权限 `0700`、数据库权限 `0600`。不复制附件、归档、Chrome profile、密码库、Token 文件或旧 LLM 配置。

[运行摘要](../../../scripts/readme_runtime.py) 仅导出白名单：会话字段形状、schema、存活判定、健康状态、绑定证明类型、时间；平台登录状态及检查时间；七项组件的安装事实与版本；已记录容器健康与来源汇总。不导出线上路径、context ID、PID、身份、错误详情或凭据。会话原文件不原样拷贝，本机另用隔离路径、虚构 context ID 与占位 PID 生成同形状记录，不启动 Chrome。

版本来自线上 Chrome `--version`、FFmpeg `-version`、已安装 Python 包 metadata 和 SQLite 版本；Bridge 的状态、版本与检查时间直接取产品入口已保存的组件记录。导出只读取这些事实，没有执行组件状态刷新、Bridge 连通探测或平台登录检查。Chrome 与 FFmpeg 仅显示实际版本号，省略产品名前缀与版权行。

2026-10-29 全量批次线上只读核验：全部 **38**、启用 **34**、需要关注 **0**；线上核验与副本截图均为 **99.9%**（副本截图时原值 **0.9986033519553073**）。口径为近 7 天全部来源的运行成功率，包括当前维护列表外的来源；分母为 `success + failed`。会话 schema v2、`is_alive=true`、`last_health_status=ready`、`binding_proof=supervised_container_dedicated_profile`。会话记录与 Bridge 检查结果分别保留；Bridge 已保存检查为 `connected`、1.0.26，检查时间 `2026-10-29T04:51:06.384857Z`。本轮没有重新探测，截图不证明此后持续连通。

| 组件 | 真实版本 | 展示边界 |
| --- | --- | --- |
| Chrome | 150.0.7871.114 | 已安装，不启动浏览器 |
| OpenCLI Bridge | 1.0.26 | 已连接；保留线上显式检查结果与原检查时间 |
| yt-dlp | 2026.7.4 | 已安装，不执行采集 |
| gallery-dl | 1.32.9 | 已安装，不执行采集 |
| FFmpeg | 6.1.1-3ubuntu5 | 已安装，不转码 |
| 本地 ASR | 1.13.4 | 已安装包版本，不执行转录 |
| SQLite | 3.45.1 | 线上 Python SQLite 版本 |

## 最终健康状态与隔离实例

[演示入口](../../../scripts/run-readme-demo.py) 先删除本机副本的 Token、请求审计、认证恢复、二维码等敏感记录，再清除凭据列和敏感 settings，执行 secure delete、VACUUM 与 quick check；不读取凭据原值。隔离 HOME、数据库、state、配置、密码库、健康文件与 Chrome profile；随机生成临时管理密码但不输出。

只启动随机 loopback 端口 API；无 scheduler、worker、agent。API 导入产品前禁止外连、外部 DNS 与子进程；Playwright 只放行本实例 GET/HEAD。不触发采集、Chrome 启动、平台登录、通知发送或外部 agent 调用。

2026-10-29 全量批次的线上源码与镜像 revision 均为 `3c327005889cb57c5ee2ffd92944924136aff640`，容器健康。副本没有生效的运行保护；没有改写副本保护状态。线上只读 collector 为 `ready`、逾期来源 0。最终代码在隔离副本上运行，带壳页面不显示「部分处理异常」；没有生效的保护时，产品自然不渲染「运行保护」区块，因此也没有「已熔断」卡片。Bridge 从线上白名单事实重建，显示「已连接」、1.0.26 与原检查时间；其余六项显示已安装及线上真实版本。

通知连接配置没有复制：控件显示本机默认未连接状态；数据库中的历史投递状态和静默时间控件照实保留。Token、账号页是清除真实凭据后的表单。首次引导单独使用空库。

## 虚构替换与自检

[截图入口](../../../scripts/capture-readme-screenshots.py) 使用真实无头 Chromium、产品主题 `dark`、`zh-CN` 和 `Asia/Shanghai`。桌面宽 1440，按内容使用 900 / 1100 / 1200 / 1400 高度；移动知识库为 390×844。来源页另核验 375 与 1440 宽度：页面无横向 overflow，口径说明可见并在视口内。

规则见 [readme-redaction.json](../../../scripts/readme-redaction.json)，执行器见 [readme_redaction.py](../../../scripts/readme_redaction.py)。同页按字段类型与固定哈希映射；作者和账号使用短虚构名称，允许不同身份映射到同一个名称，避免碰撞拼接撑长归属。编号使用保持长度与分隔符形状的虚构十六进制串；保留平台名。自动比对发现虚构值与原文碰撞时按字段类型重新替换。标题、正文、身份、输入值、属性、tooltip、辅助标签、页面标题及外链均检查；媒体隐藏，避免私人附件像素泄露。组件版本、平台、状态、评分、统计与图表保留。七项组件整个卡片纳入替换前后不变检查，防止版本被误当原文替换。

每页冻结产品脚本后脱敏，逐项比较统计、独立数字/日期、固定界面文案及样式资源。固定文案由前端源码与后端公开知识 schema 识别，分类与字段标签保持不变；不从私有数据库生成白名单。将 DOM 文本、属性、输入值和标题与清除凭据后的原库语料比对：短字段用子串，长字段用重叠的 12 字符片段；修复后命中必须为零才截图。原文集合和映射只在内存，不保存到仓库。

[图片审计](../../../scripts/readme_screenshot_audit.py) 用 Vision 识别最终像素，扫描原库身份和地址标识；正文与标题的完整原文比对由截图前 DOM 检查承担。另扫描 README、截图文档和截图脚本中的完整私有标识；标识按词边界匹配，避免把普通词内部的字母或汉字片段误判为私人身份。仅保留计数、类别、统计摘录与文件大小，不记录原文或 OCR 文本。PNG 仅按像素重编码为无损 WebP，单张 ≤400,000 字节、总量 ≤5,000,000 字节；RIFF 只允许 VP8L 像素块，不含 EXIF/XMP/ICC/文字元数据。

## 每张图的清单

2026-10-29 全量批次副本：345 条来源（含列表外来源）、13,855 张信息卡、159 张知识卡、416,803 个任务、2,674 条商机拆解。五类知识为资讯 29、商机 25、项目 44、选题 16、方法 45。数字仅描述本轮备份；组件快照时间为 `2026-10-29T05:32:55.492819Z`，数据库随后执行在线一致性备份。

| 文件 | 视图与虚构替换范围 | 保留的真实数字摘录 | DOM 原文 / OCR 私有标识命中 |
| --- | --- | --- | --- |
| [daily-brief.webp](daily-brief.webp) | 条目最多的冻结日报；标题、用处、归属 | 2026-10-29，21 个冻结条目；首组新增 22、画像 v2、强度 85/84 | 0 / 0 |
| [knowledge-library.webp](knowledge-library.webp) | 全部五类知识；标题、用处、归属 | 29 / 25 / 44 / 16 / 45 | 0 / 0 |
| [knowledge-library-mobile.webp](knowledge-library-mobile.webp) | 390×844 的真实移动布局 | 全部 159、项目 44、商机 25；首卡强度 85、证据 3 条 | 0 / 0 |
| [knowledge-detail.webp](knowledge-detail.webp) | 最多证据的未替代知识卡；字段、引文与归属 | 4 条证据，真实平台、日期与归并状态 | 0 / 0 |
| [card-browse.webp](card-browse.webp) | 推荐信息卡；作者、标题、摘要、关系 | 首三卡质量/相关分 70/85、72/90、68/85 | 0 / 0 |
| [card-detail.webp](card-detail.webp) | 多证据知识卡关联的长正文原卡 | 2026-10-28 14:48，X，内容已完整 | 0 / 0 |
| [card-management.webp](card-management.webp) | 管理列表与过线卡片详情 | 列表 13,855；质量 70、相关 85、浏览 4,529 | 0 / 0 |
| [source-management.webp](source-management.webp) | 会话快照重建后的真实来源管理；名称、账号、标识虚构 | **38 / 34 / 0 / 99.9%**，频率、最近时间与 7 天新增 | 0 / 0 |
| [collection-tasks.webp](collection-tasks.webp) | 已完成来源运行；名称、标识与执行者 | 已扫描 ≥62/≥21/≥41/≥34；首行链接 40、去重 5、完成 23 | 0 / 0 |
| [run-trace.webp](run-trace.webp) | 完成列表中完善卡片最多的运行 | 各处理阶段 23/23、尝试 1；完整链路 9 分 20 秒 | 0 / 0 |
| [collection-environment.webp](collection-environment.webp) | 合并环境、通知、保护与组件；账号/路径虚构 | **7 项组件真实版本、0 项生效保护；Bridge 1.0.26 已连接**；23:00–08:00 | 0 / 0 |
| [token-permissions.webp](token-permissions.webp) | 清除凭据后的创建前状态，不补造 Token | 接口 v1、空列表 0 行；权限 scope 保留 | 0 / 0 |
| [account-settings.webp](account-settings.webp) | 临时隔离账号与空白密码表单 | 最少 12 位、最多 72 个 UTF-8 字节、中文通常 3 字节 | 0 / 0 |
| [export-and-logs.webp](export-and-logs.webp) | 导出能力与本次日志；消息、地址、路径虚构 | 隔离实例本次日志共 1 条、第 1 页，INFO | 0 / 0 |
| [opportunity-board.webp](opportunity-board.webp) | 2026-11-02 补拍：五列调研看板；标题、赚钱信号全文与辅助标签虚构 | 待调研 175；调研中 / 已立项 / 继续观察 / 放弃均为 0；首四卡强度 88/88/85/85 | 0 / 0 |
| [opportunity-stats.webp](opportunity-stats.webp) | 2026-11-02 补拍：全部商机知识卡的状态、子类型、赚钱信号、每日新增与平台分布 | 共 175；子类型 93/0/32/50；赚钱信号 47/104/36/30/30/1；平台 128/28/18/2/1 | 0 / 0 |
| [onboarding.webp](onboarding.webp) | 独立空库首次设置 | 步骤 1/2、平台、未检查状态 | 0 / 0 |

## 验证、边界与收尾

图片须逐张打开检查。自动检查不能替代视觉检查，也不证明线上采集、ASR、Bridge 连通、通知发送、平台登录或 agent 调用通过。保留的限制：日志行较少；商机看板跟进列为空；移动端首屏仅一张完整知识卡；环境图以组件为重点，平台账号表大部分位于首屏下方。虚构名称可能重复；Bridge「已连接」徽标仍为产品原有灰色样式。上述展示未修改产品样式，也未补造业务行。

日志页只有一行，是隔离实例启动后自己的日志；没有复制线上日志文件，也没有启动后台采集。本次商机看板待调研 175 条，其余四列均为 0，照实展示副本状态；没有插入虚构业务记录。统计页使用商机知识卡口径。赚钱信号和来源平台允许同一卡片出现多个值，各项数量不要求相加等于卡片总数。

2026-10-29 全量批次验证（历史记录）：本机前端 TypeScript 检查与构建、`check_file_size.py`、`check_layer_deps.py` 通过。逐页拍摄核验 DOM 脱敏、真实统计与界面文案不变，并核对 collector、运行保护及七项组件与线上白名单事实。未运行产品 Python/前端测试全集，未执行远端测试或线上业务操作。

2026-10-29 全量批次的 17 张图片均逐张打开检查；DOM 与拍摄副本原文比对、最终像素 OCR 私有标识比对均逐图为 **0**。截图文档与脚本扫描私有标识为 **0**；所有图片仅含 VP8L 像素块，无元数据。总量 **2,030,432 字节**，最大单张 **366,184 字节**。

导出、截图与审计创建的本机/远端私有临时副本和 OCR 中间文件均已删除，原文集合未持久化；本轮隔离 API 与截图浏览器已退出。工作树没有业务 SQLite、会话文件或其他私有副本。本轮不 commit、不 push、不部署，不修改线上熔断、来源配置或其他线上状态；未读取旧 LLM 配置。

本轮仅更新截图、截图说明及拍摄辅助脚本；未修改产品代码。
