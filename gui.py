"""声呐自动化桌面入口：python gui.py。"""
import codecs
import json
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QSpinBox, QSplitter, QProgressBar,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from utils.gui_config import load_config, save_config

ROOT = Path(__file__).resolve().parent


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Boom Beach · 声呐控制台")
        self.resize(1120, 760)
        self.process = None
        self.tool_processes = []
        self.close_pending = False
        self.run_level = None
        self.output_buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.config = load_config(ROOT / "config.py")
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("声呐自动化控制台")
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 12px 0;")
        layout.addWidget(title)
        layout.addWidget(QLabel("启动前请登录游戏主界面，并确认设备分辨率为 1280 × 720。"))
        split = QSplitter()
        layout.addWidget(split, 1)
        settings = QWidget()
        settings.setMinimumWidth(340)
        form = QFormLayout(settings)
        self.serial = QLineEdit(self.config["ADB_SERIAL"])
        self.package = QComboBox()
        self.package.addItem("国服", "com.tencent.tmgp.supercell.boombeach")
        self.package.addItem("国际服", "com.supercell.boombeach")
        package_index = self.package.findData(self.config["GAME_PACKAGE_NAME"])
        self.package.setCurrentIndex(package_index)
        self.package.setPlaceholderText("请选择游戏区服")
        self.level = QSpinBox()
        self.level.setRange(1, self.config["MAX_LEVEL"])
        self.level.setValue(1)
        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level.setCurrentText(self.config["LOG_LEVEL"])
        for label, widget in [("设备地址", self.serial),
                              ("游戏区服", self.package), ("海域", self.level),
                              ("日志级别", self.log_level)]:
            form.addRow(label, widget)
        self.settings = settings
        self.save_button = QPushButton("保存配置")
        self.save_button.clicked.connect(self.save_settings)
        form.addRow(self.save_button)
        self.tool_buttons = []
        for label, script in [("截图与模板工具", "debug_gui.py"),
                              ("人工点位校准", "point_editor.py"),
                              ("网络诊断工具", "weak_network_gui.py")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, name=script: self.open_tool(name))
            self.tool_buttons.append(button)
            form.addRow(button)
        split.addWidget(settings)
        tabs = QTabWidget()
        self.tabs = tabs
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(5000)
        tabs.addTab(self.logs, "运行日志")
        self.preview = QLabel("成功完成任务后，此处显示本次命中结果。")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 240)
        tabs.addTab(self.preview, "结果预览")
        split.addWidget(tabs)
        split.setStretchFactor(1, 1)
        self.progress_title = QLabel("探索进度 · 等待开始")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("等待探索数据")
        self.progress_details = QLabel("已确认潜艇 — | 探测次数 — | 最坏剩余 — | 总运行 —")
        self.progress_details.setWordWrap(True)
        self.progress_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_details)
        controls = QHBoxLayout()
        self.start_button = QPushButton("保存并开始探索")
        self.start_button.clicked.connect(self.start_run)
        self.status = QLabel("就绪")
        controls.addWidget(self.start_button)
        controls.addWidget(self.status, 1)
        for label, path in [("打开结果目录", ROOT / "outputs"), ("打开日志目录", ROOT / "_debug/logs")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, p=path: self.open_directory(p))
            controls.addWidget(button)
        layout.addLayout(controls)

    def append_log(self, text):
        cursor = self.logs.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text).replace("\r", "\n"))
        self.logs.setTextCursor(cursor)
        self.logs.ensureCursorVisible()

    def save_settings(self):
        if not self.serial.text().strip() or self.package.currentData() is None:
            QMessageBox.warning(self, "配置不完整", "请填写设备地址并选择游戏区服。")
            return False
        try:
            save_config(ROOT / "config.py", {
                "ADB_SERIAL": self.serial.text().strip(),
                "GAME_PACKAGE_NAME": self.package.currentData(),
                "LOG_LEVEL": self.log_level.currentText(),
            })
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False
        self.status.setText("配置已保存")
        return True

    def new_process(self):
        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)
        return process

    def start_run(self):
        if self.process is not None:
            return
        if any(p.state() != QProcess.ProcessState.NotRunning for p in self.tool_processes):
            QMessageBox.warning(self, "调试工具仍在运行", "请先关闭调试工具，再启动探索，避免同时操作设备。")
            return
        if not self.save_settings():
            return
        self.run_level = self.level.value()
        self.preview.clear()
        self.preview.setText("正在探索，等待本次结果…")
        self.logs.clear()
        self.progress_title.setText(f"海域 {self.run_level} · 正在准备探索")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("等待探索数据")
        self.progress_details.setText("已确认潜艇 — | 探测次数 — | 最坏剩余 — | 总运行 —")
        self.tabs.setCurrentIndex(0)
        self.append_log(f"正在启动海域 {self.run_level}，设备：{self.serial.text().strip()}，区服：{self.package.currentText()}\n")
        self.decoder.reset()
        self.output_buffer = ""
        process = self.new_process()
        self.process = process
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(self.process_error)
        process.started.connect(lambda: self.append_log("任务进程已启动，正在检查设备连接…\n"))
        self.settings.setEnabled(False)
        self.start_button.setEnabled(False)
        self.status.setText(f"正在探索海域 {self.run_level}")
        process.start(sys.executable, ["-u", str(ROOT / "gui_worker.py"), str(self.run_level)])

    def read_output(self):
        if self.process and self.process.isOpen():
            self.output_buffer += self.decoder.decode(bytes(self.process.readAllStandardOutput())).replace("\r", "\n")
            while "\n" in self.output_buffer:
                line, self.output_buffer = self.output_buffer.split("\n", 1)
                if line.startswith("@@BBMA_PROGRESS@@"):
                    self.show_progress(line[len("@@BBMA_PROGRESS@@"):])
                elif line.strip() == "@@BBMA_CONFIRM_RECOVERY@@":
                    answer = QMessageBox.question(
                        self, "人工网络恢复",
                        "探测异常中断，网络规则已保留。\n如果你已人工核验游戏状态，是否恢复普通网络？\n仅清除 DROP / REJECT，不会点击或登录游戏。",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if self.process:
                        self.process.write(b"recover_yes\n" if answer == QMessageBox.StandardButton.Yes else b"recover_no\n")
                else:
                    self.append_log(line + "\n")

    def show_progress(self, payload):
        try:
            data = json.loads(payload)
            total = int(data["total"])
            current = int(data["current"])
            if not 0 <= current <= total or total <= 0:
                raise ValueError("无效进度范围")
            description = str(data["description"])
            postfix = str(data["postfix"])
        except (ValueError, TypeError, KeyError) as exc:
            self.append_log(f"进度数据无法读取：{exc}\n")
            return
        self.progress_title.setText(description)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        label = "已扫描" if "逐格扫描" in description else "已找到潜艇格"
        self.progress_bar.setFormat(f"{label} %v / %m · %p%")
        self.progress_details.setText(postfix)

    def process_error(self, error):
        if self.process:
            self.append_log(f"\n进程错误：{self.process.errorString()}\n")
        if error == QProcess.ProcessError.FailedToStart:
            self.finished(-1, QProcess.ExitStatus.CrashExit)

    def finished(self, code, exit_status):
        self.read_output()
        self.append_log(self.output_buffer + self.decoder.decode(b"", final=True))
        self.output_buffer = ""
        process, self.process = self.process, None
        if process:
            process.deleteLater()
        self.settings.setEnabled(True)
        self.start_button.setEnabled(True)
        success = code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        self.status.setText("探索完成" if success else f"任务失败（{code}），请查看日志")
        path = ROOT / "outputs" / f"hit_map_level_{self.run_level}.png"
        pixmap = QPixmap(str(path)) if success and path.exists() else QPixmap()
        if not pixmap.isNull():
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("本次任务未生成可显示的结果，请查看运行日志。")
        if self.close_pending:
            self.close()

    def open_tool(self, script):
        if not self.save_settings():
            return
        process = self.new_process()
        self.tool_processes.append(process)
        process.readyReadStandardOutput.connect(lambda: self.append_log(bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")))
        process.errorOccurred.connect(lambda error: self.append_log(f"\n工具错误：{process.errorString()}\n"))
        process.start(sys.executable, [str(ROOT / "_debug" / script)])

    def open_directory(self, path):
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event):
        if any(p.state() != QProcess.ProcessState.NotRunning for p in self.tool_processes):
            QMessageBox.information(self, "请先关闭调试工具", "请关闭已打开的调试窗口，确保其退出清理完成后再关闭控制台。")
            event.ignore()
        elif self.process is not None:
            self.close_pending = True
            self.status.setText("任务仍在运行，完成后关闭窗口")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
