#!/bin/bash
set -e
RT="/var/lib/regent/workspaces/previews/runtime/dd4a9401-a092-4a22-bc6f-8ba863bfd5e8"
PORT="$(cat "$RT/.regent-preview-port")"
echo "PORT=$PORT"
fuser -k "${PORT}/tcp" 2>/dev/null || true
pkill -f "$RT" || true
sleep 1
rm -f "$RT/data/store.json"
test -x "$RT/.preview-venv/bin/python"
cd "$RT"
export FLASK_APP=src.app:app
nohup "$RT/.preview-venv/bin/python" -m flask run --host 0.0.0.0 --port "$PORT" \
  >"$RT/.regent-preview.log" 2>&1 &
echo "START_PID=$!"
sleep 2
ss -ltnp | grep ":${PORT} " || true
"$RT/.preview-venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/var/lib/regent/workspaces/previews/runtime/dd4a9401-a092-4a22-bc6f-8ba863bfd5e8")
from src.domain import ContentStore
from src.seed import build_seed
s = ContentStore(path=None)
build_seed(s)
print("seed", s.get_pairwise("US", "SG") is not None, sorted(s.pairwise_agents.keys())[:6])
from src.app import app
c = app.test_client()
print("html", c.get("/crosswalks/US-SG").status_code)
print("api", c.get("/api/crosswalks/US-SG").status_code)
PY
echo -n "direct:"
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/crosswalks/US-SG"
echo
echo -n "direct_api:"
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/crosswalks/US-SG"
echo
tail -20 "$RT/.regent-preview.log"
