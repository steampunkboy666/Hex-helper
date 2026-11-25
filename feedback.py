"""Player-reviewed feedback packages and optional HTTPS submission."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import platform
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from PIL import Image, ImageOps
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWizard, QWizardPage)

import webutil


MAX_PACKAGE_SIZE = 1024 * 1024
SCREENSHOT_BUDGET = MAX_PACKAGE_SIZE - 128 * 1024
API_PATH = "/v1/feedback"


def diagnostics(helper):
    meta = helper.data.meta
    return "\n".join((
        f"Hex Helper {webutil.VERSION}",
        f"Windows {platform.version()} · {platform.machine()}",
        f"Region: {helper.settings['region']}",
        f"Data: {meta.get('source', '?')} {meta.get('version', '?')} {meta.get('updated', '?')}",
        f"OCR: {helper.ocr.engine_label} · ready={helper.ocr.ready}",
        f"Client phase: {helper.phase}",
        f"Panel scale: {helper.settings['ui_scale']}; offer scale: {helper.settings['offer_scale']}",
        *[f"[{item['level']}] {item['name']}: {item['detail']}"
          for item in helper.last_checks
          if item["name"].startswith(("系统版本", "显示器", "显卡", "处理器", "海克斯识别", "网络"))],
    ))


def read_screenshot(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    for dimension, quality in ((2560, 91), (2560, 80), (1920, 75), (1440, 68)):
        image.thumbnail((dimension, dimension))
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True)
        if buffer.tell() <= SCREENSHOT_BUDGET:
            return buffer.getvalue()
    raise ValueError("截图超过反馈包大小限制")


def package_bytes(screenshot, description, technical):
    files = (("screenshot.jpg", screenshot),
             ("description.txt", (description.strip() + "\n").encode("utf-8")),
             ("diagnostics.txt", (technical + "\n").encode("utf-8")))
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, data in files:
            # Stable timestamps keep the receipt the same when a request is retried.
            bundle.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
    return stream.getvalue()


def save_package(path, screenshot, description, technical):
    Path(path).write_bytes(package_bytes(screenshot, description, technical))


def upload_endpoint(url):
    if not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme == "https" and host.endswith(".workers.dev") and parts.netloc == host
                and parts.path == API_PATH and not parts.query and not parts.fragment):
            return parts.geturl()
    except ValueError:
        pass
    return ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def upload_package(endpoint, content):
    if not upload_endpoint(endpoint):
        raise ValueError("无效的反馈上传地址")
    if len(content) > MAX_PACKAGE_SIZE:
        raise ValueError("反馈包超过 1 MB，请裁剪截图后重试")
    request = Request(endpoint, content, headers={
        "Content-Type": "application/zip", "Accept": "application/json", "User-Agent": webutil.UA,
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            reply = json.loads(response.read(4096))
            status = response.status
    except HTTPError as exc:
        if exc.code == 429:
            raise ValueError("提交太频繁，请稍等一分钟再试") from exc
        raise ValueError(f"接收服务返回 HTTP {exc.code}") from exc
    receipt = sha256(content).hexdigest()[:32]
    if status not in (200, 201) or not isinstance(reply, dict) or reply.get("id") != receipt:
        raise ValueError("接收服务未确认反馈包")
    return receipt


class RequiredPage(QWizardPage):
    def __init__(self, title, complete):
        super().__init__()
        self.setTitle(title)
        self.complete = complete

    def isComplete(self):
        return self.complete()


class FeedbackDialog(QWizard):
    uploaded = Signal(str, str)

    def __init__(self, helper, tr):
        super().__init__(helper.control)
        self.tr = tr
        self.screenshot = None
        self.endpoint = upload_endpoint(helper.settings["feedback_url"])
        self.uploading = False
        self.uploaded.connect(self.upload_result)
        self.setWindowTitle(tr("反馈问题"))
        self.resize(610, 480)
        self.setButtonText(QWizard.BackButton, tr("上一步"))
        self.setButtonText(QWizard.NextButton, tr("下一步"))
        self.setButtonText(QWizard.FinishButton, tr("提交反馈" if self.endpoint else "保存反馈包"))
        self.setButtonText(QWizard.CancelButton, tr("取消"))

        self.image_page = RequiredPage(tr("添加问题截图"), lambda: self.screenshot is not None)
        layout = QVBoxLayout(self.image_page)
        pick = QPushButton(tr("选择截图"))
        pick.clicked.connect(self.pick_image)
        layout.addWidget(pick)
        self.preview = QLabel(tr("请选择一张显示问题的截图"))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(245)
        layout.addWidget(self.preview, 1)
        self.addPage(self.image_page)

        self.description = QPlainTextEdit()
        page = RequiredPage(tr("描述问题"), lambda: bool(self.description.toPlainText().strip()))
        self.description.setPlaceholderText(tr("你做了什么，预期发生什么，实际上发生了什么？"))
        self.description.textChanged.connect(page.completeChanged.emit)
        QVBoxLayout(page).addWidget(self.description)
        self.addPage(page)

        self.technical = diagnostics(helper)
        page = QWizardPage()
        page.setTitle(tr("确认并提交" if self.endpoint else "确认诊断并打包"))
        layout = QVBoxLayout(page)
        summary = QLabel(tr("提交会向以下地址发送所选截图、描述和诊断。") + "\n" + self.endpoint
                         if self.endpoint else tr("反馈包包含所选截图、问题描述和下方诊断。"))
        summary.setTextFormat(Qt.PlainText)
        if self.endpoint:
            save = QPushButton(tr("另存反馈包"))
            save.clicked.connect(self.save_local)
            layout.addWidget(save)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        view = QPlainTextEdit(self.technical)
        view.setReadOnly(True)
        layout.addWidget(view)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.addPage(page)

    def pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("选择问题截图"), "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        try:
            image = read_screenshot(path)
        except (OSError, ValueError, Image.DecompressionBombError):
            QMessageBox.warning(self, self.tr("无法读取截图"), self.tr("请重新选择一张有效的图片。"))
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image):
            return
        self.screenshot = image
        self.preview.setPixmap(pixmap.scaled(520, 245, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.image_page.completeChanged.emit()

    def accept(self):
        description = self.description.toPlainText().strip()
        if self.uploading or self.screenshot is None or not description:
            return
        if not self.endpoint:
            if self.save_local():
                super().accept()
            return
        if len(description.encode("utf-8")) > 32_767:
            self.status.setText(self.tr("描述过长，请缩短后重试。"))
            return
        content = package_bytes(self.screenshot, description, self.technical)
        self.set_uploading(True)
        self.status.setText(self.tr("正在上传反馈包…"))

        def send():
            try:
                receipt = upload_package(self.endpoint, content)
            except (OSError, ValueError) as exc:
                self.uploaded.emit("", str(exc))
            else:
                self.uploaded.emit(receipt, "")

        threading.Thread(target=send, daemon=True).start()

    def set_uploading(self, active):
        self.uploading = active
        for key in (QWizard.BackButton, QWizard.FinishButton, QWizard.CancelButton):
            self.button(key).setEnabled(not active)

    def upload_result(self, receipt, error):
        self.set_uploading(False)
        if error:
            self.status.setText(self.tr("上传失败，可重试或另存反馈包。") + "\n" + self.tr(error))
            return
        QMessageBox.information(self, self.tr("反馈已收到"), self.tr("反馈编号") + " " + receipt)
        super().accept()

    def reject(self):
        if not self.uploading:
            super().reject()

    def save_local(self):
        if self.screenshot is None or not self.description.toPlainText().strip():
            return False
        path, _ = QFileDialog.getSaveFileName(self, self.tr("保存反馈包"),
                                              "HexHelper-feedback.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            save_package(path, self.screenshot, self.description.toPlainText(), self.technical)
        except OSError as exc:
            QMessageBox.warning(self, self.tr("保存失败"), str(exc))
            return
        if self.endpoint:
            self.status.setText(self.tr("反馈包已保存") + "\n" + str(Path(path)))
        else:
            QMessageBox.information(self, self.tr("反馈包已保存"), str(Path(path)))
        return True
