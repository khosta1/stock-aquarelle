"""Watercolor Stock — Flask entry point.

Run locally with:  python app.py   (or: venv\\Scripts\\python.exe app.py)
Then open:          http://localhost:5000
"""

import csv
import hmac
import io
import os
import uuid
import zipfile
from datetime import date, datetime, timedelta

from flask import (Flask, Response, flash, redirect, render_template, request,
                   session, url_for)
from PIL import Image, ImageOps

import database

app = Flask(__name__)
# Secret key signs sessions/flash messages. Override in production via env var.
app.config["SECRET_KEY"] = os.environ.get("WATERCOLOR_SECRET", "dev-secret-change-me")

# --- Image upload config ---------------------------------------------------
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
# Accept large originals (phone photos) — they get compressed down on upload.
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB max per upload
IMAGES_DIR = os.path.join(app.static_folder, "images")
MAX_IMAGE_DIM = 1400   # longest side after downscaling, in pixels
JPEG_QUALITY = 85      # re-encode quality (good balance size/quality)

# Editions with this many copies in stock (or fewer) show up in "À réimprimer".
LOW_STOCK_THRESHOLD = 3

FRENCH_MONTHS = ["", "janvier", "février", "mars", "avril", "mai", "juin",
                 "juillet", "août", "septembre", "octobre", "novembre",
                 "décembre"]

# --- Authentication config -------------------------------------------------
# Single shared password. Set WATERCOLOR_PASSWORD on the server; the local
# default below only exists so development isn't locked out.
APP_PASSWORD = os.environ.get("WATERCOLOR_PASSWORD") or "aquarelle"
if not os.environ.get("WATERCOLOR_PASSWORD"):
    print("WARNING: using default password 'aquarelle'. "
          "Set WATERCOLOR_PASSWORD before hosting online.")

# Local dev convenience: set WATERCOLOR_NO_LOGIN=1 to skip the login entirely
# for fast iteration. NEVER set this on the server (it is unset there by default).
LOGIN_DISABLED = os.environ.get("WATERCOLOR_NO_LOGIN") == "1"
if LOGIN_DISABLED:
    print("WARNING: login is DISABLED (WATERCOLOR_NO_LOGIN=1) — local dev only!")

# Stay logged in for 30 days, then require the password again.
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JS can't read the cookie
    SESSION_COOKIE_SAMESITE="Lax",  # basic CSRF hardening
    # Only send the cookie over HTTPS. Enable on the server (HTTPS) by setting
    # WATERCOLOR_HTTPS=1; kept off locally so login works over plain http.
    SESSION_COOKIE_SECURE=os.environ.get("WATERCOLOR_HTTPS") == "1",
)

# Make sure the tables exist before we serve any request.
database.init_db()


@app.before_request
def require_login():
    """Block every page behind the password, except the login page and CSS."""
    if LOGIN_DISABLED:               # local dev fast-iteration mode
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


@app.context_processor
def inject_auth_flags():
    """Make login state available to templates (so the nav shows in dev mode)."""
    return {"login_disabled": LOGIN_DISABLED}


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    if request.method == "POST":
        password = request.form.get("password", "")
        # constant-time compare avoids leaking the password via timing
        if hmac.compare_digest(password, APP_PASSWORD):
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("Mot de passe incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Vous êtes déconnecté.", "success")
    return redirect(url_for("login"))


@app.template_filter("fr_date")
def fr_date(value):
    """Display an ISO date (YYYY-MM-DD, as stored) in French style JJ/MM/AAAA.

    Dates are always *stored* as YYYY-MM-DD (sortable, unambiguous); we only
    reformat for display here. Unparseable/empty values pass through safely.
    """
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return value


@app.template_filter("fr_month")
def fr_month(value):
    """Display a 'YYYY-MM' month key in French, e.g. 'juin 2026'."""
    if not value or len(value) < 7:
        return value or ""
    try:
        return f"{FRENCH_MONTHS[int(value[5:7])]} {value[:4]}"
    except (ValueError, IndexError):
        return value


def _allowed_image(filename):
    return ("." in filename
            and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)


def _flatten_to_rgb(img):
    """Convert any image mode to RGB, flattening transparency onto white."""
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background
    return img.convert("RGB")


def save_uploaded_image(file):
    """Compress + store an uploaded image under static/images.

    Large photos are downscaled to MAX_IMAGE_DIM and re-encoded as JPEG, so a
    7 MB phone photo becomes a few hundred KB. Returns (filename, error):
    filename is None if no file was sent; error is set if a file was rejected.
    """
    if not file or not file.filename:
        return None, None
    if not _allowed_image(file.filename):
        return None, "Format d'image non supporté (jpg, png, gif, webp)."
    try:
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img)             # honour phone rotation
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))   # downscale, keep ratio
        img = _flatten_to_rgb(img)
        fname = f"{uuid.uuid4().hex}.jpg"
        os.makedirs(IMAGES_DIR, exist_ok=True)
        img.save(os.path.join(IMAGES_DIR, fname), "JPEG",
                 quality=JPEG_QUALITY, optimize=True)
        return fname, None
    except Exception:
        return None, "Image illisible ou corrompue."


@app.route("/")
def index():
    """Dashboard: summary tiles + every artwork with live stock numbers."""
    q = request.args.get("q", "").strip()
    return render_template(
        "index.html",
        groups=database.list_artworks_with_variants(q or None),
        q=q,
        summary=database.dashboard_summary(),
        low_stock_count=len(database.low_stock_variants(LOW_STOCK_THRESHOLD)),
    )


@app.route("/catalogue")
def catalogue():
    """Printable catalogue / price list with photos."""
    return render_template(
        "catalogue.html",
        groups=database.list_artworks_with_variants(),
    )


# ---- Artworks ------------------------------------------------------------

@app.route("/artworks/add", methods=["GET", "POST"])
def add_artwork():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        year = request.form.get("year", "").strip()
        if not title:
            flash("Veuillez saisir un titre.", "error")
            return render_template("add_artwork.html", form=request.form)
        image_path, img_error = save_uploaded_image(request.files.get("photo"))
        if img_error:
            flash(img_error, "error")  # keep the artwork, just without a photo
        artwork_id = database.add_artwork(
            title=title,
            year=int(year) if year.isdigit() else None,
            image_path=image_path,
        )
        flash(f"Œuvre « {title} » ajoutée.", "success")
        return redirect(url_for("artwork_detail", artwork_id=artwork_id))
    return render_template("add_artwork.html", form={})


@app.route("/artworks/<int:artwork_id>")
def artwork_detail(artwork_id):
    artwork = database.get_artwork(artwork_id)
    if artwork is None:
        flash("Œuvre introuvable.", "error")
        return redirect(url_for("index"))
    return render_template(
        "artwork.html",
        artwork=artwork,
        variants=database.variants_for_artwork(artwork_id),
        today=date.today().isoformat(),
    )


@app.route("/artworks/<int:artwork_id>/edit", methods=["POST"])
def update_artwork(artwork_id):
    artwork = database.get_artwork(artwork_id)
    if artwork is None:
        flash("Œuvre introuvable.", "error")
        return redirect(url_for("index"))
    title = request.form.get("title", "").strip()
    year = request.form.get("year", "").strip()
    if not title:
        flash("Veuillez saisir un titre.", "error")
    else:
        database.update_artwork(
            artwork_id, title, int(year) if year.isdigit() else None)
        flash("Œuvre mise à jour.", "success")
    return redirect(url_for("artwork_detail", artwork_id=artwork_id))


@app.route("/artworks/<int:artwork_id>/supprimer", methods=["POST"])
def delete_artwork(artwork_id):
    artwork = database.get_artwork(artwork_id)
    if artwork is None:
        flash("Œuvre introuvable.", "error")
        return redirect(url_for("index"))
    image = database.delete_artwork(artwork_id)
    if image:  # remove the photo file too
        try:
            os.remove(os.path.join(IMAGES_DIR, image))
        except OSError:
            pass
    flash(f"Œuvre « {artwork['title']} » supprimée.", "success")
    return redirect(url_for("index"))


# ---- Variants ------------------------------------------------------------

@app.route("/artworks/<int:artwork_id>/variants/add", methods=["GET", "POST"])
def add_variant(artwork_id):
    artwork = database.get_artwork(artwork_id)
    if artwork is None:
        flash("Œuvre introuvable.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        size = request.form.get("size", "").strip()
        paper = request.form.get("paper", "").strip()
        price = request.form.get("price", "").strip()
        edition_size = request.form.get("edition_size", "").strip()

        errors = []
        if not size:
            errors.append("Le format est obligatoire.")
        if not edition_size.isdigit() or int(edition_size) < 1:
            errors.append("La taille de l'édition doit être un entier supérieur ou égal à 1.")
        try:
            price_value = float(price) if price else 0.0
        except ValueError:
            errors.append("Le prix doit être un nombre.")
            price_value = 0.0

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "add_variant.html", artwork=artwork, form=request.form,
                formats=database.list_formats(), papers=database.list_papers()
            )

        database.add_variant(
            artwork_id=artwork_id,
            size=size,
            paper=paper or None,
            price=price_value,
            edition_size=int(edition_size),
        )
        flash(f"Format {size} (édition de {edition_size}) ajouté.", "success")
        return redirect(url_for("artwork_detail", artwork_id=artwork_id))

    return render_template(
        "add_variant.html", artwork=artwork, form={},
        formats=database.list_formats(), papers=database.list_papers()
    )


@app.route("/variants/<int:variant_id>")
def variant_detail(variant_id):
    """One variant: its stock numbers and the grid of numbered copies."""
    variant = database.get_variant(variant_id)
    if variant is None:
        flash("Variant not found.", "error")
        return redirect(url_for("index"))
    return render_template(
        "variant.html",
        variant=variant,
        copies=database.copies_for_variant(variant_id),
        today=date.today().isoformat(),
        formats=database.list_formats(),
        papers=database.list_papers(),
    )


@app.route("/variants/<int:variant_id>/edit", methods=["POST"])
def update_variant(variant_id):
    variant = database.get_variant(variant_id)
    if variant is None:
        flash("Édition introuvable.", "error")
        return redirect(url_for("index"))
    size = request.form.get("size", "").strip()
    paper = request.form.get("paper", "").strip() or None
    price_raw = request.form.get("price", "").strip()
    edition_raw = request.form.get("edition_size", "").strip()

    if not size:
        flash("Le format est obligatoire.", "error")
        return redirect(url_for("variant_detail", variant_id=variant_id))
    try:
        price = float(price_raw) if price_raw else 0.0
    except ValueError:
        flash("Le prix doit être un nombre.", "error")
        return redirect(url_for("variant_detail", variant_id=variant_id))
    if not edition_raw.isdigit() or int(edition_raw) < 1:
        flash("La taille de l'édition doit être un entier d'au moins 1.", "error")
        return redirect(url_for("variant_detail", variant_id=variant_id))

    error = database.update_variant(variant_id, size, paper, price, int(edition_raw))
    flash(error or "Édition mise à jour.", "error" if error else "success")
    return redirect(url_for("variant_detail", variant_id=variant_id))


@app.route("/variants/<int:variant_id>/supprimer", methods=["POST"])
def delete_variant(variant_id):
    variant = database.get_variant(variant_id)
    if variant is None:
        flash("Édition introuvable.", "error")
        return redirect(url_for("index"))
    artwork_id = variant["artwork_id"]
    database.delete_variant(variant_id)
    flash("Édition supprimée.", "success")
    return redirect(url_for("artwork_detail", artwork_id=artwork_id))


@app.route("/variants/<int:variant_id>/print", methods=["POST"])
def print_copies(variant_id):
    quantity = request.form.get("quantity", "").strip()
    printed_date = request.form.get("printed_date", "").strip() or date.today().isoformat()
    if not quantity.isdigit():
        flash("Indiquez combien d'exemplaires vous avez imprimés.", "error")
        return redirect(url_for("variant_detail", variant_id=variant_id))

    created, error = database.print_copies(
        variant_id, int(quantity), printed_date
    )
    if error:
        flash(error, "error")
    else:
        flash(f"{created} exemplaire(s) ajouté(s) au stock.", "success")
    return redirect(url_for("variant_detail", variant_id=variant_id))


# ---- Copies (selling) ----------------------------------------------------

@app.route("/copies/<int:copy_id>/sell", methods=["GET", "POST"])
def sell_copy(copy_id):
    copy = database.get_copy(copy_id)
    if copy is None:
        flash("Exemplaire introuvable.", "error")
        return redirect(url_for("index"))
    variant = database.get_variant(copy["variant_id"])

    if request.method == "POST":
        sold_date = request.form.get("sold_date", "").strip() or date.today().isoformat()
        price = request.form.get("sale_price", "").strip()
        customer = request.form.get("customer", "").strip() or None
        channel = request.form.get("channel", "").strip() or None
        try:
            sale_price = float(price) if price else variant["price"]
        except ValueError:
            flash("Le prix de vente doit être un nombre.", "error")
            return render_template(
                "sell_copy.html", copy=copy, variant=variant,
                today=date.today().isoformat(), form=request.form
            )

        error = database.sell_copy(copy_id, sold_date, sale_price, customer, channel)
        if error:
            flash(error, "error")
            return redirect(url_for("variant_detail", variant_id=copy["variant_id"]))
        flash(f"Exemplaire n°{copy['edition_number']} vendu.", "success")
        return redirect(url_for("variant_detail", variant_id=copy["variant_id"]))

    return render_template(
        "sell_copy.html", copy=copy, variant=variant,
        today=date.today().isoformat(), form={}
    )


@app.route("/copies/<int:copy_id>/recu")
def receipt(copy_id):
    """Printable receipt for a single sold copy."""
    copy = database.get_copy(copy_id)
    if copy is None:
        flash("Exemplaire introuvable.", "error")
        return redirect(url_for("index"))
    if copy["status"] != "sold":
        flash("Cet exemplaire n'est pas vendu.", "error")
        return redirect(url_for("variant_detail", variant_id=copy["variant_id"]))
    return render_template(
        "receipt.html", copy=copy,
        variant=database.get_variant(copy["variant_id"]),
    )


@app.route("/copies/<int:copy_id>/unsell", methods=["POST"])
def unsell_copy(copy_id):
    """Undo a sale — put the copy back in stock (e.g. after a misclick)."""
    copy = database.get_copy(copy_id)
    if copy is None:
        flash("Exemplaire introuvable.", "error")
        return redirect(url_for("index"))

    error = database.unsell_copy(copy_id)
    if error:
        flash(error, "error")
    else:
        flash(f"Vente de l'exemplaire n°{copy['edition_number']} annulée — "
              f"remis en stock.", "success")
    return redirect(url_for("variant_detail", variant_id=copy["variant_id"]))


# ---- Artwork photo --------------------------------------------------------

@app.route("/artworks/<int:artwork_id>/photo", methods=["POST"])
def update_artwork_photo(artwork_id):
    artwork = database.get_artwork(artwork_id)
    if artwork is None:
        flash("Œuvre introuvable.", "error")
        return redirect(url_for("index"))

    image_path, error = save_uploaded_image(request.files.get("photo"))
    if error:
        flash(error, "error")
    elif image_path:
        old = artwork["image_path"]
        database.update_artwork_image(artwork_id, image_path)
        if old:  # remove the previous file so we don't leave orphans
            try:
                os.remove(os.path.join(IMAGES_DIR, old))
            except OSError:
                pass
        flash("Photo mise à jour.", "success")
    else:
        flash("Aucune image sélectionnée.", "error")
    return redirect(url_for("artwork_detail", artwork_id=artwork_id))


# ---- Sales report ---------------------------------------------------------

@app.route("/rapport")
def report():
    start = request.args.get("start", "").strip() or None
    end = request.args.get("end", "").strip() or None
    totals = database.sales_totals(start, end)
    print_costs = database.print_costs_total(start, end)
    expenses_total = database.expenses_total(start, end)
    net = totals["revenue"] - print_costs - expenses_total
    current_year = date.today().year
    return render_template(
        "report.html",
        totals=totals,
        by_channel=database.revenue_by_channel(start, end),
        by_month=database.revenue_by_month(start, end),
        print_costs=print_costs,
        expenses_total=expenses_total,
        expenses_by_category=database.expenses_by_category(start, end),
        best_sellers=database.best_sellers(start, end),
        profit_by_artwork=database.profit_by_artwork(start, end),
        net=net,
        start=start or "", end=end or "",
        year_start=f"{current_year}-01-01", year_end=f"{current_year}-12-31",
    )


# ---- CSV export -----------------------------------------------------------

def _euro(value):
    """French-style amount for CSV: 45.00 -> '45,00'."""
    if value is None:
        return ""
    return f"{value:.2f}".replace(".", ",")


def _csv_response(filename, header, rows):
    """Build a download Response. Uses ';' + UTF-8 BOM for French Excel."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    data = "﻿" + buffer.getvalue()  # BOM so Excel shows accents correctly
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export/ventes.csv")
def export_sales_csv():
    rows = [
        [r["artwork"], r["year"] or "", r["size"], r["paper"] or "",
         r["edition_number"], r["edition_size"], _euro(r["sale_price"]),
         fr_date(r["sold_date"]), r["customer"] or "", r["channel"] or ""]
        for r in database.sold_copies_detailed()
    ]
    header = ["Œuvre", "Année", "Format", "Papier", "N° exemplaire",
              "Taille édition", "Prix de vente", "Date de vente",
              "Client", "Canal"]
    return _csv_response("ventes.csv", header, rows)


@app.route("/export/stock.csv")
def export_stock_csv():
    status_fr = {"printed": "en stock", "sold": "vendu"}
    rows = [
        [r["artwork"], r["year"] or "", r["size"], r["paper"] or "",
         _euro(r["price"]), r["edition_number"], r["edition_size"],
         status_fr.get(r["status"], r["status"]), fr_date(r["printed_date"]),
         _euro(r["sale_price"]), fr_date(r["sold_date"]),
         r["customer"] or "", r["channel"] or ""]
        for r in database.all_copies_detailed()
    ]
    header = ["Œuvre", "Année", "Format", "Papier", "Prix", "N° exemplaire",
              "Taille édition", "Statut", "Date impression", "Prix de vente",
              "Date de vente", "Client", "Canal"]
    return _csv_response("stock.csv", header, rows)


@app.route("/export/sauvegarde.zip")
def export_backup():
    """Full restorable backup: the SQLite database + all uploaded photos."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(database.DB_PATH):
            zf.write(database.DB_PATH, arcname="store.db")
        if os.path.isdir(IMAGES_DIR):
            for name in sorted(os.listdir(IMAGES_DIR)):
                full = os.path.join(IMAGES_DIR, name)
                if os.path.isfile(full):
                    zf.write(full, arcname=f"images/{name}")
    mem.seek(0)
    fname = f"sauvegarde-stock-aquarelle-{date.today().isoformat()}.zip"
    return Response(
        mem.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---- Global parameters (settings) ----------------------------------------

@app.route("/parametres")
def settings():
    return render_template(
        "settings.html",
        papers=database.list_papers(),
        formats=database.list_formats(),
        categories=database.list_expense_categories(),
    )


@app.route("/parametres/papiers/ajouter", methods=["POST"])
def add_paper():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Indiquez un nom de papier.", "error")
    else:
        error = database.add_paper(name)
        flash(error or f"Papier « {name} » ajouté.",
              "error" if error else "success")
    return redirect(url_for("settings"))


@app.route("/parametres/papiers/<int:paper_id>/supprimer", methods=["POST"])
def delete_paper(paper_id):
    database.delete_paper(paper_id)
    flash("Papier supprimé.", "success")
    return redirect(url_for("settings"))


@app.route("/parametres/formats/ajouter", methods=["POST"])
def add_format():
    name = request.form.get("name", "").strip()
    dimensions = request.form.get("dimensions", "").strip() or None
    if not name:
        flash("Indiquez un nom de format.", "error")
    else:
        error = database.add_format(name, dimensions)
        flash(error or f"Format « {name} » ajouté.",
              "error" if error else "success")
    return redirect(url_for("settings"))


@app.route("/parametres/formats/<int:format_id>/supprimer", methods=["POST"])
def delete_format(format_id):
    database.delete_format(format_id)
    flash("Format supprimé.", "success")
    return redirect(url_for("settings"))


@app.route("/parametres/categories/ajouter", methods=["POST"])
def add_category():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Indiquez un nom de catégorie.", "error")
    else:
        error = database.add_expense_category(name)
        flash(error or f"Catégorie « {name} » ajoutée.",
              "error" if error else "success")
    return redirect(url_for("settings"))


@app.route("/parametres/categories/<int:category_id>/supprimer",
           methods=["POST"])
def delete_category(category_id):
    database.delete_expense_category(category_id)
    flash("Catégorie supprimée.", "success")
    return redirect(url_for("settings"))


# ---- Printing (batch print runs + history) -------------------------------

@app.route("/impression", methods=["GET", "POST"])
def print_run_page():
    if request.method == "POST":
        run_date = request.form.get("date", "").strip() or date.today().isoformat()
        note = request.form.get("note", "").strip() or None
        cost_raw = request.form.get("cost", "").strip()
        try:
            cost = float(cost_raw) if cost_raw else 0.0
        except ValueError:
            flash("Le coût doit être un nombre.", "error")
            return redirect(url_for("print_run_page"))

        # Collect quantities from fields named qty_<variant_id>.
        items = []
        for key, value in request.form.items():
            if key.startswith("qty_") and value.strip():
                try:
                    items.append((int(key[4:]), int(value)))
                except ValueError:
                    continue
        items = [(vid, qty) for vid, qty in items if qty > 0]
        if not items:
            flash("Indiquez une quantité pour au moins une édition.", "error")
            return redirect(url_for("print_run_page"))

        run_id, printed, errors = database.create_print_run(
            run_date, cost, note, items)
        for e in errors:
            flash(e, "error")
        if run_id:
            total = sum(p["quantity"] for p in printed)
            flash(f"Tirage enregistré : {total} exemplaire(s) sur "
                  f"{len(printed)} édition(s).", "success")
            return redirect(url_for("print_history"))
        return redirect(url_for("print_run_page"))

    return render_template(
        "print_run.html",
        groups=database.list_artworks_with_variants(),
        today=date.today().isoformat(),
        low_stock=database.low_stock_variants(LOW_STOCK_THRESHOLD),
        low_threshold=LOW_STOCK_THRESHOLD,
    )


@app.route("/impressions")
def print_history():
    return render_template(
        "print_history.html",
        runs=database.list_print_runs(),
        total_cost=database.print_costs_total(),
    )


# ---- Bulk selling --------------------------------------------------------

@app.route("/vente-groupee", methods=["GET", "POST"])
def sell_batch_page():
    if request.method == "POST":
        sold_date = request.form.get("date", "").strip() or date.today().isoformat()
        channel = request.form.get("channel", "").strip() or None

        items = []
        for key, value in request.form.items():
            if key.startswith("qty_") and value.strip():
                try:
                    items.append((int(key[4:]), int(value)))
                except ValueError:
                    continue
        items = [(vid, qty) for vid, qty in items if qty > 0]
        if not items:
            flash("Indiquez une quantité pour au moins une édition.", "error")
            return redirect(url_for("sell_batch_page"))

        total, summary, errors = database.create_sale_batch(
            sold_date, channel, items)
        for e in errors:
            flash(e, "error")
        if total:
            revenue = sum(s["count"] * s["price"] for s in summary)
            flash(f"{total} vente(s) enregistrée(s) — recette {revenue:.2f}.",
                  "success")
            return redirect(url_for("report"))
        return redirect(url_for("sell_batch_page"))

    return render_template(
        "sell_batch.html",
        groups=database.list_artworks_with_variants(),
        today=date.today().isoformat(),
    )


# ---- Expenses ------------------------------------------------------------

@app.route("/depenses", methods=["GET", "POST"])
def expenses_page():
    if request.method == "POST":
        exp_date = request.form.get("date", "").strip() or date.today().isoformat()
        category = request.form.get("category", "").strip() or None
        note = request.form.get("note", "").strip() or None
        amount_raw = request.form.get("amount", "").strip()
        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            flash("Le montant doit être un nombre.", "error")
            return redirect(url_for("expenses_page"))
        if amount <= 0:
            flash("Indiquez un montant supérieur à 0.", "error")
            return redirect(url_for("expenses_page"))

        database.add_expense(exp_date, category, amount, note)
        flash("Dépense enregistrée.", "success")
        return redirect(url_for("expenses_page"))

    return render_template(
        "expenses.html",
        expenses=database.list_expenses(),
        categories=database.list_expense_categories(),
        total=database.expenses_total(),
        today=date.today().isoformat(),
    )


@app.route("/depenses/<int:expense_id>/supprimer", methods=["POST"])
def delete_expense(expense_id):
    database.delete_expense(expense_id)
    flash("Dépense supprimée.", "success")
    return redirect(url_for("expenses_page"))


if __name__ == "__main__":
    # debug=True gives auto-reload + error pages while developing.
    # For real hosting you'd use a production server (see README).
    app.run(host="127.0.0.1", port=5000, debug=True)
