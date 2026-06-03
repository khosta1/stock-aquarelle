"""Watercolor Stock — Flask entry point.

Run locally with:  python app.py   (or: venv\\Scripts\\python.exe app.py)
Then open:          http://localhost:5000
"""

import hmac
import os
from datetime import date, datetime, timedelta

from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)

import database

app = Flask(__name__)
# Secret key signs sessions/flash messages. Override in production via env var.
app.config["SECRET_KEY"] = os.environ.get("WATERCOLOR_SECRET", "dev-secret-change-me")

# --- Authentication config -------------------------------------------------
# Single shared password. Set WATERCOLOR_PASSWORD on the server; the local
# default below only exists so development isn't locked out.
APP_PASSWORD = os.environ.get("WATERCOLOR_PASSWORD") or "aquarelle"
if not os.environ.get("WATERCOLOR_PASSWORD"):
    print("WARNING: using default password 'aquarelle'. "
          "Set WATERCOLOR_PASSWORD before hosting online.")

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
    if request.endpoint in ("login", "static"):
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


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


@app.route("/")
def index():
    """Dashboard: every artwork with its variants and live stock numbers."""
    return render_template(
        "index.html",
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
        artwork_id = database.add_artwork(
            title=title,
            year=int(year) if year.isdigit() else None,
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
                "add_variant.html", artwork=artwork, form=request.form
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

    return render_template("add_variant.html", artwork=artwork, form={})


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
    )


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


if __name__ == "__main__":
    # debug=True gives auto-reload + error pages while developing.
    # For real hosting you'd use a production server (see README).
    app.run(host="127.0.0.1", port=5000, debug=True)
