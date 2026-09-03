from pathlib import Path

from _ssh import Remote

with Remote() as remote:
    root = Path(__file__).resolve().parents[2]
    remote.put(
        str(root / "core" / "Dockerfile.novel-mvp"),
        "/opt/regent/core/Dockerfile.novel-mvp",
    )
