import uuid

import pytest
from regent.domain.errors import DomainError
from regent.infrastructure.static_app_publisher import StaticAppPublisher


def valid_files() -> dict[str, str]:
    return {
        "index.html": (
            "<html><head><link rel='stylesheet' href='./styles.css'></head><body>"
            "<main><button data-regent-event='activate'>Start</button></main>"
            "<script src='./app.js'></script></body></html>"
        ),
        "styles.css": (
            "body { color: #111; background: #fff; } main { max-width: 60rem; margin: auto; }"
        ),
        "app.js": (
            "document.querySelector('button').addEventListener('click', "
            "() => document.body.classList.add('active'));"
        ),
    }


def test_static_preview_is_immutable_and_verified(tmp_path) -> None:
    publisher = StaticAppPublisher(tmp_path)
    project_id, release_id = uuid.uuid4(), uuid.uuid4()
    first = publisher.publish(project_id, release_id, valid_files())
    replay = publisher.publish(project_id, release_id, valid_files())
    assert first.source_hash == replay.source_hash
    assert all(item["passed"] for item in first.checks)
    assert (first.root / "index.html").is_file()


def test_static_preview_rejects_external_network_and_path_set(tmp_path) -> None:
    publisher = StaticAppPublisher(tmp_path)
    files = valid_files()
    files["app.js"] = "fetch('https://example.com/api')"
    with pytest.raises(DomainError):
        publisher.publish(uuid.uuid4(), uuid.uuid4(), files)
    files = valid_files()
    files["secret.txt"] = "not allowed"
    with pytest.raises(DomainError):
        publisher.publish(uuid.uuid4(), uuid.uuid4(), files)
