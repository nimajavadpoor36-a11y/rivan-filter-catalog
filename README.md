# Filter Finder

Searchable filter catalogue: public search page + password-protected admin page.
Runs as a website, installs as an app from the browser (PWA), and as a desktop app.

## Run it
    pip install -r requirements.txt
    python app.py                 # http://127.0.0.1:5000  (admin: /admin)

On the first run a random admin password is printed in the console and saved in
`data/INITIAL_ADMIN_PASSWORD.txt`. Change it under Admin > Settings, then delete that file.
To set the password yourself, start with the environment variable `ADMIN_PASSWORD`.

## Your logo
Admin > Settings > Logo. It is used in the header, the browser icon and the installed app icon.

## Put it on the internet
    gunicorn -w 2 -b 127.0.0.1:8000 app:app
Put nginx (or Caddy) in front with HTTPS, and set `FILTERFINDER_HTTPS=1` so the login cookie is secure.
Let nginx serve `/uploads/` straight from the `uploads` folder so Python never handles image traffic:

    location /uploads/ { alias /path/to/filterfinder/uploads/; expires 1y; }

Back up two things: the `data` folder (database + settings) and the `uploads` folder.

## Install as an app
- Phones and tablets, Windows, macOS, Linux, ChromeOS: open the site in Chrome/Edge/Safari and choose
  "Install app" / "Add to Home Screen". (Needs HTTPS on a real server.)
- Desktop window: `python desktop.py` (own local database) or `python desktop.py --url https://your-site`
  (uses your hosted data). See the top of `desktop.py` for building a single executable with PyInstaller.

## Where things are
- `app.py`: server, database (SQLite), searches, admin API
- `templates/`, `static/`: pages, styles, scripts
- `uploads/`: filter images (resized to 900 px, WebP) and the logo
