import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

r = Remote()
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -F, -c"
WORK = "50177b33-bb97-455d-b9ed-48a9c9ddd5ec"

sql = (
    "SELECT id::text, state, coalesce(current_step,''), work_id::text, created_at::text "
    "FROM novel_chapter_runs ORDER BY created_at DESC LIMIT 6"
)
print(r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out)

sql2 = (
    f"SELECT id::text, state, coalesce(current_step,'') "
    f"FROM novel_chapter_runs WHERE work_id = '{WORK}'"
)
row = r.run(f"{PSQL} {shlex.quote(sql2)}", timeout=60).out.strip()
print("RUN4", row)
if not row:
    sys.exit(1)
rid = row.split(",")[0]
raw = r.run(
    f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{rid}{chr(39)}')}",
    timeout=180,
).out.strip()
ctx = json.loads(raw)
prod = ctx.get("production") or {}
print("phase", prod.get("phase"), "accepted", prod.get("accepted"), "calls", prod.get("call_count"))
for i, take in enumerate(prod.get("takes") or []):
    vers = take.get("prose_versions") or []
    print(
        f"take[{i}] rev={take.get('revisions')} patch={bool(take.get('last_patch'))} "
        f"err={take.get('patch_error')!r} lens={[len(v) for v in vers]} "
        f"val={(take.get('validation') or {}).get('passed')}"
    )
    if take.get("last_patch"):
        print("  n_repl", len(take["last_patch"].get("replacements") or []))
