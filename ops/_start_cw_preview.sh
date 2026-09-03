#!/bin/bash
set -e
DID=dd4a9401-a092-4a22-bc6f-8ba863bfd5e8
PORT=42395
for p in /var/lib/regent/workspaces/previews/runtime/$DID /opt/regent/workspaces/previews/runtime/$DID; do
  if [ -e "$p/.preview-venv/bin/python" ]; then WS=$p; break; fi
done
echo WS=$WS
if [ -z "$WS" ]; then exit 2; fi
cd "$WS"
# best-effort kill
if command -v fuser >/dev/null 2>&1; then fuser -k ${PORT}/tcp || true; fi
python3 -c "import socket; s=socket.socket();
import sys
try:
 s.bind(('0.0.0.0', $PORT)); s.close(); print('port_free')
except OSError as e:
 print('port_busy', e); sys.exit(0)" || true
export FLASK_APP=src.app:app
export FLASK_DEBUG=0
export PORT=$PORT
export REGENT_PREVIEW_PORT=$PORT
nohup .preview-venv/bin/python -m flask run --host 0.0.0.0 --port $PORT >> .regent-preview.log 2>&1 &
echo started_pid=$!
sleep 3
.preview-venv/bin/python -c "import urllib.request; print('probe', urllib.request.urlopen('http://127.0.0.1:$PORT/', timeout=5).status)"
