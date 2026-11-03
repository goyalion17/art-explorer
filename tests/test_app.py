import pytest
import requests

import aic
from app import app

ART = {
    "id": 27992,
    "title": "A Sunday on La Grande Jatte",
    "artist_title": "Georges Seurat",
    "artist_display": "Georges Seurat\nFrench, 1859-1891",
    "date_display": "1884-86",
    "image_id": "abc-123",
    "thumbnail": {"lqip": None, "alt_text": "People in a park"},
    "description": "<p>First <em>paragraph</em>.</p><p>Second.</p>",
    "is_public_domain": True,
    "medium_display": "Oil on canvas",
}


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.data


@pytest.fixture(autouse=True)
def empty_cache():
    # кеш живе в модулі між тестами — інакше тест отримав би чужу відповідь
    for func in (aic.search, aic.get_artwork, aic._get_many):
        func.cache_clear()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def fake_api(monkeypatch):
    # жодних справжніх запитів до музею: тести мають працювати офлайн
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/artworks/search"):
            return FakeResponse({"data": [ART], "pagination": {"total": 30}})
        if url.endswith("/artworks/1"):
            return FakeResponse({"status": 404}, 404)
        if url.endswith(f"/artworks/{ART['id']}"):
            return FakeResponse({"data": ART})
        if url.endswith("/artworks"):
            return FakeResponse({"data": [ART]})
        return FakeResponse({}, 500)

    monkeypatch.setattr(aic.requests, "request", fake_request)
    return calls


def test_image_url():
    assert aic.image_url("abc") == "https://www.artic.edu/iiif/2/abc/full/843,/0/default.jpg"


def test_paragraphs_strip_html():
    assert aic.paragraphs(ART["description"]) == ["First paragraph.", "Second."]
    assert aic.paragraphs(None) == []


def test_search_sends_query_and_filters(fake_api):
    artworks, pages = aic.search("seurat", page=2, public_only=True)
    body = fake_api[0][2]["json"]
    assert body["q"] == "seurat" and body["page"] == 2
    assert {"term": {"is_public_domain": True}} in body["query"]["bool"]["filter"]
    assert artworks == [ART] and pages == 3


def test_empty_search_shows_highlights(fake_api):
    aic.search()
    body = fake_api[0][2]["json"]
    assert "q" not in body
    assert {"term": {"is_boosted": True}} in body["query"]["bool"]["filter"]


def test_index_page(client, fake_api):
    response = client.get("/?q=seurat")
    assert response.status_code == 200
    assert b"A Sunday on La Grande Jatte" in response.data
    assert b"Page 1 of 3" in response.data


def test_artwork_page(client, fake_api):
    response = client.get(f"/artwork/{ART['id']}")
    assert response.status_code == 200
    assert b"Oil on canvas" in response.data
    assert b"First paragraph." in response.data
    assert b"<em>paragraph" not in response.data


def test_missing_artwork_is_404(client, fake_api):
    assert client.get("/artwork/1").status_code == 404


def test_api_down_shows_friendly_error(client, monkeypatch):
    def broken(*args, **kwargs):
        raise requests.ConnectionError

    monkeypatch.setattr(aic.requests, "request", broken)
    response = client.get("/")
    assert response.status_code == 502
    assert b"not responding" in response.data


def test_favourites_toggle(client, fake_api):
    client.post(f"/favourites/{ART['id']}", data={"next": "/favourites"})
    response = client.get("/favourites")
    assert b"A Sunday on La Grande Jatte" in response.data

    client.post(f"/favourites/{ART['id']}")
    with client.session_transaction() as session:
        assert session["favourites"] == []


def test_favourites_redirect_stays_on_site(client, fake_api):
    response = client.post(f"/favourites/{ART['id']}", data={"next": "//evil.example"})
    assert response.headers["Location"] == f"/artwork/{ART['id']}"


def test_random_redirects_to_artwork(client, fake_api):
    response = client.get("/random")
    assert response.headers["Location"] == f"/artwork/{ART['id']}"
    body = fake_api[0][2]["json"]
    assert body["sort"] == [{"id": "asc"}]
    assert "range" in body["query"]["bool"]["filter"][-1]


IMAGE_ID = "e966799b-97ee-1cc6-bd2f-a94b4b8bb8f9"


def test_image_proxy_sends_museum_header(client, monkeypatch):
    sent = {}

    def fake_get(url, headers, timeout):
        sent.update(url=url, headers=headers)
        response = FakeResponse(None)
        response.content, response.headers = b"jpeg-bytes", {"Content-Type": "image/jpeg"}
        return response

    monkeypatch.setattr(aic.requests, "get", fake_get)
    response = client.get(f"/image/{IMAGE_ID}")
    assert response.data == b"jpeg-bytes" and response.mimetype == "image/jpeg"
    assert "AIC-User-Agent" in sent["headers"]
    assert IMAGE_ID in sent["url"]


def test_image_proxy_rejects_bad_id(client, monkeypatch):
    monkeypatch.setattr(aic.requests, "get", lambda *a, **k: pytest.fail("must not call the museum"))
    assert client.get("/image/not-a-uuid").status_code == 404


def test_pages_use_local_image_urls(client, fake_api):
    html = client.get("/").text
    assert f'src="/image/{ART["image_id"]}"' in html


def test_repeated_search_uses_cache(fake_api):
    aic.search("seurat")
    aic.search("seurat")
    aic.search("monet")
    assert len(fake_api) == 2


def test_cache_expires(fake_api, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(aic.time, "monotonic", lambda: now[0])
    aic.get_artwork(ART["id"])
    now[0] += 59 * 60
    aic.get_artwork(ART["id"])
    assert len(fake_api) == 1
    now[0] += 2 * 60
    aic.get_artwork(ART["id"])
    assert len(fake_api) == 2


def test_errors_are_not_cached(client, fake_api, monkeypatch):
    real = aic.requests.request
    monkeypatch.setattr(aic.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert client.get("/").status_code == 502
    monkeypatch.setattr(aic.requests, "request", real)
    assert client.get("/").status_code == 200


def test_cache_drops_oldest_when_full():
    calls = []
    cached = aic.ttl_cache(seconds=60, max_size=2)(calls.append)
    for n in (1, 2, 3, 1):
        cached(n)
    # 1 витіснила трійка, тож її довелося рахувати знову
    assert calls == [1, 2, 3, 1]
