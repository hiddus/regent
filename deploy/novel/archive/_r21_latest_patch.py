import json
import shlex
import sys

sys.path.insert(0, r"C:/regent/deploy/novel")
from _ssh import Remote

r = Remote()
PSQL = "docker exec -i regent-postgres psql -U regent -d regent -t -A -F, -c"
sql = (
    "SELECT id::text, state, coalesce(current_step,''), work_id::text "
    "FROM novel_chapter_runs ORDER BY created_at DESC LIMIT 1"
)
row = r.run(f"{PSQL} {shlex.quote(sql)}", timeout=60).out.strip()
print("LATEST", row)
rid = row.split(",")[0]
raw = r.run(
    f"{PSQL} {shlex.quote(f'SELECT generation_context::text FROM novel_chapter_runs WHERE id = {chr(39)}{rid}{chr(39)}')}",
    timeout=180,
).out.strip()
prod = json.loads(raw)["production"]
print("phase", prod.get("phase"), "accepted", prod.get("accepted"), "calls", prod.get("call_count"))
patch_seen = False
for i, take in enumerate(prod.get("takes") or []):
    vers = take.get("prose_versions") or []
    has = bool(take.get("last_patch"))
    patch_seen = patch_seen or has
    print(
        f"take[{i}] rev={take.get('revisions')} patch={has} err={take.get('patch_error')!r} "
        f"lens={[len(v) for v in vers]}"
    )
for d in prod.get("decisions") or []:
    if d.get("phase") == "WATCH_PROSE":
        print("prose", d.get("action"), "rmode", d.get("revision_mode"), "take", d.get("take_no"))
print("PATCH_SEEN", patch_seen)
