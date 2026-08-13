"""Preview dependency selection must honor requirements.txt runtime packages."""

from __future__ import annotations

from regent.infrastructure.runtime_preview import _runtime_packages


def test_runtime_packages_include_app_deps_not_just_flask() -> None:
    req = """
Flask==3.0.3
feedparser==6.0.11
requests==2.32.3
beautifulsoup4==4.12.3
pytest==8.0.0
"""
    pkgs = _runtime_packages(None, req)
    joined = "\n".join(pkgs).lower()
    assert "flask" in joined
    assert "feedparser" in joined
    assert "requests" in joined
    assert "beautifulsoup4" in joined
    assert "pytest" not in joined


def test_runtime_packages_fallback_flask_when_empty() -> None:
    assert _runtime_packages(None, "") == ["Flask>=3.0.0"]
