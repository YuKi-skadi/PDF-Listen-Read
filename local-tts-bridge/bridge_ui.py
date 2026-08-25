"""PySide6 desktop interface for the PDF local TTS bridge."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QPlainTextEdit, QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from resource_scanner import ResourceScan, RuntimeCandidate, scan_resource_root


class UiBus(QObject):
    event = Signal(str, object)


def format_time(value: str) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return value[:16]


class ConnectionDialog(QDialog):
    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("监听服务设置")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        title = QLabel("PDF 本地 TTS 中介")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("PDF 朗读器将通过这个地址提交生成任务。")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        form = QFormLayout()
        self.host = QLineEdit(host)
        self.host.setReadOnly(True)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(port)
        form.addRow("监听地址", self.host)
        form.addRow("监听端口", self.port)
        layout.addLayout(form)
        self.result_label = QLabel("")
        self.result_label.setObjectName("muted")
        layout.addWidget(self.result_label)
        self.test_button = QPushButton("测试端口")
        self.test_button.clicked.connect(self._test_port)
        layout.addWidget(self.test_button, alignment=Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test_port(self) -> None:
        import socket
        try:
            with socket.socket() as sock:
                sock.bind((self.host.text(), self.port.value()))
            self.result_label.setText("端口可用")
            self.result_label.setStyleSheet("color:#6ee7b7")
        except OSError as exc:
            self.result_label.setText(f"端口不可用：{exc}")
            self.result_label.setStyleSheet("color:#fb7185")


class BridgeWindow(QMainWindow):
    def __init__(self, controller: Any):
        super().__init__()
        self.controller = controller
        self.bus = UiBus()
        self.bus.event.connect(self.handle_event)
        self.notified_failures: set[str] = set()
        self.setWindowTitle("PDF Local TTS Bridge")
        self.setMinimumSize(1120, 720)
        self.resize(1280, 800)
        self._build_ui()
        self.refresh_all()
        QTimer.singleShot(100, lambda: self.scan_resources(show_message=False))

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(20, 26, 20, 20)
        brand = QLabel("PDF\nLOCAL TTS")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        sub = QLabel("本地语音中介服务")
        sub.setObjectName("muted")
        side_layout.addWidget(sub)
        side_layout.addSpacing(32)
        self.nav_queue = self._nav_button("任务队列", "▣")
        self.nav_packages = self._nav_button("已生成语音包", "◈")
        self.nav_settings = self._nav_button("服务设置", "⚙")
        self.nav_logs = self._nav_button("运行日志", "≡")
        self.nav_queue.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.nav_packages.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        self.nav_settings.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        self.nav_logs.clicked.connect(lambda: self.pages.setCurrentIndex(3))
        side_layout.addWidget(self.nav_queue)
        side_layout.addWidget(self.nav_packages)
        side_layout.addWidget(self.nav_settings)
        side_layout.addWidget(self.nav_logs)
        side_layout.addStretch()
        self.service_badge = QLabel("● 服务未启动")
        self.service_badge.setObjectName("badgeOff")
        side_layout.addWidget(self.service_badge)
        root_layout.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(34, 28, 34, 24)
        content_layout.setSpacing(20)
        header = QHBoxLayout()
        self.page_title = QLabel("任务队列")
        self.page_title.setObjectName("pageTitle")
        header.addWidget(self.page_title)
        header.addStretch()
        self.address_label = QLabel("127.0.0.1:47840")
        self.address_label.setObjectName("pill")
        header.addWidget(self.address_label)
        content_layout.addLayout(header)
        self.pages = QStackedWidget()
        self.queue_page = self._build_queue_page()
        self.packages_page = self._build_packages_page()
        self.settings_page = self._build_settings_page()
        self.logs_page = self._build_logs_page()
        self.pages.addWidget(self.queue_page)
        self.pages.addWidget(self.packages_page)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.logs_page)
        self.pages.currentChanged.connect(self._page_changed)
        content_layout.addWidget(self.pages)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

    def _nav_button(self, text: str, icon: str) -> QPushButton:
        button = QPushButton(f"  {icon}    {text}")
        button.setObjectName("navButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_queue_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        cards = QHBoxLayout()
        self.queue_card = self._card("待处理任务", "0")
        self.running_card = self._card("正在生成", "0")
        self.done_card = self._card("已完成语音包", "0")
        cards.addWidget(self.queue_card)
        cards.addWidget(self.running_card)
        cards.addWidget(self.done_card)
        cards.addStretch()
        layout.addLayout(cards)
        toolbar = QHBoxLayout()
        self.start_button = QPushButton("开始选中任务")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_selected)
        self.start_all_button = QPushButton("开始全部")
        self.start_all_button.clicked.connect(self.controller.manager.start_all)
        self.stop_button = QPushButton("停止选中任务")
        self.stop_button.clicked.connect(self.stop_selected)
        self.clear_button = QPushButton("清除已结束")
        self.clear_button.clicked.connect(self.controller.manager.clear_finished)
        self.clear_selected_button = QPushButton("清除选中")
        self.clear_selected_button.clicked.connect(self.clear_selected)
        self.release_memory_button = QPushButton("释放显存")
        self.release_memory_button.setToolTip("停止外部 TTS 运行时并释放模型占用的 CUDA/ROCm 显存；下次生成时会自动重新启动")
        self.release_memory_button.clicked.connect(self.release_runtime_memory)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.start_all_button)
        toolbar.addWidget(self.stop_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.clear_selected_button)
        toolbar.addWidget(self.release_memory_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.job_table = QTableWidget(0, 6)
        self.job_table.setHorizontalHeaderLabels(["状态", "论文 / 文本版本", "模型 / 音色", "进度", "消息", "创建时间"])
        self._configure_table(self.job_table)
        layout.addWidget(self.job_table)
        return page

    def _build_packages_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("完成的语音包会保存在 data/packages，也可以复制到任意目录后直接在 PDF 朗读器导入。")
        intro.setObjectName("muted")
        layout.addWidget(intro)
        toolbar = QHBoxLayout()
        export_button = QPushButton("导出选中语音包")
        export_button.setObjectName("primaryButton")
        export_button.clicked.connect(self.export_selected)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_packages)
        toolbar.addWidget(export_button)
        toolbar.addWidget(refresh_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.package_table = QTableWidget(0, 7)
        self.package_table.setHorizontalHeaderLabels(["论文 / 文本版本", "模型", "音色", "分段", "状态", "生成时间", "ZIP 文件"])
        self._configure_table(self.package_table)
        layout.addWidget(self.package_table)
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("这里记录中介服务、任务执行和 TTS 运行时的错误信息。日志会保存在 data/bridge.log；模型运行时的标准输出保存在 data/tts-runtime.log。")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        toolbar = QHBoxLayout()
        refresh_button = QPushButton("刷新日志")
        refresh_button.clicked.connect(self.refresh_logs)
        clear_button = QPushButton("清除界面日志")
        clear_button.clicked.connect(self.clear_logs)
        open_button = QPushButton("打开日志目录")
        open_button.clicked.connect(self.open_log_folder)
        toolbar.addWidget(refresh_button)
        toolbar.addWidget(clear_button)
        toolbar.addWidget(open_button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_view)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("panel")
        form = QFormLayout(panel)
        form.setContentsMargins(28, 28, 28, 28)
        form.setVerticalSpacing(18)
        form.addRow("中介软件环境", QLabel("已随 EXE 打包在中介软件目录内；不包含 CUDA、ROCm、TTS Python 环境或模型。", objectName="muted"))
        self.port_edit = QSpinBox(); self.port_edit.setRange(1024, 65535)
        form.addRow("监听端口", self.port_edit)
        self.resource_edit, resource_row = self._path_row("外部 TTS 资源根目录", self.browse_resource_root)
        form.addRow(resource_row[0], resource_row[1])
        self.detected_runtime_combo = QComboBox()
        self.detected_runtime_combo.currentIndexChanged.connect(self.runtime_candidate_changed)
        form.addRow("外部推理运行时", self.detected_runtime_combo)
        self.detected_model_label = QLabel("尚未扫描")
        self.detected_model_label.setObjectName("muted")
        form.addRow("外部模型目录与模型", self.detected_model_label)
        self.backend_combo = QComboBox(); self.backend_combo.addItems(["cuda", "rocm", "cpu"])
        form.addRow("外部推理后端", self.backend_combo)
        self.model_combo = QComboBox(); self.model_combo.setEditable(True)
        form.addRow("外部模型", self.model_combo)
        self.voice_mode_combo = QComboBox()
        self.voice_mode_combo.addItem("内置音色", "custom_voice")
        self.voice_mode_combo.addItem("声音克隆", "voice_clone")
        self.voice_mode_combo.currentIndexChanged.connect(self.voice_mode_changed)
        form.addRow("声音模式", self.voice_mode_combo)
        self.reference_audio_edit, reference_audio_row = self._path_row("参考音频", self.browse_reference_audio)
        form.addRow(reference_audio_row[0], reference_audio_row[1])
        self.reference_text_edit = QLineEdit()
        self.reference_text_edit.setPlaceholderText("可选：填写参考音频中说的文字，留空则使用纯音色克隆")
        form.addRow("参考音频文字", self.reference_text_edit)
        self.speaker_edit = QLineEdit()
        form.addRow("默认音色", self.speaker_edit)
        self.save_settings_button = QPushButton("保存并应用设置")
        self.save_settings_button.setObjectName("primaryButton")
        self.save_settings_button.clicked.connect(self.save_settings)
        form.addRow("", self.save_settings_button)
        self.release_settings_button = QPushButton("释放外部 TTS 显存")
        self.release_settings_button.setToolTip("停止外部 TTS 运行时并释放模型占用的 CUDA/ROCm 显存；中介服务不会关闭")
        self.release_settings_button.clicked.connect(self.release_runtime_memory)
        form.addRow("显存管理", self.release_settings_button)
        outer.addWidget(panel)
        self.scan_status_label = QLabel("选择外部 TTS 资源根目录后，软件会自动识别 Python 推理环境、CUDA/ROCm 后端和模型。", objectName="muted")
        self.scan_status_label.setWordWrap(True)
        outer.addWidget(self.scan_status_label)
        outer.addWidget(QLabel("声音克隆需要选择参考音频，并使用名称包含 Base 的模型；参考音频路径必须能被中介服务所在电脑访问。", objectName="muted"))
        outer.addWidget(QLabel("提示：中介软件只保存外部资源路径，不会复制或打包 CUDA、ROCm、推理 Python 环境和大模型。", objectName="muted"))
        outer.addStretch()
        return page

    def _path_row(self, label: str, callback):
        line = QLineEdit()
        button = QPushButton("选择")
        button.clicked.connect(callback)
        row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(line); row_layout.addWidget(button)
        return line, (label, row)

    def _configure_table(self, table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(False)

    def _card(self, title: str, value: str):
        frame = QFrame(); frame.setObjectName("statCard"); frame.setMinimumWidth(190)
        layout = QVBoxLayout(frame); layout.setContentsMargins(18, 14, 18, 14)
        label = QLabel(title); label.setObjectName("muted")
        number = QLabel(value); number.setObjectName("statValue")
        layout.addWidget(label); layout.addWidget(number)
        frame.number_label = number
        return frame

    def _page_changed(self, index: int) -> None:
        titles = ["任务队列", "已生成语音包", "服务设置", "运行日志"]
        self.page_title.setText(titles[index])
        if index == 1:
            self.refresh_packages()
        elif index == 3:
            self.refresh_logs()

    def handle_event(self, kind: str, payload: Any) -> None:
        if kind == "job":
            self.refresh_queue()
            self.refresh_packages()
            if isinstance(payload, dict):
                job_id = str(payload.get("id") or "")
                status = payload.get("status")
                if status in {"queued", "running"}:
                    self.notified_failures.discard(job_id)
                elif status in {"failed", "completed_with_errors"} and job_id and job_id not in self.notified_failures:
                    self.notified_failures.add(job_id)
                    QTimer.singleShot(0, lambda job=dict(payload): self.show_job_error(job))
        elif kind == "jobs_cleared":
            self.refresh_queue()
            self.refresh_packages()
        elif kind == "service":
            self.service_badge.setText(payload)
            self.service_badge.setObjectName("badgeOn" if payload.startswith("● 服务运行") else "badgeOff")
            self.service_badge.style().unpolish(self.service_badge); self.service_badge.style().polish(self.service_badge)
        elif kind == "log":
            self.controller.last_log = payload.get("message", "") if isinstance(payload, dict) else str(payload)
            if self.pages.currentIndex() == 3:
                self.refresh_logs()
        elif kind == "logs_cleared":
            self.refresh_logs()

    def refresh_all(self) -> None:
        settings = self.controller.settings
        self.port_edit.setValue(int(settings.get("port", 47840)))
        self.resource_edit.setText(settings.get("resource_root", "") or self._resource_root_from_runtime(settings.get("runtime_dir", "")))
        self.backend_combo.setCurrentText(settings.get("backend", "cuda"))
        self.voice_mode_combo.setCurrentIndex(1 if settings.get("voice_mode", "custom_voice") == "voice_clone" else 0)
        self.reference_audio_edit.setText(settings.get("reference_audio", ""))
        self.reference_text_edit.setText(settings.get("reference_text", ""))
        self.speaker_edit.setText(settings.get("speaker", "Vivian"))
        self.detected_runtime_combo.clear()
        self.detected_model_label.setText("等待扫描……")
        self.refresh_queue()
        self.refresh_packages()
        self.address_label.setText(f"127.0.0.1:{settings.get('port', 47840)}")

    @staticmethod
    def _resource_root_from_runtime(runtime_dir: str) -> str:
        runtime = Path(runtime_dir).expanduser()
        return str(runtime.parent) if runtime.name.lower().startswith("runtime") else str(runtime)

    def refresh_models(self) -> None:
        current = self.controller.settings.get("model_name", "Qwen3-TTS-12Hz-0.6B-CustomVoice")
        self.model_combo.clear()
        model_dir = Path(self.controller.settings.get("model_dir", ""))
        if model_dir.exists():
            self.model_combo.addItems(sorted(path.name for path in model_dir.iterdir() if path.is_dir() and (path / "config.json").exists()))
        if self.model_combo.findText(current) < 0:
            self.model_combo.addItem(current)
        self.model_combo.setCurrentText(current)

    def scan_resources(self, show_message: bool = True) -> None:
        root_value = self.resource_edit.text().strip()
        if not root_value:
            self.scan_status_label.setText("尚未选择外部 TTS 资源根目录；中介服务仍可运行，但暂时不能生成语音。")
            return
        self.scan_status_label.setText("正在扫描资源目录和 Torch 后端，请稍候……")
        QApplication.processEvents()
        scan = scan_resource_root(root_value)
        self.controller.resource_scan = scan
        self.detected_runtime_combo.blockSignals(True)
        self.detected_runtime_combo.clear()
        for runtime in scan.runtimes:
            self.detected_runtime_combo.addItem(runtime.label, runtime)
        self.detected_runtime_combo.blockSignals(False)
        if scan.runtimes:
            preferred = self.controller.settings.get("runtime_dir", "")
            index = next((i for i, runtime in enumerate(scan.runtimes) if str(runtime.path) == str(Path(preferred).resolve())), 0)
            self.detected_runtime_combo.setCurrentIndex(index)
            self.runtime_candidate_changed(index)
        else:
            self.backend_combo.clear()
            self.model_combo.clear()
            self.detected_model_label.setText("未找到")
        messages = scan.messages[:]
        if scan.runtimes:
            messages.insert(0, f"已识别 {len(scan.runtimes)} 个运行时、{len(scan.all_models)} 个模型")
        self.scan_status_label.setText("；".join(messages) if messages else "扫描完成")
        if show_message and not scan.runtimes:
            QMessageBox.warning(self, "未识别到外部推理环境", self.scan_status_label.text())

    def runtime_candidate_changed(self, index: int) -> None:
        if index < 0:
            return
        runtime = self.detected_runtime_combo.itemData(index)
        if not isinstance(runtime, RuntimeCandidate):
            return
        self.controller.settings["runtime_dir"] = str(runtime.path)
        self.controller.settings["model_dir"] = str(runtime.model_dir or "")
        self.backend_combo.clear()
        self.backend_combo.addItems(runtime.backends or ["cpu"])
        preferred_backend = self.controller.settings.get("backend", "cuda")
        self.backend_combo.setCurrentText(preferred_backend if preferred_backend in runtime.backends else (runtime.backends[0] if runtime.backends else "cpu"))
        self.model_combo.clear()
        self.model_combo.addItems(runtime.models)
        preferred_model = self.controller.settings.get("model_name", "Qwen3-TTS-12Hz-0.6B-CustomVoice")
        if self.voice_mode_combo.currentData() == "voice_clone" and "base" not in preferred_model.lower():
            preferred_model = next((model for model in runtime.models if "base" in model.lower()), preferred_model)
        if self.model_combo.findText(preferred_model) < 0 and preferred_model:
            self.model_combo.addItem(preferred_model)
        self.model_combo.setCurrentText(preferred_model)
        model_names = ", ".join(runtime.models) if runtime.models else "未找到模型"
        self.detected_model_label.setText(f"{runtime.model_dir or '未找到模型目录'}\n{model_names}")
        self.detected_model_label.setToolTip(runtime.backend_detail)

    def voice_mode_changed(self, _index: int) -> None:
        clone = self.voice_mode_combo.currentData() == "voice_clone"
        self.reference_audio_edit.setEnabled(clone)
        self.reference_text_edit.setEnabled(clone)
        if clone:
            current = self.model_combo.currentText().strip()
            if "base" not in current.lower():
                for index in range(self.model_combo.count()):
                    if "base" in self.model_combo.itemText(index).lower():
                        self.model_combo.setCurrentIndex(index)
                        break

    def browse_resource_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 TTS 资源总目录", self.resource_edit.text() or str(Path.home()))
        if path:
            self.resource_edit.setText(path)
            self.scan_resources()

    def browse_reference_audio(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择声音克隆参考音频", self.reference_audio_edit.text() or str(Path.home()), "音频文件 (*.wav *.mp3 *.m4a *.flac *.ogg *.aac)")
        if path:
            self.reference_audio_edit.setText(path)

    def refresh_queue(self) -> None:
        jobs = list(self.controller.manager.jobs.values())
        jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        self.job_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            status = job.get("status", "")
            labels = {"queued":"排队中", "running":"生成中", "completed":"已完成", "completed_with_errors":"部分完成", "failed":"失败", "cancelled":"已停止"}
            self.job_table.setItem(row, 0, QTableWidgetItem(labels.get(status, status)))
            self.job_table.setItem(row, 1, QTableWidgetItem(f"{job.get('paper_id','')}\n{job.get('text_version_label','')}"))
            self.job_table.setItem(row, 2, QTableWidgetItem(f"{job.get('model','')}\n{job.get('voice_id','')}"))
            self.job_table.setItem(row, 3, QTableWidgetItem(f"{float(job.get('progress',0))*100:.0f}%"))
            message = str(job.get("message", ""))
            message_item = QTableWidgetItem(message)
            error_message = str(job.get("error_message", "") or "")
            if error_message:
                message_item.setToolTip(error_message)
            self.job_table.setItem(row, 4, message_item)
            self.job_table.setItem(row, 5, QTableWidgetItem(format_time(job.get("created_at", ""))))
            self.job_table.setRowHeight(row, 52)
            self.job_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, job["id"])
        queued = sum(job.get("status") == "queued" for job in jobs)
        running = sum(job.get("status") == "running" for job in jobs)
        done = sum(bool(job.get("package_path")) for job in jobs)
        self.queue_card.number_label.setText(str(queued)); self.running_card.number_label.setText(str(running)); self.done_card.number_label.setText(str(done))

    def refresh_packages(self) -> None:
        jobs = self.controller.manager.completed_packages()
        self.package_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            self.package_table.setItem(row, 0, QTableWidgetItem(f"{job.get('paper_id','')}\n{job.get('text_version_label','')}"))
            self.package_table.setItem(row, 1, QTableWidgetItem(job.get("model", "")))
            self.package_table.setItem(row, 2, QTableWidgetItem(job.get("voice_id", "")))
            self.package_table.setItem(row, 3, QTableWidgetItem(str(len(job.get("segments", [])))))
            self.package_table.setItem(row, 4, QTableWidgetItem("部分完成" if job.get("status") == "completed_with_errors" else "完成"))
            self.package_table.setItem(row, 5, QTableWidgetItem(format_time(job.get("finished_at", ""))))
            self.package_table.setItem(row, 6, QTableWidgetItem(Path(job["package_path"]).name))
            self.package_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, job["id"])

    def selected_job_id(self) -> str | None:
        row = self.job_table.currentRow()
        if row < 0 or not self.job_table.item(row, 0):
            return None
        return self.job_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def start_selected(self) -> None:
        job_id = self.selected_job_id()
        if not job_id:
            QMessageBox.information(self, "开始任务", "请先在任务列表中选择一个排队中的任务。")
            return
        if not self.controller.manager.start_job(job_id):
            QMessageBox.warning(self, "无法开始任务", "这个任务当前不是可开始状态，或者已经在运行。请查看任务状态和运行日志。")

    def show_job_error(self, job: dict[str, Any]) -> None:
        status = str(job.get("status", ""))
        partial = status == "completed_with_errors"
        title = "语音任务部分失败" if partial else "语音任务失败"
        message = str(job.get("error_message") or job.get("message") or "未知错误")
        failed = job.get("failed_segments") or []
        info = f"任务：{job.get('id', '')}\n论文：{job.get('paper_id', '')}\n文本版本：{job.get('text_version_label', '')}"
        if failed:
            info += f"\n失败分段：{len(failed)} 个"
        details = [info, "", "任务错误：", message]
        for entry in self.controller.manager.logs_for_job(str(job.get("id", ""))):
            details.extend(["", f"[{entry.get('level', 'info')}] {entry.get('timestamp', '')} {entry.get('message', '')}"])
            if entry.get("details"):
                details.append(str(entry["details"]))
        details.extend(["", f"运行时日志：{self.controller.manager.runtime.log_path}"])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if partial else QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        box.setInformativeText(info)
        box.setDetailedText("\n".join(details))
        box.exec()

    @staticmethod
    def _format_log(entry: dict[str, Any]) -> str:
        level = str(entry.get("level", "info")).upper()
        job_id = f" [{entry.get('job_id')}]" if entry.get("job_id") else ""
        lines = [f"{entry.get('timestamp', '')} {level}{job_id} {entry.get('message', '')}"]
        if entry.get("details"):
            lines.append(str(entry["details"]))
        return "\n".join(lines)

    def refresh_logs(self) -> None:
        entries = self.controller.manager.recent_logs(1000)
        bridge_text = "\n".join(self._format_log(entry) for entry in entries) or "（中介日志为空）"
        runtime_text = self.controller.manager.runtime_log_tail()
        self.log_view.setPlainText(f"{bridge_text}\n\n{'=' * 80}\nTTS 运行时日志（最近内容）\n{'=' * 80}\n{runtime_text}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_logs(self) -> None:
        answer = QMessageBox.question(self, "清除日志", "确定清除当前中介日志记录吗？TTS 运行时日志文件不会被删除。")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.manager.clear_logs()

    def open_log_folder(self) -> None:
        try:
            os.startfile(str(self.controller.manager.data_dir))
        except OSError as exc:
            QMessageBox.warning(self, "打开日志目录失败", str(exc))

    def stop_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id:
            self.controller.manager.cancel_job(job_id)

    def release_runtime_memory(self) -> None:
        answer = QMessageBox.question(
            self,
            "释放显存",
            "将停止外部 TTS 运行时并释放模型占用的 CUDA/ROCm 显存。\n\n下次生成语音时会自动重新加载模型，是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.controller.manager.release_runtime_memory()
        if not ok:
            QMessageBox.warning(self, "无法释放显存", message)
        else:
            QMessageBox.information(self, "释放显存", message + "\n\n下次生成语音时会自动重新加载模型。")

    def clear_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id and not self.controller.manager.clear_job(job_id):
            QMessageBox.information(self, "无法清除", "正在生成的任务不能直接清除，请先停止任务。")

    def export_selected(self) -> None:
        row = self.package_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "导出语音包", "请先选择一个已生成的语音包。")
            return
        job_id = self.package_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        package_path = self.controller.manager.package_path(job_id)
        if not package_path:
            QMessageBox.warning(self, "导出失败", "这个任务的语音包已经不存在。")
            return
        destination = QFileDialog.getExistingDirectory(self, "选择导出目录", str(Path.home()))
        if destination:
            from audio_package import copy_export
            target = copy_export(package_path, Path(destination))
            QMessageBox.information(self, "导出完成", f"已导出到：\n{target}")

    def save_settings(self) -> None:
        runtime = self.detected_runtime_combo.currentData()
        if not isinstance(runtime, RuntimeCandidate):
            QMessageBox.warning(self, "无法保存", "请先选择一个包含外部 Python 推理环境和 TTS 模型的资源根目录。")
            return
        resource_root = Path(self.resource_edit.text().strip()).expanduser()
        if not resource_root.is_dir():
            QMessageBox.warning(self, "无法保存", "外部 TTS 资源根目录不存在，请重新选择。")
            return
        backend = self.backend_combo.currentText().strip()
        model_name = self.model_combo.currentText().strip()
        if backend not in runtime.backends:
            QMessageBox.warning(self, "无法保存", f"当前外部运行时不支持推理后端：{backend}")
            return
        if model_name not in runtime.models:
            QMessageBox.warning(self, "无法保存", "请选择扫描到的外部模型，不要手动填写不存在的模型目录名。")
            return
        voice_mode = str(self.voice_mode_combo.currentData() or "custom_voice")
        reference_audio = self.reference_audio_edit.text().strip()
        if voice_mode == "voice_clone" and not Path(reference_audio).is_file():
            QMessageBox.warning(self, "无法保存", "声音克隆模式需要选择存在的参考音频文件。")
            return
        if voice_mode == "voice_clone" and "base" not in self.model_combo.currentText().lower():
            QMessageBox.warning(self, "无法保存", "声音克隆请选用名称包含 Base 的模型。")
            return
        self.controller.apply_settings({"port": self.port_edit.value(), "resource_root": str(resource_root.resolve()),
                                        "runtime_dir": str(runtime.path), "model_dir": str(runtime.model_dir or ""), "backend": backend,
                                        "model_name": model_name, "speaker": self.speaker_edit.text().strip() or "Vivian",
                                        "voice_mode": voice_mode, "reference_audio": reference_audio,
                                        "reference_text": self.reference_text_edit.text().strip()})
        self.address_label.setText(f"127.0.0.1:{self.port_edit.value()}")
        QMessageBox.information(self, "设置已应用", "服务已按新设置重新启动。")

    def closeEvent(self, event) -> None:
        self.controller.shutdown()
        event.accept()


def apply_theme(app: QApplication) -> None:
    app.setStyleSheet("""
    QWidget { background:#101522; color:#e5e7eb; font-family:'Microsoft YaHei UI'; font-size:13px; }
    QMainWindow { background:#101522; }
    #sidebar { background:#0b101b; border-right:1px solid #202a3a; }
    #brand { color:#f8fafc; font-size:20px; font-weight:800; letter-spacing:2px; }
    #pageTitle { color:#f8fafc; font-size:27px; font-weight:700; }
    #muted { color:#8994a7; }
    #navButton { text-align:left; border:0; border-radius:9px; padding:12px 8px; color:#9aa6ba; font-size:14px; }
    #navButton:hover { background:#172238; color:#f8fafc; }
    QPushButton { background:#1a2435; color:#d9e2f0; border:1px solid #2a3951; border-radius:7px; padding:9px 14px; }
    QPushButton:hover { background:#23334d; }
    #primaryButton { background:#5b5ce2; color:white; border:0; font-weight:600; }
    #primaryButton:hover { background:#7071f0; }
    #badgeOn, #badgeOff, #pill { border-radius:14px; padding:6px 12px; }
    #badgeOn { background:#12382f; color:#6ee7b7; }
    #badgeOff { background:#3c2028; color:#fda4af; }
    #pill { background:#182236; color:#aebbd1; }
    #statCard, #panel { background:#151e2d; border:1px solid #263349; border-radius:12px; }
    #statValue { color:#f8fafc; font-size:25px; font-weight:700; }
    QTableWidget { background:#121a28; border:1px solid #263349; border-radius:10px; gridline-color:#202b3e; alternate-background-color:#151f30; selection-background-color:#29396a; }
    QHeaderView::section { background:#182337; color:#95a3ba; padding:10px; border:0; border-bottom:1px solid #2a3850; }
    QLineEdit, QSpinBox, QComboBox { background:#0e1624; border:1px solid #2b3b55; border-radius:7px; padding:8px; color:#e5e7eb; }
    QComboBox QAbstractItemView { background:#151e2d; selection-background-color:#29396a; }
    QScrollBar:vertical { background:#101522; width:10px; }
    QScrollBar::handle:vertical { background:#33415a; border-radius:5px; }
    #dialogTitle { font-size:18px; font-weight:700; }
    """)
