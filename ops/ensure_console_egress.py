"""恢复并核对 regent-console（:3000 静态控制台）与 regent-egress（squid 出口代理）。

两个容器都靠宿主机上的单个配置文件 bind mount 起来。配置文件不在时 docker 会在原路径
自动建一个同名空目录，此后容器每次启动都报 "not a directory"，重启策略救不回来。
本脚本把仓库里的配置写到宿主机的持久位置（不放 /tmp），按原参数重建容器，再核对：
控制台首页与 /v1/ 反代、出口代理放行白名单域名并拒绝其他域名。可重复执行。

用法：python ops/ensure_console_egress.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "novel"))
from _ssh import Remote  # noqa: E402

CONSOLE_DIST = "/opt/regent/console-dist"
CONSOLE_CONF = "/opt/regent/console-nginx/nginx.conf"
EGRESS_CONF = "/opt/regent/egress/squid.conf"

CONSOLE_RUN = (
    "docker run -d --name regent-console --network regent-net --restart unless-stopped "
    f"-p 3000:80 -v {CONSOLE_DIST}:/usr/share/nginx/html:ro "
    f"-v {CONSOLE_CONF}:/etc/nginx/conf.d/default.conf:ro nginx:1.27-alpine"
)
EGRESS_RUN = (
    "docker run -d --name regent-egress --network regent-net --restart unless-stopped "
    f"-v {EGRESS_CONF}:/etc/squid/squid.conf:ro sameersbn/squid:3.5.27-2"
)

# 在 worker 里经代理各取一次：白名单域名应放行，其他域名应被 squid 拒绝（403）
PROBE = """
import urllib.request, urllib.error
opener = urllib.request.build_opener(urllib.request.ProxyHandler(
    {"http": "http://regent-egress:3128", "https": "http://regent-egress:3128"}))
for url in ("https://pypi.org/simple/pip/", "http://example.com/"):
    try:
        code = opener.open(url, timeout=20).status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:
        code = type(exc).__name__ + ":" + str(exc)[:80]
    print(url, code)
"""


def write_file(r: Remote, remote: str, local: Path) -> None:
    parent = remote.rsplit("/", 1)[0]
    # 原路径若被 docker 建成了空目录，先删掉才能写文件
    r.run(f"mkdir -p {parent} && if [ -d {remote} ]; then rmdir {remote}; fi", check=True)
    r.write_text(remote, local.read_text(encoding="utf-8"))


def wait_running(r: Remote, name: str) -> bool:
    for _ in range(10):
        time.sleep(2)
        if r.run(f"docker inspect {name} --format '{{{{.State.Running}}}}'").text.strip() == "true":
            return True
    return False


def main() -> int:
    with Remote() as r:
        if r.run(f"test -f {CONSOLE_DIST}/index.html").code != 0:
            print(f"[FAIL] 宿主机没有控制台前端 {CONSOLE_DIST}，先跑 ops/deploy_console.py")
            return 1
        write_file(r, CONSOLE_CONF, ROOT / "apps" / "regent-console" / "nginx.conf")
        write_file(r, EGRESS_CONF, ROOT / "deploy" / "squid" / "squid.conf")
        print("配置已写入", CONSOLE_CONF, EGRESS_CONF)

        for name, command in (("regent-console", CONSOLE_RUN), ("regent-egress", EGRESS_RUN)):
            r.run(f"docker rm -f {name} >/dev/null 2>&1 || true")
            r.run(command, check=True)
            if not wait_running(r, name):
                print(f"[FAIL] {name} 没起来：", r.run(f"docker logs --tail 20 {name} 2>&1").text)
                return 1
            print(f"{name} 运行中")

        bad = 0
        home = r.run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/").text.strip()
        api = r.run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/health/live").text.strip()
        print(f"控制台首页 {home}，经控制台反代到 API 的健康检查 {api}")
        bad += home != "200" or api != "200"

        r.write_text("/tmp/egress_probe.py", PROBE)
        r.run("docker cp /tmp/egress_probe.py regent-worker:/tmp/egress_probe.py", check=True)
        time.sleep(3)
        probe = r.run("docker exec regent-worker python /tmp/egress_probe.py", timeout=90).text.strip()
        print(probe)
        lines = dict(line.rsplit(" ", 1) for line in probe.splitlines() if " " in line)
        allowed = lines.get("https://pypi.org/simple/pip/")
        denied = lines.get("http://example.com/")
        print(f"出口代理：白名单 pypi.org → {allowed}，非白名单 example.com → {denied}")
        bad += allowed != "200" or denied != "403"
    if bad:
        print("[FAIL] 核对未通过")
        return 1
    print("[OK] regent-console 与 regent-egress 已恢复")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
