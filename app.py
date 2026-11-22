"""JEF website: Flask version of the prototype."""
import json
import hmac
import os
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__, instance_relative_config=True)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
MAX_REPORT_IMAGE_SIZE = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
os.makedirs(app.instance_path, exist_ok=True)

REPORTS_FILE = os.path.join(app.instance_path, "reports.json")
REPORT_FILES_DIR = os.path.join(app.instance_path, "reports")
os.makedirs(REPORT_FILES_DIR, exist_ok=True)

# ---- Admin database ----
DB_PATH = os.path.join(app.instance_path, "jef.db")


def get_db():
    """Return a connection to the admin SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the admin_users table and seed the default admin account."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    # Seed the default admin if no admin exists yet
    row = conn.execute("SELECT id FROM admin_users WHERE username = ?", ("admin",)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("JEF@003")),
        )
        conn.commit()
    conn.close()


init_db()

ORG = {
    "name": "Jonuel Entrepreneurship Foundation (JEF)",
    "email": "jonuelentrepreneurshipfound@gmail.com",
}


def F(n, l, t="text", req=False, opts=None):
    return (n, l, t, req, opts or [])


FORMS = {
    "contact": {"title": "Contact form", "button": "Send message", "ok": "Thank you for contacting JEF. We will respond as soon as possible.",
        "fields": [F("name", "Full name", req=True), F("email", "Email", "email", True), F("phone", "Phone", "tel"), F("subject", "Subject"), F("message", "Message", "textarea", True)]},
    "nominate": {"title": "Nominate a young person", "button": "Submit Nomination", "ok": "Thank you. Your nomination has been received.",
        "fields": [F("name", "Your name", req=True), F("organisation", "Your organisation"), F("position", "Your position"), F("email", "Your email", "email", True), F("phone", "Your phone"),
                   F("nominee_name", "Nominee’s name", req=True), F("nominee_phone", "Nominee’s phone"),
                   F("category", "Category", "select", True, ["Grade 12 school leaver", "College or university student or graduate", "Community, faith group or informal sector (including local entrepreneur)"]),
                   F("reason", "Why are you nominating this person?", "textarea", True)]},
    "partner": {"title": "Partner with JEF", "button": "Submit", "ok": "Thank you. We will be in touch about partnering with JEF.",
        "fields": [F("name", "Name", req=True), F("organisation", "Organisation"), F("email", "Email", "email", True), F("phone", "Phone"),
                   F("how", "How would you like to partner?", "select", True, ["Programme collaboration", "Funding and sponsorship", "Technical expertise", "Internships and mentorship", "In-kind contribution", "Other"]),
                   F("message", "Message", "textarea")]},
    "volunteer": {"title": "Volunteer or mentor", "button": "Sign Up", "ok": "Thank you for offering your time. JEF will contact you soon.",
        "fields": [F("name", "Name", req=True), F("email", "Email", "email", True), F("phone", "Phone"), F("expertise", "Area of expertise", req=True), F("availability", "Availability")]},
    "movement": {"title": "Join the Financial Awareness Movement", "button": "Submit", "ok": "Thank you. JEF will be in touch about the Financial Awareness Movement.",
        "fields": [F("name", "Name", req=True), F("organisation", "Organisation"), F("email", "Email", "email", True), F("phone", "Phone"), F("interest", "Area of interest"), F("message", "Message", "textarea")]},
    "enquiry": {"title": "Financial literacy partnership enquiry", "button": "Submit", "ok": "Thank you. Your enquiry has been received.",
        "fields": [F("name", "Full name", req=True), F("organisation", "Organisation"), F("position", "Position"), F("email", "Email", "email", True), F("phone", "Phone"),
                   F("interest", "Type of interest", "select", True, ["Partner with the movement", "Become an Ambassador", "Establish an FA Association", "Other"]), F("message", "Message", "textarea")]},
    "newsletter": {"title": "Newsletter", "button": "Subscribe", "ok": "Thank you for subscribing.", "fields": [F("email", "Email address", "email", True)]},
}

TEAM = [  # photo: path under static/ (e.g. "img/name.jpg"). Leave None to show the reserved photo space.
    {"name": "Mr. Jimmy Sakala", "role": "Head of IT", "photo": "img/jimmy.jpg", "bio": ""},
    {"name": "Mr. Chisenga Mumba", "role": "Deputy Executive Director", "photo": "img/JEF2.jpeg", "bio": "", "imgstyle": "object-position:center 20%"},
    {"name": "Mr. Prince Kapeya", "role": "Chief Operations Officer", "photo": "img/JEF4.jpeg", "bio": ""},
    {"name": "Dr. Wakisa Ndovie", "role": "Programs Manager", "photo": "img/JEF5.jpeg", "bio": ""},
    {"name": "Ms. Harriet Lungu", "role": "Executive Secretary", "photo": "img/JEF6.jpeg", "bio": ""},
    {"name": "Ms. Isabel Biemba", "role": "Head of Finance", "photo": "img/JEF7.jpeg", "bio": ""},
    {"name": "Mr. Bill Sam Geebli", "role": "Logistics and Resource Manager", "photo": "img/JEF8.jpeg", "bio": ""},
    {"name": "Ms. Choolwe Chiiko", "role": "Academics Coordinator", "photo": "img/JEF9.jpeg", "bio": ""},
    {"name": "Ms. Monde Kalimukwa", "role": "Communications & Advocacy Coordinator", "photo": "img/JEF10.jpeg", "bio": ""},
    {"name": "Mr. Mwitila Lutangu", "role": "Youth Coordinator", "photo": "img/JEF11.jpeg", "bio": ""},
    {"name": "Mr. Lucky Mukubonda C.C", "role": "Graphic Designer", "photo": "img/JEF12.jpeg", "bio": ""},
    {"name": "Ms. Gertrude Kambeu", "role": "President", "photo": "img/JEF13.jpeg", "bio": ""},
    {"name": "Mr. Ignatius Ndalameyu", "role": "Vice President", "photo": "img/JEF14.jpeg", "bio": ""},
    {"name": "Mr. Mpulu Polota", "role": "Secretary General", "photo": "img/JEF15.jpeg", "bio": ""},
    {"name": "Ms. Mayamba Kekelwa", "role": "Deputy Secretary General", "photo": "img/JEF16.jpeg", "bio": ""},
    {"name": "Mr. Mathews Sababwa", "role": "Treasurer", "photo": "img/JEF17.jpeg", "bio": ""},
    {"name": "Ms. Kasiwa Namushi", "role": "Deputy Treasurer", "photo": "img/JEF18.jpeg", "bio": ""},
    {"name": "Ms. Njekwa In'utu", "role": "Programs Manager", "photo": "img/JEF19.jpeg", "bio": ""},
    {"name": "Mr. Luneta Jimmy", "role": "Research and Education Director", "photo": "img/JEF20.jpeg", "bio": ""},
    {"name": "Ms. Naomi Chibesa", "role": "Events Coordinator and Public Relations Officer", "photo": "img/JEF21.jpeg", "bio": ""},
]

APR = [("lLc5lMFc-WQ", "Official Online Launch"), ("8Rcb563SF54", "Week 1 - Financial Literacy Series"), ("haRpuPpGbd8", "Week 2 - Financial Literacy Series"),
       ("6NpnkdzFOMs", "Week 3 - Financial Literacy Series"), ("lpO1QYHCXtg", "Week 4 - Financial Literacy Series")]


@app.context_processor
def inject_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"org": ORG, "year": datetime.now().year, "csrf_token": session["csrf_token"], "FORMS": FORMS, "TEAM": TEAM, "APR": APR}


@app.after_request
def log_page_view(resp):
    """Basic privacy-friendly analytics: path + time only, no personal data."""
    if request.method == "GET" and resp.status_code == 200 and resp.mimetype == "text/html" and not request.path.startswith("/admin"):
        try:
            with open(os.path.join(app.instance_path, "pageviews.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "path": request.path}) + "\n")
        except OSError:
            pass
    return resp


def load_reports():
    if not os.path.exists(REPORTS_FILE):
        return []
    with open(REPORTS_FILE, encoding="utf-8") as reports_file:
        return json.load(reports_file)


def save_reports(reports):
    temporary_file = f"{REPORTS_FILE}.{secrets.token_hex(8)}.tmp"
    try:
        with open(temporary_file, "w", encoding="utf-8") as reports_file:
            json.dump(reports, reports_file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, REPORTS_FILE)
    finally:
        if os.path.exists(temporary_file):
            os.remove(temporary_file)


def valid_csrf_token():
    submitted_token = request.form.get("csrf_token", "")
    stored_token = session.get("csrf_token", "")
    return bool(stored_token and submitted_token and hmac.compare_digest(stored_token, submitted_token))


def find_report(report_id):
    report = next((item for item in load_reports() if item["id"] == report_id), None)
    if report is None:
        abort(404)
    return report


def valid_image_extension(uploaded_image):
    extension = os.path.splitext(secure_filename(uploaded_image.filename))[1].lower()
    signatures = {
        ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        ".gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
        ".webp": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    is_valid_image = signatures.get(extension)
    if is_valid_image is None:
        return None

    image_data = uploaded_image.stream.read(MAX_REPORT_IMAGE_SIZE + 1)
    uploaded_image.stream.seek(0)
    if len(image_data) > MAX_REPORT_IMAGE_SIZE or not is_valid_image(image_data):
        return None
    return extension


def send_email(name, sender, message):
    """Send via SMTP if MAIL_* variables are set, else return False."""
    host = os.environ.get("MAIL_SERVER")
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Website enquiry from {name}"
    msg["From"] = os.environ.get("MAIL_USERNAME", ORG["email"])
    msg["To"] = os.environ.get("MAIL_TO", ORG["email"])
    msg["Reply-To"] = sender
    msg.set_content(f"From: {name} <{sender}>\n\n{message}")
    with smtplib.SMTP(host, int(os.environ.get("MAIL_PORT", 587)), timeout=15) as s:
        s.starttls()
        s.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        s.send_message(msg)
    return True


def save_message(name, sender, message):
    """Keep a copy of every enquiry in instance/messages.jsonl."""
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "email": sender,
        "message": message,
    }
    with open(os.path.join(app.instance_path, "messages.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@app.get("/")
def index():
    return render_template("index.html", reports=load_reports()[:3])


def page(name):
    key = name.replace("-", "_")
    app.add_url_rule("/" + name, key + "_page", lambda: render_template(key + ".html"))


for _p in ("about", "what-we-do", "financial-literacy", "yesdf", "get-involved", "contact"):
    page(_p)


@app.get("/impact")
def impact_page():
    return render_template("impact.html", reports=load_reports())


@app.get("/news")
def news():
    return render_template("news.html", reports=load_reports())


@app.post("/submit/<kind>")
def submit(kind):
    spec = FORMS.get(kind) or abort(404)
    if not valid_csrf_token():
        abort(400)
    nxt = request.form.get("next", "")
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index")
    if request.form.get("website"):
        return redirect(nxt)
    data, missing = {}, False
    for n, label, t, req, opts in spec["fields"]:
        v = request.form.get(n, "").strip()[:3000]
        if req and not v or (t == "email" and v and "@" not in v):
            missing = True
        data[label] = v
    if missing:
        flash("Please complete the required fields and use a valid email address.", "error")
        return redirect(nxt)
    record = {"time": datetime.now(timezone.utc).isoformat(), "form": kind, **data}
    with open(os.path.join(app.instance_path, "submissions.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    email = next((v for (n, l, t, r, o), v in zip(spec["fields"], data.values()) if t == "email"), "")
    body = "\n".join(f"{k}: {v}" for k, v in data.items())
    try:
        send_email(f"{spec['title']} ({data.get('Full name') or data.get('Name') or data.get('Your name') or 'website'})", email, body)
    except Exception:
        app.logger.exception("Email sending failed")
    flash(spec["ok"], "ok")
    return redirect(nxt)


@app.get("/reports")
def reports():
    return render_template("reports.html", reports=load_reports())


@app.get("/reports/<report_id>")
def report_detail(report_id):
    return render_template("report.html", report=find_report(report_id))


@app.get("/reports/<report_id>/pdf")
def report_pdf(report_id):
    report = find_report(report_id)
    filename = report.get("filename")
    if not filename:
        abort(404)
    return send_from_directory(
        REPORT_FILES_DIR,
        filename,
        mimetype="application/pdf",
        as_attachment=request.args.get("download") == "1",
        download_name=f"{secure_filename(report['title']) or 'report'}.pdf",
    )


@app.get("/reports/<report_id>/image")
def report_image(report_id):
    report = find_report(report_id)
    filename = report.get("image_filename")
    if not filename:
        abort(404)
    mimetype = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(os.path.splitext(filename)[1].lower())
    if not mimetype:
        abort(404)
    return send_from_directory(REPORT_FILES_DIR, filename, mimetype=mimetype)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if not valid_csrf_token():
            abort(400)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["admin_authenticated"] = True
            return redirect(url_for("admin_reports"))
        flash("Invalid username or password.", "error")

    return render_template("admin_login.html", configuration_error=False)


@app.route("/admin/reports", methods=["GET", "POST"])
def admin_reports():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        if not valid_csrf_token():
            abort(400)
        title = request.form.get("title", "").strip()[:160]
        description = request.form.get("description", "").strip()[:2000]
        uploaded_file = request.files.get("pdf")
        uploaded_image = request.files.get("image")
        if not title:
            flash("A report title is required.", "error")
            return redirect(url_for("admin_reports"))

        has_pdf = bool(uploaded_file and uploaded_file.filename)
        if has_pdf:
            if not secure_filename(uploaded_file.filename).lower().endswith(".pdf"):
                flash("Please upload a PDF file.", "error")
                return redirect(url_for("admin_reports"))
            if uploaded_file.stream.read(5) != b"%PDF-":
                flash("The uploaded file does not appear to be a valid PDF.", "error")
                return redirect(url_for("admin_reports"))
            uploaded_file.stream.seek(0)

        image_extension = None
        if uploaded_image is not None and uploaded_image.filename:
            image_extension = valid_image_extension(uploaded_image)
            if image_extension is None:
                flash("Please upload a valid PNG, JPEG, GIF or WebP image no larger than 5 MB.", "error")
                return redirect(url_for("admin_reports"))

        reports = load_reports()
        report_id = uuid.uuid4().hex
        filename = f"{report_id}.pdf" if has_pdf else None
        image_filename = f"{report_id}{image_extension}" if image_extension else None
        report = {
            "id": report_id,
            "title": title,
            "description": description,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        if filename:
            report["filename"] = filename
        if image_filename:
            report["image_filename"] = image_filename
        reports.insert(0, report)
        pdf_path = os.path.join(REPORT_FILES_DIR, filename) if filename else None
        image_path = os.path.join(REPORT_FILES_DIR, image_filename) if image_filename else None
        try:
            if filename:
                uploaded_file.save(pdf_path)
            if image_filename:
                uploaded_image.save(image_path)
            save_reports(reports)
        except (OSError, TypeError, ValueError):
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            if image_path and os.path.exists(image_path):
                os.remove(image_path)
            raise
        flash("The report has been published.", "ok")
        return redirect(url_for("admin_reports"))

    return render_template("admin_reports.html", reports=load_reports())


@app.post("/admin/reports/<report_id>/delete")
def delete_report(report_id):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    if not valid_csrf_token():
        abort(400)

    reports = load_reports()
    report = next((item for item in reports if item["id"] == report_id), None)
    if report is None:
        abort(404)

    save_reports([item for item in reports if item["id"] != report_id])

    cleanup_failed = False
    for filename in (report.get("filename"), report.get("image_filename")):
        if not filename:
            continue
        if os.path.basename(filename) != filename:
            app.logger.warning("Cannot safely delete report file with invalid name: %r", filename)
            cleanup_failed = True
            continue
        try:
            os.remove(os.path.join(REPORT_FILES_DIR, filename))
        except FileNotFoundError:
            continue
        except OSError:
            app.logger.exception("Failed to delete uploaded file for report %s", report_id)
            cleanup_failed = True

    if cleanup_failed:
        flash("The report was removed, but some uploaded files could not be deleted.", "error")
    else:
        flash("The report has been deleted.", "ok")
    return redirect(url_for("admin_reports"))


@app.post("/admin/logout")
def admin_logout():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    if not valid_csrf_token():
        abort(400)
    session.clear()
    return redirect(url_for("admin_login"))


@app.post("/contact")
def contact():
    if request.form.get("website"):  # honeypot: bots fill this in
        return redirect(url_for("index") + "#contact")
    name = request.form.get("name", "").strip()[:100]
    sender = request.form.get("email", "").strip()[:150]
    message = request.form.get("msg", "").strip()[:3000]
    if not (name and message and "@" in sender):
        flash("Please fill in your name, a valid email and a message.", "error")
        return redirect(url_for("index") + "#contact")
    save_message(name, sender, message)
    try:
        if send_email(name, sender, message):
            flash("Thank you. Your message has been sent to JEF.", "ok")
        else:
            flash("Your message was saved, but email delivery is not configured. Please email us directly.", "error")
    except Exception:
        app.logger.exception("Email sending failed")
        flash("Your message was saved, but email delivery failed. Please email us directly.", "error")
    return redirect(url_for("index") + "#contact")


@app.errorhandler(404)
def not_found(_):
    return render_template("index.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
