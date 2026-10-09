from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import os
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4

app = Flask(__name__)

app.secret_key = "forex-smart-hub-secret-key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "forex.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_EMAIL = "ngusalutego@gmail.com"
ADMIN_PASSWORD = "123456"

ALLOWED_EXTENSIONS = {
    "mp4",
    "webm",
    "mov",
    "avi",
    "mkv",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp"
}


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(DATABASE)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    # USERS TABLE
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL,

            email TEXT UNIQUE NOT NULL,

            password TEXT NOT NULL,

            is_admin INTEGER DEFAULT 0,

            is_blocked INTEGER DEFAULT 0,

            warning TEXT DEFAULT '',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # VIDEOS TABLE
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            description TEXT,

            category TEXT NOT NULL,

            video_type TEXT NOT NULL,

            video_url TEXT,

            filename TEXT,

            user_id INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Check old database
    columns = conn.execute(
        "PRAGMA table_info(videos)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

    # Add user_id if old database doesn't have it
    if "user_id" not in column_names:

        conn.execute(
            "ALTER TABLE videos ADD COLUMN user_id INTEGER"
        )

    # Create administrator automatically
    admin = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (ADMIN_EMAIL,)
    ).fetchone()

    if not admin:

        conn.execute("""
            INSERT INTO users
            (
                name,
                email,
                password,
                is_admin
            )
            VALUES (?, ?, ?, 1)
        """, (
            "Administrator",
            ADMIN_EMAIL,
            generate_password_hash(ADMIN_PASSWORD)
        ))

    else:

        conn.execute("""
            UPDATE users

            SET is_admin = 1,
                is_blocked = 0

            WHERE email = ?
        """, (
            ADMIN_EMAIL,
        ))

    conn.commit()

    conn.close()


# =========================================================
# LOGIN REQUIRED
# =========================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            flash(
                "Please login first.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        conn.close()

        if not user:

            session.clear()

            flash(
                "Your account was not found.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        if user["is_blocked"]:

            session.clear()

            flash(
                "Your account has been blocked by the administrator.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        return function(
            *args,
            **kwargs
        )

    return wrapper


# =========================================================
# ADMIN REQUIRED
# =========================================================

def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            flash(
                "Admin login required.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        conn.close()

        if not user or not user["is_admin"]:

            flash(
                "Administrator access required.",
                "error"
            )

            return redirect(
                url_for("home")
            )

        return function(
            *args,
            **kwargs
        )

    return wrapper


# =========================================================
# FILE CHECK
# =========================================================

def allowed_file(filename):

    return (
        "." in filename
        and
        filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


# =========================================================
# YOUTUBE EMBED
# =========================================================

def get_youtube_embed(url):

    try:

        if not url:
            return None

        url = url.strip()

        # Normal YouTube video
        if "youtube.com/watch?v=" in url:

            video_id = (
                url.split("watch?v=")[1]
                .split("&")[0]
            )

            if video_id:
                return (
                    f"https://www.youtube.com/embed/{video_id}"
                )

        # Short YouTube link
        if "youtu.be/" in url:

            video_id = (
                url.split("youtu.be/")[1]
                .split("?")[0]
            )

            if video_id:
                return (
                    f"https://www.youtube.com/embed/{video_id}"
                )

        # YouTube Shorts
        if "youtube.com/shorts/" in url:

            video_id = (
                url.split("youtube.com/shorts/")[1]
                .split("?")[0]
            )

            if video_id:
                return (
                    f"https://www.youtube.com/embed/{video_id}"
                )

        # Already an embed link
        if "youtube.com/embed/" in url:
            return url

        # Invalid YouTube link
        return None

    except Exception as error:

        print("YouTube link error:", error)

        return None




# =========================================================
# PROFILE / SUBSCRIPTION DATABASE
# =========================================================

def init_profile_db():

    conn = get_db()

    columns = conn.execute(
        "PRAGMA table_info(users)"
    ).fetchall()

    column_names = [
        column["name"] for column in columns
    ]

    if "bio" not in column_names:
        conn.execute(
            "ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''"
        )

    if "profile_image" not in column_names:
        conn.execute(
            "ALTER TABLE users ADD COLUMN profile_image TEXT DEFAULT ''"
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id INTEGER NOT NULL,
            subscribed_to_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(subscriber_id, subscribed_to_id)
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# CURRENT USER
# =========================================================

@app.context_processor
def inject_user():

    current_user = None

    if "user_id" in session:

        conn = get_db()

        current_user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        conn.close()

    return {
        "current_user": current_user
    }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    conn = get_db()

    videos = conn.execute("""
        SELECT
            videos.*,
            users.name AS username

        FROM videos

        LEFT JOIN users
        ON videos.user_id = users.id

        ORDER BY videos.id DESC

        LIMIT 6
    """).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM videos"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "index.html",
        videos=videos,
        total=total
    )


# =========================================================
# SIGN UP
# =========================================================

@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        if not name or not email or not password:

            flash(
                "Please fill all required fields.",
                "error"
            )

            return redirect(
                url_for("signup")
            )

        if password != confirm_password:

            flash(
                "Passwords do not match.",
                "error"
            )

            return redirect(
                url_for("signup")
            )

        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("signup")
            )

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing:

            conn.close()

            flash(
                "Email is already registered.",
                "error"
            )

            return redirect(
                url_for("signup")
            )

        conn.execute("""
            INSERT INTO users
            (
                name,
                email,
                password
            )
            VALUES (?, ?, ?)
        """, (
            name,
            email,
            generate_password_hash(password)
        ))

        conn.commit()

        conn.close()

        flash(
            "Account created successfully. Please login.",
            "success"
        )

        return redirect(
            url_for("login")
        )

    return render_template(
        "signup.html"
    )


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if not user:

            flash(
                "Invalid email or password.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        if user["is_blocked"]:

            flash(
                "Your account has been blocked by the administrator.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        if not check_password_hash(
            user["password"],
            password
        ):

            flash(
                "Invalid email or password.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        session["user_id"] = user["id"]

        session["user_name"] = user["name"]

        session["is_admin"] = bool(
            user["is_admin"]
        )

        flash(
            f"Welcome, {user['name']}!",
            "success"
        )

        return redirect(
            url_for("home")
        )

    return render_template(
        "login.html"
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out.",
        "success"
    )

    return redirect(
        url_for("home")
    )


# =========================================================
# VIDEOS
# =========================================================

@app.route("/videos")
def videos():

    search = request.args.get(
        "search",
        ""
    ).strip()

    category = request.args.get(
        "category",
        ""
    ).strip()

    conn = get_db()

    query = """
        SELECT
            videos.*,
            users.name AS username

        FROM videos

        LEFT JOIN users
        ON videos.user_id = users.id

        WHERE 1=1
    """

    params = []

    if search:

        query += """
            AND (
                videos.title LIKE ?
                OR videos.description LIKE ?
                OR videos.category LIKE ?
            )
        """

        keyword = f"%{search}%"

        params.extend([
            keyword,
            keyword,
            keyword
        ])

    if category:

        query += """
            AND videos.category = ?
        """

        params.append(
            category
        )

    query += """
        ORDER BY videos.id DESC
    """

    video_list = conn.execute(
        query,
        params
    ).fetchall()

    conn.close()

    categories = [
        "Forex Basics",
        "Technical Analysis",
        "Fundamental Analysis",
        "Risk Management",
        "Trading Psychology",
        "Indicators",
        "Gold / XAUUSD",
        "Trading Strategies",
        "Other"
    ]

    return render_template(
        "videos.html",
        videos=video_list,
        categories=categories,
        search=search,
        selected_category=category
    )


# =========================================================
# UPLOAD VIDEO
# =========================================================

@app.route(
    "/upload",
    methods=["GET", "POST"]
)
@login_required
def upload():

    categories = [
        "Forex Basics",
        "Technical Analysis",
        "Fundamental Analysis",
        "Risk Management",
        "Trading Psychology",
        "Indicators",
        "Gold / XAUUSD",
        "Trading Strategies",
        "Other"
    ]

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        category = request.form.get(
            "category",
            ""
        ).strip()

        rights = request.form.get(
            "rights"
        )

        video_file = request.files.get(
            "video"
        )

        if not title:

            flash(
                "Please enter the video title.",
                "error"
            )

            return redirect(
                url_for("upload")
            )

        if not category:

            flash(
                "Please select a category.",
                "error"
            )

            return redirect(
                url_for("upload")
            )

        if not rights:

            flash(
                "You must confirm that you have permission.",
                "error"
            )

            return redirect(
                url_for("upload")
            )

        if (
            not video_file
            or
            video_file.filename == ""
        ):

            flash(
                "Please select a video file.",
                "error"
            )

            return redirect(
                url_for("upload")
            )

        if not allowed_file(
            video_file.filename
        ):

            flash(
                "This file type is not allowed.",
                "error"
            )

            return redirect(
                url_for("upload")
            )

        filename = secure_filename(
            video_file.filename
        )

        original_name = filename

        name, extension = os.path.splitext(
            original_name
        )

        counter = 1

        while os.path.exists(
            os.path.join(
                UPLOAD_FOLDER,
                filename
            )
        ):

            filename = (
                f"{name}_{counter}{extension}"
            )

            counter += 1

        file_path = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        video_file.save(
            file_path
        )

        conn = get_db()

        conn.execute("""
            INSERT INTO videos
            (
                title,
                description,
                category,
                video_type,
                video_url,
                filename,
                user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
            description,
            category,
            "upload",
            "",
            filename,
            session["user_id"]
        ))

        conn.commit()

        conn.close()

        flash(
            "Video uploaded successfully!",
            "success"
        )

        return redirect(
            url_for("videos")
        )

    return render_template(
        "upload.html",
        categories=categories
    )


# =========================================================
# ADD YOUTUBE
# =========================================================

@app.route(
    "/youtube",
    methods=["GET", "POST"]
)
@login_required
def add_youtube():

    categories = [
        "Forex Basics",
        "Technical Analysis",
        "Fundamental Analysis",
        "Risk Management",
        "Trading Psychology",
        "Indicators",
        "Gold / XAUUSD",
        "Trading Strategies",
        "Other"
    ]

    if request.method == "POST":

        try:

            title = request.form.get(
                "title",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()

            category = request.form.get(
                "category",
                ""
            ).strip()

            youtube_url = request.form.get(
                "youtube_url",
                ""
            ).strip()

            if not title:

                flash(
                    "Please enter the video title.",
                    "error"
                )

                return redirect(
                    url_for("add_youtube")
                )

            if not category:

                flash(
                    "Please select a category.",
                    "error"
                )

                return redirect(
                    url_for("add_youtube")
                )

            if not youtube_url:

                flash(
                    "Please enter the YouTube URL.",
                    "error"
                )

                return redirect(
                    url_for("add_youtube")
                )

            # Convert and validate YouTube link
            embed_url = get_youtube_embed(
                youtube_url
            )

            if not embed_url:

                flash(
                    "Invalid YouTube link. "
                    "Please use a valid YouTube video link.",
                    "error"
                )

                return redirect(
                    url_for("add_youtube")
                )

            conn = get_db()

            conn.execute("""
                INSERT INTO videos
                (
                    title,
                    description,
                    category,
                    video_type,
                    video_url,
                    filename,
                    user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                description,
                category,
                "youtube",
                embed_url,
                "",
                session["user_id"]
            ))

            conn.commit()
            conn.close()

            flash(
                "YouTube video added successfully!",
                "success"
            )

            return redirect(
                url_for("videos")
            )

        except Exception as error:

            print(
                "YouTube error:",
                error
            )

            flash(
                "Something went wrong while adding "
                "the YouTube video.",
                "error"
            )

            return redirect(
                url_for("add_youtube")
            )

    return render_template(
        "add_youtube.html",
        categories=categories
    )

# =========================================================
# DELETE VIDEO
# USER = OWN VIDEO
# ADMIN = ANY VIDEO
# =========================================================

@app.route(
    "/delete/<int:video_id>",
    methods=["POST"]
)
@login_required
def delete_video(video_id):

    conn = get_db()

    video = conn.execute(
        "SELECT * FROM videos WHERE id = ?",
        (video_id,)
    ).fetchone()

    if not video:

        conn.close()

        flash(
            "Video not found.",
            "error"
        )

        return redirect(
            url_for("videos")
        )

    # ADMIN CAN DELETE ANY VIDEO
    if current_user_is_admin():

        if video["filename"]:

            file_path = os.path.join(
                UPLOAD_FOLDER,
                video["filename"]
            )

            if os.path.exists(file_path):

                os.remove(file_path)

        conn.execute(
            "DELETE FROM videos WHERE id = ?",
            (video_id,)
        )

        conn.commit()

        conn.close()

        flash(
            "Video deleted by Guardian.",
            "success"
        )

        return redirect(
            url_for("videos")
        )

    # USER CAN DELETE OWN VIDEO ONLY
    if video["user_id"] != session["user_id"]:

        conn.close()

        flash(
            "You can only delete your own video.",
            "error"
        )

        return redirect(
            url_for("videos")
        )

    if video["filename"]:

        file_path = os.path.join(
            UPLOAD_FOLDER,
            video["filename"]
        )

        if os.path.exists(file_path):

            os.remove(file_path)

    conn.execute(
        "DELETE FROM videos WHERE id = ?",
        (video_id,)
    )

    conn.commit()

    conn.close()

    flash(
        "Your video has been deleted.",
        "success"
    )

    return redirect(
        url_for("videos")
    )


# =========================================================
# CHECK ADMIN
# =========================================================

def current_user_is_admin():

    if "user_id" not in session:

        return False

    conn = get_db()

    user = conn.execute(
        "SELECT is_admin FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    conn.close()

    if user:

        return bool(
            user["is_admin"]
        )

    return False


# =========================================================
# ADMIN / GUARDIAN PANEL
# =========================================================

@app.route("/admin")
@admin_required
def admin_panel():

    conn = get_db()

    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY id DESC
    """).fetchall()

    posts = conn.execute("""
        SELECT
            videos.*,
            users.name AS username,
            users.email AS user_email

        FROM videos

        LEFT JOIN users
        ON videos.user_id = users.id

        ORDER BY videos.id DESC
    """).fetchall()

    total_users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    total_posts = conn.execute(
        "SELECT COUNT(*) FROM videos"
    ).fetchone()[0]

    blocked_users = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE is_blocked = 1
        """
    ).fetchone()[0]

    conn.close()

    return render_template(
        "admin.html",
        users=users,
        posts=posts,
        total_users=total_users,
        total_posts=total_posts,
        blocked_users=blocked_users
    )


# =========================================================
# BLOCK USER
# =========================================================

@app.route(
    "/admin/block/<int:user_id>",
    methods=["POST"]
)
@admin_required
def block_user(user_id):

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if user and user["email"] != ADMIN_EMAIL:

        conn.execute("""
            UPDATE users

            SET is_blocked = 1

            WHERE id = ?
        """, (
            user_id,
        ))

        conn.commit()

        flash(
            "User has been blocked.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================================================
# UNBLOCK USER
# =========================================================

@app.route(
    "/admin/unblock/<int:user_id>",
    methods=["POST"]
)
@admin_required
def unblock_user(user_id):

    conn = get_db()

    conn.execute("""
        UPDATE users

        SET is_blocked = 0

        WHERE id = ?

        AND email != ?
    """, (
        user_id,
        ADMIN_EMAIL
    ))

    conn.commit()

    conn.close()

    flash(
        "User has been unblocked.",
        "success"
    )

    return redirect(
        url_for("admin_panel")
    )


# =========================================================
# WARNING USER
# =========================================================

@app.route(
    "/admin/warning/<int:user_id>",
    methods=["POST"]
)
@admin_required
def warn_user(user_id):

    warning = request.form.get(
        "warning",
        ""
    ).strip()

    if not warning:

        flash(
            "Please write a warning.",
            "error"
        )

        return redirect(
            url_for("admin_panel")
        )

    conn = get_db()

    conn.execute("""
        UPDATE users

        SET warning = ?

        WHERE id = ?

        AND email != ?
    """, (
        warning,
        user_id,
        ADMIN_EMAIL
    ))

    conn.commit()

    conn.close()

    flash(
        "Warning sent to the user.",
        "success"
    )

    return redirect(
        url_for("admin_panel")
    )


# =========================================================
# DELETE USER
# =========================================================

@app.route(
    "/admin/delete-user/<int:user_id>",
    methods=["POST"]
)
@admin_required
def delete_user(user_id):

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if user and user["email"] != ADMIN_EMAIL:

        user_videos = conn.execute("""
            SELECT *
            FROM videos
            WHERE user_id = ?
        """, (
            user_id,
        )).fetchall()

        for video in user_videos:

            if video["filename"]:

                path = os.path.join(
                    UPLOAD_FOLDER,
                    video["filename"]
                )

                if os.path.exists(path):

                    os.remove(path)

        conn.execute(
            "DELETE FROM videos WHERE user_id = ?",
            (user_id,)
        )

        conn.execute(
            "DELETE FROM users WHERE id = ?",
            (user_id,)
        )

        conn.commit()

        flash(
            "User and their posts have been deleted.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================================================
# ADMIN DELETE POST
# =========================================================

@app.route(
    "/admin/delete-post/<int:video_id>",
    methods=["POST"]
)
@admin_required
def admin_delete_post(video_id):

    conn = get_db()

    video = conn.execute(
        "SELECT * FROM videos WHERE id = ?",
        (video_id,)
    ).fetchone()

    if video:

        if video["filename"]:

            path = os.path.join(
                UPLOAD_FOLDER,
                video["filename"]
            )

            if os.path.exists(path):

                os.remove(path)

        conn.execute(
            "DELETE FROM videos WHERE id = ?",
            (video_id,)
        )

        conn.commit()

        flash(
            "Post deleted by administrator.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("admin_panel")
    )


# =========================================================
# START DATABASE
# =========================================================


# =========================================================
# USER PROFILE
# =========================================================

@app.route("/profile/<int:user_id>")
def profile(user_id):

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

    if not user:
        conn.close()

        flash(
            "User profile not found.",
            "error"
        )

        return redirect(
            url_for("home")
        )

    posts = conn.execute("""
        SELECT *
        FROM videos
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    subscribers = conn.execute("""
        SELECT COUNT(*)
        FROM subscriptions
        WHERE subscribed_to_id = ?
    """, (user_id,)).fetchone()[0]

    following = conn.execute("""
        SELECT COUNT(*)
        FROM subscriptions
        WHERE subscriber_id = ?
    """, (user_id,)).fetchone()[0]

    is_subscribed = False

    if "user_id" in session:

        is_subscribed = conn.execute("""
            SELECT id
            FROM subscriptions
            WHERE subscriber_id = ?
            AND subscribed_to_id = ?
        """, (
            session["user_id"],
            user_id
        )).fetchone() is not None

    conn.close()

    return render_template(
        "profile.html",
        user=user,
        posts=posts,
        subscribers=subscribers,
        following=following,
        is_subscribed=is_subscribed
    )


# =========================================================
# SEARCH USERS
# =========================================================

@app.route("/users")
def users():

    search = request.args.get(
        "search",
        ""
    ).strip()

    conn = get_db()

    if search:

        users_list = conn.execute("""
            SELECT *
            FROM users
            WHERE name LIKE ?
               OR bio LIKE ?
            ORDER BY name ASC
        """, (
            f"%{search}%",
            f"%{search}%"
        )).fetchall()

    else:

        users_list = conn.execute("""
            SELECT *
            FROM users
            ORDER BY name ASC
        """).fetchall()

    conn.close()

    return render_template(
        "users.html",
        users=users_list,
        search=search
    )


# =========================================================
# SUBSCRIBE
# =========================================================

@app.route(
    "/subscribe/<int:user_id>",
    methods=["POST"]
)
@login_required
def subscribe(user_id):

    if user_id == session["user_id"]:

        flash(
            "You cannot subscribe to yourself.",
            "error"
        )

        return redirect(
            url_for(
                "profile",
                user_id=user_id
            )
        )

    conn = get_db()

    target = conn.execute("""
        SELECT id
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

    if not target:

        conn.close()

        flash(
            "User not found.",
            "error"
        )

        return redirect(
            url_for("users")
        )

    try:

        conn.execute("""
            INSERT INTO subscriptions
            (
                subscriber_id,
                subscribed_to_id
            )
            VALUES (?, ?)
        """, (
            session["user_id"],
            user_id
        ))

        conn.commit()

        flash(
            "You subscribed successfully.",
            "success"
        )

    except sqlite3.IntegrityError:

        flash(
            "You are already subscribed to this user.",
            "error"
        )

    except Exception as error:

        print(
            "Subscribe error:",
            error
        )

        flash(
            "Something went wrong.",
            "error"
        )

    conn.close()

    return redirect(
        url_for(
            "profile",
            user_id=user_id
        )
    )


# =========================================================
# UNSUBSCRIBE
# =========================================================

@app.route(
    "/unsubscribe/<int:user_id>",
    methods=["POST"]
)
@login_required
def unsubscribe(user_id):

    conn = get_db()

    conn.execute("""
        DELETE FROM subscriptions
        WHERE subscriber_id = ?
        AND subscribed_to_id = ?
    """, (
        session["user_id"],
        user_id
    ))

    conn.commit()
    conn.close()

    flash(
        "You unsubscribed successfully.",
        "success"
    )

    return redirect(
        url_for(
            "profile",
            user_id=user_id
        )
    )


# =========================================================
# EDIT PROFILE
# =========================================================

@app.route(
    "/edit-profile",
    methods=["GET", "POST"]
)
@login_required
def edit_profile():

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (
        session["user_id"],
    )).fetchone()

    conn.close()

    if request.method == "POST":

        try:

            name = request.form.get(
                "name",
                ""
            ).strip()

            bio = request.form.get(
                "bio",
                ""
            ).strip()

            profile_file = request.files.get(
                "profile_image"
            )

            if not name:

                flash(
                    "Name cannot be empty.",
                    "error"
                )

                return redirect(
                    url_for("edit_profile")
                )

            filename = user["profile_image"] or ""

            if (
                profile_file
                and profile_file.filename
            ):

                allowed_images = {
                    "jpg",
                    "jpeg",
                    "png",
                    "gif",
                    "webp"
                }

                original = secure_filename(
                    profile_file.filename
                )

                if (
                    "." not in original
                    or original.rsplit(
                        ".",
                        1
                    )[1].lower()
                    not in allowed_images
                ):

                    flash(
                        "Please upload a valid image.",
                        "error"
                    )

                    return redirect(
                        url_for("edit_profile")
                    )

                extension = original.rsplit(
                    ".",
                    1
                )[1].lower()

                filename = (
                    f"profile_{session['user_id']}_"
                    f"{uuid4().hex[:10]}.{extension}"
                )

                profile_file.save(
                    os.path.join(
                        UPLOAD_FOLDER,
                        filename
                    )
                )

            conn = get_db()

            conn.execute("""
                UPDATE users
                SET name = ?,
                    bio = ?,
                    profile_image = ?
                WHERE id = ?
            """, (
                name,
                bio,
                filename,
                session["user_id"]
            ))

            conn.commit()
            conn.close()

            session["user_name"] = name

            flash(
                "Profile updated successfully.",
                "success"
            )

            return redirect(
                url_for(
                    "profile",
                    user_id=session["user_id"]
                )
            )

        except Exception as error:

            print(
                "Edit profile error:",
                error
            )

            flash(
                "Something went wrong while updating your profile.",
                "error"
            )

            return redirect(
                url_for("edit_profile")
            )

    return render_template(
        "edit_profile.html",
        user=user
    )


# =========================================================
# SETTINGS
# =========================================================

@app.route("/settings")
@login_required
def settings():

    return render_template(
        "settings.html"
    )


# =========================================================
# CHANGE PASSWORD
# =========================================================

@app.route(
    "/change-password",
    methods=["POST"]
)
@login_required
def change_password():

    current_password = request.form.get(
        "current_password",
        ""
    )

    new_password = request.form.get(
        "new_password",
        ""
    )

    confirm_password = request.form.get(
        "confirm_password",
        ""
    )

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (
        session["user_id"],
    )).fetchone()

    if not check_password_hash(
        user["password"],
        current_password
    ):

        conn.close()

        flash(
            "Current password is incorrect.",
            "error"
        )

        return redirect(
            url_for("settings")
        )

    if len(new_password) < 6:

        conn.close()

        flash(
            "New password must contain at least 6 characters.",
            "error"
        )

        return redirect(
            url_for("settings")
        )

    if new_password != confirm_password:

        conn.close()

        flash(
            "New passwords do not match.",
            "error"
        )

        return redirect(
            url_for("settings")
        )

    conn.execute("""
        UPDATE users
        SET password = ?
        WHERE id = ?
    """, (
        generate_password_hash(new_password),
        session["user_id"]
    ))

    conn.commit()
    conn.close()

    flash(
        "Password changed successfully.",
        "success"
    )

    return redirect(
        url_for("settings")
    )



init_db()


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
