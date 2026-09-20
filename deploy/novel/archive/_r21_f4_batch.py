"""F4 batch runner: chapter1 + evidence dump into artifacts."""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "artifacts")


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    limit = sys.argv[2] if len(sys.argv) > 2 else "2400"
    out_path = os.path.join(ART, f"r21_f4_ch1_{label}.txt")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    with open(out_path, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [PY, "-u", os.path.join(HERE, "run_chapter1.py"), limit],
            cwd=ROOT,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        code = proc.wait()
    text = open(out_path, encoding="utf-8", errors="replace").read()
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", text)
    work_id = m.group(1) if m else ""
    print(f"[{label}] exit={code} work={work_id} log={out_path}")
    if work_id:
        ev = subprocess.run(
            [PY, "-u", os.path.join(HERE, "_r21_f4_evidence.py"), work_id],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        ev_path = os.path.join(ART, f"r21_f4_evidence_{label}.json")
        open(ev_path, "w", encoding="utf-8").write(ev.stdout)
        print(ev.stdout)
        print(f"[{label}] evidence -> {ev_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
