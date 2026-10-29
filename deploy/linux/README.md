# InfoOS 原生 systemd 遗留辅助面

状态：遗留维护资料，不是当前采集运行真相，也不是最终交付入口。

Docker Appliance 是当前交付形态；原生 systemd 只供已有安装的迁移或遗留维护，不代表用户主机一定存在这些服务。

连接使用用户自己的 SSH config。操作前核对目标环境、容器 image/config/volume、目标 SQLite 与 systemd 单元；不要把遗留 Web 的 healthz 当成采集链验收。

## 保留边界

- `/etc/infoos/infoos.env` 是遗留服务的环境注入入口，不得把服务器路径硬编码进业务层；
- `/opt/infoos/app` 是服务器 checkout，不天然等同于运行容器内容；
- `/var/lib/infoos/state` 与 `/var/lib/infoos/data` 可能被不同运行面引用，操作前必须核对实际 mount/环境；
- 原生 Web 默认只监听 loopback，不公开 SQLite、CDP、OpenCLI、VNC/noVNC 或 debug 端口；
- systemd 端不得重新启动原生 worker/scheduler 或操作宿主 Chrome，除非用户明确授权回滚到该运行面；
- 不删除 `/var/lib/infoos`、Chrome profile、登录态、SQLite、WAL/SHM 或 assets。

## 允许的维护

本目录只用于：

- 查看遗留 Web unit、环境、日志和健康；
- 保留迁移期 install/unit/host-monitor 的历史可审计实现；
- 在明确回滚方案中恢复已知版本，但必须先冻结 Docker 写入者、核对数据所有权并获得用户授权；
- Docker Appliance 正式生产切换关闭后归档原生交付面。

不再把以下内容作为常规操作：

- 安装或升级原生 worker/scheduler；
- 在宿主启动 Chrome/OpenCLI 采集；
- 用 systemd 任务消费验证新功能；
- 把原生 CUDA/ASR 结果当作容器 GPU 证据。

## 只读排障

通过自己配置的 SSH alias 连接，不把明文凭据写入命令、文档或日志：

```bash
ssh linux-host 'systemctl status infoos-web --no-pager'
ssh linux-host 'journalctl -u infoos-web -n 200 --no-pager'
ssh linux-host 'systemctl is-enabled infoos-worker infoos-scheduler.timer'
```

预期边界是 Web 辅助面可以保留，而 worker/scheduler 停止。若状态偏离，不要立即重启；先确认 Docker Appliance 是否正在写同一数据目录，避免双 scheduler、双消费者或两个运行面同时持有 Chrome profile。

主机级取证工具若已安装，可只读使用：

```bash
infoos-host-monitor status
infoos-host-monitor incidents
infoos-host-monitor collect --reason manual-check
```

它用于保留主机 boot、journal、PSI、OOM、温度、磁盘和容器退出证据，不属于 InfoOS application/domain，也不能替代容器应用验收。

## 回滚边界

回滚到原生 systemd 是会改变运行权威的重大操作，不能从“排障”中推断授权。必须事先明确：

1. Docker Appliance 已停止所有写入者并记录 image/config/volume；
2. SQLite、WAL/SHM、assets、state 与 Chrome profile 已完成一致备份；
3. 原生 checkout、venv、frontend build、环境与运行用户是已知可用版本；
4. 只有一个 scheduler、一个对应队列消费者集合和一个 Browser Runtime owner；
5. 用户明确批准回滚与恢复窗口。

正式交付合同与未关闭事项见 [`../../docs/engineering/docker-appliance-target.md`](../../docs/engineering/docker-appliance-target.md)，当前远端操作路径见 [`../../docs/engineering/local-env-playbook.md`](../../docs/engineering/local-env-playbook.md)。
