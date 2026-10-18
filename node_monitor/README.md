# 镜像节点自动上报与监控

上报程序在每次镜像开机后读取当前实例的地址，向独立的腾讯云监控服务发送心跳。监控服务不使用生成业务数据库，也不启动模型。

## 地址与状态

- AutoDL 默认读取本次启动环境的 `AutoDLService6008URL`；这是当前工作台的应用入口。`6006` 和 `6008` 对应不同应用，不混用。实例标识优先使用本次 `AutoDLContainerUUID`，不使用镜像复制的 `machine-id`。
- 平台环境优先于镜像内残留的手动环境变量；从 SSH 启动且缺少平台变量时，只读取 PID 1 和平台 Supervisor 的相关变量，不收集平台 Token。
- 同时上报解析到的访问 IP、端口和局域网 IP，接收端记录真实连接来源 IP。无域名时以来源 IP 和固定端口显示备用地址；端口是否可直连仍取决于实例映射，AutoDL 的共享网关可能需要域名。
- 页面同时显示域名入口和完整 IP 备用地址，分别提供复制、打开按钮；优先使用上报的域名解析 IP，缺失时使用上报来源 IP，明确标注地址来源。IP 链接仅为候选入口，不能把心跳在线当作直连验证通过；共享网关的 HTTPS 证书和路由可能要求原域名。
- 默认每 15 秒上报，超过 60 秒未收到新心跳则离线，页面每 5 秒刷新。在线代表上报进程可达，服务就绪另取节点本地 `/api/status`。
- 网络故障按 5/10/20/40/60 秒退避重试，服务恢复后继续登记。启动会话与序列号防止旧请求把最新地址覆盖回去；接收端用自己的时间判断在线。

## 腾讯云监控服务

当前部署使用独立空闲端口 `18443`、`https://ee2x.cn:18443`，不修改既有 80/443/8443 网站。程序和虚拟环境位于 `/opt/minimax-node-monitor`，SQLite 位于 `/var/lib/minimax-node-monitor`。

`config.example.json` 是配置示例。实际配置含独立的随机 `agent_key` 和 `view_key`，只放远端私有配置，不提交 Git。上报接口为 `POST /api/heartbeat`，使用 Bearer 写入凭据。当前 `public_dashboard=true`，根地址 `/` 和 `/api/nodes` 直接提供只读监控，无 IP 白名单，不展示局域网 IP、启动会话或写入凭据。私有诊断链接 `/m/<view_key>/` 继续可用；如需只通过私有链接查看，可关闭 `public_dashboard`。访问日志关闭，页面链接不向节点地址发送 Referrer。

使用 `deploy/node-monitor/` 中的 systemd 单元启动。服务通过 `LoadCredential` 读取私有配置和现有有效证书，使用动态服务用户、独立状态目录。证书变更只重启监控服务，不重载既有网站。复用到其他服务器时先核对空闲端口、域名证书路径，调整单元中的路径。

常用命令：

```sh
systemctl status minimax-node-monitor.service
journalctl -u minimax-node-monitor.service -n 50
systemctl restart minimax-node-monitor.service
```

## AutoDL 镜像安装

把 `node_monitor/` 和私有 `config/node-reporter.json` 放进镜像。配置格式见 `reporter.example.json`，其中仅监控地址与上报凭据固定；不写入旧机器的应用域名、IP 或实例 ID。程序仅依赖 Python 标准库，Supervisor 使用镜像已有安装。

```sh
cd /root/minimax-node
/root/miniconda3/bin/python -m node_monitor.install_reporter \
  --root /root/minimax-node \
  --config /root/minimax-node/config/node-reporter.json \
  --supervisord /root/miniconda3/bin/supervisord \
  --supervisorctl /root/miniconda3/bin/supervisorctl
```

安装器创建独立的 `deployment/node-reporter-supervisord.ini` 和 `scripts/start_node_reporter.sh`，向官方 `/etc/autodl.sh` 开机钩子写入幂等管理块，保留原有命令。AutoDL 的 `customer.cmd.sh` 会在用户自定义开机命令之前运行此钩子；不用 `.bashrc` 冒充开机启动。独立守护进程不重启现有 GPU 或工作台服务。

保存镜像时保留以上源文件、配置、启动脚本和 `/etc/autodl.sh`。新机器启动后平台变量会自动更新，不需要回填旧域名。单实例文件锁阻止重复上报进程，镜像内旧 PID/锁文件不作为节点身份。

```sh
/root/miniconda3/bin/supervisorctl -c deployment/node-reporter-supervisord.ini status
bash /etc/autodl.sh
```

安装器对已有开机钩子保留单文件备份；重复安装不会添加重复启动块。需要回退时先停止独立上报 Supervisor，再移除自己的管理块或恢复备份，不改其他启动服务。

## 验证

```sh
python -m pytest tests/test_node_monitor.py -q
```

检查鉴权、地址变化与旧会话、无域名 IP 回退、在线/离线/恢复、持久化、当前实例环境优先级、开机钩子幂等和真实 HTTP 上报。正式部署另检查实际节点心跳、只停上报进程后的离线状态、官方钩子恢复和监控服务重启；不通过重启 GPU 或取消任务验证监控。
