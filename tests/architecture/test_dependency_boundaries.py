import ast
from pathlib import Path

FORBIDDEN_DOMAIN_IMPORTS = ("fastapi", "sqlalchemy", "regent.api", "regent.infrastructure")
DOMAIN_ROOT = Path("core/src/regent/domain")


def test_domain_does_not_import_frameworks_or_infrastructure() -> None:
    violations: list[str] = []
    for path in DOMAIN_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_DOMAIN_IMPORTS):
                    violations.append(f"{path}: {name}")
    assert violations == []
