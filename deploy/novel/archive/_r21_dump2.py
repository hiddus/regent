import json, shlex, sys
sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -c"
r = Remote()
sql = "SELECT id::text || '|' || status || '|' || work_id::text || '|' || created_at::text FROM novel_chapter_runs ORDER BY created_at DESC LIMIT 5"
print(r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out)
rid = r.run(
    f"{PSQL} {shlex.quote('SELECT id::text FROM novel_chapter_runs ORDER BY created_at DESC LIMIT 1')}",
    timeout=60,
).out.strip()
print("RID", rid)
raw = r.run(
    f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{rid}{chr(39)}')}",
    timeout=180,
).out.strip()
ctx = json.loads(raw)
prod = ctx.get("production") or {}
take = (prod.get("takes") or [{}])[-1]
print("phase", prod.get("phase"), "revisions", take.get("revisions"))
print("versions", [len(x) for x in (take.get("prose_versions") or [])])
print("patch_error", take.get("patch_error"))
print("reqs", len(take.get("requirements") or []))
val = take.get("validation")
if val:
    print("validation.passed", val.get("passed"), "issues", val.get("issues"))
    print("report_invalid", val.get("report_invalid"))
for d in (prod.get("decisions") or [])[-8:]:
    print("dec", d.get("phase"), d.get("action"), (d.get("evidence") or [""])[0][:50])
