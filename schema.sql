-- Watercolor Stock — database schema
-- Run automatically on first start (see database.py).
-- Safe to re-run: every table uses CREATE TABLE IF NOT EXISTS.

-- Artworks: one row per print design.
CREATE TABLE IF NOT EXISTS artworks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    year        INTEGER,
    image_path  TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Variants: a sellable version of an artwork (size / paper).
-- Each variant is its OWN limited edition, so edition_size lives here.
CREATE TABLE IF NOT EXISTS variants (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artwork_id    INTEGER NOT NULL,
    size          TEXT    NOT NULL,
    paper         TEXT,
    price         REAL    NOT NULL DEFAULT 0,
    edition_size  INTEGER NOT NULL,
    FOREIGN KEY (artwork_id) REFERENCES artworks (id) ON DELETE CASCADE
);

-- Copies: one physical numbered print — the heart of the app.
-- A copy is 'printed' (in stock) or 'sold'. Sale details live here
-- because a numbered copy is sold at most once.
CREATE TABLE IF NOT EXISTS copies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id      INTEGER NOT NULL,
    edition_number  INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'printed'
                            CHECK (status IN ('printed', 'sold')),
    printed_date    TEXT,
    sold_date       TEXT,
    sale_price      REAL,
    customer        TEXT,
    channel         TEXT,
    FOREIGN KEY (variant_id) REFERENCES variants (id) ON DELETE CASCADE,
    -- no two copies of the same variant can share an edition number
    UNIQUE (variant_id, edition_number)
);

-- ==========================================================================
-- Global parameters (Phase A): reusable lookup lists chosen from dropdowns.
-- Editions/expenses store the chosen NAME as text, so deleting an item here
-- never breaks existing records — it just removes it from future menus.
-- ==========================================================================

-- Paper types (e.g. "Mat 300g").
CREATE TABLE IF NOT EXISTS papers (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- Formats / sizes (e.g. name "A3", dimensions "29,7 × 42 cm").
CREATE TABLE IF NOT EXISTS formats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    dimensions TEXT
);

-- Expense categories (e.g. "Cadre", "Essence", "TVA").
CREATE TABLE IF NOT EXISTS expense_categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- ==========================================================================
-- Printing (Phase B): a print session has a total cost and prints copies
-- across one or more editions at once.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS print_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT,
    cost       REAL NOT NULL DEFAULT 0,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- What each session printed, per edition (variant), and which numbers.
CREATE TABLE IF NOT EXISTS print_run_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    print_run_id  INTEGER NOT NULL,
    variant_id    INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    first_number  INTEGER,
    last_number   INTEGER,
    FOREIGN KEY (print_run_id) REFERENCES print_runs (id) ON DELETE CASCADE,
    FOREIGN KEY (variant_id) REFERENCES variants (id) ON DELETE CASCADE
);

-- ==========================================================================
-- Other expenses (Phase C): frames, fuel, exhibition fees, VAT, etc.
-- The category name is stored as text (chosen from expense_categories).
-- ==========================================================================
CREATE TABLE IF NOT EXISTS expenses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT,
    category   TEXT,
    amount     REAL NOT NULL DEFAULT 0,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ==========================================================================
-- App settings (key/value): seller/billing info shown on invoices.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ==========================================================================
-- Invoices (factures). Lines snapshot the sold copy so the invoice stays a
-- faithful record even if the copy/price changes later.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS invoices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    number           TEXT NOT NULL UNIQUE,
    date             TEXT,
    customer_name    TEXT,
    customer_address TEXT,
    note             TEXT,
    commission_pct   REAL NOT NULL DEFAULT 45,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL,
    copy_id      INTEGER,        -- which sold copy, to prevent double-invoicing
    designation  TEXT,           -- snapshot, e.g. "Coucher de soleil — A3"
    edition_no   TEXT,           -- snapshot, e.g. "7/50"
    unit_price   REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
);