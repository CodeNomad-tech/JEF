"""JEF website: Flask version of the prototype."""
import json
import hmac
import os
import secrets
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
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

ORG = {
    "name": "Jonuel Entrepreneurship Foundation (JEF)",
    "email": "jonuelentrepreneurshipfound@gmail.com",
}


@app.context_processor
def inject_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"org": ORG, "year": datetime.now().year, "csrf_token": session["csrf_token"]}


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


@app.get("/reports")
def reports():
    return render_template("reports.html", reports=load_reports())


@app.get("/reports/<report_id>")
def report_detail(report_id):
    return render_template("report.html", report=find_report(report_id))


@app.get("/reports/<report_id>/pdf")
def report_pdf(report_id):
    report = find_report(report_id)
    return send_from_directory(
        REPORT_FILES_DIR,
        report["filename"],
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
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not admin_password or not os.environ.get("SECRET_KEY"):
        return render_template("admin_login.html", configuration_error=True), 503

    if request.method == "POST":
        if not valid_csrf_token():
            abort(400)
        if hmac.compare_digest(request.form.get("password", ""), admin_password):
            session.clear()
            session["admin_authenticated"] = True
            return redirect(url_for("admin_reports"))
        flash("The password is incorrect.", "error")

    return render_template("admin_login.html", configuration_error=False)


@app.route("/admin/reports", methods=["GET", "POST"])
def admin_reports():
    if not os.environ.get("ADMIN_PASSWORD") or not os.environ.get("SECRET_KEY"):
        return render_template("admin_login.html", configuration_error=True), 503
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        if not valid_csrf_token():
            abort(400)
        title = request.form.get("title", "").strip()[:160]
        description = request.form.get("description", "").strip()[:2000]
        uploaded_file = request.files.get("pdf")
        uploaded_image = request.files.get("image")
        if not title or uploaded_file is None or not uploaded_file.filename:
            flash("A report title and PDF file are required.", "error")
            return redirect(url_for("admin_reports"))
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
        filename = f"{report_id}.pdf"
        image_filename = f"{report_id}{image_extension}" if image_extension else None
        report = {
            "id": report_id,
            "title": title,
            "description": description,
            "filename": filename,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        if image_filename:
            report["image_filename"] = image_filename
        reports.insert(0, report)
        pdf_path = os.path.join(REPORT_FILES_DIR, filename)
        image_path = os.path.join(REPORT_FILES_DIR, image_filename) if image_filename else None
        try:
            uploaded_file.save(pdf_path)
            if image_filename:
                uploaded_image.save(image_path)
            save_reports(reports)
        except (OSError, TypeError, ValueError):
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
            if image_path and os.path.exists(image_path):
                os.remove(image_path)
            raise
        flash("The report has been published.", "ok")
        return redirect(url_for("admin_reports"))

    return render_template("admin_reports.html", reports=load_reports())


@app.post("/admin/reports/<report_id>/delete")
def delete_report(report_id):
    if not os.environ.get("ADMIN_PASSWORD") or not os.environ.get("SECRET_KEY"):
        return render_template("admin_login.html", configuration_error=True), 503
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
