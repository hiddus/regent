# deploy/squid

Squid 出口代理（Egress Proxy）配置，约束 Worker 与能力对外的 HTTP 出口，实现 fail-closed。

## 目录内容

文件：
- `squid.conf`：生产上 `regent-egress` 挂载的就是这份，宿主机位置 `/opt/regent/egress/squid.conf`。改完用 `python ops/ensure_console_egress.py` 写到服务器并重建容器。

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
