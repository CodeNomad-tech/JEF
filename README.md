# JEF website (Flask)

## Run locally
    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    flask --app app run --debug
Open http://127.0.0.1:5000

## Structure
    app.py                 site routes, contact handling, report publishing and optional SMTP
    templates/base.html    header, footer (copyright), layout
    templates/index.html   all page sections
    templates/admin_*.html password-protected report publishing screens
    templates/reports.html public report list
    templates/report.html  report details and PDF viewer
    static/css/style.css   styles (colours are CSS variables at the top)
    static/img/            founder.jpg, jimmy.jpg, team-member.jpg

## Contact form
Every message is saved to instance/messages.jsonl. To also email it to JEF,
set the MAIL_* variables from .env.example (Gmail needs an App Password).

## Reports
Public reports are listed at `/reports`. Each report has a page with its
description and an embedded PDF viewer. To publish one, set `ADMIN_PASSWORD`
and a strong, unique `SECRET_KEY`, then sign in at `/admin/login`. For local
development, copy `.env.example` to `.env` and set both values; the app loads
`.env` automatically. For production, configure both as secrets in your
hosting provider. Admins can optionally add a PNG, JPEG, GIF or WebP image
(up to 5 MB) when publishing a PDF; both files together are limited to 20 MB.
Admins can delete published reports from the report management page.

Report metadata and uploaded PDFs and images are stored in the `instance/`
directory. Keep this directory on persistent storage when deploying the site.

## Deploy
    gunicorn app:app
Set `SECRET_KEY` and `ADMIN_PASSWORD` in the host's environment settings.
