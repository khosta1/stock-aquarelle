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