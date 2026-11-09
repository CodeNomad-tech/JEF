import os
import re
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

import app as website


class ReportRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_reports_file = website.REPORTS_FILE
        cls.original_reports_dir = website.REPORT_FILES_DIR
        cls.original_secret_key = website.app.config["SECRET_KEY"]
        website.REPORTS_FILE = os.path.join(cls.temp_dir.name, "reports.json")
        website.REPORT_FILES_DIR = os.path.join(cls.temp_dir.name, "reports")
        os.makedirs(website.REPORT_FILES_DIR)
        cls.environment = patch.dict(
            os.environ,
            {"ADMIN_PASSWORD": "test-admin-password", "SECRET_KEY": "test-secret-key"},
        )
        cls.environment.start()
        website.app.config["SECRET_KEY"] = "test-secret-key"
        website.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        website.REPORTS_FILE = cls.original_reports_file
        website.REPORT_FILES_DIR = cls.original_reports_dir
        website.app.config["SECRET_KEY"] = cls.original_secret_key
        cls.temp_dir.cleanup()

    def setUp(self):
        if os.path.exists(website.REPORTS_FILE):
            os.remove(website.REPORTS_FILE)
        for filename in os.listdir(website.REPORT_FILES_DIR):
            os.remove(os.path.join(website.REPORT_FILES_DIR, filename))
        self.client = website.app.test_client()

    def csrf_token(self):
        response = self.client.get("/admin/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def sign_in(self):
        token = self.csrf_token()
        response = self.client.post(
            "/admin/login",
            data={"csrf_token": token, "password": "test-admin-password"},
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_requires_password_and_csrf(self):
        self.assertEqual(self.client.get("/admin/reports").status_code, 302)
        token = self.csrf_token()
        response = self.client.post(
            "/admin/login",
            data={"csrf_token": token, "password": "wrong-password"},
            follow_redirects=True,
        )
        self.assertIn(b"The password is incorrect.", response.data)
        self.assertEqual(
            self.client.post("/admin/login", data={"password": "test-admin-password"}).status_code,
            400,
        )

    def test_admin_can_publish_and_visitors_can_view_pdf_report(self):
        self.sign_in()
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            self.client.get("/admin/reports").data,
        ).group(1).decode()
        response = self.client.post(
            "/admin/reports",
            data={
                "csrf_token": token,
                "title": "Annual Impact Report",
                "description": "Results from the year.",
                "pdf": (BytesIO(b"%PDF-1.4 test report"), "impact.pdf", "application/pdf"),
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n image"), "impact.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        listing = self.client.get("/reports")
        self.assertIn(b"Annual Impact Report", listing.data)
        self.assertIn(b"/image", listing.data)
        self.assertIn(b"/image", self.client.get("/").data)
        report_id = website.load_reports()[0]["id"]
        details = self.client.get(f"/reports/{report_id}")
        self.assertIn(b"Full report: Annual Impact Report", details.data)
        self.assertIn(b"/image", details.data)
        image = self.client.get(f"/reports/{report_id}/image")
        self.assertEqual(image.mimetype, "image/png")
        self.assertTrue(image.data.startswith(b"\x89PNG\r\n\x1a\n"))
        image.close()
        pdf = self.client.get(f"/reports/{report_id}/pdf")
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF-"))
        pdf.close()

    def test_rejects_invalid_report_image(self):
        self.sign_in()
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            self.client.get("/admin/reports").data,
        ).group(1).decode()
        response = self.client.post(
            "/admin/reports",
            data={
                "csrf_token": token,
                "title": "Invalid image",
                "pdf": (BytesIO(b"%PDF-1.4 test report"), "report.pdf", "application/pdf"),
                "image": (BytesIO(b"not an image"), "report.png", "image/png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Please upload a valid PNG, JPEG, GIF or WebP image", response.data)
        self.assertEqual(website.load_reports(), [])

    def test_admin_can_delete_report_and_uploaded_files(self):
        self.sign_in()
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            self.client.get("/admin/reports").data,
        ).group(1).decode()
        self.client.post(
            "/admin/reports",
            data={
                "csrf_token": token,
                "title": "Report to delete",
                "pdf": (BytesIO(b"%PDF-1.4 test report"), "report.pdf", "application/pdf"),
                "image": (BytesIO(b"\x89PNG\r\n\x1a\n image"), "report.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        report = website.load_reports()[0]
        pdf_path = os.path.join(website.REPORT_FILES_DIR, report["filename"])
        image_path = os.path.join(website.REPORT_FILES_DIR, report["image_filename"])
        self.assertTrue(os.path.exists(pdf_path))
        self.assertTrue(os.path.exists(image_path))

        listing = self.client.get("/admin/reports")
        self.assertIn(b"Delete report", listing.data)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', listing.data).group(1).decode()
        response = self.client.post(
            f"/admin/reports/{report['id']}/delete",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(website.load_reports(), [])
        self.assertFalse(os.path.exists(pdf_path))
        self.assertFalse(os.path.exists(image_path))
        self.assertEqual(self.client.get(f"/reports/{report['id']}").status_code, 404)

    def test_report_delete_requires_admin_and_csrf(self):
        self.assertEqual(self.client.post("/admin/reports/missing/delete").status_code, 302)
        self.sign_in()
        self.assertEqual(self.client.post("/admin/reports/missing/delete").status_code, 400)

    def test_rejects_non_pdf_upload(self):
        self.sign_in()
        token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            self.client.get("/admin/reports").data,
        ).group(1).decode()
        response = self.client.post(
            "/admin/reports",
            data={
                "csrf_token": token,
                "title": "Not a PDF",
                "pdf": (BytesIO(b"not a PDF"), "report.pdf", "application/pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"does not appear to be a valid PDF", response.data)
        self.assertEqual(website.load_reports(), [])

    def test_admin_is_disabled_without_deployment_secrets(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "test-admin-password"}, clear=True):
            response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
