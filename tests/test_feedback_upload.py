import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parent))
import feedback

ENDPOINT = "https://hex-helper-feedback.test.workers.dev/v1/feedback"


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.qt = QApplication.instance() or QApplication([])

    def test_worker_accepts_real_client_zip(self):
        with TemporaryDirectory() as directory:
            screenshot = Path(directory, "large.png")
            Image.effect_noise((2560, 1440), 95).save(screenshot)
            image = feedback.read_screenshot(screenshot)
            self.assertLessEqual(len(image), feedback.SCREENSHOT_BUDGET)
            report = Path(directory, "feedback.zip")
            feedback.save_package(report, image, "測試回報", "CN/TW mock")
            self.assertEqual(report.read_bytes(), feedback.package_bytes(image, "測試回報", "CN/TW mock"))
            with ZipFile(report) as bundle:
                self.assertEqual(bundle.namelist(), ["screenshot.jpg", "description.txt", "diagnostics.txt"])
                self.assertEqual(bundle.read("description.txt").decode(), "測試回報\n")
            subprocess.run(["node", str(TEST_DIR / "test_feedback_worker.mjs"), str(report)], check=True)

    def test_https_transport_and_receipt(self):
        self.assertEqual(feedback.upload_endpoint(ENDPOINT), ENDPOINT)
        for invalid in (ENDPOINT.replace("https:", "http:"), ENDPOINT + "?track=1",
                        ENDPOINT.replace("workers.dev", "workers.dev.example.com"),
                        ENDPOINT.replace("//", "//person@")):
            self.assertEqual(feedback.upload_endpoint(invalid), "")

        package = feedback.package_bytes(b"synthetic JPEG", "problem", "diagnostics")
        receipt = feedback.sha256(package).hexdigest()[:32]
        with patch.object(feedback, "build_opener") as factory:
            opener = factory.return_value
            reply = opener.open.return_value.__enter__.return_value
            reply.status = 201
            reply.read.return_value = json.dumps({"id": receipt}).encode()
            self.assertEqual(feedback.upload_package(ENDPOINT, package), receipt)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.data, package)
            self.assertEqual(request.get_header("Content-type"), "application/zip")
            self.assertIsNone(factory.call_args.args[0].redirect_request(None, None, 302, "", {}, "https://example.com"))
            reply.read.return_value = b'{"id":"wrong"}'
            with self.assertRaisesRegex(ValueError, "未确认"):
                feedback.upload_package(ENDPOINT, package)
            opener.open.side_effect = HTTPError(ENDPOINT, 429, "Too Many Requests", {}, None)
            with self.assertRaisesRegex(ValueError, "太频繁"):
                feedback.upload_package(ENDPOINT, package)

    def test_wizard_can_retry_and_save_after_network_failure(self):
        helper = SimpleNamespace(
            control=QWidget(), data=SimpleNamespace(meta={}),
            settings={"feedback_url": ENDPOINT, "region": "TW", "ui_scale": 1, "offer_scale": 1},
            ocr=SimpleNamespace(engine_label="mock", ready=True), phase="Lobby", last_checks=[],
        )
        dialog = feedback.FeedbackDialog(helper, lambda value: value)
        dialog.screenshot = b"test fixture"
        dialog.description.setPlainText("無法識別")
        dialog.show()
        dialog.next()
        dialog.next()
        expected = feedback.package_bytes(dialog.screenshot, "無法識別", dialog.technical)
        receipt = feedback.sha256(expected).hexdigest()[:32]

        with patch.object(feedback, "upload_package", side_effect=[OSError("offline"), receipt]) as send:
            dialog.accept()
            self.assertTrue(dialog.uploading)
            self.wait_upload(dialog)
            self.assertIn("上传失败", dialog.status.text())
            self.assertEqual(dialog.description.toPlainText(), "無法識別")
            with TemporaryDirectory() as directory:
                path = Path(directory, "saved.zip")
                with patch.object(feedback.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                    self.assertTrue(dialog.save_local())
                    self.assertEqual(path.read_bytes(), expected)
            with patch.object(feedback.QMessageBox, "information") as notice:
                dialog.accept()
                self.wait_upload(dialog)
                self.assertIn(receipt, notice.call_args.args[2])
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all(call.args == (ENDPOINT, expected) for call in send.call_args_list))

    def wait_upload(self, dialog):
        for _ in range(100):
            QTest.qWait(10)
            if not dialog.uploading:
                return
        self.fail("feedback worker thread did not signal completion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
