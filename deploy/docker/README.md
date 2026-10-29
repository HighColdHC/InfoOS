# InfoOS Docker Appliance

首次安装入口：[给 AI agent 的安装与使用说明](../../docs/INSTALL-for-agents.md)。本文的 Compose 命令默认使用 `deploy/docker/.env`、project `infoos` 和镜像标签 `infoos-appliance:local`；自定义实例须始终使用自己的同一配置、project、overlay 与容器名。

所有 Compose 命令（包括 `up`、`ps`、`logs`）都会解析必填的 `INFOOS_SOURCE_COMMIT`。在本次审核 checkout 根目录、每个新 shell 先设置完整 revision；构建 wrapper 内部的 export 不会传回父 shell：

```bash
export INFOOS_SOURCE_COMMIT="$(git rev-parse --verify 'HEAD^{commit}')"
```

## 已有 Appliance 的版本更新

更新前核对已审核源码 revision、容器身份、schema 与恢复点。涉及 schema 或持久数据语义变化时使用 `--update-kind data-migration`，在一致性备份副本运行 `python3 scripts/migration-dry-run.py --db <副本路径>`；不能让旧镜像打开高于其支持版本的库。迁移规则见 [SQLite 迁移手册](../../docs/engineering/sqlite-migrations.md)。

从审核后的干净 checkout 用完整 Dockerfile 构建。`build-image.sh` 的分支限制以所用版本和显式配置为准；通过干净 `git archive` 构建时传完整 `INFOOS_SOURCE_COMMIT`，禁止单文件叠层发布。

### 远端构建网络约束

如果构建机无法直连 Docker Hub，需区分 BuildKit 元数据解析网络与 Dockerfile RUN 网络：`--build-arg HTTPS_PROXY` 只作用于 RUN，不影响 FROM 解析。可用自备代理查询 manifest 的 `Docker-Content-Digest`，固定 Dockerfile 前端、Node 与 GUI 基础镜像 digest。查询 Accept 同时包括 OCI index 与 Docker manifest list v2；digest 由本次构建重新核对，不沿用实例历史值。

无凭据的查询示例（`repository` 使用 Docker Hub registry 名称，例如 `library/node`；Dockerfile 前端为 `docker/dockerfile`）：

```bash
repository=library/node; tag=22.23.1-bookworm-slim
token=$(curl -fsS --proxy <用户自备代理URL> \
  "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repository}:pull" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsSI --proxy <用户自备代理URL> \
  -H "Authorization: Bearer ${token}" \
  -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
  "https://registry-1.docker.io/v2/${repository}/manifests/${tag}" \
  | tr -d '\r' | sed -n '/^[Dd]ocker-[Cc]ontent-[Dd]igest:/p'
```

在已审核发布版本的干净 checkout 上制作完整 `git archive` 并传到远端；以下命令在远端已校验且解包的 `<源码目录>` 中执行，三个 digest 均替换为本次查询值：

```bash
cd '<源码目录>'
DOCKER_BUILDKIT=1 docker build --network host \
  --build-arg BUILDKIT_SYNTAX='docker/dockerfile@sha256:<本次查询的前端digest>' \
  --build-arg NODE_IMAGE='node:22.23.1-bookworm-slim@sha256:<本次查询的node digest>' \
  --build-arg BASE_GUI_IMAGE='jlesage/baseimage-gui:ubuntu-24.04-v4.12.6@sha256:<本次查询的gui digest>' \
  --build-arg HTTPS_PROXY='<用户自备代理URL>' \
  --build-arg INFOOS_SOURCE_COMMIT='<完整40位源码commit>' \
  --build-arg INFOOS_INSTALL_CUDA_ASR=1 \
  --build-arg INFOOS_CUDA_ASR_PIP_INDEX_URL='https://pypi.org/simple' \
  -f deploy/docker/Dockerfile -t 'infoos-appliance:review-<commit短号>' .
```

备用方案是创建临时 `docker-container` buildx 构建器，并在构建器容器中设置代理环境变量，以 `--load` 导入镜像，用后删除该临时构建器。两种方案均不修改 Docker daemon 或 DNS，也不重启 `dockerd`。

新增 `deploy-existing.py` 用于更新已有 test 容器：资源值只读取本目录 Compose，不再复制一套内存/停止参数；现有端口、认证、GPU 和持久卷通过本机 Docker socket 复用。它使用 Docker daemon 报告的 API 版本，协议依据 [Docker Engine API 文档](https://docs.docker.com/reference/api/engine/)。

先预览已构建镜像的切换计划：

```bash
python3 deploy/docker/deploy-existing.py --environment test \
  --container infoos-acceptance --image infoos-appliance:REVISION \
  --revision FULL_GIT_COMMIT --update-kind code-only
```

`--update-kind` 必填，避免把普通代码更新误当成需要复制全部数据的迁移。确认目标和镜像后，同命令加 `--apply`：`code-only` 停止旧容器，复用原有 SQLite、附件和 state 卷，保留旧容器为 rollback 后启动新镜像。`--apply` 在停机前用 SQLite 在线备份 API 生成恢复点 `<backup-root>/<UTC时间>-<commit12>-sqlite/`（`infoos.sqlite3.zst` 与 `receipt.json`，目录 `0700`，写入前 `quick_check`，zstd 压缩后约为原库 1/6）；附件与 state 不复制，旧容器回滚也不会撤销新版本对共享卷的写入。选择此模式前，核对本次没有 schema、启动迁移、附件格式或持久数据语义变更。恢复时 `zstd -d infoos.sqlite3.zst -o infoos.sqlite3`，在所有写入者停止后替换数据卷中的库并删除旧 `-wal`/`-shm`。

test Appliance 需要在可信局域网直接访问时，在预览及随后同一条 `--apply` 命令中加入 `--bind-address 127.0.0.1 --bind-address <可信局域网IP>`。不传此参数会完全沿用旧容器端口绑定；传入后只改产品端口 `5800/tcp` 的绑定，沿用旧宿主端口（以当前容器 inspect 为准），预览 JSON 会列出新旧绑定。可通过 `https://<宿主局域网IP>:<端口>` 访问，例如 `https://<可信局域网IP>:<产品端口>`；首次访问需在浏览器中信任容器自签名证书，并使用面板 Web 登录。Save to InfoOS 扩展的服务器地址改为该 HTTPS 地址即可，manifest 已包含 `https://*/*`。此方式仅限可信内网；不得绑定 `0.0.0.0` 或公网地址，也不得发布产品端口以外的端口。

涉及上述数据变更时改用 `--update-kind data-migration`。它先在旧容器运行时预复制 assets、archive 和 state，停机后再同步变化并生成 SQLite 一致性副本，然后切换镜像。新快照在 `--backup-root` 下保存为 `assets/`、`archive/`、`state/`、`infoos.sqlite3` 和 `receipt.json`（`backup_format=linked-files-v2`）；与同一目录中上次完成的同卷快照相比，未变化文件使用快照之间的硬链接，不再重复占用附件空间。硬链接只连接备份快照，不连接运行数据卷。没有可链接的上一份快照时需完整复制一次；旧卷没有 archive 时保存空目录。备份目录权限为 `0700`，不会输出认证配置。

预览和 `--apply` 都会先做 nginx 预检：用新镜像起一个不联网、不挂卷的一次性容器，执行基础镜像自己的证书与 nginx 初始化脚本，再运行 `nginx -t`。预检失败时命令直接退出，不停旧容器、不建恢复点。这一步防的是基础镜像的 nginx 拒绝某些指令的情况（例如不适用的 `limit_except { deny all; }`）；静态测试看不出这类问题，旧做法要等切换后才以反复重启的形式暴露。

上述迁移快照不替代独立磁盘上的持续备份。`--backup-root` 可指定另一块已挂载、支持 POSIX 权限、扩展属性和硬链接的本地磁盘；同盘快照只便于版本恢复，不能抵御磁盘故障。`deploy-existing.py` 本身不删除任何备份、镜像或数据卷；新版本通过 revision 一致性和真实入口验收后，再按下文存储保留策略执行 `infoos-docker-storage`。切换失败时恢复旧容器；切换后仍须比较源码 HEAD、镜像 OCI revision label 与容器 `/opt/infoos/source-revision`，并执行真实业务入口及界面验收。

### 可选的逐文件媒体备份精简

备份排除必须由使用者逐文件审批；默认保留媒体。只有可核验的逐资产转录和原文件版本匹配时才适用，不能按低分整批删除。操作教程、软件演示、游戏、画面参考、转录异常及未知内容应保留原视频。

宿主 `/etc/infoos/backup-media-policy.json` 使用 `infoos-speech-media-exclusions-v1` 格式，`files` 按 assets 相对路径记录已批准文件的 `size`、`sha256`、`transcript_sha256`，另有 `approval` 审批记录。`deploy-existing.py` 与 `infoos-backup create` 自动读取该清单；每次只排除大小与视频字节 SHA-256 均匹配、且实际备份的数据库副本中仍有相同逐资产转录的条目。资产须为 `video`，`local_path` 为 assets 相对路径或容器内 `/var/lib/infoos/data/assets/` 下的绝对路径；转录须为非空 `video_transcript`，以 `source_url=asset:<asset_id>` 关联同卡资产，状态为 `enriched`、`preserved` 或 `done`。转录哈希按 `body` 原文的 UTF-8 字节计算，不去除空白。视频、转录改变或证据缺失时重新备份；新视频默认保留，核验后再追加清单。清单不存在时完整备份；清单格式、路径或证据字段非法时明确报错，不执行排除。迁移最终同步会重新检查，未变文件复用本次哈希结果，不在停机窗口重复读取全部媒体。

迁移精简快照标为 `linked-files-selective-v1`，逐文件排除清单写在 `excluded-media.json`；独立备份 manifest 标为 `media_scope=selective` 并记录 `excluded_media`。旧手工精简格式 `selective-media-pruned-v1` 同样可作增量链接基准（允许没有 `archive/`）；以精简快照作 `--link-dest` 不会沿用排除规则，下一份完整快照会从现用目录复制缺少的视频。这些文件不包含视频内容，也不更改现用数据库和媒体。恢复时，已排除视频的本地播放、画面与重新转录能力需依赖仍在的原盘、其他媒体备份或可重新获取的原链接，文字不能替代画面。部署两个脚本到同一目录才能使用这一策略；仅复制旧版本部署脚本不会生效。

### 独立磁盘备份

在 Docker 宿主上把 `deploy/docker/infoos-backup` 与同目录的 `deploy-existing.py` 放在一起，确保已安装 `python3` 和 `zstd`。将 `--dest` 指向另一块已挂载且支持硬链接的磁盘，不要指向运行数据卷或与其同盘的 `/var/backups/infoos`。命令读取当前容器的数据卷路径及镜像 commit，不读取认证配置：

```bash
python3 deploy/docker/infoos-backup create --dest /mnt/infoos-offsite
python3 deploy/docker/infoos-backup verify --dest /mnt/infoos-offsite/infoos-backup-<时间戳>
python3 deploy/docker/infoos-backup prune --dest /mnt/infoos-offsite --keep 3
```

`create` 对主库及 `archive/` 下每个 `.sqlite3` 使用 SQLite 在线备份 API，分别执行 `quick_check`，压缩为 `.sqlite3.zst`；归档目录不存在时正常备份。活动 SQLite 的 `-wal`、`-shm`、`-journal` 伴随文件由在线备份读取，不直接复制；`archive/` 下其他文件会明确报错，避免静默漏备。复制 `assets/` 时，仅对内容哈希一致的附件与上一份同源快照建立硬链接；未变化的归档库也与上一快照硬链接。快照目录为 `0700`，`manifest.json` 记录镜像 commit、主库及各归档库的 `user_version`、各表行数和 SHA-256，以及每个备份文件的 SHA-256 和大小。创建完成前命令会自行验证；`verify` 重新核对文件清单、哈希、逐库解压后的 SQLite `quick_check` 和行数。`prune --keep` 至少为 1，只删除该目标目录中带本工具 manifest 的快照，不碰部署恢复点或 Docker volume。首次备份需要为实际纳入的附件预留空间；后续同盘快照才可硬链接。备份应在附件不会被并发删除的维护窗口执行，以保证 SQLite 副本和附件集合的时点一致；可在创建后执行 `verify`，并按恢复演练确认可用性。本次不安装定时任务。

离机快照只包含 data 卷中的主库、归档库和 `assets/`，不包含 state 卷。state 卷保存 Chrome profile、Cookie 等登录凭据；离机复制会扩大泄露面。恢复后应在原 InfoOS 专用 Chrome profile 中重新登录，并对需要登录的平台做功能探针，不能仅凭容器启动判断登录态可用。

恢复步骤：先对目标快照运行 `infoos-backup verify --dest <快照目录>`；精简 manifest 中 `excluded_media` 的视频本来就不在备份文件清单内，缺少它们不算失败，其余文件照常校验。停止容器及所有可能写入 data 卷的进程；将 `infoos.sqlite3.zst` 和 `archive/` 中每个 `.sqlite3.zst` 用 `zstd -d -c` 解压到数据卷中对应的 `.sqlite3` 路径，替换前删除目标库旧的 `-wal`、`-shm` 文件，并移除数据卷中不属于该快照的旧归档库；把快照 `assets/` 复制回数据卷的 `assets/`。精简快照恢复后仅有已备份附件与数据库中的转录；若要补回被排除的视频，须从原盘或其他完整备份单独复制。启动后对主库和每个归档库执行 `PRAGMA quick_check`，并与 manifest 中的逐表行数核对，再完成登录功能探针。恢复前保留原数据卷的独立恢复点，避免覆盖后无法回退。

### 异地数据库备份

这是「独立磁盘备份」的轻量补充：同一物理盘上的不同分区与 `/var/backups/infoos` 不能防磁盘故障。附件通常可重新下载，数据库中的卡片、评估、关注画像和冻结日报不可再生；空间有限的目的地优先保存主库与 `archive/` 下的归档库。`infoos-backup create --db-only` 沿用在线备份、逐库 `quick_check`、zstd 与 manifest 校验，不复制 `assets/` 或 state。清单新增 `scope=full|db-only`；老清单视为 full，`prune --keep N` 对完整附件备份、精简附件备份及 db-only 三组分别保留 N 份，互不挤占；老清单无 `media_scope` 时按完整附件备份计数。

| 目的地 | 做法 | 取舍 |
|---|---|---|
| 另一块本地物理盘 | 确认已挂载后将 rsync 目的地设为该盘的专属目录 | 无网络费用，抗单盘故障，但不防家中失窃或火灾；未挂载的 NTFS 盘不能直接当作可用目的地 |
| 自有 VPS 经 SSH | 预建目的地专属目录，使用 SSH key 与已核验的 known_hosts，设 `user@host:/path/` | 能隔离家中故障；需足够容量、两端 rsync 与 SSH 权限，定期检查传输费用 |
| 对象存储 | 用 rclone 挂载后设为本地目的地，或自行替换传输命令并实现相同成功后保留语义 | 不内置 rclone；挂载需支持 rsync 所用操作，元数据或重命名兼容性须实测，原生上传常更合适 |

建议配置 age 公钥加密。库中包含 Token 哈希、来源配置及个人内容；公钥可留在宿主，解密私钥应保存在另一处受保护位置。设置公钥后缺少 age 或加密失败会直接退出，绝不退化为明文传输；暂存的原始快照仍是明文，目录必须为 `0700`。不设置公钥时，SSH 只保护传输，目的地仍保存明文。

以下仅为用户确认目的地、凭据与保留容量后的安装示例，本任务不执行。宿主需要 Python 3、zstd、rsync，启用加密时还需 age；SSH 目的地需预先完成非交互认证及主机指纹核验，不关闭 host key 校验。脚本不读取容器凭据、不保存 SSH 密钥，环境变量优先于配置文件；配置文件使用 `KEY=value`（空格值加引号），不执行 shell 展开。

```bash
# 在已批准的宿主上操作；以下路径为模板，不是实际配置。
sudo install -d -m 0755 /usr/local/lib/infoos
sudo install -m 0755 deploy/docker/infoos-backup deploy/docker/infoos-offsite-sync /usr/local/lib/infoos/
sudo install -m 0644 deploy/docker/deploy-existing.py /usr/local/lib/infoos/
sudo install -d -m 0700 /etc/infoos
sudo install -m 0600 /dev/null /etc/infoos/offsite.env
# 用安全编辑器填写 /etc/infoos/offsite.env（root 属主，0600）：
# INFOOS_OFFSITE_STAGING=/absolute/staging/directory
# INFOOS_OFFSITE_RSYNC_DEST=user@host:/absolute/backup/directory/
# INFOOS_OFFSITE_KEEP=7
# INFOOS_OFFSITE_SSH_OPTS='-o BatchMode=yes -i /absolute/key/file'
# INFOOS_OFFSITE_AGE_RECIPIENT=age1...    # 可选，填写完整公钥
sudo /usr/local/lib/infoos/infoos-offsite-sync --dry-run
# 确认步骤后手工运行一次并在目的地核验，再决定是否启用模板：
sudo /usr/local/lib/infoos/infoos-offsite-sync
sudo install -m 0644 deploy/docker/infoos-offsite-sync.service deploy/docker/infoos-offsite-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now infoos-offsite-sync.timer
```

模板每天 03:00 运行，带最多一小时随机延迟，`Persistent=true` 会补跑错过的任务。运行用户为 root，配置文件 `EnvironmentFile` 与脚本读取同一路径。用 `systemctl list-timers infoos-offsite-sync.timer` 和 `journalctl -u infoos-offsite-sync.service` 检查；最终日志为单行 JSON：UTC 时间、快照名、传输字节数、是否加密、目的地主机（本地为 `local`），不含用户名、完整路径或 SSH 参数。`--dry-run` 只读配置、打印步骤，不创建文件、不检查 Docker、不联网。

同步顺序为 create → verify → 可选 tar + age → rsync 上传 → 两端保留。使用目的地下的专属 `infoos-db-only-plain/` 或 `infoos-db-only-encrypted/` 子目录，只传新快照后才以 `rsync --delete-after` 将该子目录收敛到最近 N 份，再清理本地旧份；失败非零退出，保留本机上一份成功快照。**一个目标子目录只供一个暂存目录/同步实例使用**，其中不要放其他文件；目标保留以本机成功记录为准，首次使用空暂存目录同步也会替换该专属目标集合。更换暂存目录或目的地前先单独保留已有远端副本。两种加密模式的保留空间分别计数，切换模式不自动清理另一种。失败的 rsync 可能在目标留下不完整的新副本，下次成功会收敛；恢复时仍必须 verify。暂存目录有互斥锁，不与 full 备份共用。

恢复时先将所选快照取回隔离目录。加密快照先解密并解包，随后 verify：

```bash
age -d -i <受保护的私钥文件> -o <恢复目录>/snapshot.tar <快照名>.tar.age
# 仅解包自有备份，使用空的隔离恢复目录。
tar -xf <恢复目录>/snapshot.tar -C <恢复目录>
python3 /usr/local/lib/infoos/infoos-backup verify --dest <恢复目录>/<快照名>
```

明文快照直接 verify。再按「独立磁盘备份」的现有恢复步骤，保留旧数据恢复点、停止所有写入者、解压并还原主库与全部归档库、移除旧 WAL/SHM 和不属于快照的旧归档库。db-only 没有附件，跳过 assets 复制；附件需从另一份备份恢复或通过产品入口重新下载，无法重新取得的附件可能永久缺失。state 不在快照内，必要时重新登录。启动后核对逐库 `quick_check`、manifest 表行数和真实卡片/日报阅读。

每月演练一次：从目的地取回一份备份，在全新的隔离 data/state 目录解密、verify、还原主库和归档库，记录快照时间、校验结果、恢复耗时，并检查一张带评估的卡片及一份冻结日报；记录附件缺口和登录状态。不得覆盖运行卷；仅传输成功或 verify 通过都不代表已完成应用恢复演练。

Compose 的内存 reservation/limit/swap、120 秒停止宽限及日志轮转必须在实际 `docker inspect` 中核对。如需隧道，用用户自行配置的 SSH 转发产品 HTTPS 端口；不要转发内部浏览器/debug 端口，避免误连遗留 Web 或其他数据集。

## 1. 准备配置

```bash
cp deploy/docker/.env.example deploy/docker/.env
```

填写 `.env` 中自己的管理员名与强密码（建议至少 12 字符，且不超过 72 UTF-8 字节）；首次初始化只校验非空、字节上限和无换行，尚未像登录后改密接口一样强制 12 字符下限或排除账户名，须用户自行选择强密码。模板留空，首次启动缺失时明确失败，不提供可用默认密钥。Token 在登录后由用户创建。真实 `.env` 被 `.gitignore` 排除，不得提交。

首次从空卷安装见[公共首页](../../README.md#从零安装候选安装草稿)：启动时顺序迁移建库，不使用种子库。默认 project `infoos` 保留原容器/卷名；隔离安装用独立 `-p`，容器与卷名随 project 改变。不要用显式容器/卷覆盖指向已有实例；并行实例还须分配不同 HTTPS 端口、镜像标签与 agent 配置目录。所有构建与启动命令使用同一个 project 和 env 文件。

### 知识卡归档映射（可选）

MCP `get_knowledge_schema` 的 `archive_purpose` 描述通用归档用途；`pkm_target` 只取用户运行配置，缺省或未配置的类型返回空字符串。InfoOS 不读取 PKM 文件，也不自动物化知识卡。可以在 `INFOOS_CONFIG_PATH` 指向的 YAML（容器默认 `/var/lib/infoos/state/config.yaml`）合并以下配置，保留其他现有配置项：

```yaml
knowledge:
  pkm_targets:
    project: "案例库/"
    topic: "内容项目/选题/"
```

支持 `project`、`opportunity`、`method`、`topic`、`news`、`product`、`resource`；后两类仍未启用。映射每次读取 schema 时生效，不写入数据库画像，不改变画像版本、拆解版本或历史知识卡，无需数据库迁移。配置错误会显式报错。

## 2. 构建和启动

从已提交且干净的 Git worktree 构建；默认不绑定特定分支，需要分支限制时显式设置 `INFOOS_CANONICAL_BRANCH`。发布入口会从 `HEAD^{commit}` 读取完整
Git object ID，注入镜像并拒绝 dirty worktree、空值、`unknown`、短 SHA 和伪造
的全零 SHA：

```bash
deploy/docker/build-image.sh
docker compose --env-file deploy/docker/.env -f deploy/docker/compose.yaml up -d
```

GPU 镜像使用同一入口并附加 overlay：

```bash
deploy/docker/build-image.sh -f deploy/docker/compose.gpu.yaml
```

构建入口默认使用 BuildKit 和 `linux/amd64` 平台。Python CPU/Web/媒体闭包与
CUDA ASR 闭包是两个连续的稳定镜像层，并由
`python-constraints-linux-amd64.txt` 固定版本；新增账号哈希依赖 bcrypt 5.0.0
仍需随本次镜像进行 Linux 构建验收。其余依赖亦须在用户目标环境核验。
普通后端源码变化不会让这些重依赖层失效；只有依赖声明、约束文件或相关构建参数
变化时才会重新安装。默认安装 CPU/Web/媒体依赖；CUDA ASR 依赖仅在
`INFOOS_INSTALL_CUDA_ASR=1` 的 GPU 构建中安装，不表示默认镜像包含 CUDA。
Torch 仅裁掉运行时没有引用的头文件与测试目录。

自动化构建若不使用 wrapper，必须显式传入由受信任 checkout 解析的完整 revision：

```bash
export INFOOS_SOURCE_COMMIT="$(git rev-parse --verify 'HEAD^{commit}')"
docker compose --env-file deploy/docker/.env -f deploy/docker/compose.yaml build
```

Dockerfile 本身仍会验证 revision，因此漏传或传入 `unknown` 的 acceptance/release
构建会失败，不会生成无法追溯源码的镜像。

默认只监听 `127.0.0.1:8443`，使用 HTTPS。首次自签名证书需要在受信任操作端确认。镜像已固定 OpenCLI Bridge 1.0.22；通过面板“采集环境”进入短时授权的 Browser Console，确认扩展连接并完成人工登录。

镜像同时固化生财有术和飞书外部文档 OpenCLI 适配器。生财帖子中检测到受信任的
`feishu.cn` / `larksuite.com` 链接后，`CONTENT_ENRICH` 使用容器内专用 Chrome
读取可见文档正文，并保存为用户可见的 `external_doc` ContentBlock；链接本身不能
冒充已取得的飞书正文。

### 内网账号与安全

管理面「账号与安全」（`/?menu=account`）可修改当前密码和账户名，支持邮箱形式。
先校验当前密码；新密码至少 12 个字符、最多 72 个 UTF-8 字节，不得包含账户名或与
当前密码相同。新账户名限 3–128 位字母、数字及 `@._+-`。旧 apr1 账户会提示修改
一次密码，升级到 bcrypt cost 12；单独改名保留原哈希。

首次安装的空密码库需要 `.env` 中的 `INFOOS_ADMIN_USERNAME`、
`INFOOS_ADMIN_PASSWORD`（映射到容器的 `WEB_AUTHENTICATION_USERNAME/PASSWORD`）。
缺失时启动明确失败。登录后应从 Web 修改凭据，然后可从部署环境中移除这两个变量。
已有任何用户时，启动脚本完全忽略初始化变量。密码库仍位于 state 卷映射的
`/config/webauth-htpasswd`，权限 `0600`，重启和后续镜像升级使用持久库。
不要删除 state 卷来重新初始化账户。

既有容器由 `deploy-existing.py` 升级时，重复传入以下参数，先预览再由运维执行：

```text
--drop-env WEB_AUTHENTICATION_USERNAME --drop-env WEB_AUTHENTICATION_PASSWORD
```

不传时保留原行为；预览仅列 `env_removed` 名称，不显示值。此选项只影响替换后的
容器，不会编辑宿主 `.env`，也不会清除旧 rollback 容器的配置。回退到仍会覆盖密码的
旧镜像时，必须另行处理其初始化变量，否则旧环境密码可能重新生效。
本功能没有 SQLite schema 或既有数据格式迁移，发布可用 `code-only`；密码库、失败
计数文件均在 state 卷。现有 code-only SQLite 恢复点不包含账号库。

实现与验收边界：

- rootfs 在 Dockerfile 最后的 `COPY deploy/docker/rootfs/ /` 覆盖同路径
  `/etc/cont-init.d/10-webauth.sh`，并显式赋予执行权限。它仍占基础镜像 10 阶段的
  初始化位置，先于 `20-infoos-runtime.sh` 和服务启动；不叠加一个再次写库的初始化器。
  本机 Docker daemon 不可用，实际镜像内的执行顺序和覆盖产物须在镜像验收时回读。
- 新登录页位于 `/opt/noVNC/login/`，只用本地 CSS/JS。按公开基础镜像协议向
  `/login/login` 发送表单 POST，字段 `username/password`，由 webauth 设置 cookie
  并跳转；`login_result=INVALID_CREDENTIALS` 展示中文错误。
- webauth 不提供可靠的用户名响应头，token 内容也不含用户名。单用户库直接识别，
  多用户库要求同时输入当前账户名和当前密码，不提供账户列表。
- API 再次通过私有 webauth socket 验证会话，并沿用全局同源防护。来源地址使用
  nginx 重写的 `X-Real-IP`；后端仅监听 loopback，不应绕过 gateway 对外暴露。
  每个来源及来源/账户组合 15 分钟最多 5 次当前密码失败；计数在密码库锁内持久化，
  不随 Web 进程或 webauth 重启清零。
- 密码库同目录临时写、fsync、rename，独立文件锁覆盖校验与修改；新哈希由 Python
  bcrypt 生成。兼容 apr1 校验调用本地 OpenSSL（密码仅 stdin，不在 argv）。
- 修改后发送 SIGHUP，再向同 UID、精确进程名的 webauth 发 SIGTERM，由基础镜像
  supervisor 拉起，等待旧 PID 消失、新 PID 与 socket 就绪。所有旧会话失效；
  若库已写入但重启未确认，API 返回 503 并明确提示，不报告成功或自动重试。
- 审计复用 InfoOS 结构化运行日志，记录 UTC 时间、账户、来源 IP、动作、改名前后名
  和固定结果码；不记录请求体、密码、哈希或 cookie。未新增审计表。

公开协议依据：[登录页](https://github.com/jlesage/docker-baseimage-gui/blob/master/rootfs/opt/noVNC/login/index.html)、
[webauth 源码](https://github.com/jlesage/docker-baseimage-gui/blob/master/src/webauth/webauth.go)、
[nginx auth 配置](https://github.com/jlesage/docker-baseimage-gui/blob/master/rootfs/opt/base/etc/nginx/include/auth.conf)。
本次只能取得 master；固定的 `ubuntu-24.04-v4.12.6` 原文件未能读取，不能视为该镜像
实测证据。发布前须比对其登录协议、会话生命周期和 supervisor 重启行为。

### 容器内受限网络出口

Docker Appliance 可选在同一容器内运行固定版本的 headless Mihomo，不依赖
宿主桌面、Clash Verge 或宿主 systemd 代理服务。**默认构建不下载、不安装 mihomo**，运行开关也默认为关闭。启用时镜像才包含经过 SHA256 校验的
内核；节点、订阅和其他秘密只允许放在 `infoos-state` 持久卷的
`/var/lib/infoos/state/proxy/source.yaml`，权限为 `0600`。

先在构建环境或 `.env` 设置 `INFOOS_WITH_MIHOMO=1`，通过 Compose 构建参数传入 Dockerfile；直接构建使用 `--build-arg INFOOS_WITH_MIHOMO=1`。例如 `INFOOS_WITH_MIHOMO=1 deploy/docker/build-image.sh`。构建开关与运行开关独立，仅设置运行开关不能给已有镜像补装二进制。已有代理实例更新镜像时必须保留此构建参数；未安装 mihomo 的镜像不能运行内置代理。默认代理关闭时不需要该二进制。

再把一份已经可用的 Mihomo/Clash 配置写入该路径，在
`deploy/docker/.env` 中设置：

```text
INFOOS_EMBEDDED_PROXY_ENABLED=1
INFOOS_OUTBOUND_PROXY_URL=http://127.0.0.1:17897
INFOOS_BROWSER_PROXY_URL=http://127.0.0.1:17897
INFOOS_NO_PROXY=127.0.0.1,localhost
```

启动器会基于源配置生成临时运行配置，强制 mixed proxy 只监听容器 loopback
`127.0.0.1:17897`，关闭 TUN、透明代理、LAN listener 和源配置中的控制接口，
再把只读健康控制接口绑定到 `127.0.0.1:19090`。这两个端口都不通过 Docker
`ports` 发布，也不增加 `NET_ADMIN` capability。启用内置代理时，标准 HTTP
客户端和专用 Chrome 的代理地址必须同时精确等于上述 loopback URL；如果仍残留
宿主 bridge、LAN 或带凭据代理地址，容器会 fail closed，避免双层代理、回环和
凭据出现在 Chrome 进程参数中。

源配置中的节点、分组、规则和内部 DNS 策略会保留，但 DNS listener 会被移除。
代理配置和 Geo 数据属于 `infoos-state/proxy/` 的备份范围。更换配置时先保留旧
文件，使用 `mihomo -t` 验证后再原子替换并重建容器；不要把订阅 URL 写入
Compose、镜像层、Git 或命令行。

### 可选的 Wigolo shadow 对照

普通公开网页仍由 InfoOS 的有界 HTTP provider 生成 RawItem 和信息卡。需要收集
provider 选型证据时，可以把隔离运行的 Wigolo 作为只读 shadow：

```text
INFOOS_PUBLIC_DOCUMENT_SHADOW_PROVIDER=wigolo
INFOOS_WIGOLO_SHADOW_BASE_URL=http://wigolo:3333
INFOOS_WIGOLO_SHADOW_TOKEN_FILE=/var/lib/infoos/state/providers/wigolo.token
INFOOS_WIGOLO_SHADOW_TIMEOUT_SECONDS=45
```

token 文件只放在 state volume，权限设为 `0600`；不要写进 `.env`、Compose、
镜像层或日志。Wigolo 必须通过未发布端口的隔离 Docker network 访问。shadow
只在 InfoOS 主 HTTP 请求成功后运行，结果正文不会写入 RawItem、ContentBlock
或卡片；task/run evidence 只保留 provider、final URL、字符/链接/媒体计数、
content hash、耗时、cache/fetch tier、typed failure 和可疑截断比率。

该能力默认关闭，不会把 Wigolo 变成 Appliance 依赖。正式启用默认路由前仍需
完成样本量、AGPL、第二数据卷保留/删除、资源配额、供应链和备份恢复评审。
Wigolo 当前响应不提供完整 redirect hop，InfoOS 无法复验 provider 内部的每一跳；
因此只能在隔离网络对明确公开样本做 PoC。在 provider 提供 redirect evidence，
或 egress 网络策略能彻底阻断 loopback、link-local 和私网目标前，不得用于
scheduled shadow，更不得成为默认抓取路径。

容器内仍只有一张 SQLite `tasks` 表，但由六个独立的
`backend.workers.task_worker` 进程按持久化 `queue_name` 消费：
`public_fetch`、`browser_fetch`、`urgent_media`、`card_pipeline`、
`gpu_asr` 和 `maintenance_io`；`cloud_ai` 仅保留为历史队列名，不启动 worker。默认并发可分别通过
`INFOOS_PUBLIC_FETCH_WORKERS`、`INFOOS_BROWSER_FETCH_WORKERS`、
`INFOOS_URGENT_MEDIA_WORKERS`、`INFOOS_CARD_PIPELINE_WORKERS`、
`INFOOS_GPU_ASR_WORKERS` 和
`INFOOS_MAINTENANCE_IO_WORKERS` 调整。队列只隔离资源和时效，不复制
SQLite，也不按平台创建第二套业务实现。

除六个队列消费者外，`infoos-worker-auth-qr-check` 专门轮询扫码轮次，
`infoos-feishu-inbox` 作为单进程常驻服务接收飞书私聊。两者均依赖 `infoos-web`，
纳入被动健康检查及进程重启频率检查。飞书未就绪时 inbox 每 60 秒复查；
就绪后通过 lark-cli 长连接订阅 `im.message.receive_v1`，无需公网回调或额外端口。
只接受已绑定接收人的文字私聊，支持「小红书二维码」「抖音二维码」
「知识星球二维码」「登录状态」「帮助」。手动请求不受静默时段和每日轮数限制；
回复只走飞书并立即入发送队列。原消息正文不写日志、不落库，仅用 message_id 去重。
inbox 退出时关闭 consume 的 stdin，必要时发送 SIGTERM，不使用 SIGKILL。

视频抽音频与 GPU 转录还会跨 `urgent_media` / `gpu_asr` 共享
SQLite `media_compute` 资源池，默认只允许两个计算任务同时运行。worker 在宿主
memory PSI `full.avg10`、可用内存或容器内存达到保护水位时停止认领新的媒体计算
任务，但继续处理轻量构卡与维护工作。相关水位通过
`INFOOS_MEDIA_COMPUTE_SLOTS`、`INFOOS_RESOURCE_PRESSURE_FULL_AVG10`、
`INFOOS_RESOURCE_MIN_AVAILABLE_MIB` 和 `INFOOS_MEMORY_SOFT_LIMIT_MIB`
配置；不得仅提高单个队列并发而绕过该资源池。

Compose 默认给容器 6 GiB reservation、12 GiB 硬上限和 14 GiB
memory+swap 总上限（最多 2 GiB swap，仍受宿主可用 swap 限制），在 32 GiB
宿主上为长期运行的 Claude / Codex agent、内核和 Docker 留出空间。
reservation 是回收优先级设置，不预占 RAM，也不代替任务准入水位。
`INFOOS_MEMORY_SOFT_LIMIT_MIB` 默认 10240（10 GiB），按包含 page cache 的
cgroup `memory.current` 判定，为已运行任务留出 2 GiB 增长余量；宿主可用内存
低于 `INFOOS_RESOURCE_MIN_AVAILABLE_MIB`（默认 4096）或 PSI 超标也会停止
认领新的媒体计算任务。水位不终止已运行任务，Chrome 也不受媒体任务准入控制，
因此不能保证不发生 OOM。调整上述配置应核对目标主机的历史采样、工作集、
转录记录及 OOM/保护水位证据，不能以单次快照代替长期容量验收。

已有容器可在线调整这三个 Docker 限制，无需重启：

```bash
docker update --memory 12g --memory-swap 14g --memory-reservation 6g infoos-acceptance
```

该命令不更新环境变量；`INFOOS_MEMORY_SOFT_LIMIT_MIB=10240` 要等下次部署创建
新容器才生效。`deploy-existing.py` 会从 Compose 读取这三个限制及软水位，
下次部署前应预览并核对；Compose 启动用的旧 `.env` 或部署进程的 shell 环境
可能覆盖默认值。

成功转录使用的中间 WAV 默认保留 7 天。每小时
`PIPELINE_RECONCILE` 会在 `maintenance_io` 队列中分批清理：只有
`VIDEO_TRANSCRIBE` 已成功、任务完成和文件修改时间都超过保留期，并且同一路径
没有 pending、running、failed 或 blocked 转录任务引用时才会删除。处置结果会
回写任务产物并追加审计事件。可通过 `INFOOS_AUDIO_RETENTION_DAYS` 调整天数，
设为 `0` 可关闭；`INFOOS_AUDIO_RETENTION_BATCH` 控制每轮上限，默认 500。

Browser Console 必须从面板启动：启动 API 会设置 HttpOnly、SameSite=Strict 的短时授权，gateway 同时复验外层登录会话和同源请求。默认空闲 10 分钟失效、绝对时限 30 分钟，可通过 `.env` 缩短；FastAPI WebSocket proxy 会持续执行该时限并主动关闭已经建立的过期控制连接。不要把 `/browser-console/` 当作可收藏的永久入口。

平台采集因登录失效进入人工熔断时，InfoOS 会持久化本次恢复事件。启用通知邮件并配置 `INFOOS_PUBLIC_BASE_URL=https://<InfoOS 外部地址>` 后，首次故障邮件会指向受认证面板中的恢复事件；该值只接受无 path、query、fragment 和凭据的 HTTPS origin。用户从面板打开容器 Browser Console 完成扫码、密码、MFA 或二次确认，再点击“完成并验证”。只有平台功能探针通过，系统才关闭对应 outage epoch 的人工熔断并重新排队本次关联的登录失败任务。邮件链接不绕过产品认证，也不携带 Browser Console grant、Cookie 或账号凭据。

部署态浏览器写操作采用 fail-closed 所有权检查：`INFOOS_STATE_HOME=/var/lib/infoos/state` 时，必须同时证明 `INFOOS_BROWSER_RUNTIME=supervised-container`、进程位于 Docker 容器内且 Profile 精确为 `/var/lib/infoos/state/chrome-profile`。原生 systemd Web/worker 即使配置了同名 launcher、Chrome executable 和 Profile 路径，也不得启动、绑定、重启、清理或通过 OpenCLI 操作宿主 Chrome。

### NVIDIA GPU 视频转录

有 NVIDIA GPU 的正式主机使用 GPU overlay 构建和运行；不要只设置
`INFOOS_FASTER_WHISPER_CUDA_ENABLED=1`，否则镜像可能没有 CUDA ASR 依赖，
容器也不会得到 GPU device：

```bash
deploy/docker/build-image.sh -f deploy/docker/compose.gpu.yaml
docker compose --env-file deploy/docker/.env \
  -f deploy/docker/compose.yaml \
  -f deploy/docker/compose.gpu.yaml up -d
```

该 overlay 固定启用 `faster-whisper large-v3-turbo`、CUDA、持久化模型目录，
以及由 GPU worker 管理的进程隔离常驻模型运行时。任务执行只从本地缓存解析
模型；将 `INFOOS_FASTER_WHISPER_PERSISTENT_RUNTIME_ENABLED=0` 可回滚到
每任务子进程模式。

镜像会把 CUDA ASR 构建能力写入 `/opt/infoos/capabilities/cuda-asr`。容器启动和
被动 healthcheck 都通过 `infoos-verify-runtime-capabilities` 强制核对以下不变量：
运行配置启用 CUDA ASR 时，镜像必须包含 CUDA ASR 构建标记、
`FasterWhisperCudaProvider` 必须可用，并且容器必须能访问 NVIDIA GPU。任一事实
不成立时容器拒绝进入 healthy，不能让 `VIDEO_TRANSCRIBE` 在队列中批量变成
`blocked_faster_whisper_cuda_missing`。
本地 ASR 只生成视频转录事实；InfoOS 不调用 LLM，摘要与评估由外部 agent 写回，历史翻译与总结 block 继续可读。
没有音轨的视频，或 faster-whisper 明确报告 `duration_after_vad == 0` 的视频，会记录
`transcript_not_applicable`（reason code 分别为 `no_audio_stream` 或
`no_speech_detected`）；有语音但 ASR blocked/failed 的卡片必须保持 `partial`，
不能再显示为完整。其他 provider 的普通空转录仍记录为
`failed_empty_transcript`，不能据此判定无需转录。

## 3. 边界检查

```bash
docker compose --env-file deploy/docker/.env -f deploy/docker/compose.yaml ps
docker inspect infoos --format '{{json .HostConfig.PortBindings}}'
docker inspect infoos --format '{{.State.Health.Status}}'
docker exec infoos /opt/infoos/bin/infoos-healthcheck
docker exec infoos sh -lc 'google-chrome-stable --version && opencli --version'
```

GPU 版本还必须验证容器能看见设备和 CUDA ASR 运行时：

```bash
docker exec infoos nvidia-smi
docker exec infoos /opt/infoos/venv/bin/python -c \
  'from backend.application.enrichment.asr.providers.faster_whisper import FasterWhisperCudaProvider; print(FasterWhisperCudaProvider().is_available())'
```

`PortBindings` 只能包含 gateway 的 `5800/tcp`。不要新增 `5900`、`9222`、`19825` 或其他 Browser Runtime 端口映射。

聚合 healthcheck 只做被动检查：Web、六个队列 worker、扫码检查 worker、飞书 inbox、scheduler、Chrome、OpenCLI daemon、SQLite 可读性和两分钟内的进程重启频率。它不会创建 tab 或执行平台抓取；缺少任一队列消费者或某个子进程持续崩溃都会把容器标记为 unhealthy，而不是被 supervisor 无限重启掩盖。

## 4. 持久化和回滚

### 任务事件归档与空间回收

每小时由 scheduler 规划一次 `TASK_EVENT_ARCHIVE`，由 `maintenance_io` 持续 worker 执行。普通 `task_events` 保留最近 30 天；认证恢复、provider 恢复、熔断、失败、租约恢复和 superseded 关键事件保留 180 天。仅归档终态或已不存在 task 的过期事件，以及无 task_id 的过期事件；未解决认证恢复关联事件继续留在主库。`tasks` 行不自动删除。单次任务默认最多处理 50,000 条或运行 5 分钟，按 2,000 条分批；天数和预算可用对应 `INFOOS_TASK_EVENT_*` 环境变量调整。

新建 Compose 容器从 `deploy/docker/.env` 读取这些变量。`deploy-existing.py` 保留旧容器已有值；需要在该更新入口调整归档参数时，将目标 `INFOOS_TASK_EVENT_*` 变量显式传入命令环境，预览后用同样变量执行 `--apply`。

归档按事件 UTC 月份写入持久 data 卷的 `archive/task_events/task_events-YYYY-MM.sqlite3`，应与主库一同备份和恢复。`data-migration` 快照包含 `archive/`。来源追踪中较早运行的逐事件证据可在服务器查询：

```bash
python -m backend.workers.task_event_archive query --task-id '<task_id>' --base-dir .
python -m backend.workers.task_event_archive query --chain-id '<chain_id>' --base-dir .
```

数据导出的 `task_events` 只包含主库热窗中的事件，不自动合并归档。归档任务 artifacts 每次记录删除量、剩余估计和空间统计；每日结构化日志 `task_history_stats` 记录主库与归档体积、freelist 和 24 小时新增量。

删除事件后 SQLite 会把页放入 freelist 供后续写入复用，主库文件通常不会立刻缩小。归档任务不会执行 `VACUUM`。当 freelist 占比持续较高且确需归还磁盘空间时，在维护窗口先制作并核验主库与 archive 的一致恢复点，确认可用空间约为主库大小的两倍，停止所有写入者，再由运维人员对主库手动执行 `VACUUM`，完成后验证 `PRAGMA quick_check`、主库/归档计数和服务恢复。不要在在线采集期间执行。 v15 起卡片及 block 检索索引依赖 rowid，VACUUM 须与索引重建一起做：停服并核验恢复点后运行 `/opt/infoos/venv/bin/python /opt/infoos/app/scripts/rebuild-card-text-search.py --db /var/lib/infoos/data/infoos.sqlite3`（默认只读预览），确认后加 `--apply` 由脚本执行 VACUUM → 重建 FTS → 校验（行数、抽样命中、`integrity_check`、freelist），校验通过再恢复服务；中断后保持停服，用 `--rebuild-only --apply` 修复。步骤详见 [SQLite 迁移手册](../../docs/engineering/sqlite-migrations.md)。

- `infoos-state`：setup、TLS/web auth、Chrome Profile、OpenCLI binding 和日志。
- `infoos-data`：SQLite、CardAsset、导出和运行日志。

普通升级只替换容器，不删除 volumes。回滚时使用上一版 image 重新创建同名容器。不要同时把 `infoos-state` 挂给两个运行中的容器，也不要运行 `docker compose down -v`。

### 镜像和构建缓存占用

GPU Appliance 显示的约 9.34 GB 是 Docker 镜像层在宿主磁盘上的占用，不是容器
启动后必然占用的 RAM。运行内存取决于实际启动的 Chrome、worker、模型和任务；
同一镜像启动多个容器时共享只读镜像层，不会为每个容器重复复制 9.34 GB。容器额外
写入层、持久 volume 与 BuildKit 缓存也分别计量，不能把它们都算成运行内存。

### 存储保留策略

每次部署验收通过后，在宿主上先预览再执行（脚本随源码维护，由用户自己的 SSH/SCP 方式上传到 `<宿主运维目录>`）：

```bash
<宿主运维目录>/infoos-docker-storage inspect
<宿主运维目录>/infoos-docker-storage apply
```

默认保留：

| 对象 | 保留 | 清理 |
|---|---|---|
| rollback 容器 | 最新 2 个 `infoos-acceptance-rollback-*` | 更老且已停止的 |
| `infoos-appliance` 镜像 tag | 当前容器与保留 rollback 引用的 | 其余没有保留容器引用的 tag |
| BuildKit 缓存 | 当前镜像构建前 6 小时内用过的（即当前构建链） | 更早就没再用过的；不按容量裁剪 |
| 完整快照（`linked-files-v1` / `linked-files-v2`） | 最新 1 份 | 更老的 |
| 已批准的媒体精简快照 | 最新 1 份，与完整快照分别计数 | 更老的精简快照；不据此删除唯一完整快照 |
| SQLite 恢复点 | 最新 2 份，未压缩的旧格式就地压缩为 `.zst` | 更老的 |
| 旧 `.tar` 备份 | 无 | 仅当存在更新的完整快照时删除 |

`apply` 要求当前 `infoos-acceptance` 正在运行；无法识别的备份目录、`.pending-*` 残留和所有 Docker volume 都不处理。脚本不调用 `docker system/image/volume prune`，也不处理非 InfoOS 容器或镜像。保留数量可用脚本帮助中的 `INFOOS_DOCKER_*`、`INFOOS_BACKUP_*` 环境变量覆盖；不安装定时器。

以上恢复点与运行数据同盘，只防版本回退，不防磁盘故障；需要抗盘损时，把 `--backup-root` / `INFOOS_BACKUP_ROOT` 指向另一块本地磁盘。

`docker stop` 会先让各 wrapper 写入 drain 标记，并按 scheduler → 六个队列 worker、扫码检查 worker 与飞书 inbox → Chrome → OpenCLI → Web 的顺序停止；task worker 收到 TERM 后不再领取新批次，并让当前 `run_due_tasks` 批次返回。Compose 给 supervisor 90 秒子进程宽限期和 120 秒容器宽限期。只有日志中出现完整顺序的 `infoos-lifecycle ... action=stopped` 且同一 Profile 重启后的平台探针成功，才算优雅停止证据。

## MCP 客户端

agent 端点为 `https://infoos.example.com/mcp`，使用独立设备 Token 的 Bearer 鉴权。只读问答仅需 `intelligence:read`；定时知识整理需 `intelligence:read` + `evaluation:write`，先读画像、rubric 与知识卡 schema，再分诊、拆解与归并，回读知识卡证据。初始化画像另用交互式 `profile:write`；来源修改和商机调研状态修改分别需要交互式 `sources:write`、`verification:write`，并按用户决定执行。

完整工具清单与各 scope 见[安装指南](../../docs/INSTALL-for-agents.md#5-mcp-工具与权限)，Codex CLI 定时启动器、受限 Token 文件及 `agent.env` 配置见[外部 agent 手册](../../docs/engineering/opportunity-agent-runbook.md)。旧商机拆解、核实与跟进工具仍注册，保留兼容，不作为新知识卡主链。`feedback:write` 允许创建 Token，但当前无对应 MCP feedback 工具。未设置 `INFOOS_PUBLIC_BASE_URL` 时，返回的深链接是相对路径。

### 公网全站边缘鉴权

通用配置起点见[边缘双因素模板](../edge/README.md)（包含 Nginx、Authelia 与验证说明；未替用户部署）。不用双因素网关时，产品端口只绑定 loopback 或防火墙限制的可信局域网，使用 HTTPS、强管理员密码/表单会话与最小 scope Token；不得将管理面或 Console 直接暴露公网。内部端口不发布，同源检查不能替代认证。

公网边缘 nginx 在 Authelia 双因素验证通过后**覆盖**请求头 `X-InfoOS-Edge-Auth`，不得透传客户端提供的同名头。用户在受保护环境生成随机密钥（至少 32 字符）；只存于边缘 nginx 的受限配置及容器的 `INFOOS_EDGE_READER_SECRET` 环境变量，不写入仓库、命令行参数或日志。边缘全站代理管理面、浏览器控制台、阅读面与附件，拒绝 `/api/internal/` 与 `/_infoos_` 前缀；只有 `/mcp`、`/api/plugin/v1/` 与 `/api/capture/v1/` 按模板绕过 Authelia，保留各自 Bearer 鉴权，本地登录路径及其他路径仍经过双因素。产品端口只绑定 loopback 或可信局域网，不发布 CDP/VNC/debug 端口。

在 appliance 宿主准备 `/etc/infoos/edge.env`，属主为 root 或执行用户，权限必须为 `0600`，使用逐行、无引号的 `KEY=VALUE` 格式：只允许 `INFOOS_PUBLIC_BASE_URL`、`INFOOS_EDGE_READER_SECRET`（空行和 `#` 注释可用，不做 shell 展开）。公网地址设为 `https://infoos.example.com`；密钥在安全编辑器中填写。该运维文件只用于向容器传递配置，不是第三份长期密钥真源，操作结束后按运维策略移除。

```bash
python3 deploy/docker/deploy-existing.py --environment test \
  --image "$APPROVED_IMAGE" --revision "$APPROVED_REVISION" \
  --update-kind code-only --env-override-file /etc/infoos/edge.env
# 核对预览后，使用相同参数加 --apply 执行。
```

预览只列本次覆盖键，公网 URL 可以显示，密钥显示 `<redacted>`。未指定的键继续继承旧容器环境；密钥不会进入 argv。密钥为空、未设置或少于 32 字符时，边缘鉴权关闭，局域网表单会话仍可使用。

轮换采用单密钥：生成新密钥，先用上述文件更新容器并核对健康状态，再更新边缘受限配置、检查 nginx 配置并 reload。也可先更新边缘再更新容器；两端不同步期间公网管理面与阅读面会鉴权失败并跳转到容器登录页，局域网表单登录不受影响。因此应在维护窗口连续完成两步；若失败，把已更新的一端恢复旧值。两端一致后验证 Authelia 双因素登录可直接打开管理面、浏览器控制台、阅读页、卡片附件与日报，并验证局域网表单登录。

边缘全站代理须支持所有管理操作的方法，`client_max_body_size 64m`；控制台 `/browser-console/websockify` 透传 WebSocket Upgrade，读取超时 31m。仅剔除 Authelia 自身的会话 cookie，其余 Cookie 和上游 `Set-Cookie` 透传，以保留控制台 grant 与局域网会话。Authelia 全站规则使用 `two_factor`（所有方法），`default_policy` 保持 deny。边缘改动前备份，执行 `nginx -t` 与 `validate-config` 后再应用；仓库改动不代表边缘已更新。

容器 nginx 为带 hash 的前端资源设置一年缓存，为卡片附件设置七天私有缓存；其余响应沿用上游缓存策略或 `no-cache`。nginx 层无 gzip 模块；压缩由应用层中间件完成，仅覆盖 JSON、JS、CSS、HTML、纯文本和 SVG，不含 `/mcp`、Range 请求与二进制附件。

既有阅读面白名单保留（除明确列出的唯一 PUT 外，只放行 GET/HEAD），全站管理代理上线后作为冗余路径：

- `/r/`：当前页面 `/r/card/:id`、`/r/brief/:date`、`/r/browse`、`/r/knowledge`、`/r/knowledge/:id`、`/r/opportunities`、`/r/opportunities/stats`。
- `/assets/`：构建后的 JS、CSS 等前端静态资源。
- 裸 `/api/card-preview` 列表、`/api/card-preview/facets` 筛选项、`/api/card-preview/{card_id}`、`/api/card-preview/assets/{asset_id}`。边缘路径匹配须同时覆盖裸路径与斜杠后缀，例如 `api/card-preview(?:/|$)`，不能只匹配 `api/card-preview/`。这些读取仍经过原有 Authelia 和容器阅读鉴权。
- `/api/opportunities/board`、`/api/opportunities/stats`、`/api/opportunities/{card_id}`：GET/HEAD。唯一额外放行的写路径为 `PUT /api/opportunities/{card_id}/follow-up`；同样做阅读鉴权，并由应用全局同源变更检查保护，其他路径的 PUT/DELETE 仍拒绝。
- `/api/intelligence/daily-brief/{brief_date}`；不包括 `/rebuild`。知识卡列表 `/api/intelligence/knowledge` 与单卡 `/api/intelligence/knowledge/{knowledge_id}`。
- `/favicon.ico`：浏览器自动请求，鉴权后容器返回空 204；使用同一阅读鉴权，边缘同样限制 GET/HEAD。

全站代理与 Authelia 全站双因素规则由边缘运维单独配置，容器 nginx 的改动不能替代这两层。既有阅读面路径与方法检查保持原样；新增管理操作经管理鉴权和同源变更检查。新增 v0017 需使用 `data-migration`。

当前 HTML 没有 manifest；漫步页会请求卡片列表和筛选项，不请求 capture 预览、管理状态或 setup。阅读鉴权允许 GET/HEAD 不代表每个业务 API 都实现 HEAD；当前列表数据接口使用 GET。`/mcp` 使用独立 Bearer scope 鉴权，不使用边缘密钥。容器 `/_infoos_reader_auth`、`/_infoos_management_auth` 只供 nginx 内部子请求使用，`/api/internal/` 在 nginx 直接返回 404。管理鉴权接受边缘密钥或表单会话，保留基础镜像的 `/login/` 与登录/退出服务。原始边缘密钥只进入鉴权子请求，其他 FastAPI 代理清除该头；nginx 覆盖 `X-InfoOS-Edge-Authenticated`，仅把已验证的边缘结果传给账号与控制台入口。控制台仍要求限时 grant 和同源 Origin/Referer，账号修改仍校验当前用户名与密码。阅读面的原始方法与 URI 在 FastAPI 再次校验；不合规请求即使携带正确密钥也无法放行。MCP 深链接和日报读取投影按 `INFOOS_PUBLIC_BASE_URL` 生成绝对地址；冻结日报内容不因域名变更而重写。

## 飞书 CLI 与登录态

镜像内 CLI 的版本与安装位置以 Dockerfile 为准，Web 引导用户扫码并绑定接收人。state 卷的 `lark-cli/` 保存登录态，升级和重建时必须保留；删除目录会断开连接。

凭据目录不进入数据导出或异地数据库同步，不写日志或 HTTP 响应。data-migration 的本机完整快照包含 state，按 Chrome profile 同等保护，不等于离机备份包含登录态。恢复后分别核对连接、真实送达与凭据持久化。
