"""Filter Finder - searchable filter catalogue with an admin area.

Run:  python app.py            (development)
      gunicorn app:app         (production)
"""
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, make_response, redirect, render_template,
                   request, send_file, send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from PIL import Image, ImageOps
except ImportError:  # images are then stored as uploaded
    Image = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FILTERFINDER_DATA", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.environ.get("FILTERFINDER_UPLOADS", BASE_DIR / "uploads"))
DB_PATH = DATA_DIR / "filters.db"
CONFIG_PATH = DATA_DIR / "config.json"
for _d in (DATA_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

FILTER_TYPES = [
    ("oil", "Oil"),
    ("air", "Air"),
    ("diesel", "Diesel"),
    ("diesel_water_separator", "Diesel water separator"),
    ("hydraulic", "Hydraulic"),
    ("transmission", "Transmission"),
    ("air_dryer", "Air dryer"),
    ("separator", "Separator"),
    ("micro_filter", "Micro-filter"),
    ("radiator_water", "Radiator water"),
]
TYPE_KEYS = {k for k, _ in FILTER_TYPES}
TYPE_LABELS = dict(FILTER_TYPES)

# Offered in the admin form; the search dropdown lists only threads that exist in the data.
COMMON_THREADS = [
    "M14x1.5", "M16x1.5", "M18x1.5", "M20x1.5", "M22x1.5", "M24x1.5", "M26x1.5",
    "M27x2", "M30x1.5", "M32x1.5", "M35x1.5", "M36x2", "M38x1.5", "M42x2",
    "3/8-24 UNF", "1/2-20 UNF", "5/8-18 UNF", "3/4-16 UNF", "13/16-16 UN",
    "7/8-14 UNF", "1-12 UN", "1 1/8-16 UN", "1 3/16-12 UN", "1 1/2-16 UN",
    "3/4-14 NPT", "1/2-14 NPT", "G 1/2", "G 3/4", "G 1",
]

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_RESULTS = 200

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FILTERFINDER_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
)


# --------------------------------------------------------------------------- config
def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    return {}


def save_config(cfg):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), "utf-8")
    tmp.replace(CONFIG_PATH)


def bootstrap_config():
    cfg = load_config()
    changed = False
    if "secret_key" not in cfg:
        cfg["secret_key"] = secrets.token_hex(32)
        changed = True
    if "site_name" not in cfg:
        cfg["site_name"] = "Filter catalogue"
        changed = True
    if "admin_hash" not in cfg and not os.environ.get("ADMIN_PASSWORD"):
        pw = secrets.token_urlsafe(9)
        cfg["admin_hash"] = generate_password_hash(pw)
        note = DATA_DIR / "INITIAL_ADMIN_PASSWORD.txt"
        note.write_text(f"Initial admin password: {pw}\nChange it in Admin > Settings, then delete this file.\n")
        print(f"\n  First run. Admin password: {pw}\n  (also saved in {note})\n")
        changed = True
    if changed:
        save_config(cfg)
    return cfg


CONFIG = bootstrap_config()
app.secret_key = os.environ.get("SECRET_KEY") or CONFIG["secret_key"]


# --------------------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS filters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,
    part_number  TEXT NOT NULL,
    part_norm    TEXT NOT NULL,
    brand        TEXT NOT NULL DEFAULT '',
    height_mm    REAL,
    diameter_mm  REAL,
    mouth_mm     REAL,
    thread       TEXT NOT NULL DEFAULT '',
    equivalents  TEXT NOT NULL DEFAULT '',
    machines     TEXT NOT NULL DEFAULT '',
    flange_od_mm REAL,
    flange_holes INTEGER,
    image        TEXT,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filters_type ON filters(type);
CREATE INDEX IF NOT EXISTS idx_filters_part ON filters(part_norm);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA)


# --------------------------------------------------------------------------- helpers
def norm_part(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def split_list(s):
    return [p.strip() for p in re.split(r"[\n,;]+", s or "") if p.strip()]


def words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def fmt_row(r, **extra):
    d = {
        "id": r["id"],
        "type": r["type"],
        "type_label": TYPE_LABELS.get(r["type"], r["type"]),
        "part_number": r["part_number"],
        "brand": r["brand"],
        "height_mm": r["height_mm"],
        "diameter_mm": r["diameter_mm"],
        "mouth_mm": r["mouth_mm"],
        "thread": r["thread"],
        "equivalents": split_list(r["equivalents"]),
        "machines": split_list(r["machines"]),
        "flange_od_mm": r["flange_od_mm"],
        "flange_holes": r["flange_holes"],
        "notes": r["notes"],
        "image_url": f"/uploads/{r['image']}" if r["image"] else None,
    }
    d.update(extra)
    return d


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# --------------------------------------------------------------------------- images / logo
def process_image(file_storage, max_side=900, keep_alpha=False):
    """Validate and store an uploaded image in UPLOAD_DIR; returns the stored file name."""
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError("Images must be JPG, PNG, WebP or GIF.")
    raw = file_storage.read()
    if not raw:
        raise ValueError("The image file is empty.")
    name = uuid.uuid4().hex
    if Image is None:
        (UPLOAD_DIR / f"{name}{ext}").write_bytes(raw)
        return f"{name}{ext}"
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("That file is not a valid image.")
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_side, max_side))
    if keep_alpha:
        out_name = f"{name}.png"
        img.convert("RGBA").save(UPLOAD_DIR / out_name, "PNG", optimize=True)
    else:
        out_name = f"{name}.webp"
        has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
        img.convert("RGBA" if has_alpha else "RGB").save(UPLOAD_DIR / out_name, "WEBP", quality=82)
    return out_name


def remove_upload(name):
    if name:
        try:
            (UPLOAD_DIR / Path(name).name).unlink()
        except FileNotFoundError:
            pass


def current_logo_path():
    name = load_config().get("logo")
    if name and (UPLOAD_DIR / name).exists():
        return UPLOAD_DIR / name
    return BASE_DIR / "static" / "placeholder-logo.png"


# --------------------------------------------------------------------------- auth
_attempts = {}  # ip -> (failures, first_failure_time)


def is_locked(ip):
    f = _attempts.get(ip)
    if not f:
        return False
    if time.time() - f[1] > 300:
        _attempts.pop(ip, None)
        return False
    return f[0] >= 5


def password_ok(pw):
    env_pw = os.environ.get("ADMIN_PASSWORD")
    if env_pw:
        return secrets.compare_digest(pw.encode(), env_pw.encode())
    h = load_config().get("admin_hash")
    return bool(h) and check_password_hash(h, pw)


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return jsonify(error="Please sign in."), 401
        if request.method in ("POST", "PUT", "DELETE"):
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not secrets.compare_digest(token, session.get("csrf", "")):
                return jsonify(error="Your session expired. Reload the page."), 403
        return fn(*a, **kw)
    return wrapper


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="The file is too large (12 MB maximum)."), 413


# --------------------------------------------------------------------------- pages
@app.get("/")
def index():
    return render_template("index.html", site_name=load_config().get("site_name", ""),
                           logo_v=int(current_logo_path().stat().st_mtime))


@app.get("/admin")
def admin_page():
    return render_template("admin.html", site_name=load_config().get("site_name", ""),
                           logo_v=int(current_logo_path().stat().st_mtime))


@app.get("/logo")
def logo():
    return send_file(current_logo_path(), max_age=3600)


@app.get("/icon/<int:size>.png")
def icon(size):
    size = size if size in (192, 512) else 192
    src = current_logo_path()
    if Image is None:
        return send_file(src)
    img = Image.open(src).convert("RGBA")
    inner = int(size * 0.78)
    img.thumbnail((inner, inner))
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=3600)


@app.get("/manifest.webmanifest")
def manifest():
    name = load_config().get("site_name", "Filter catalogue")
    body = {
        "name": name, "short_name": name[:12], "start_url": "/", "display": "standalone",
        "background_color": "#EDF0F1", "theme_color": "#0F4C5C",
        "icons": [
            {"src": "/icon/192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon/512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    resp = make_response(json.dumps(body))
    resp.mimetype = "application/manifest+json"
    return resp


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(BASE_DIR / "static", "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/uploads/<path:name>")
def uploads(name):
    # File names are random, so they can be cached forever. In production, let nginx serve /uploads directly.
    return send_from_directory(UPLOAD_DIR, name, max_age=31536000)


# --------------------------------------------------------------------------- public API
@app.get("/api/meta")
def api_meta():
    db = get_db()
    threads = sorted({r[0] for r in db.execute("SELECT DISTINCT thread FROM filters WHERE thread != ''")},
                     key=natural_key)
    return jsonify(types=[{"key": k, "label": v} for k, v in FILTER_TYPES], threads=threads,
                   count=db.execute("SELECT COUNT(*) FROM filters").fetchone()[0])


@app.get("/api/search/vehicle")
def search_vehicle():
    """Fuzzy search on the compatible machinery list. Typos, spacing and partial names are fine."""
    q = request.args.get("q", "").strip()
    qt = words(q)
    if not qt:
        return jsonify(results=[])
    joined = "".join(qt)
    scored = []
    for r in get_db().execute("SELECT * FROM filters WHERE machines != ''"):
        best, best_line = 0.0, ""
        for line in split_list(r["machines"]):
            lw = words(line)
            ln = "".join(lw)
            if not ln:
                continue
            if joined in ln:
                s = 1.0
            else:
                total = 0.0
                for t in qt:
                    if t in lw:
                        total += 1.0
                    elif t in ln:
                        total += 0.9
                    else:
                        ratio = max(SequenceMatcher(None, t, w).ratio() for w in lw)
                        if ratio >= 0.75:
                            total += ratio * 0.85
                        else:
                            total = -1
                            break
                s = total / len(qt) if total >= 0 else 0.0
            if s > best:
                best, best_line = s, line
        if best > 0:
            scored.append((best, r, best_line))
    scored.sort(key=lambda x: (-x[0], x[1]["type"], x[1]["part_number"]))
    return jsonify(results=[fmt_row(r, match_note=f"Fits {line}") for _, r, line in scored[:MAX_RESULTS]])


@app.get("/api/search/part")
def search_part():
    """Part number search. Ignores case, dashes and spaces; also checks equivalent numbers."""
    qn = norm_part(request.args.get("q", ""))
    if len(qn) < 2:
        return jsonify(results=[])
    scored = []
    for r in get_db().execute("SELECT * FROM filters"):
        cands = [(r["part_norm"], None)] + [(norm_part(e), e) for e in split_list(r["equivalents"])]
        best, note = 0.0, None
        for cn, original in cands:
            if not cn:
                continue
            if cn == qn:
                s = 1.0
            elif cn.startswith(qn):
                s = 0.9
            elif qn in cn:
                s = 0.8
            elif len(qn) >= 4:
                ratio = SequenceMatcher(None, qn, cn).ratio()
                s = ratio * 0.7 if ratio >= 0.75 else 0.0
            else:
                s = 0.0
            if original:
                s -= 0.01  # prefer a direct part-number hit over an equivalent
            if s > best:
                best, note = s, original
        if best > 0:
            scored.append((best, r, f"Matches equivalent number {note}" if note else None))
    scored.sort(key=lambda x: (-x[0], x[1]["part_number"]))
    return jsonify(results=[fmt_row(r, match_note=n) for _, r, n in scored[:MAX_RESULTS]])


@app.get("/api/search/specs")
def search_specs():
    a = request.args
    where, params = [], []

    def num(name):
        v = a.get(name, "").strip().replace(",", ".")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    tol = num("tol") or 0.0
    if a.get("type") in TYPE_KEYS:
        where.append("type = ?")
        params.append(a["type"])
    if a.get("thread"):
        where.append("thread = ?")
        params.append(a["thread"])
    if a.get("brand"):
        where.append("brand LIKE ?")
        params.append(f"%{a['brand'].strip()}%")
    targets = {}
    for arg, col in (("height", "height_mm"), ("diameter", "diameter_mm"),
                     ("mouth", "mouth_mm"), ("flange_od", "flange_od_mm")):
        v = num(arg)
        if v is not None:
            targets[col] = v
            where.append(f"{col} BETWEEN ? AND ?")
            params += [v - tol - 1e-6, v + tol + 1e-6]
    holes = a.get("flange_holes", "").strip()
    if holes.isdigit():
        where.append("flange_holes = ?")
        params.append(int(holes))
    if not where:
        return jsonify(results=[], hint="Choose at least one specification.")
    rows = get_db().execute(f"SELECT * FROM filters WHERE {' AND '.join(where)}", params).fetchall()
    rows.sort(key=lambda r: (sum(abs(r[c] - v) for c, v in targets.items()), r["part_number"]))
    return jsonify(results=[fmt_row(r) for r in rows[:MAX_RESULTS]])


# --------------------------------------------------------------------------- admin API
@app.post("/api/admin/login")
def admin_login():
    ip = request.remote_addr or "?"
    if is_locked(ip):
        return jsonify(error="Too many attempts. Try again in a few minutes."), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if password_ok(pw):
        _attempts.pop(ip, None)
        session.clear()
        session.permanent = True
        session["admin"] = True
        session["csrf"] = secrets.token_hex(16)
        return jsonify(ok=True, csrf=session["csrf"])
    f = _attempts.get(ip)
    _attempts[ip] = (f[0] + 1, f[1]) if f and time.time() - f[1] <= 300 else (1, time.time())
    time.sleep(0.5)
    return jsonify(error="Wrong password."), 401


@app.get("/api/admin/session")
def admin_session():
    if session.get("admin"):
        cfg = load_config()
        return jsonify(admin=True, csrf=session["csrf"], site_name=cfg.get("site_name", ""),
                       threads=COMMON_THREADS)
    return jsonify(admin=False)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify(ok=True)


def read_payload(form):
    def text(k):
        return (form.get(k) or "").strip()

    def number(k, label, integer=False):
        v = text(k).replace(",", ".")
        if not v:
            return None
        try:
            n = float(v)
        except ValueError:
            raise ValueError(f"{label} must be a number.")
        if n < 0:
            raise ValueError(f"{label} cannot be negative.")
        return int(n) if integer else n

    ftype = text("type")
    if ftype not in TYPE_KEYS:
        raise ValueError("Choose a filter type.")
    part = text("part_number")
    if not part:
        raise ValueError("Part number is required.")
    is_sep = ftype == "separator"
    return {
        "type": ftype,
        "part_number": part,
        "part_norm": norm_part(part),
        "brand": text("brand"),
        "height_mm": number("height_mm", "Height"),
        "diameter_mm": number("diameter_mm", "Diameter"),
        "mouth_mm": number("mouth_mm", "Opening size"),
        "thread": text("thread"),
        "equivalents": "\n".join(split_list(form.get("equivalents"))),
        "machines": "\n".join(split_list(form.get("machines"))),
        "flange_od_mm": number("flange_od_mm", "Flange outer diameter") if is_sep else None,
        "flange_holes": number("flange_holes", "Flange hole count", integer=True) if is_sep else None,
        "notes": text("notes"),
    }


@app.get("/api/admin/filters")
@admin_required
def admin_list():
    q = norm_part(request.args.get("q", ""))
    rows = get_db().execute("SELECT * FROM filters ORDER BY updated_at DESC").fetchall()
    if q:
        rows = [r for r in rows if q in r["part_norm"] or q in norm_part(r["brand"])
                or q in norm_part(r["equivalents"])]
    return jsonify(filters=[fmt_row(r) for r in rows[:500]], total=len(rows))


@app.post("/api/admin/filters")
@admin_required
def admin_create():
    try:
        p = read_payload(request.form)
        image = process_image(request.files["image"]) if request.files.get("image") else None
    except ValueError as e:
        return jsonify(error=str(e)), 400
    now = time.time()
    cols = list(p) + ["image", "created_at", "updated_at"]
    vals = list(p.values()) + [image, now, now]
    db = get_db()
    cur = db.execute(f"INSERT INTO filters ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid), 201


@app.put("/api/admin/filters/<int:fid>")
@admin_required
def admin_update(fid):
    db = get_db()
    row = db.execute("SELECT * FROM filters WHERE id = ?", (fid,)).fetchone()
    if not row:
        abort(404)
    try:
        p = read_payload(request.form)
        new_image = process_image(request.files["image"]) if request.files.get("image") else None
    except ValueError as e:
        return jsonify(error=str(e)), 400
    image = row["image"]
    if new_image or request.form.get("remove_image") == "1":
        remove_upload(image)
        image = new_image
    p.update(image=image, updated_at=time.time())
    db.execute(f"UPDATE filters SET {', '.join(k + ' = ?' for k in p)} WHERE id = ?", [*p.values(), fid])
    db.commit()
    return jsonify(ok=True)


@app.delete("/api/admin/filters/<int:fid>")
@admin_required
def admin_delete(fid):
    db = get_db()
    row = db.execute("SELECT image FROM filters WHERE id = ?", (fid,)).fetchone()
    if not row:
        abort(404)
    remove_upload(row["image"])
    db.execute("DELETE FROM filters WHERE id = ?", (fid,))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/admin/settings")
@admin_required
def admin_settings():
    cfg = load_config()
    name = (request.form.get("site_name") or "").strip()
    if name:
        cfg["site_name"] = name[:60]
    f = request.files.get("logo")
    if f and f.filename:
        try:
            new = process_image(f, max_side=600, keep_alpha=True)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        remove_upload(cfg.get("logo"))
        cfg["logo"] = new
    save_config(cfg)
    return jsonify(ok=True, site_name=cfg["site_name"])


@app.post("/api/admin/password")
@admin_required
def admin_password():
    if os.environ.get("ADMIN_PASSWORD"):
        return jsonify(error="The password is set by the ADMIN_PASSWORD environment variable."), 400
    data = request.get_json(silent=True) or {}
    if not password_ok(data.get("current", "")):
        return jsonify(error="The current password is wrong."), 400
    new = data.get("new", "")
    if len(new) < 8:
        return jsonify(error="Use at least 8 characters."), 400
    cfg = load_config()
    cfg["admin_hash"] = generate_password_hash(new)
    save_config(cfg)
    (DATA_DIR / "INITIAL_ADMIN_PASSWORD.txt").unlink(missing_ok=True)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
