# Watercolor Stock

A small local web app to track stock of limited-edition watercolor art prints:
how many of each have been **printed** and **sold**, per size/edition.

## What it tracks

- **Artworks** — your designs.
- **Variants** — a size/paper version of an artwork; each is its own limited edition.
- **Copies** — individual numbered prints (e.g. #7 of 50). A copy is `printed` (in stock)
  or `sold`, and carries the sale price, customer, and channel once sold.

Stock numbers are always *calculated* from copies, never stored directly:

| Number | How it's computed (per variant) |
|--------|---------------------------------|
| Printed | count of copies |
| Sold | count of copies with status `sold` |
| In stock | printed − sold |
| Edition remaining | edition_size − printed |
| Revenue | sum of `sale_price` over sold copies |

## Requirements

- Python 3.10+ (tested on 3.14)

## Setup (first time)

From this folder:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```powershell
venv\Scripts\activate
python app.py
```

Then open <http://localhost:5000>. The database file `store.db` is created
automatically on first run.

## Backups

Your data lives in a single file: **`store.db`**. To back up, just copy it
somewhere safe. To restore, copy it back.

## Hosting it online later

This runs on the Flask development server, which is fine for local single-user use.
To host it on the internet (e.g. a VPS or a Python host like Render / PythonAnywhere):

1. Run it behind a production server, not `python app.py`. A ready-to-use
   script is included — `python serve.py` runs the same app under **Waitress**
   (Windows-friendly). On a Linux host you could instead use
   `gunicorn app:app`. No code changes are needed either way.
2. Set environment variables instead of the defaults:
   - `WATERCOLOR_SECRET` — a long random secret key (signs the login session).
   - `WATERCOLOR_PASSWORD` — the password for logging into the app.
   - `WATERCOLOR_DB` — path to the database file.
   - `WATERCOLOR_HTTPS=1` — when served over HTTPS, so the session cookie is
     marked secure.
3. The app is protected by a single-password login (see `WATERCOLOR_PASSWORD`).
   Serve it over HTTPS (the host usually provides this) before exposing it
   publicly. Locally over plain http it still works; just leave
   `WATERCOLOR_HTTPS` unset.

Note: Hostinger's cheap *shared* hosting is for PHP/MySQL and won't run Flask;
you'd need their VPS plan or a Python-friendly host.