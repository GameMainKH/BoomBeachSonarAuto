from __future__ import annotations

import atexit
import signal
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import ADB_SERIAL, GAME_PACKAGE_NAME
from utils.adb_control import AdbController


GUI_LOG_FILE = PROJECT_ROOT / "_debug" / "logs" / "weak_network_gui.log"
_cleanup_done = False


def write_gui_log(message: str) -> None:
    """写入弱网 GUI 专用日志，避免和主脚本日志混在一起。"""
    GUI_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with GUI_LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{now} {message}\n")


def format_weak_network_diagnostics(adb: AdbController, label: str) -> str:
    """读取并格式化弱网诊断信息。"""
    diagnostics = adb.get_weak_network_diagnostics(GAME_PACKAGE_NAME)
    return f"弱网诊断[{label}]\n{diagnostics}"


def cleanup_weak_network(reason: str = "工具退出") -> None:
    """工具退出时关闭弱网。"""
    global _cleanup_done
    if _cleanup_done:
        return

    _cleanup_done = True
    try:
        message = f"{reason}，正在关闭弱网"
        print(message)
        write_gui_log(message)
        adb = AdbController(ADB_SERIAL)
        adb.ensure_root_shell()
        write_gui_log(format_weak_network_diagnostics(adb, "退出清理前"))
        adb.disable_weak_network(GAME_PACKAGE_NAME)
        write_gui_log(format_weak_network_diagnostics(adb, "退出清理后"))
    except Exception as exc:
        message = f"关闭弱网失败: {exc}"
        print(message)
        write_gui_log(message)


def handle_exit_signal(signum: int, _frame) -> None:
    """收到退出信号时先关闭弱网再退出。"""
    cleanup_weak_network(f"收到退出信号 {signum}")
    raise SystemExit(128 + signum)


def register_exit_cleanup() -> None:
    """注册工具退出清理，避免弱网规则残留。"""
    atexit.register(cleanup_weak_network)
    for signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            signal.signal(signum, handle_exit_signal)


class WeakNetworkWorker(QObject):
    """在后台线程执行弱网开关，避免阻塞界面。"""

    finished = pyqtSignal(bool, str, str)

    def __init__(self, enabled: bool):
        super().__init__()
        self.enabled = enabled

    def run(self) -> None:
        """执行一次弱网开启或关闭操作。"""
        action = "开启" if self.enabled else "关闭"
        try:
            adb = AdbController(ADB_SERIAL)
            adb.ensure_root_shell()
            logs = [format_weak_network_diagnostics(adb, f"{action}前")]
            if self.enabled:
                adb.enable_weak_network(GAME_PACKAGE_NAME)
                message = "弱网已开启"
            else:
                adb.disable_weak_network(GAME_PACKAGE_NAME)
                message = "弱网已关闭"
            logs.append(format_weak_network_diagnostics(adb, f"{action}后"))
            self.finished.emit(True, message, "\n".join(logs))
        except Exception as exc:
            self.finished.emit(False, f"操作失败: {exc}", "")


class WeakNetworkWindow(QMainWindow):
    """弱网调试窗口，提供手动开启和关闭按钮。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BBMA 弱网调试工具")
        self.resize(520, 320)

        self._thread: QThread | None = None
        self._worker: WeakNetworkWorker | None = None

        self.status_label = QLabel("当前未执行操作")
        self.open_button = QPushButton("开启弱网")
        self.close_button = QPushButton("关闭弱网")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)

        self.open_button.clicked.connect(lambda: self._run_operation(True))
        self.close_button.clicked.connect(lambda: self._run_operation(False))

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"目标设备: {ADB_SERIAL}"))
        layout.addWidget(QLabel(f"目标包名: {GAME_PACKAGE_NAME}"))
        layout.addLayout(button_layout)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_text)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self._append_log("工具已启动")
        self._append_log(f"专用日志: {GUI_LOG_FILE}")

    def _run_operation(self, enabled: bool) -> None:
        """启动后台弱网操作。"""
        if self._thread is not None:
            QMessageBox.information(self, "操作进行中", "请等待当前弱网操作完成")
            return

        action = "开启" if enabled else "关闭"
        self.status_label.setText(f"正在{action}弱网...")
        self._append_log(f"开始{action}弱网")
        write_gui_log(f"开始{action}弱网")
        self._set_buttons_enabled(False)

        self._thread = QThread(self)
        self._worker = WeakNetworkWorker(enabled)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_operation_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_operation_finished(self, success: bool, message: str, diagnostics: str) -> None:
        """处理后台弱网操作结果。"""
        self.status_label.setText(message)
        self._append_log(message)
        write_gui_log(message)
        if diagnostics:
            write_gui_log(diagnostics)
        if not success:
            QMessageBox.warning(self, "弱网操作失败", message)

    def _on_thread_finished(self) -> None:
        """清理后台线程引用并恢复按钮。"""
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """统一设置两个弱网按钮的可用状态。"""
        self.open_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)

    def _append_log(self, message: str) -> None:
        """向窗口日志追加一行状态。"""
        now = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"{now}  {message}")

    def closeEvent(self, event) -> None:
        """关闭窗口时关闭弱网。"""
        self.status_label.setText("正在关闭弱网并退出...")
        self._append_log("窗口关闭，正在关闭弱网")
        write_gui_log("窗口关闭，正在关闭弱网")
        cleanup_weak_network("窗口关闭")
        super().closeEvent(event)


def main() -> None:
    """启动弱网调试工具。"""
    write_gui_log("弱网 GUI 工具启动")
    register_exit_cleanup()
    app = QApplication(sys.argv)
    window = WeakNetworkWindow()
    window.show()
    try:
        sys.exit(app.exec())
    finally:
        cleanup_weak_network("GUI 主循环结束")


if __name__ == "__main__":
    main()
