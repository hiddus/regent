"""Start a durable multi-agent app project on S0 from product intent.

No preset verification thresholds — start, then let Regent run/evolve.
Ops only unsticks soft-pause / infra failures later.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import paramiko
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    (k.lstrip("\ufeff") if isinstance(k, str) else k): v
    for k, v in dotenv_values(ROOT / ".env").items()
}
HOST = CFG.get("SERVER_IP") or "118.31.171.159"
PASSWORD = CFG.get("LOGIN_PASSWORD") or ""
OUT = ROOT / "docs" / "durable-multiagent-run-2026-08-13.json"

IDEA = """
围绕经营目标持续工作：做一个「互联网产品增长经营体检台」Web 应用（Flask 可 Preview）。

产品定位（对齐 Regent 滩头市场：互联网产品增长经营）：
- 面向增长/产品负责人，输入产品名、主指标（激活/留存/转化之一）、基线与观察窗口。
- 输出经营现状摘要、机会假设、低风险实验建议、周报骨架；应用可迭代加深。
- 多 Agent 按需要自行组织（产品/增长/数据/工程可分化），不要固定人数拓扑。
- 探索默认开放：可研究、原型、自检、修订；不可逆外部动作不做。
- 边界：无支付、无大规模 PII、无生产写权限；公开可逆 Preview 即可。

运作原则（强制）：
- 边跑边修边进化，不要为凑数字门槛堆假数据。
- 不要停在大纲页；做出可交互的最小可用体检台后继续按真实自检加深。
- 卡住时说明原因；能自愈就自愈，不能则 ASK_HUMAN。
""".strip()

CREATE = f"""
import json, urllib.request, urllib.error
body = json.dumps({{
  "idea": {json.dumps(IDEA, ensure_ascii=False)},
  "actor": "regent-ops:durable-multiagent-2026-08-13",
}}).encode()
req = urllib.request.Request(
  "http://127.0.0.1:8000/v1/app-projects/drafts",
  data=body,
  headers={{"Content-Type": "application/json"}},
  method="POST",
)
try:
  with urllib.request.urlopen(req, timeout=600) as resp:
    print(json.dumps({{"http": resp.status, "body": json.loads(resp.read().decode())}}, ensure_ascii=False, default=str))
except urllib.error.HTTPError as exc:
  print(json.dumps({{"http": exc.code, "error": exc.read().decode()[:2000]}}, ensure_ascii=False))
"""


def main() -> int:
    if not PASSWORD:
        raise SystemExit("LOGIN_PASSWORD missing")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        HOST,
        username=CFG.get("LOGIN_USER") or "root",
        password=PASSWORD,
        timeout=40,
        banner_timeout=120,
        auth_timeout=60,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        remote = "/tmp/_start_durable_multiagent.py"
        sftp = ssh.open_sftp()
        with sftp.file(remote, "w") as f:
            f.write(CREATE)
        sftp.close()
        _, out, err = ssh.exec_command(
            f"docker cp {remote} regent-api:{remote} && "
            f"docker exec -w /tmp -e PYTHONIOENCODING=utf-8 regent-api python {remote}",
            timeout=700,
        )
        text = (out.read() + err.read()).decode("utf-8", "replace")
        print(text)
        try:
            payload = json.loads(text.strip().splitlines()[-1])
        except Exception:
            payload = {"raw": text[-4000:]}
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", OUT)
        return 0 if isinstance(payload, dict) and payload.get("http") in (200, 201) else 1
    finally:
        ssh.close()


if __name__ == "__main__":
    raise SystemExit(main())
