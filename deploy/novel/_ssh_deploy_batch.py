"""Deploy batch: editor sample, scene write hints, living gates already on box."""
from __future__ import annotations
from pathlib import Path
from _ssh import Remote

r = Remote()
FILES = [
    (r"C:\regent\core\src\regent\novel\application\editor_audit.py",
     "/opt/regent/core/src/regent/novel/application/editor_audit.py"),
    (r"C:\regent\core\src\regent\novel\application\direction.py",
     "/opt/regent/core/src/regent/novel/application/direction.py"),
    (r"C:\regent\core\src\regent\novel\domain\scene_card.py",
     "/opt/regent/core/src/regent/novel/domain/scene_card.py"),
    (r"C:\regent\core\src\regent\novel\domain\prose_front_gates.py",
     "/opt/regent/core/src/regent/novel/domain/prose_front_gates.py"),
    (r"C:\regent\core\src\regent\novel\domain\dossiers.py",
     "/opt/regent/core/src/regent/novel/domain/dossiers.py"),
    (r"C:\regent\core\src\regent\novel\domain\world_bible.py",
     "/opt/regent/core/src/regent/novel/domain/world_bible.py"),
]
for src, dst in FILES:
    r.put(str(src), dst)
    print("put", Path(dst).name, flush=True)
for c in ["regent-api", "regent-worker", "regent-worker-2", "regent-worker-3"]:
    for _, dst in FILES:
        name = Path(dst).name
        parent = str(Path(dst).parent).replace("\\", "/").replace("/opt/regent", "/app")
        r.run(f"docker exec {c} mkdir -p {parent}", timeout=15)
        r.run(f"docker cp {dst} {c}:{parent}/{name}", timeout=30)
print(r.run("docker restart regent-api regent-worker regent-worker-2 regent-worker-3", timeout=120))
print(r.run(
    'docker exec regent-worker python -c "from regent.novel.application.editor_audit import should_run_editor_audit; print(should_run_editor_audit(chapter_no=1, mode=\\"sample\\"), should_run_editor_audit(chapter_no=2, mode=\\"sample\\"))"',
    timeout=40,
))
