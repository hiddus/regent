import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote

r = Remote()
print("models:")
print(r.run("docker exec regent-api printenv REGENT_MODEL_NAME REGENT_MODEL_NAME_2", timeout=20).out)
r.write_text(
    "/tmp/_chk_max.py",
    "from regent.novel.application.direction import MAX_CALLS\nprint(MAX_CALLS)\n",
)
r.run("docker cp /tmp/_chk_max.py regent-api:/tmp/_chk_max.py", timeout=15)
print("MAX_CALLS:", r.run("docker exec regent-api python /tmp/_chk_max.py", timeout=30).out)
print("health:", r.run("curl -sf http://localhost:8000/health | head -c 120", timeout=20).out)
