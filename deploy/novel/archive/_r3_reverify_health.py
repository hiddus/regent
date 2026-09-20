"""Quick remote health + model + sample call for re-verify."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

r = Remote()
print("=== env ===")
print(r.run("docker exec regent-api printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2", timeout=20).out)
r.write_text("/tmp/_chk.py", "from regent.novel.application.direction import MAX_CALLS\nprint('MAX_CALLS', MAX_CALLS)\n")
r.run("docker cp /tmp/_chk.py regent-api:/tmp/_chk.py", timeout=15)
print(r.run("docker exec regent-api python /tmp/_chk.py", timeout=30).out)
print("=== health ===")
print(r.run("curl -sf --max-time 10 http://localhost:8000/health || echo HEALTH_FAIL", timeout=20).out[:400])
print("=== workers ===")
print(r.run("docker ps --format '{{.Names}} {{.Status}}' | grep regent", timeout=20).out)
print("=== model ping ===")
probe = r'''
import json,os,urllib.request
base=os.environ["REGENT_MODEL_BASE_URL"].rstrip("/")
key=os.environ["REGENT_MODEL_API_KEY"]
model=os.environ["REGENT_MODEL_NAME"]
body={"model":model,"messages":[{"role":"user","content":"Reply with exactly: ok"}],"max_tokens":16,"temperature":0,"thinking":{"type":"disabled"}}
req=urllib.request.Request(base+"/chat/completions",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
with urllib.request.urlopen(req,timeout=60) as resp:
    data=json.loads(resp.read().decode())
msg=((data.get("choices") or [{}])[0].get("message") or {})
print(json.dumps({"content":msg.get("content"),"usage":data.get("usage")},ensure_ascii=False))
'''
r.write_text("/tmp/_ping.py", probe)
r.run("docker cp /tmp/_ping.py regent-api:/tmp/_ping.py", timeout=15)
print(r.run("docker exec regent-api python /tmp/_ping.py", timeout=90).out)
