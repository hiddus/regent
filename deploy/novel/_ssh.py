"""Minimal paramiko helper for remote deploy/verification.

Usage as library:
    from _ssh import Remote
    r = Remote()
    print(r.run("hostname").out)

Usage as CLI:
    python _ssh.py "cmd1" "cmd2" ...
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field

def _load_dotenv() -> None:
    """Load repo-root .env without third-party deps (values needed for SSH)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

HOST = os.environ.get("SERVER_IP", "118.31.171.159")
USER = os.environ.get("LOGIN_USER", "root")
PASSWORD = os.environ.get("LOGIN_PASSWORD", "")


@dataclass
class Result:
    cmd: str
    out: str
    err: str
    code: int

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out or "").strip()

    def __str__(self) -> str:
        head = f"$ {self.cmd}\n[exit {self.code}]"
        body = self.out.strip()
        err = self.err.strip()
        if body:
            head += "\n" + body
        if err:
            head += "\n[stderr] " + err
        return head


class Remote:
    def __init__(self, host: str = HOST, user: str = USER, password: str | None = None):
        import paramiko  # local import: only deploy tooling needs it

        self.host, self.user = host, user
        self.password = password or PASSWORD
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            host,
            username=user,
            password=self.password,
            timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )

    # -- command ---------------------------------------------------------
    def run(self, cmd: str, timeout: int = 300, check: bool = False) -> Result:
        _, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        res = Result(
            cmd=cmd,
            out=stdout.read().decode("utf-8", errors="replace"),
            err=stderr.read().decode("utf-8", errors="replace"),
            code=code,
        )
        if check and not res.ok:
            raise RuntimeError(str(res))
        return res

    def run_many(self, cmds: list[str], timeout: int = 300) -> list[Result]:
        return [self.run(c, timeout=timeout) for c in cmds]

    # -- files -----------------------------------------------------------
    def put(self, local: str, remote: str) -> None:
        sftp = self._client.open_sftp()
        try:
            sftp.put(local, remote)
        finally:
            sftp.close()

    def put_tree(self, local_dir: str, remote_dir: str, exclude: tuple[str, ...] = ()) -> int:
        """Recursively upload a directory. Returns number of files uploaded."""
        sftp = self._client.open_sftp()
        count = 0
        try:
            for root, dirs, files in os.walk(local_dir):
                dirs[:] = [d for d in dirs if d not in exclude]
                rel = os.path.relpath(root, local_dir).replace("\\", "/")
                target_dir = remote_dir if rel == "." else f"{remote_dir}/{rel}"
                self._mkdir_p(sftp, target_dir)
                for name in files:
                    if name in exclude:
                        continue
                    sftp.put(os.path.join(root, name), f"{target_dir}/{name}")
                    count += 1
        finally:
            sftp.close()
        return count

    @staticmethod
    def _mkdir_p(sftp, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        cur = ""
        for part in parts:
            cur += "/" + part
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)

    def write_text(self, remote_path: str, content: str, mode: int | None = None) -> None:
        sftp = self._client.open_sftp()
        try:
            with sftp.open(remote_path, "w") as fh:
                fh.write(content.encode("utf-8"))
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

    # -- misc ------------------------------------------------------------
    def exists(self, path: str) -> bool:
        sftp = self._client.open_sftp()
        try:
            sftp.stat(path)
            return True
        except OSError:
            return False
        finally:
            sftp.close()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Remote":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


if __name__ == "__main__":
    with Remote() as r:
        for c in sys.argv[1:]:
            print(r.run(c))
