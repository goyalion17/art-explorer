import os
import secrets
from pathlib import Path

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for

import aic

BASE_DIR = Path(__file__).parent
MAX_FAVOURITES = 60

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)


@app.context_processor
def helpers():
    # змінюється при кожній правці css, тож браузер не тримає старі стилі в кеші
    return {
        "asset_v": int((BASE_DIR / "static" / "style.css").stat().st_mtime),
        "image_url": lambda image_id: url_for("image", image_id=image_id) if image_id else None,
        "favourites": session.get("favourites", []),
    }


@app.errorhandler(aic.ApiError)
def api_error(error):
    return render_template("error.html", message=str(error)), 502


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", message="We couldn't find this artwork."), 404


def to_page(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


@app.get("/")
def index():
    query = request.args.get("q", "").strip()
    public_only = request.args.get("public") == "on"
    page = min(to_page(request.args.get("page")), aic.MAX_PAGE)
    artworks, pages = aic.search(query, page, public_only)
    return render_template(
        "index.html", artworks=artworks, query=query, public_only=public_only,
        page=min(page, pages), pages=pages,
    )


@app.get("/artwork/<int:artwork_id>")
def artwork(artwork_id):
    art = aic.get_artwork(artwork_id)
    if art is None:
        abort(404)
    return render_template(
        "artwork.html", art=art, description=aic.paragraphs(art.get("description")),
        museum_url=f"{aic.WEBSITE_URL}/{artwork_id}",
    )


@app.get("/random")
def random_artwork():
    artwork_id = aic.random_artwork_id()
    if artwork_id is None:
        return redirect(url_for("index"))
    return redirect(url_for("artwork", artwork_id=artwork_id))


@app.get("/image/<image_id>")
def image(image_id):
    result = aic.fetch_image(image_id)
    if result is None:
        abort(404)
    content, content_type = result
    response = Response(content, mimetype=content_type)
    # картини не змінюються — нехай браузер тримає їх у кеші добу
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.post("/favourites/<int:artwork_id>")
def toggle_favourite(artwork_id):
    favourites = session.get("favourites", [])
    if artwork_id in favourites:
        favourites.remove(artwork_id)
    elif len(favourites) >= MAX_FAVOURITES:
        # список живе в cookie, а вона не може бути більшою за ~4 КБ
        flash(f"You can save up to {MAX_FAVOURITES} artworks. Remove one to add another.")
    else:
        favourites.append(artwork_id)
    session["favourites"] = favourites
    # повертаємо туди, звідки прийшли, але лише в межах сайту
    back = request.form.get("next", "")
    if not back.startswith("/") or back.startswith("//"):
        back = url_for("artwork", artwork_id=artwork_id)
    return redirect(back)


@app.get("/favourites")
def favourites_page():
    return render_template("favourites.html", artworks=aic.get_many(session.get("favourites", [])))


if __name__ == "__main__":
    # на macOS порт 5000 займає AirPlay Receiver, і браузер потрапляє до нього, а не до Flask
    app.run(debug=True, port=5001)
