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
