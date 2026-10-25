# cbt_service/tests/test_uploads.py
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

from cbt_service.core.storage.file_storage import (
    LocalFileStorage,
    SpacesFileStorage,
    get_storage,
)
from cbt_service.main import app
from cbt_service.services.media.image_service import build_image_key, org_media_prefix, sniff_image_type

from .conftest import ORG_ID

URL = "/api/v1/uploads/images"
BASE_URL = "https://media.example.test"

PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG: bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF: bytes = b"GIF89a" + b"\x00" * 64
WEBP: bytes = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64
SVG: bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
def local_storage(tmp_path: Path) -> Iterator[LocalFileStorage]:
    storage = LocalFileStorage(root=tmp_path, base_url=BASE_URL)
    app.dependency_overrides[get_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)


def upload(
    client: TestClient,
    headers: dict[str, str],
    data: bytes,
    filename: str = "diagram.png",
    content_type: str = "image/png",
):
    return client.post(URL, headers=headers, files={"file": (filename, data, content_type)})


# ------------------------------------------------------------------
# Local folder backend
# ------------------------------------------------------------------

def test_teacher_uploads_png_to_local_folder(
    client: TestClient, teacher_headers: dict[str, str], local_storage: LocalFileStorage
) -> None:
    response = upload(client, teacher_headers, PNG)

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["content_type"] == "image/png"
    assert body["size"] == len(PNG)
    assert body["key"].startswith(f"orgs/{ORG_ID}/images/")
    assert body["key"].endswith(".png")
    assert body["url"] == f"{BASE_URL}/{body['key']}"
    assert (local_storage.root / body["key"]).read_bytes() == PNG


@pytest.mark.parametrize(
    ("data", "expected_type", "extension"),
    [(JPEG, "image/jpeg", ".jpg"), (GIF, "image/gif", ".gif"), (WEBP, "image/webp", ".webp")],
)
def test_other_raster_formats_are_accepted(
    client: TestClient,
    teacher_headers: dict[str, str],
    local_storage: LocalFileStorage,
    data: bytes,
    expected_type: str,
    extension: str,
) -> None:
    response = upload(client, teacher_headers, data, filename="x.bin", content_type="application/octet-stream")

    assert response.status_code == 201, response.json()
    assert response.json()["content_type"] == expected_type
    assert response.json()["key"].endswith(extension)


def test_type_comes_from_bytes_not_the_client(
    client: TestClient, teacher_headers: dict[str, str], local_storage: LocalFileStorage
) -> None:
    # Claims to be a PNG, is actually an SVG with a script: rejected.
    response = upload(client, teacher_headers, SVG, filename="evil.png", content_type="image/png")
    assert response.status_code == 415


def test_non_image_is_rejected(
    client: TestClient, teacher_headers: dict[str, str], local_storage: LocalFileStorage
) -> None:
    response = upload(client, teacher_headers, b"hello", filename="a.txt", content_type="text/plain")
    assert response.status_code == 415


def test_empty_file_is_rejected(
    client: TestClient, teacher_headers: dict[str, str], local_storage: LocalFileStorage
) -> None:
    response = upload(client, teacher_headers, b"")
    assert response.status_code == 400


def test_oversized_image_is_rejected(
    client: TestClient,
    teacher_headers: dict[str, str],
    local_storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cbt_service.core.settings import settings

    monkeypatch.setattr(settings, "MEDIA_MAX_IMAGE_MB", 1)
    response = upload(client, teacher_headers, PNG + b"\x00" * (1024 * 1024))
    assert response.status_code == 413
    assert not any(local_storage.root.rglob("*.png"))


def test_student_cannot_upload(
    client: TestClient, student_headers: dict[str, str], local_storage: LocalFileStorage
) -> None:
    response = upload(client, student_headers, PNG)
    assert response.status_code == 403


def test_upload_requires_authentication(client: TestClient, local_storage: LocalFileStorage) -> None:
    response = client.post(URL, files={"file": ("a.png", PNG, "image/png")})
    assert response.status_code == 401


def test_local_storage_refuses_keys_outside_root(tmp_path: Path) -> None:
    storage = LocalFileStorage(root=tmp_path / "media", base_url=BASE_URL)
    with pytest.raises(ValueError):
        storage.save("../../etc/passwd", PNG, "image/png")


# ------------------------------------------------------------------
# DigitalOcean Spaces backend (S3 API, stubbed: no network)
# ------------------------------------------------------------------

def test_teacher_uploads_png_to_spaces(client: TestClient, teacher_headers: dict[str, str]) -> None:
    s3 = boto3.client(
        "s3",
        region_name="fra1",
        endpoint_url="https://fra1.digitaloceanspaces.com",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    storage = SpacesFileStorage(client=s3, bucket="juwazi-media", base_url=BASE_URL)

    with Stubber(s3) as stubber:
        stubber.add_response(
            "put_object",
            {"ETag": '"abc"'},
            {
                "Bucket": "juwazi-media",
                "Key": ANY,
                "Body": PNG,
                "ContentType": "image/png",
                "ACL": "public-read",
                "CacheControl": SpacesFileStorage.CACHE_CONTROL,
            },
        )
        app.dependency_overrides[get_storage] = lambda: storage
        try:
            response = upload(client, teacher_headers, PNG)
        finally:
            app.dependency_overrides.pop(get_storage, None)
        stubber.assert_no_pending_responses()

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["key"].startswith(f"orgs/{ORG_ID}/images/")
    assert body["url"] == f"{BASE_URL}/{body['key']}"


def test_spaces_outage_returns_502(client: TestClient, teacher_headers: dict[str, str]) -> None:
    s3 = boto3.client(
        "s3", region_name="fra1", endpoint_url="https://fra1.digitaloceanspaces.com",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    storage = SpacesFileStorage(client=s3, bucket="juwazi-media", base_url=BASE_URL)

    with Stubber(s3) as stubber:
        stubber.add_client_error("put_object", service_error_code="ServiceUnavailable", http_status_code=503)
        app.dependency_overrides[get_storage] = lambda: storage
        try:
            response = upload(client, teacher_headers, PNG)
        finally:
            app.dependency_overrides.pop(get_storage, None)

    assert response.status_code == 502


def test_image_key_layout() -> None:
    from datetime import datetime, timezone
    from uuid import UUID

    org_id = UUID("11111111-1111-1111-1111-111111111111")
    key = build_image_key(org_id, "png", now=datetime(2026, 10, 23, tzinfo=timezone.utc))

    assert key.startswith("orgs/11111111-1111-1111-1111-111111111111/images/2026/10/")
    assert key.startswith(org_media_prefix(org_id))
    assert key.endswith(".png")
    # Random part makes every key unique.
    assert key != build_image_key(org_id, "png", now=datetime(2026, 10, 23, tzinfo=timezone.utc))


def test_sniffer() -> None:
    assert sniff_image_type(PNG) == "image/png"
    assert sniff_image_type(SVG) is None
    assert sniff_image_type(b"") is None
