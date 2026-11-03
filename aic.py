import random
import re

import requests
from markupsafe import Markup

API_URL = "https://api.artic.edu/api/v1"
IIIF_URL = "https://www.artic.edu/iiif/2"
WEBSITE_URL = "https://www.artic.edu/artworks"
# музей просить підписувати запити назвою застосунку
HEADERS = {"AIC-User-Agent": "art-explorer (student project)"}
TIMEOUT = 8
PER_PAGE = 12
# пошук не віддає результати далі 1000-го (offset + limit), інакше 403
MAX_RESULTS = 1000
MAX_PAGE = MAX_RESULTS // PER_PAGE
# id творів у колекції зараз сягають ~288 000
MAX_ARTWORK_ID = 290_000
# image_id — це UUID; інше в URL до музею не підставляємо
IMAGE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

CARD_FIELDS = ["id", "title", "artist_title", "date_display", "image_id", "thumbnail"]
DETAIL_FIELDS = CARD_FIELDS + [
    "artist_display", "medium_display", "dimensions", "place_of_origin",
    "department_title", "style_title", "credit_line", "description",
    "is_public_domain", "classification_title",
]


class ApiError(Exception):
    pass


def image_url(image_id):
    # IIIF-сервер музею стабільно віддає лише рекомендовану ширину 843 px, інші може блокувати
    return f"{IIIF_URL}/{image_id}/full/843,/0/default.jpg"


def fetch_image(image_id):
    """Повертає (байти, content-type) або None.

    Без заголовка AIC-User-Agent IIIF-сервер відповідає 403, а браузер не може
    додати його до <img>, тому картинку завантажує наш сервер.
    """
    if not IMAGE_ID.fullmatch(image_id):
        return None
    try:
        response = requests.get(image_url(image_id), headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    return response.content, response.headers.get("Content-Type", "image/jpeg")


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, f"{API_URL}{path}", headers=HEADERS, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as error:
        raise ApiError("The museum API is not responding. Please try again in a moment.") from error
    if response.status_code == 404:
        return None
    if not response.ok:
        raise ApiError(f"The museum API returned an error ({response.status_code}).")
    return response.json()


def _filters(public_only):
    # лише твори з картинкою: без неї в галереї показувати нічого
    filters = [{"exists": {"field": "image_id"}}]
    if public_only:
        filters.append({"term": {"is_public_domain": True}})
    return filters


def search(query="", page=1, public_only=False):
    """Повертає (твори, кількість сторінок). Порожній запит — підбірка музею."""
    filters = _filters(public_only)
    if not query:
        filters.append({"term": {"is_boosted": True}})
    body = {
        "query": {"bool": {"filter": filters}},
        "fields": CARD_FIELDS,
        "limit": PER_PAGE,
        "page": page,
    }
    if query:
        body["q"] = query
    data = _request("POST", "/artworks/search", json=body)
    pages = -(-data["pagination"]["total"] // PER_PAGE)
    return data["data"], min(max(1, pages), MAX_PAGE)


def get_artwork(artwork_id):
    data = _request("GET", f"/artworks/{artwork_id}", params={"fields": ",".join(DETAIL_FIELDS)})
    return data["data"] if data else None


def get_many(ids):
    if not ids:
        return []
    data = _request("GET", "/artworks", params={"ids": ",".join(map(str, ids)), "fields": ",".join(CARD_FIELDS)})
    # API не гарантує порядок — повертаємо в тому, в якому їх додали
    by_id = {art["id"]: art for art in data["data"]}
    return [by_id[i] for i in ids if i in by_id]


def random_artwork_id():
    # випадкова сторінка не підходить: далі 1000-го результату пошук не пускає,
    # а random_score API ігнорує. Тому беремо перший твір з id >= випадкового числа.
    for start in (random.randint(1, MAX_ARTWORK_ID), 0):
        filters = _filters(public_only=True) + [{"range": {"id": {"gte": start}}}]
        body = {"query": {"bool": {"filter": filters}}, "sort": [{"id": "asc"}], "fields": ["id"], "limit": 1}
        data = _request("POST", "/artworks/search", json=body)
        if data["data"]:
            return data["data"][0]["id"]
    return None


def paragraphs(description):
    # опис приходить як HTML; беремо лише текст абзаців, щоб не вставляти чужу розмітку
    if not description:
        return []
    parts = re.split(r"</p>|<br\s*/?>", description)
    return [text for part in parts if (text := Markup(part).striptags())]
