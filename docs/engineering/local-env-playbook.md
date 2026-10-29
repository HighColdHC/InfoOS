# InfoOS 开发与隔离测试

产品边界见 [产品合同](../product/infoos-intelligence-workbench-target.md)，部署与恢复见 [Docker 手册](../../deploy/docker/README.md)。源码 checkout、镜像、运行容器与 SQLite 是不同证据，不能互相替代。

## 开发环境

Linux amd64 是运行目标；macOS 可编辑、检查与控制远端开发机。依赖安装：

```bash
make install
npm --prefix frontend install
```

`scripts/run-infoos-test-env.sh` 仅供已准备好本地 fixture 的开发环境使用：state 固定为 `$HOME/.infoos-test`，data 为其 `data/`，Chrome profile 为其 `chrome-profile/`。空库启动依赖 `INFOOS_TEST_SEED_DATA_HOME` 指向用户自备、已脱敏的测试数据目录（内含 `infoos.sqlite3`），默认历史快照不随公开源码提供；另需 Google Chrome、OpenCLI 与绑定扩展。不得从生产库复制私有数据来补齐公开验收。准备完成后才运行 `bash scripts/run-infoos-test-env.sh`；它会启动 Web、前端、scheduler 与 worker，不是只读检查，也不支持任意临时根目录。首次产品安装改用 [安装指南](../INSTALL-for-agents.md) 的 Docker 空卷流程。

不要使用默认 `~/.infoos` 或个人 Chrome profile 验收。历史 LLM 配置只保留读取兼容，InfoOS 不发起 LLM 调用。

## 自备 Linux 远端测试机

公共测试入口使用用户自己配置的 SSH alias，无连接文件依赖。目标需具备 Python/venv、Node/npm、GNU 工具与 systemd，运行用户需有创建 transient scope 的权限（现有 runner 按 root 设计）。不要指向生产机或有共享业务数据的主机。

```bash
INFOOS_TEST_SSH_TARGET=linux-dev \
  bash scripts/infoos-remote-test --tree /path/to/source --python -- tests/infoos/<相关测试>.py -q
INFOOS_TEST_SSH_TARGET=linux-dev bash scripts/infoos-remote-test --frontend
```

连接、端口和密钥交给本机 SSH config / agent；脚本不读取凭据文件。也可用 `INFOOS_REMOTE_TEST_TRANSPORT=/absolute/transport` 指定支持 `command`、`copy-to` 的自有 shell 入口（由 bash 调用），由该入口自行管理连接。`--tree` 指定实际打包目录，包含未提交、未忽略文件，排除 `.env*`；运行前检查忽略规则，不放入运行数据或凭据。

远端源码、依赖缓存与 state/data 位于 `/var/tmp/infoos-devtest`，runner 限制内存、超时与进程生命周期。它不访问运行 Appliance 卷；测试通过也不证明实例采集、GPU 或真实浏览器验收。目标的网络代理由目标环境自行配置，公共入口不内置维护者代理地址。

## 实例识别与升级

用自己的连接方式在目标主机核对环境、容器 health、image/revision、mount 与 SQLite 路径；诊断或变更前建立基线。原生 systemd 辅助面的健康不代表容器采集链。

- data 与 state 由环境变量注入，SQLite 位于本地文件系统；不要硬编码宿主路径到产品层。
- 修改配置先确认消费进程、备份与重启方式，再验证实际读取结果。
- 登录与人工修复使用受认证 Browser Console；不开放原始控制端口，不使用 root Chrome 或 `--no-sandbox`。
- 只读诊断按问题选取任务链、终态/恢复分布、浏览器被动状态与资源证据，不以历史错误数量推断当前故障。
- 版本更新用完整 Dockerfile，核对源码 revision、OCI 镜像 label 和容器 `/opt/infoos/source-revision`。无 schema/持久语义变化用 code-only，否则做 data-migration 与备份副本预演；真实链路与恢复演练分别验收。

多会话协作时，每个分支交付 revision 与验证证据，由单一集成者合并；合并内容变化后验证受影响结果。部署打包到验收期间冻结待部署版本，不能把后续提交算进已部署 revision。
