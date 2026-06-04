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

Your data lives in two places, both git-ignored (they are local data, not code):

- **`store.db`** — the database (artworks, editions, copies, sales).
- **`static/images/`** — uploaded artwork photos.

### One-click full backup (recommended)

On the **Rapport** page, click **"Télécharger la sauvegarde (.zip)"**. This
downloads `sauvegarde-stock-aquarelle-YYYY-MM-DD.zip` containing `store.db` and
all photos — a complete, restorable snapshot. Do this regularly and keep the
file somewhere safe (your PC, a USB key, cloud drive). The sales report also has
CSV exports (sales / full stock) for accounting, but those are *not* a full
restorable backup — the `.zip` is.

### Restoring from a backup

Unzip the backup, then:

- **On the live server (PythonAnywhere):** in the *Files* tab, upload `store.db`
  into `/home/khosta1/stock-aquarelle/` (overwrite the existing one) and upload
  the photos into `/home/khosta1/stock-aquarelle/static/images/`. Then click
  **Reload** in the Web tab.
- **Locally:** put `store.db` back in the project folder and the photos back in
  `static/images/`.

Because both files are git-ignored, `git pull`/redeploys never touch them — your
data is safe across code updates.

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

## Deploying to PythonAnywhere (recommended free host)

PythonAnywhere keeps a persistent disk on the free tier, so the SQLite
`store.db` survives restarts (unlike Render/Railway free tiers). HTTPS is
included. Repo: <https://github.com/khosta1/stock-aquarelle>

1. **Create a free account** at <https://www.pythonanywhere.com> ("Beginner").
2. **Open a Bash console** (Consoles → Bash) and pull the code:
   ```bash
   git clone https://github.com/khosta1/stock-aquarelle.git
   cd stock-aquarelle
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Web tab** → *Add a new web app* → *Manual configuration* → pick the same
   Python 3 version as the venv.
4. Set, in the Web tab:
   - **Source code:** `/home/khosta1/stock-aquarelle`
   - **Virtualenv:** `/home/khosta1/stock-aquarelle/venv`
   - **Static files mapping:** URL `/static/` →
     `/home/khosta1/stock-aquarelle/static`
5. **Edit the WSGI file** (link in the Web tab) — replace its contents with:
   ```python
   import os
   import sys

   path = "/home/khosta1/stock-aquarelle"
   if path not in sys.path:
       sys.path.insert(0, path)

   # Secrets live HERE (this file is private, not in the git repo):
   os.environ["WATERCOLOR_SECRET"] = "<a-long-random-string>"
   os.environ["WATERCOLOR_PASSWORD"] = "<your-login-password>"
   os.environ["WATERCOLOR_HTTPS"] = "1"

   from app import app as application  # noqa: E402
   ```
6. Click **Reload** → visit `https://khosta1.pythonanywhere.com`.

### Updating the live site later

```bash
# in the PythonAnywhere Bash console:
cd stock-aquarelle && git pull
```
then click **Reload** in the Web tab. Your `store.db` is untouched by `git pull`
(it's git-ignored), so live data is safe across updates.