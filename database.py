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
    """Create tables if needed, then apply small migrations. Safe every start."""
    with get_connection() as conn:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        _migrate(conn)


def _migrate(conn):
    """Idempotent migrations for columns added after first release."""
    invoice_cols = [r["name"] for r in conn.execute("PRAGMA table_info(invoices)")]
    if invoice_cols and "commission_pct" not in invoice_cols:
        conn.execute(
            "ALTER TABLE invoices ADD COLUMN commission_pct REAL NOT NULL DEFAULT 45"
        )


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

def list_artworks_with_variants(query=None):
    """Every artwork with its variants (each carrying computed stock numbers).

    If `query` is given, only artworks whose title matches are returned.
    """
    conn = get_connection()
    if query:
        artworks = conn.execute(
            "SELECT * FROM artworks WHERE title LIKE ? ORDER BY title",
            (f"%{query}%",),
        ).fetchall()
    else:
        artworks = conn.execute(
            "SELECT * FROM artworks ORDER BY title"
        ).fetchall()
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


def update_artwork(artwork_id, title, year):
    """Edit an artwork's title and year."""
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE artworks SET title = ?, year = ? WHERE id = ?",
            (title, year, artwork_id),
        )
    conn.close()


def delete_artwork(artwork_id):
    """Delete an artwork and everything under it (editions, copies cascade).

    Returns the artwork's image filename (if any) so the caller can remove it.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT image_path FROM artworks WHERE id = ?", (artwork_id,)
    ).fetchone()
    with conn:
        conn.execute("DELETE FROM artworks WHERE id = ?", (artwork_id,))
    conn.close()
    return row["image_path"] if row else None


def update_variant(variant_id, size, paper, price, edition_size):
    """Edit an edition. Returns an error message, or None on success.

    The edition size cannot be set below the number already printed.
    Already-sold copies keep the price recorded at sale time; the new price
    only affects future sales.
    """
    conn = get_connection()
    printed = conn.execute(
        "SELECT COUNT(*) AS n FROM copies WHERE variant_id = ?", (variant_id,)
    ).fetchone()["n"]
    if edition_size < printed:
        conn.close()
        return (f"La taille d'édition ne peut pas être inférieure au nombre "
                f"déjà imprimé ({printed}).")
    with conn:
        conn.execute(
            """UPDATE variants
               SET size = ?, paper = ?, price = ?, edition_size = ?
               WHERE id = ?""",
            (size, paper, price, edition_size, variant_id),
        )
    conn.close()
    return None


def delete_variant(variant_id):
    """Delete an edition and its copies (cascade)."""
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM variants WHERE id = ?", (variant_id,))
    conn.close()


# --------------------------------------------------------------------------
# Sales reporting
# --------------------------------------------------------------------------

def _date_filter(column, start, end):
    """Return (sql_fragment, params) for an optional inclusive date range.

    The fragment starts with ' AND ' so it appends after an existing WHERE.
    Dates are 'YYYY-MM-DD' strings, compared lexicographically.
    """
    frag, params = "", []
    if start:
        frag += f" AND {column} >= ?"
        params.append(start)
    if end:
        frag += f" AND {column} <= ?"
        params.append(end)
    return frag, params


def sales_totals(start=None, end=None):
    """Overall sold count and total revenue (optionally within a date range)."""
    frag, params = _date_filter("sold_date", start, end)
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS sold, COALESCE(SUM(sale_price), 0) AS revenue "
        "FROM copies WHERE status = 'sold'" + frag, params
    ).fetchone()
    conn.close()
    return row


def revenue_by_channel(start=None, end=None):
    """Sold count and revenue grouped by sales channel."""
    frag, params = _date_filter("sold_date", start, end)
    conn = get_connection()
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(channel), ''), 'Non précisé') AS channel, "
        "COUNT(*) AS sold, COALESCE(SUM(sale_price), 0) AS revenue "
        "FROM copies WHERE status = 'sold'" + frag +
        " GROUP BY channel ORDER BY revenue DESC", params
    ).fetchall()
    conn.close()
    return rows


def revenue_by_month(start=None, end=None):
    """Sold count and revenue grouped by month (YYYY-MM), newest first."""
    frag, params = _date_filter("sold_date", start, end)
    conn = get_connection()
    rows = conn.execute(
        "SELECT substr(sold_date, 1, 7) AS month, COUNT(*) AS sold, "
        "COALESCE(SUM(sale_price), 0) AS revenue FROM copies "
        "WHERE status = 'sold' AND sold_date IS NOT NULL" + frag +
        " GROUP BY month ORDER BY month DESC", params
    ).fetchall()
    conn.close()
    return rows


def best_sellers(start=None, end=None, limit=10):
    """Top editions by revenue (optionally within a date range)."""
    frag, params = _date_filter("c.sold_date", start, end)
    conn = get_connection()
    rows = conn.execute(
        "SELECT a.title AS artwork, v.size AS size, COUNT(*) AS sold, "
        "COALESCE(SUM(c.sale_price), 0) AS revenue "
        "FROM copies c JOIN variants v ON v.id = c.variant_id "
        "JOIN artworks a ON a.id = v.artwork_id "
        "WHERE c.status = 'sold'" + frag +
        " GROUP BY c.variant_id ORDER BY revenue DESC, sold DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()
    return rows


def profit_by_artwork(start=None, end=None):
    """Per-artwork profit = its sales revenue − its share of print costs.

    A print session's cost is split across the editions it printed, in
    proportion to quantity. General expenses (frames, fuel, VAT…) are NOT
    attributed to a single artwork, so they are excluded here.
    """
    rev_frag, rev_params = _date_filter("c.sold_date", start, end)
    cost_frag, cost_params = _date_filter("pr.date", start, end)
    conn = get_connection()
    arts = conn.execute("SELECT id, title FROM artworks ORDER BY title").fetchall()

    revenue = {r["artwork_id"]: r for r in conn.execute(
        "SELECT v.artwork_id AS artwork_id, COUNT(*) AS sold, "
        "COALESCE(SUM(c.sale_price), 0) AS revenue "
        "FROM copies c JOIN variants v ON v.id = c.variant_id "
        "WHERE c.status = 'sold'" + rev_frag +
        " GROUP BY v.artwork_id", rev_params).fetchall()}

    cost = {r["artwork_id"]: r["print_cost"] for r in conn.execute(
        "SELECT v.artwork_id AS artwork_id, "
        "COALESCE(SUM(pr.cost * pri.quantity * 1.0 / rt.total_qty), 0) AS print_cost "
        "FROM print_run_items pri "
        "JOIN print_runs pr ON pr.id = pri.print_run_id "
        "JOIN variants v ON v.id = pri.variant_id "
        "JOIN (SELECT print_run_id, SUM(quantity) AS total_qty "
        "      FROM print_run_items GROUP BY print_run_id) rt "
        "     ON rt.print_run_id = pri.print_run_id "
        "WHERE 1=1" + cost_frag +
        " GROUP BY v.artwork_id", cost_params).fetchall()}

    conn.close()
    result = []
    for a in arts:
        r = revenue.get(a["id"])
        rev = r["revenue"] if r else 0
        sold = r["sold"] if r else 0
        pc = cost.get(a["id"], 0) or 0
        result.append({
            "title": a["title"], "sold": sold, "revenue": rev,
            "print_cost": pc, "profit": rev - pc,
        })
    result.sort(key=lambda x: x["profit"], reverse=True)
    return result


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
# App settings (key/value) — seller / billing info
# --------------------------------------------------------------------------

def get_settings():
    """All app settings as a dict (key -> value)."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def save_settings(values):
    """Upsert a dict of key -> value."""
    conn = get_connection()
    with conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    conn.close()


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

def uninvoiced_sold_copies():
    """Sold copies not yet on any invoice, with context for selection."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT c.id, c.edition_number, c.sale_price, c.sold_date,
                  c.customer, c.channel, v.size, v.edition_size,
                  a.title AS artwork
           FROM copies c
           JOIN variants v ON v.id = c.variant_id
           JOIN artworks a ON a.id = v.artwork_id
           WHERE c.status = 'sold'
             AND c.id NOT IN (SELECT copy_id FROM invoice_items
                              WHERE copy_id IS NOT NULL)
           ORDER BY c.sold_date DESC, a.title"""
    ).fetchall()
    conn.close()
    return rows


def create_invoice(inv_date, customer_name, customer_address, note, copy_ids,
                   commission_pct=45):
    """Create an invoice from selected sold copies. Returns (id, number, error).

    Skips copies that aren't sold or are already invoiced. Lines snapshot the
    copy details so the invoice stays fixed even if data changes later.
    """
    conn = get_connection()
    valid = []
    for cid in copy_ids:
        row = conn.execute(
            """SELECT c.id, c.edition_number, c.sale_price,
                      v.size, v.edition_size, a.title
               FROM copies c
               JOIN variants v ON v.id = c.variant_id
               JOIN artworks a ON a.id = v.artwork_id
               WHERE c.id = ? AND c.status = 'sold'""", (cid,)
        ).fetchone()
        if row is None:
            continue
        taken = conn.execute(
            "SELECT 1 FROM invoice_items WHERE copy_id = ?", (cid,)
        ).fetchone()
        if taken:
            continue
        valid.append(row)

    if not valid:
        conn.close()
        return None, None, "Aucun exemplaire facturable sélectionné."

    year = (inv_date or "")[:4] or "0000"
    existing = conn.execute(
        "SELECT number FROM invoices WHERE number LIKE ?", (f"{year}-%",)
    ).fetchall()
    max_seq = 0
    for r in existing:
        try:
            max_seq = max(max_seq, int(r["number"].split("-")[1]))
        except (ValueError, IndexError):
            pass
    number = f"{year}-{max_seq + 1:03d}"

    with conn:
        cur = conn.execute(
            """INSERT INTO invoices
                   (number, date, customer_name, customer_address, note,
                    commission_pct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (number, inv_date, customer_name, customer_address, note,
             commission_pct),
        )
        invoice_id = cur.lastrowid
        for row in valid:
            conn.execute(
                """INSERT INTO invoice_items
                       (invoice_id, copy_id, designation, edition_no, unit_price)
                   VALUES (?, ?, ?, ?, ?)""",
                (invoice_id, row["id"],
                 f"{row['title']} — {row['size']}",
                 f"{row['edition_number']}/{row['edition_size']}",
                 row["sale_price"] or 0),
            )
    conn.close()
    return invoice_id, number, None


def get_invoice(invoice_id):
    """One invoice with its lines (key 'lines') and computed total."""
    conn = get_connection()
    inv = conn.execute(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
    ).fetchone()
    if inv is None:
        conn.close()
        return None
    items = conn.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY id",
        (invoice_id,),
    ).fetchall()
    conn.close()
    data = dict(inv)
    data["lines"] = items
    data["total"] = sum(it["unit_price"] for it in items)
    pct = data.get("commission_pct") or 0
    data["commission_amount"] = data["total"] * pct / 100
    data["net_amount"] = data["total"] - data["commission_amount"]
    return data


def update_invoice_commission(invoice_id, commission_pct):
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE invoices SET commission_pct = ? WHERE id = ?",
            (commission_pct, invoice_id),
        )
    conn.close()


def delete_invoice(invoice_id):
    """Delete an invoice; its lines cascade, freeing the copies to be re-invoiced."""
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.close()


def list_invoices():
    """All invoices, newest first, with line count and total."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT inv.*, COUNT(it.id) AS line_count,
                  COALESCE(SUM(it.unit_price), 0) AS total
           FROM invoices inv
           LEFT JOIN invoice_items it ON it.invoice_id = inv.id
           GROUP BY inv.id
           ORDER BY inv.date DESC, inv.number DESC"""
    ).fetchall()
    conn.close()
    return rows


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


def print_costs_total(start=None, end=None):
    """Sum of print-session costs (optionally within a date range)."""
    frag, params = _date_filter("date", start, end)
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM print_runs WHERE 1=1" + frag,
        params
    ).fetchone()
    conn.close()
    return row["total"]


# --------------------------------------------------------------------------
# Bulk selling: sell several editions at once at their own price
# --------------------------------------------------------------------------

def create_sale_batch(sold_date, channel, items):
    """Sell `quantity` in-stock copies for each edition.

    `items` is a list of (variant_id, quantity, price). If price is None, the
    edition's own price is used; otherwise the given price overrides it (useful
    when the same print sells for different prices depending on the venue).
    Lowest-numbered in-stock copies are sold first; if fewer are in stock than
    requested, all available are sold and a message reports the shortfall.

    Returns (total_sold, summary, errors):
      - summary: list of dicts {label, count, price}
      - errors:  human-readable messages (e.g. not enough stock)
    """
    conn = get_connection()
    summary, errors = [], []
    with conn:
        for variant_id, quantity, price_override in items:
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
            price = price_override if price_override is not None else variant["price"]
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


def expenses_total(start=None, end=None):
    frag, params = _date_filter("date", start, end)
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE 1=1" + frag,
        params
    ).fetchone()
    conn.close()
    return row["total"]


def expenses_by_category(start=None, end=None):
    frag, params = _date_filter("date", start, end)
    conn = get_connection()
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(category), ''), 'Non précisé') AS category, "
        "COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total "
        "FROM expenses WHERE 1=1" + frag +
        " GROUP BY category ORDER BY total DESC", params
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Dashboard summary + low-stock view
# --------------------------------------------------------------------------

def dashboard_summary():
    """Headline numbers for the dashboard tiles."""
    conn = get_connection()
    artworks = conn.execute("SELECT COUNT(*) AS c FROM artworks").fetchone()["c"]
    variants = conn.execute("SELECT COUNT(*) AS c FROM variants").fetchone()["c"]
    in_stock = conn.execute(
        "SELECT COUNT(*) AS c FROM copies WHERE status = 'printed'"
    ).fetchone()["c"]
    stock_value = conn.execute(
        """SELECT COALESCE(SUM(v.price), 0) AS t
           FROM copies c JOIN variants v ON v.id = c.variant_id
           WHERE c.status = 'printed'"""
    ).fetchone()["t"]
    month = conn.execute(
        """SELECT COUNT(*) AS c, COALESCE(SUM(sale_price), 0) AS r
           FROM copies
           WHERE status = 'sold'
             AND substr(sold_date, 1, 7) = strftime('%Y-%m', 'now', 'localtime')"""
    ).fetchone()
    conn.close()
    return {
        "artworks": artworks, "variants": variants, "in_stock": in_stock,
        "stock_value": stock_value,
        "month_sold": month["c"], "month_revenue": month["r"],
    }


def low_stock_variants(threshold):
    """Editions whose stock is at or below `threshold` and that can still be
    printed (edition not yet fully printed). Lowest stock first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT v.id, v.size, v.paper, v.edition_size, a.title AS artwork_title,
                  COUNT(c.id) AS printed,
                  COALESCE(SUM(CASE WHEN c.status = 'sold' THEN 1 END), 0) AS sold
           FROM variants v
           JOIN artworks a ON a.id = v.artwork_id
           LEFT JOIN copies c ON c.variant_id = v.id
           GROUP BY v.id
           HAVING (COUNT(c.id)
                   - COALESCE(SUM(CASE WHEN c.status = 'sold' THEN 1 END), 0)) <= ?
              AND (v.edition_size - COUNT(c.id)) > 0
           ORDER BY (COUNT(c.id)
                     - COALESCE(SUM(CASE WHEN c.status = 'sold' THEN 1 END), 0)) ASC,
                    a.title""",
        (threshold,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["in_stock"] = d["printed"] - d["sold"]
        d["remaining"] = d["edition_size"] - d["printed"]
        result.append(d)
    return result
