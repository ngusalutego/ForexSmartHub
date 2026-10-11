"""
Forex Smart Hub
Database : Neon (PostgreSQL)      -> DATABASE_URL
Storage  : Cloudflare R2 (S3 API) -> R2_* variables
"""
import os
import re
import secrets
import time
from urllib.parse import urlparse
from functools import wraps
from uuid import uuid4

import boto3
import psycopg2
import psycopg2.extras
import psycopg2.pool
from botocore.client import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

# =========================================================
# CONFIG
# =========================================================
DEBUG = os.getenv("FLASK_DEBUG") == "1"

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_secret = os.getenv("SECRET_KEY")
if not _secret:
    print("WARNING: SECRET_KEY haijawekwa - ninatumia ya muda (sessions zitapotea ukirestart).")
    _secret = secrets.token_hex(32)
app.secret_key = _secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEBUG,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,  # fomu na picha tu; video zinaenda moja kwa moja R2
)

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "ngusalutego@gmail.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "").strip()
if not R2_ENDPOINT and R2_ACCOUNT_ID:
    R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
if R2_ENDPOINT:  # ondoa path ikiwa endpoint imewekwa na jina la bucket mwishoni
    _p = urlparse(R2_ENDPOINT if "://" in R2_ENDPOINT else "https://" + R2_ENDPOINT)
    R2_ENDPOINT = f"{_p.scheme}://{_p.netloc}"
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET_NAME") or os.getenv("R2_BUCKET", "")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")  # hiari
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "500"))

VIDEO_EXT = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
             "mkv": "video/x-matroska", "avi": "video/x-msvideo"}
IMAGE_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "webp": "image/webp"}

CATEGORIES = ["Forex Basics", "Technical Analysis", "Fundamental Analysis",
              "Risk Management", "Trading Psychology", "Indicators",
              "Gold / XAUUSD", "Trading Strategies", "Other"]
PER_PAGE = 12


# =========================================================
# DATABASE (Neon)
# =========================================================
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL haijawekwa. Weka connection string ya Neon.")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            1, 10, DATABASE_URL,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
        )
    return _pool


def _run(fn):
    """Endesha kazi kwenye connection; jaribu tena mara moja Neon ikikata connection."""
    for attempt in (1, 2):
        pool = get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                result = fn(cur)
            conn.commit()
            pool.putconn(conn)
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            pool.putconn(conn, close=True)
            if attempt == 2:
                raise
        except Exception:
            conn.rollback()
            pool.putconn(conn)
            raise


def fetchall(sql, params=()):
    return _run(lambda c: (c.execute(sql, params), c.fetchall())[1])


def fetchone(sql, params=()):
    return _run(lambda c: (c.execute(sql, params), c.fetchone())[1])


def execute(sql, params=()):
    """Inarudisha idadi ya safu zilizoathirika."""
    return _run(lambda c: (c.execute(sql, params), c.rowcount)[1])


def init_db():
    def work(cur):
        cur.execute("SELECT pg_advisory_lock(727274)")  # workers wasigongane
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                warning TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                profile_image TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                video_type TEXT NOT NULL,
                video_url TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                views INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                subscriber_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subscribed_to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (subscriber_id, subscribed_to_id)
            );
            CREATE TABLE IF NOT EXISTS likes (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, video_id)
            );
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_videos_created ON videos (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_user ON videos (user_id);
            CREATE INDEX IF NOT EXISTS idx_comments_video ON comments (video_id);
        """)
        cur.execute("SELECT id FROM users WHERE email = %s", (ADMIN_EMAIL,))
        if cur.fetchone():
            cur.execute("UPDATE users SET is_admin = TRUE, is_blocked = FALSE WHERE email = %s",
                        (ADMIN_EMAIL,))
        else:
            pw = ADMIN_PASSWORD
            if not pw:
                pw = secrets.token_urlsafe(10)
                print(f"ADMIN mpya imeundwa: {ADMIN_EMAIL} / nenosiri la muda: {pw}  (libadilishe Settings)")
            cur.execute(
                "INSERT INTO users (name, email, password, is_admin) VALUES (%s, %s, %s, TRUE)",
                ("Administrator", ADMIN_EMAIL, generate_password_hash(pw)))
        cur.execute("SELECT pg_advisory_unlock(727274)")
    _run(work)


# =========================================================
# STORAGE (Cloudflare R2)
# =========================================================
_s3 = None


def r2_configured():
    return all([R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET])


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _s3


def media_url(key):
    """URL ya umma ikiwa R2_PUBLIC_URL ipo; vinginevyo link ya muda (masaa 6) iliyosainiwa."""
    if not key:
        return ""
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{key}"
    if not r2_configured():
        return ""
    return s3().generate_presigned_url(
        "get_object", Params={"Bucket": R2_BUCKET, "Key": key}, ExpiresIn=21600)


def r2_delete(*keys):
    if not r2_configured():
        return
    for key in keys:
        if not key:
            continue
        try:
            s3().delete_object(Bucket=R2_BUCKET, Key=key)
        except Exception as error:
            print("R2 delete error:", key, error)


def ext_of(name):
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


# =========================================================
# HELPERS
# =========================================================
YT_ID = r"([A-Za-z0-9_-]{11})"
YT_PATTERNS = [
    rf"(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:[^#]*&)?v={YT_ID}",
    rf"youtu\.be/{YT_ID}",
    rf"(?:www\.|m\.)?youtube\.com/shorts/{YT_ID}",
    rf"(?:www\.)?youtube(?:-nocookie)?\.com/embed/{YT_ID}",
    rf"(?:www\.|m\.)?youtube\.com/live/{YT_ID}",
]


def get_youtube_embed(url):
    url = (url or "").strip()
    for pattern in YT_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return f"https://www.youtube.com/embed/{match.group(1)}"
    return None


def safe_next(url):
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return None


def get_user():
    if "user" not in g:
        g.user = None
        if "user_id" in session:
            g.user = fetchone("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    return g.user


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


_attempts = {}


def too_many_attempts(key):
    now = time.time()
    recent = [t for t in _attempts.get(key, []) if now - t < 600]
    _attempts[key] = recent
    return len(recent) >= 8


def add_attempt(key):
    _attempts.setdefault(key, []).append(time.time())


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not get_user():
            if request.path.startswith("/api/"):
                return jsonify(error="Session imeisha. Ingia (login) tena."), 401
            flash("Tafadhali ingia (login) kwanza.", "error")
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = get_user()
        if not user:
            flash("Unahitaji kuingia kama admin.", "error")
            return redirect(url_for("login", next=request.path))
        if not user["is_admin"]:
            flash("Ruhusa ya Administrator inahitajika.", "error")
            return redirect(url_for("home"))
        return fn(*a, **kw)
    return wrapper


VIDEO_SELECT = """
    SELECT v.*, u.name AS username,
           (SELECT COUNT(*) FROM likes l WHERE l.video_id = v.id) AS like_count,
           (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.id) AS comment_count
    FROM videos v LEFT JOIN users u ON u.id = v.user_id
"""


def can_manage(video):
    user = get_user()
    return bool(user and (user["is_admin"] or video["user_id"] == user["id"]))


def delete_video_row(video):
    execute("DELETE FROM videos WHERE id = %s", (video["id"],))
    if video["video_type"] == "upload":
        r2_delete(video["filename"])


# =========================================================
# REQUEST HOOKS
# =========================================================
@app.before_request
def guard():
    user = get_user()
    if user and user["is_blocked"]:
        session.clear()
        g.user = None
        flash("Akaunti yako imezuiwa na administrator.", "error")
        if request.endpoint not in ("login", "static", "healthz"):
            return redirect(url_for("login"))
    if request.method == "POST":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf"):
            if request.path.startswith("/api/"):
                return jsonify(error="Token ya usalama imekwisha. Refresh ukurasa ujaribu tena."), 400
            flash("Ombi limekataliwa (usalama). Jaribu tena.", "error")
            return redirect(request.referrer or url_for("home"))


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


@app.context_processor
def inject_globals():
    return {"current_user": get_user(), "csrf_token": csrf_token,
            "media": media_url, "categories": CATEGORIES}


@app.template_filter("dt")
def format_dt(value):
    return value.strftime("%d %b %Y") if value else ""


@app.template_filter("yt_id")
def yt_id(url):
    return (url or "").rsplit("/embed/", 1)[-1]


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="Ukurasa haukupatikana."), 404


@app.errorhandler(413)
def too_large(_):
    flash("Faili ni kubwa mno.", "error")
    return redirect(request.referrer or url_for("home"))


@app.errorhandler(500)
def server_error(error):
    print("SERVER ERROR:", repr(getattr(error, "original_exception", error)))
    if request.path.startswith("/api/"):
        return jsonify(error="Hitilafu ya server: "
                       + str(getattr(error, "original_exception", error))[:200]), 500
    return render_template("error.html", code=500, message="Kuna hitilafu ya server. Jaribu tena."), 500


@app.route("/healthz")
def healthz():
    return "ok"


# =========================================================
# HOME / VIDEOS
# =========================================================
@app.route("/")
def home():
    videos = fetchall(VIDEO_SELECT + " ORDER BY v.id DESC LIMIT 6")
    stats = fetchone("""SELECT (SELECT COUNT(*) FROM videos) AS videos,
                               (SELECT COUNT(*) FROM users) AS users,
                               (SELECT COALESCE(SUM(views),0) FROM videos) AS views""")
    return render_template("index.html", videos=videos, stats=stats)


@app.route("/videos")
def videos():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)

    where, params = ["1=1"], []
    if search:
        where.append("(v.title ILIKE %s OR v.description ILIKE %s OR v.category ILIKE %s)")
        params += [f"%{search}%"] * 3
    if category:
        where.append("v.category = %s")
        params.append(category)
    clause = " WHERE " + " AND ".join(where)

    total = fetchone("SELECT COUNT(*) AS n FROM videos v" + clause, params)["n"]
    rows = fetchall(VIDEO_SELECT + clause + " ORDER BY v.id DESC LIMIT %s OFFSET %s",
                    params + [PER_PAGE, (page - 1) * PER_PAGE])
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    return render_template("videos.html", videos=rows, search=search, selected_category=category,
                           page=page, pages=pages, total=total)


@app.route("/following")
@login_required
def following():
    rows = fetchall(VIDEO_SELECT + """
        WHERE v.user_id IN (SELECT subscribed_to_id FROM subscriptions WHERE subscriber_id = %s)
        ORDER BY v.id DESC LIMIT 60""", (get_user()["id"],))
    return render_template("following.html", videos=rows)


@app.route("/watch/<int:video_id>")
def watch(video_id):
    video = fetchone(VIDEO_SELECT + " WHERE v.id = %s", (video_id,))
    if not video:
        abort(404)
    seen = session.get("viewed", [])
    if video_id not in seen:
        execute("UPDATE videos SET views = views + 1 WHERE id = %s", (video_id,))
        video["views"] += 1
        session["viewed"] = (seen + [video_id])[-100:]
    comments = fetchall("""SELECT c.*, u.name, u.profile_image FROM comments c
                           JOIN users u ON u.id = c.user_id
                           WHERE c.video_id = %s ORDER BY c.id DESC""", (video_id,))
    user = get_user()
    liked = bool(user and fetchone("SELECT 1 FROM likes WHERE user_id=%s AND video_id=%s",
                                   (user["id"], video_id)))
    related = fetchall(VIDEO_SELECT + " WHERE v.category = %s AND v.id <> %s ORDER BY v.id DESC LIMIT 6",
                       (video["category"], video_id))
    return render_template("watch.html", video=video, comments=comments, liked=liked,
                           related=related, can_manage=can_manage(video))


# =========================================================
# AUTH
# =========================================================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or not password:
            flash("Jaza sehemu zote zinazohitajika.", "error")
        elif "@" not in email or len(email) > 254 or len(name) > 80:
            flash("Barua pepe au jina si sahihi.", "error")
        elif password != confirm:
            flash("Nenosiri halifanani.", "error")
        elif len(password) < 6:
            flash("Nenosiri liwe na angalau herufi 6.", "error")
        else:
            created = execute(
                "INSERT INTO users (name, email, password) VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING",
                (name, email, generate_password_hash(password)))
            if created:
                flash("Akaunti imeundwa. Tafadhali ingia.", "success")
                return redirect(url_for("login"))
            flash("Barua pepe hii tayari imesajiliwa.", "error")
        return redirect(url_for("signup"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        key = f"{request.remote_addr}|{email}"
        if too_many_attempts(key):
            flash("Majaribio mengi. Subiri dakika chache ujaribu tena.", "error")
            return redirect(url_for("login"))
        user = fetchone("SELECT * FROM users WHERE email = %s", (email,))
        if not user or not check_password_hash(user["password"], password):
            add_attempt(key)
            flash("Barua pepe au nenosiri si sahihi.", "error")
            return redirect(url_for("login"))
        if user["is_blocked"]:
            flash("Akaunti yako imezuiwa na administrator.", "error")
            return redirect(url_for("login"))
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        flash(f"Karibu, {user['name']}!", "success")
        return redirect(safe_next(request.args.get("next")) or url_for("home"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Umetoka (logout).", "success")
    return redirect(url_for("home"))


@app.route("/warning/dismiss", methods=["POST"])
@login_required
def dismiss_warning():
    execute("UPDATE users SET warning = '' WHERE id = %s", (get_user()["id"],))
    return redirect(request.referrer or url_for("home"))


# =========================================================
# UPLOAD (video moja kwa moja kwenda R2 kwa presigned URL)
# =========================================================
@app.route("/api/presign-upload", methods=["POST"])
@login_required
def presign_upload():
    if not r2_configured():
        return jsonify(error="Storage (R2) haijawekwa na admin."), 503
    data = request.get_json(silent=True) or {}
    filename = secure_filename(str(data.get("filename", "")))
    try:
        size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    ext = ext_of(filename)
    if ext not in VIDEO_EXT:
        return jsonify(error="Aina ya faili hairuhusiwi. Tumia mp4, webm, mov, mkv au avi."), 400
    if size <= 0 or size > MAX_VIDEO_MB * 1024 * 1024:
        return jsonify(error=f"Video iwe chini ya {MAX_VIDEO_MB} MB."), 400
    key = f"videos/{get_user()['id']}/{uuid4().hex}.{ext}"
    content_type = VIDEO_EXT[ext]
    try:
        url = s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": R2_BUCKET, "Key": key, "ContentType": content_type},
            ExpiresIn=3600)
    except Exception as error:
        print("PRESIGN ERROR:", repr(error))
        return jsonify(error="Imeshindwa kuunganisha R2: " + str(error)[:200]), 500
    return jsonify(url=url, key=key, content_type=content_type)


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        key = request.form.get("video_key", "").strip()
        user = get_user()

        error = None
        if not title or len(title) > 150:
            error = "Weka kichwa cha video (herufi hadi 150)."
        elif category not in CATEGORIES:
            error = "Chagua category."
        elif not request.form.get("rights"):
            error = "Lazima uthibitishe kuwa una ruhusa ya kupakia video hii."
        elif not r2_configured():
            error = "Storage (R2) haijawekwa."
        elif not key.startswith(f"videos/{user['id']}/") or ext_of(key) not in VIDEO_EXT:
            error = "Video haijapakiwa vizuri. Jaribu tena."
        else:
            try:
                head = s3().head_object(Bucket=R2_BUCKET, Key=key)
                if head["ContentLength"] > MAX_VIDEO_MB * 1024 * 1024:
                    r2_delete(key)
                    error = "Video ni kubwa mno."
            except ClientError:
                error = "Video haikupatikana kwenye storage. Jaribu kupakia tena."
        if error:
            flash(error, "error")
            return redirect(url_for("upload"))

        new_id = fetchone("""INSERT INTO videos (title, description, category, video_type, filename, user_id)
                             VALUES (%s, %s, %s, 'upload', %s, %s) RETURNING id""",
                          (title, description, category, key, user["id"]))["id"]
        flash("Video imepakiwa!", "success")
        return redirect(url_for("watch", video_id=new_id))
    return render_template("upload.html", max_mb=MAX_VIDEO_MB, r2_ready=r2_configured())


@app.route("/youtube", methods=["GET", "POST"])
@login_required
def add_youtube():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        embed = get_youtube_embed(request.form.get("youtube_url", ""))
        if not title or len(title) > 150:
            flash("Weka kichwa cha video.", "error")
        elif category not in CATEGORIES:
            flash("Chagua category.", "error")
        elif not embed:
            flash("Link ya YouTube si sahihi.", "error")
        else:
            new_id = fetchone("""INSERT INTO videos (title, description, category, video_type, video_url, user_id)
                                 VALUES (%s, %s, %s, 'youtube', %s, %s) RETURNING id""",
                              (title, description, category, embed, get_user()["id"]))["id"]
            flash("Video ya YouTube imeongezwa!", "success")
            return redirect(url_for("watch", video_id=new_id))
        return redirect(url_for("add_youtube"))
    return render_template("add_youtube.html")


@app.route("/video/<int:video_id>/edit", methods=["GET", "POST"])
@login_required
def edit_video(video_id):
    video = fetchone("SELECT * FROM videos WHERE id = %s", (video_id,))
    if not video:
        abort(404)
    if not can_manage(video):
        flash("Unaweza kuhariri video zako tu.", "error")
        return redirect(url_for("watch", video_id=video_id))
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        if not title or len(title) > 150 or category not in CATEGORIES:
            flash("Kichwa au category si sahihi.", "error")
            return redirect(url_for("edit_video", video_id=video_id))
        execute("UPDATE videos SET title=%s, description=%s, category=%s WHERE id=%s",
                (title, request.form.get("description", "").strip(), category, video_id))
        flash("Video imesasishwa.", "success")
        return redirect(url_for("watch", video_id=video_id))
    return render_template("edit_video.html", video=video)


@app.route("/delete/<int:video_id>", methods=["POST"])
@login_required
def delete_video(video_id):
    video = fetchone("SELECT * FROM videos WHERE id = %s", (video_id,))
    if not video:
        flash("Video haipo.", "error")
    elif not can_manage(video):
        flash("Unaweza kufuta video zako tu.", "error")
    else:
        delete_video_row(video)
        flash("Video imefutwa.", "success")
    return redirect(safe_next(request.form.get("next")) or url_for("videos"))


# =========================================================
# LIKES / COMMENTS
# =========================================================
@app.route("/like/<int:video_id>", methods=["POST"])
@login_required
def like(video_id):
    if not fetchone("SELECT 1 FROM videos WHERE id = %s", (video_id,)):
        abort(404)
    uid = get_user()["id"]
    added = execute("INSERT INTO likes (user_id, video_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (uid, video_id))
    if not added:
        execute("DELETE FROM likes WHERE user_id = %s AND video_id = %s", (uid, video_id))
    return redirect(url_for("watch", video_id=video_id) + "#actions")


@app.route("/comment/<int:video_id>", methods=["POST"])
@login_required
def add_comment(video_id):
    body = request.form.get("body", "").strip()
    if not fetchone("SELECT 1 FROM videos WHERE id = %s", (video_id,)):
        abort(404)
    if not body or len(body) > 1000:
        flash("Maoni yawe kati ya herufi 1 na 1000.", "error")
    else:
        execute("INSERT INTO comments (video_id, user_id, body) VALUES (%s, %s, %s)",
                (video_id, get_user()["id"], body))
    return redirect(url_for("watch", video_id=video_id) + "#comments")


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = fetchone("SELECT * FROM comments WHERE id = %s", (comment_id,))
    if not comment:
        abort(404)
    user = get_user()
    if user["is_admin"] or comment["user_id"] == user["id"]:
        execute("DELETE FROM comments WHERE id = %s", (comment_id,))
        flash("Maoni yamefutwa.", "success")
    return redirect(url_for("watch", video_id=comment["video_id"]) + "#comments")


# =========================================================
# PROFILE / USERS / SUBSCRIPTIONS
# =========================================================
@app.route("/profile/<int:user_id>")
def profile(user_id):
    user = fetchone("SELECT * FROM users WHERE id = %s", (user_id,))
    if not user:
        flash("Profile haipo.", "error")
        return redirect(url_for("home"))
    posts = fetchall(VIDEO_SELECT + " WHERE v.user_id = %s ORDER BY v.id DESC", (user_id,))
    counts = fetchone("""SELECT
        (SELECT COUNT(*) FROM subscriptions WHERE subscribed_to_id = %s) AS subscribers,
        (SELECT COUNT(*) FROM subscriptions WHERE subscriber_id = %s) AS following""",
                      (user_id, user_id))
    me = get_user()
    is_subscribed = bool(me and fetchone(
        "SELECT 1 FROM subscriptions WHERE subscriber_id=%s AND subscribed_to_id=%s",
        (me["id"], user_id)))
    return render_template("profile.html", user=user, posts=posts, subscribers=counts["subscribers"],
                           following_count=counts["following"], is_subscribed=is_subscribed)


@app.route("/users")
def users():
    search = request.args.get("search", "").strip()
    if search:
        rows = fetchall("SELECT * FROM users WHERE name ILIKE %s OR bio ILIKE %s ORDER BY name LIMIT 100",
                        (f"%{search}%", f"%{search}%"))
    else:
        rows = fetchall("SELECT * FROM users ORDER BY name LIMIT 100")
    return render_template("users.html", users=rows, search=search)


@app.route("/subscribe/<int:user_id>", methods=["POST"])
@login_required
def subscribe(user_id):
    me = get_user()["id"]
    if user_id == me:
        flash("Huwezi kujisubscribe mwenyewe.", "error")
    elif not fetchone("SELECT 1 FROM users WHERE id = %s", (user_id,)):
        flash("Mtumiaji hapatikani.", "error")
    else:
        execute("""INSERT INTO subscriptions (subscriber_id, subscribed_to_id) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""", (me, user_id))
        flash("Umesubscribe.", "success")
    return redirect(url_for("profile", user_id=user_id))


@app.route("/unsubscribe/<int:user_id>", methods=["POST"])
@login_required
def unsubscribe(user_id):
    execute("DELETE FROM subscriptions WHERE subscriber_id = %s AND subscribed_to_id = %s",
            (get_user()["id"], user_id))
    flash("Umeondoa subscription.", "success")
    return redirect(url_for("profile", user_id=user_id))


@app.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    user = get_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        bio = request.form.get("bio", "").strip()[:500]
        image = request.files.get("profile_image")
        if not name or len(name) > 80:
            flash("Jina haliwezi kuwa tupu.", "error")
            return redirect(url_for("edit_profile"))

        key = user["profile_image"]
        old_key = None
        if image and image.filename:
            ext = ext_of(secure_filename(image.filename))
            image.stream.seek(0, 2)
            size = image.stream.tell()
            image.stream.seek(0)
            if ext not in IMAGE_EXT:
                flash("Tafadhali pakia picha halali (jpg, png, gif, webp).", "error")
                return redirect(url_for("edit_profile"))
            if size > 5 * 1024 * 1024:
                flash("Picha iwe chini ya 5 MB.", "error")
                return redirect(url_for("edit_profile"))
            if not r2_configured():
                flash("Storage (R2) haijawekwa.", "error")
                return redirect(url_for("edit_profile"))
            new_key = f"avatars/{user['id']}_{uuid4().hex[:10]}.{ext}"
            try:
                s3().upload_fileobj(image.stream, R2_BUCKET, new_key,
                                    ExtraArgs={"ContentType": IMAGE_EXT[ext]})
            except Exception as error:
                print("Avatar upload error:", error)
                flash("Imeshindwa kupakia picha. Jaribu tena.", "error")
                return redirect(url_for("edit_profile"))
            old_key, key = user["profile_image"], new_key

        execute("UPDATE users SET name=%s, bio=%s, profile_image=%s WHERE id=%s",
                (name, bio, key, user["id"]))
        r2_delete(old_key)
        flash("Profile imesasishwa.", "success")
        return redirect(url_for("profile", user_id=user["id"]))
    return render_template("edit_profile.html", user=user)


@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    user = get_user()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not check_password_hash(user["password"], current):
        flash("Nenosiri la sasa si sahihi.", "error")
    elif len(new) < 6:
        flash("Nenosiri jipya liwe na angalau herufi 6.", "error")
    elif new != confirm:
        flash("Nenosiri jipya halifanani.", "error")
    else:
        execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(new), user["id"]))
        flash("Nenosiri limebadilishwa.", "success")
    return redirect(url_for("settings"))


# =========================================================
# ADMIN / GUARDIAN
# =========================================================
@app.route("/admin")
@admin_required
def admin_panel():
    all_users = fetchall("SELECT * FROM users ORDER BY id DESC")
    posts = fetchall(VIDEO_SELECT.replace("u.name AS username,", "u.name AS username, u.email AS user_email,")
                     + " ORDER BY v.id DESC")
    stats = fetchone("""SELECT (SELECT COUNT(*) FROM users) AS total_users,
                               (SELECT COUNT(*) FROM videos) AS total_posts,
                               (SELECT COUNT(*) FROM users WHERE is_blocked) AS blocked_users,
                               (SELECT COUNT(*) FROM comments) AS total_comments""")
    return render_template("admin.html", users=all_users, posts=posts, **stats)


def _target_user(user_id):
    target = fetchone("SELECT * FROM users WHERE id = %s", (user_id,))
    if not target or target["is_admin"] or target["email"] == ADMIN_EMAIL:
        return None
    return target


@app.route("/admin/block/<int:user_id>", methods=["POST"])
@admin_required
def block_user(user_id):
    if _target_user(user_id):
        execute("UPDATE users SET is_blocked = TRUE WHERE id = %s", (user_id,))
        flash("Mtumiaji amezuiwa.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/unblock/<int:user_id>", methods=["POST"])
@admin_required
def unblock_user(user_id):
    if _target_user(user_id):
        execute("UPDATE users SET is_blocked = FALSE WHERE id = %s", (user_id,))
        flash("Mtumiaji ameruhusiwa tena.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/warning/<int:user_id>", methods=["POST"])
@admin_required
def warn_user(user_id):
    text = request.form.get("warning", "").strip()[:300]
    if not text:
        flash("Andika onyo.", "error")
    elif _target_user(user_id):
        execute("UPDATE users SET warning = %s WHERE id = %s", (text, user_id))
        flash("Onyo limetumwa; mtumiaji ataliona akiingia.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    target = _target_user(user_id)
    if target:
        keys = [v["filename"] for v in fetchall(
            "SELECT filename FROM videos WHERE user_id = %s AND video_type = 'upload'", (user_id,))]
        keys.append(target["profile_image"])
        execute("DELETE FROM users WHERE id = %s", (user_id,))  # CASCADE inafuta data zake zote
        r2_delete(*keys)
        flash("Mtumiaji na posts zake zimefutwa.", "success")
    return redirect(url_for("admin_panel"))


# =========================================================
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=DEBUG)
