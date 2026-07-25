from regent.infrastructure.evidence_capability import (
    CAPABILITY_NAME,
    load_allowlisted_http_capability_package,
)


def test_allowlisted_http_capability_package_is_pool_owned() -> None:
    package = load_allowlisted_http_capability_package()
    assert package.name == CAPABILITY_NAME
    assert package.status == "VERIFIED"
    assert package.default_feeds
    assert all(url.startswith("https://") for url in package.default_feeds)
    assert "Core" not in package.description or "Not a Core" in package.description
