"""Verify F1/F2/F3 landed on remote containers."""
from __future__ import annotations

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402

CODE = r"""
from regent.novel.domain.scene_requirements import REQUIREMENTS_VERSION, freeze_scene_requirements
from regent.novel.domain import prose_patch as p
from regent.novel.application import direction as d
print('version', REQUIREMENTS_VERSION)
reqs = freeze_scene_requirements([
    {'reader_visible': True, 'statement': 'a', 'state_changes': {'door': 'closed'}},
])
report = d.SceneValidation(passed=True, facts=[d.VerifiedFact(statement='rain', quote='rain')])
defects = d._validation_report_defects(report, 'rain', reqs, protocol_version=REQUIREMENTS_VERSION)
print('empty_defects', bool(defects), defects[:1])
b = 'A\n\nKEEP\n\nC\n\nTAIL'
h = p.content_hash(b)
try:
    p.apply_patch(base_text=b, base_hash=h, paragraphs=p.split_paragraphs(b), patch_hash=h,
                  replacements=[{'paragraph_ids': ['p0000', 'p0002'], 'text': 'NEW'}])
    print('noncontig', 'ALLOWED')
except Exception as e:
    print('noncontig', type(e).__name__, str(e)[:100])
"""


def main() -> int:
    r = Remote()
    r.write_text("/tmp/_r21_verify_remote.py", CODE)
    r.run("docker cp /tmp/_r21_verify_remote.py regent-api:/tmp/_r21_verify_remote.py", timeout=15)
    out = r.run("docker exec regent-api python /tmp/_r21_verify_remote.py", timeout=30).out
    print(out)
    return 0 if "r21_v2" in out and "ALLOWED" not in out else 1


if __name__ == "__main__":
    raise SystemExit(main())
