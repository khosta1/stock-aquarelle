"""Database access for Watercolor Stock.

All SQLite access lives in THIS file. If we ever outgrow SQLite
(e.g. hosting it online for multiple users), only this module changes.

Stock numbers are never stored — they are computed from the `copies` rows:
    printed   = number of copies
    sold      = copies with status 'sold'
    in_stock  = printed - sold
    remaining = edition_size - printed   (edition slots still printable)
    revenue   = sum of sale_price over sold copies
"""

import os
import sqlite3

# Where the database file and schema live. The DB path can be overridden
# with an environment variable, which is how hosted setups configure it.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WATERCOLOR_DB", os.path.join(BASE_DIR, "store.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_connection():
    """Open a connection. Rows behave like dicts; foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they don't exist yet. Safe to call on every start."""
    with get_connection() as conn:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())


# --------------------------------------------------------------------------
# Helpers that turn a raw variant row + its copy counts into display numbers
# --------------------------------------------------------------------------

def _decorate_variant(row):
    """Add computed stock numbers to a variant row from the query below."""
    v = dict(row)
    v["in_stock"] = v["printed"] - v["sold"]
    v["remaining"] = v["edition_size"] - v["printed"]
    v["sold_out"] = v["remaining"] <= 0
    return v


_VARIANT_STATS_SQL = """
    SELECT v.*,
           COUNT(c.id)                                              AS printed,
           COALESCE(SUM(CASE WHEN c.status = 'sold' THEN 1 END), 0) AS sold,
           COALESCE(SUM(CASE WHEN c.status = 'sold'
                             THEN c.sale_price END), 0)             AS revenue
    FROM variants v
    LEFT JOIN copies c ON c.variant_id = v.id
    {where}
    GROUP BY v.id
    ORDER BY v.size
"""


# --------------------------------------------------------------------------
# Read queries
# --------------------------------------------------------------------------

def list_artworks_with_variants():
    """Every artwork with its variants (each carrying computed stock numbers)."""
    conn = get_connection()
    artworks = conn.execute("SELECT * FROM artworks ORDER BY title").fetchall()
    result = []
    for art in artworks:
        rows = conn.execute(
            _VARIANT_STATS_SQL.format(where="WHERE v.artwork_id = ?"),
            (art["id"],),
        ).fetchall()
        result.append({
            "artwork": art,
            "variants": [_decorate_variant(r) for r in rows],
        })
    conn.close()
    return result


def get_artwork(artwork_id):
    conn = get_connection()
    art = conn.execute(
        "SELECT * FROM artworks WHERE id = ?", (artwork_id,)
    ).fetchone()
    conn.close()
    return art


def variants_for_artwork(artwork_id):
    """Variants of one artwork, with computed stock numbers."""
    conn = get_connection()
    rows = conn.execute(
        _VARIANT_STATS_SQL.format(where="WHERE v.artwork_id = ?"),
        (artwork_id,),
    ).fetchall()
    conn.close()
    return [_decorate_variant(r) for r in rows]


def get_variant(variant_id):
    """One variant joined to its artwork title, with computed stock numbers."""
    conn = get_connection()
    row = conn.execute(
        _VARIANT_STATS_SQL.format(where="WHERE v.id = ?"),
        (variant_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    art = conn.execute(
        "SELECT title FROM artworks WHERE id = ?", (row["artwork_id"],)
    ).fetchone()
    conn.close()
    v = _decorate_variant(row)
    v["artwork_title"] = art["title"] if art else "?"
    return v


def copies_for_variant(variant_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM copies WHERE variant_id = ? ORDER BY edition_number",
        (variant_id,),
    ).fetchall()
    conn.close()
    return rows


def get_copy(copy_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM copies WHERE id = ?", (copy_id,)
    ).fetchone()
    conn.close()
    return row


# --------------------------------------------------------------------------
# Write actions
# --------------------------------------------------------------------------

def add_artwork(title, year, image_path=None):
    conn = get_connection()
    with conn:
        cur = conn.execute(
            "INSERT INTO artworks (title, year, image_path) VALUES (?, ?, ?)",
            (title, year, image_path),
        )
    conn.close()
    return cur.lastrowid


def add_variant(artwork_id, size, paper, price, edition_size):
    conn = get_connection()
    with conn:
        cur = conn.execute(
            """INSERT INTO variants (artwork_id, size, paper, price, edition_size)
               VALUES (?, ?, ?, ?, ?)""",
            (artwork_id, size, paper, price, edition_size),
        )
    conn.close()
    return cur.lastrowid


def print_copies(variant_id, quantity, printed_date):
    """Create `quantity` new numbered copies. Returns (created, error_message).

    Enforces the edition cap: you can't print more than edition_size total.
    New copies are numbered continuing from the highest existing number.
    """
    conn = get_connection()
    variant = conn.execute(
        "SELECT edition_size FROM variants WHERE id = ?", (variant_id,)
    ).fetchone()
    if variant is None:
        conn.close()
        return 0, "Format introuvable."

    agg = conn.execute(
        """SELECT COUNT(*) AS printed,
                  COALESCE(MAX(edition_number), 0) AS last
           FROM copies WHERE variant_id = ?""",
        (variant_id,),
    ).fetchone()
    printed, last = agg["printed"], agg["last"]
    remaining = variant["edition_size"] - printed

    if quantity < 1:
        conn.close()
        return 0, "La quantité doit être d'au moins 1."
    if quantity > remaining:
        conn.close()
        return 0, (f"Il ne reste que {remaining} emplacement(s) dans l'édition "
                   f"(édition de {variant['edition_size']}, {printed} déjà imprimé(s)).")

    with conn:
        for number in range(last + 1, last + 1 + quantity):
            conn.execute(
                """INSERT INTO copies (variant_id, edition_number, status, printed_date)
                   VALUES (?, ?, 'printed', ?)""",
                (variant_id, number, printed_date),
            )
    conn.close()
    return quantity, None


def sell_copy(copy_id, sold_date, sale_price, customer, channel):
    """Mark one copy sold. Returns an error message, or None on success."""
    conn = get_connection()
    copy = conn.execute(
        "SELECT status FROM copies WHERE id = ?", (copy_id,)
    ).fetchone()
    if copy is None:
        conn.close()
        return "Exemplaire introuvable."
    if copy["status"] == "sold":
        conn.close()
        return "Cet exemplaire est déjà marqué comme vendu."

    with conn:
        conn.execute(
            """UPDATE copies
               SET status = 'sold', sold_date = ?, sale_price = ?,
                   customer = ?, channel = ?
               WHERE id = ?""",
            (sold_date, sale_price, customer, channel, copy_id),
        )
    conn.close()
    return None


def unsell_copy(copy_id):
    """Revert a sale: put the copy back in stock and clear its sale details.

    Used to undo an accidental sale. Returns an error message, or None on success.
    """
    conn = get_connection()
    copy = conn.execute(
        "SELECT status FROM copies WHERE id = ?", (copy_id,)
    ).fetchone()
    if copy is None:
        conn.close()
        return "Exemplaire introuvable."
    if copy["status"] != "sold":
        conn.close()
        return "Cet exemplaire n'est pas vendu."

    with conn:
        conn.execute(
            """UPDATE copies
               SET status = 'printed', sold_date = NULL, sale_price = NULL,
                   customer = NULL, channel = NULL
               WHERE id = ?""",
            (copy_id,),
        )
    conn.close()
    return None


def update_artwork_image(artwork_id, image_path):
    """Set (or replace) the stored image filename for an artwork."""
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE artworks SET image_path = ? WHERE id = ?",
            (image_path, artwork_id),
        )
    conn.close()


# --------------------------------------------------------------------------
# Sales reporting
# --------------------------------------------------------------------------

def sales_totals():
    """Overall sold count and total revenue."""
    conn = get_connection()
    row = conn.execute(
        """SELECT COUNT(*) AS sold, COALESCE(SUM(sale_price), 0) AS revenue
           FROM copies WHERE status = 'sold'"""
    ).fetchone()
    conn.close()
    return row


def revenue_by_channel():
    """Sold count and revenue grouped by sales channel."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(TRIM(channel), ''), 'Non précisé') AS channel,
                  COUNT(*) AS sold,
                  COALESCE(SUM(sale_price), 0) AS revenue
           FROM copies WHERE status = 'sold'
           GROUP BY channel ORDER BY revenue DESC"""
    ).fetchall()
    conn.close()
    return rows


def revenue_by_month():
    """Sold count and revenue grouped by month (YYYY-MM), newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT substr(sold_date, 1, 7) AS month,
                  COUNT(*) AS sold,
                  COALESCE(SUM(sale_price), 0) AS revenue
           FROM copies
           WHERE status = 'sold' AND sold_date IS NOT NULL
           GROUP BY month ORDER BY month DESC"""
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# CSV export queries
# --------------------------------------------------------------------------

def sold_copies_detailed():
    """One row per sold copy, with artwork/variant context (for accounting)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.title AS artwork, a.year AS year,
                  v.size AS size, v.paper AS paper,
                  c.edition_number AS edition_number, v.edition_size AS edition_size,
                  c.sale_price AS sale_price, c.sold_date AS sold_date,
                  c.customer AS customer, c.channel AS channel
           FROM copies c
           JOIN variants v ON v.id = c.variant_id
           JOIN artworks a ON a.id = v.artwork_id
           WHERE c.status = 'sold'
           ORDER BY c.sold_date, a.title"""
    ).fetchall()
    conn.close()
    return rows


def all_copies_detailed():
    """One row per copy (sold or not), with context — for a full backup export."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.title AS artwork, a.year AS year,
                  v.size AS size, v.paper AS paper, v.price AS price,
                  c.edition_number AS edition_number, v.edition_size AS edition_size,
                  c.status AS status, c.printed_date AS printed_date,
                  c.sale_price AS sale_price, c.sold_date AS sold_date,
                  c.customer AS customer, c.channel AS channel
           FROM copies c
           JOIN variants v ON v.id = c.variant_id
           JOIN artworks a ON a.id = v.artwork_id
           ORDER BY a.title, v.size, c.edition_number"""
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Global parameters: papers, formats, expense categories
# --------------------------------------------------------------------------

def list_papers():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM papers ORDER BY name").fetchall()
    conn.close()
    return rows


def add_paper(name):
    conn = get_connection()
    try:
        with conn:
            conn.execute("INSERT INTO papers (name) VALUES (?)", (name,))
        return None
    except sqlite3.IntegrityError:
        return "Ce papier existe déjà."
    finally:
        conn.close()


def delete_paper(paper_id):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    conn.close()


def list_formats():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM formats ORDER BY name").fetchall()
    conn.close()
    return rows


def add_format(name, dimensions):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO formats (name, dimensions) VALUES (?, ?)",
                (name, dimensions),
            )
        return None
    except sqlite3.IntegrityError:
        return "Ce format existe déjà."
    finally:
        conn.close()


def delete_format(format_id):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM formats WHERE id = ?", (format_id,))
    conn.close()


def list_expense_categories():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM expense_categories ORDER BY name"
    ).fetchall()
    conn.close()
    return rows


def add_expense_category(name):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO expense_categories (name) VALUES (?)", (name,)
            )
        return None
    except sqlite3.IntegrityError:
        return "Cette catégorie existe déjà."
    finally:
        conn.close()


def delete_expense_category(category_id):
    conn = get_connection()
    with conn:
        conn.execute(
            "DELETE FROM expense_categories WHERE id = ?", (category_id,)
        )
    conn.close()


# --------------------------------------------------------------------------
# Print runs (Phase B): batch printing across editions, with a session cost
# --------------------------------------------------------------------------

def create_print_run(run_date, cost, note, items):
    """Print several editions at once.

    `items` is a list of (variant_id, quantity). Editions are auto-numbered
    continuing from their highest existing number, respecting the edition cap.
    An edition whose quantity exceeds its remaining slots is skipped with an
    error message (the rest still print).

    Returns (run_id, printed, errors):
      - run_id:  id of the created print_run, or None if nothing was printed
      - printed: list of dicts {variant_id, label, quantity, first, last}
      - errors:  list of human-readable messages for skipped editions
    """
    conn = get_connection()
    printed, errors = [], []

    for variant_id, quantity in items:
        if quantity < 1:
            continue
        variant = conn.execute(
            """SELECT v.edition_size, a.title, v.size
               FROM variants v JOIN artworks a ON a.id = v.artwork_id
               WHERE v.id = ?""", (variant_id,)
        ).fetchone()
        if variant is None:
            errors.append(f"Édition #{variant_id} introuvable.")
            continue
        agg = conn.execute(
            """SELECT COUNT(*) AS printed,
                      COALESCE(MAX(edition_number), 0) AS last
               FROM copies WHERE variant_id = ?""", (variant_id,)
        ).fetchone()
        remaining = variant["edition_size"] - agg["printed"]
        label = f"{variant['title']} — {variant['size']}"
        if quantity > remaining:
            errors.append(f"{label} : seulement {remaining} emplacement(s) "
                          f"restant(s), {quantity} demandé(s) — ignoré.")
            continue
        printed.append({
            "variant_id": variant_id, "label": label, "quantity": quantity,
            "first": agg["last"] + 1, "last": agg["last"] + quantity,
        })

    if not printed:
        conn.close()
        return None, printed, errors

    with conn:
        cur = conn.execute(
            "INSERT INTO print_runs (date, cost, note) VALUES (?, ?, ?)",
            (run_date, cost, note),
        )
        run_id = cur.lastrowid
        for p in printed:
            for number in range(p["first"], p["last"] + 1):
                conn.execute(
                    """INSERT INTO copies (variant_id, edition_number, status,
                                           printed_date)
                       VALUES (?, ?, 'printed', ?)""",
                    (p["variant_id"], number, run_date),
                )
            conn.execute(
                """INSERT INTO print_run_items
                       (print_run_id, variant_id, quantity, first_number, last_number)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, p["variant_id"], p["quantity"], p["first"], p["last"]),
            )
    conn.close()
    return run_id, printed, errors


def list_print_runs():
    """All print sessions, newest first, each with its itemised breakdown."""
    conn = get_connection()
    runs = conn.execute(
        "SELECT * FROM print_runs ORDER BY date DESC, id DESC"
    ).fetchall()
    result = []
    for run in runs:
        items = conn.execute(
            """SELECT pri.quantity, pri.first_number, pri.last_number,
                      a.title AS artwork, v.size AS size
               FROM print_run_items pri
               JOIN variants v ON v.id = pri.variant_id
               JOIN artworks a ON a.id = v.artwork_id
               WHERE pri.print_run_id = ?
               ORDER BY a.title, v.size""", (run["id"],)
        ).fetchall()
        result.append({
            "run": run,
            "lines": items,
            "total_copies": sum(it["quantity"] for it in items),
        })
    conn.close()
    return result


def print_costs_total():
    """Sum of all print-session costs (used in the profit report)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM print_runs"
    ).fetchone()
    conn.close()
    return row["total"]


# --------------------------------------------------------------------------
# Bulk selling: sell several editions at once at their own price
# --------------------------------------------------------------------------

def create_sale_batch(sold_date, channel, items):
    """Sell `quantity` in-stock copies for each edition, at the edition's price.

    `items` is a list of (variant_id, quantity). The lowest-numbered in-stock
    copies are sold first. If an edition has fewer in stock than requested,
    all available are sold and a message is returned for the shortfall.

    Returns (total_sold, summary, errors):
      - summary: list of dicts {label, count, price}
      - errors:  human-readable messages (e.g. not enough stock)
    """
    conn = get_connection()
    summary, errors = [], []
    with conn:
        for variant_id, quantity in items:
            if quantity < 1:
                continue
            variant = conn.execute(
                """SELECT v.price, v.size, a.title
                   FROM variants v JOIN artworks a ON a.id = v.artwork_id
                   WHERE v.id = ?""", (variant_id,)
            ).fetchone()
            if variant is None:
                errors.append(f"Édition #{variant_id} introuvable.")
                continue
            copies = conn.execute(
                """SELECT id FROM copies
                   WHERE variant_id = ? AND status = 'printed'
                   ORDER BY edition_number LIMIT ?""",
                (variant_id, quantity)
            ).fetchall()
            label = f"{variant['title']} — {variant['size']}"
            if len(copies) < quantity:
                errors.append(f"{label} : seulement {len(copies)} en stock, "
                              f"{quantity} demandé(s) — {len(copies)} vendu(s).")
            if not copies:
                continue
            price = variant["price"]
            for cp in copies:
                conn.execute(
                    """UPDATE copies
                       SET status = 'sold', sold_date = ?, sale_price = ?,
                           customer = NULL, channel = ?
                       WHERE id = ?""",
                    (sold_date, price, channel, cp["id"]),
                )
            summary.append({"label": label, "count": len(copies), "price": price})
    conn.close()
    return sum(s["count"] for s in summary), summary, errors


# --------------------------------------------------------------------------
# Expenses (Phase C)
# --------------------------------------------------------------------------

def add_expense(exp_date, category, amount, note):
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO expenses (date, category, amount, note) VALUES (?, ?, ?, ?)",
            (exp_date, category, amount, note),
        )
    conn.close()


def list_expenses():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM expenses ORDER BY date DESC, id DESC"
    ).fetchall()
    conn.close()
    return rows


def delete_expense(expense_id):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.close()


def expenses_total():
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses"
    ).fetchone()
    conn.close()
    return row["total"]


def expenses_by_category():
    conn = get_connection()
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(TRIM(category), ''), 'Non précisé') AS category,
                  COUNT(*) AS count,
                  COALESCE(SUM(amount), 0) AS total
           FROM expenses GROUP BY category ORDER BY total DESC"""
    ).fetchall()
    conn.close()
    return rows
