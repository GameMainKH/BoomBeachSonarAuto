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
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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


class AdbSession:
    """按设备地址复用 ADB 控制器，保留连接和设备能力缓存。"""

    def __init__(self, controller_factory=AdbController):
        self._controller_factory = controller_factory
        self._controllers: dict[str, AdbController] = {}

    def get_controller(self, serial: str) -> AdbController:
        """返回已准备好的控制器；同一地址只连接和检查 root 一次。"""
        serial = serial.strip()
        if not serial:
            raise ValueError("模拟器 ADB 地址不能为空")

        adb = self._controllers.get(serial)
        if adb is None:
            adb = self._controller_factory(serial, auto_connect=False)
            adb.connect()
            adb.ensure_root_shell()
            self._controllers[serial] = adb
        return adb

    def controllers(self) -> tuple[AdbController, ...]:
        """返回本次工具运行期间已经实际使用过的控制器。"""
        return tuple(self._controllers.values())


_adb_session = AdbSession()


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


def format_reject_network_diagnostics(adb: AdbController, label: str) -> str:
    """读取并格式化 REJECT 断网诊断信息。"""
    diagnostics = adb.get_reject_network_diagnostics(GAME_PACKAGE_NAME)
    return f"断网诊断[{label}]\n{diagnostics}"


def cleanup_weak_network(reason: str = "工具退出") -> None:
    """工具退出时关闭 DROP 弱网和 REJECT 断网。"""
    global _cleanup_done
    if _cleanup_done:
        return

    _cleanup_done = True
    message = f"{reason}，正在关闭弱网和断网"
    print(message)
    write_gui_log(message)
    controllers = _adb_session.controllers()
    if not controllers:
        try:
            controllers = (_adb_session.get_controller(ADB_SERIAL),)
        except Exception as exc:
            message = f"初始化清理失败: {exc}"
            print(message)
            write_gui_log(message)
            return

    for adb in controllers:
        try:
            adb.disable_weak_network(GAME_PACKAGE_NAME)
        except Exception as exc:
            message = f"关闭 {adb.serial} 弱网失败: {exc}"
            print(message)
            write_gui_log(message)

        try:
            adb.disable_reject_network(GAME_PACKAGE_NAME)
        except Exception as exc:
            message = f"关闭 {adb.serial} 断网失败: {exc}"
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

    def __init__(
        self,
        session: AdbSession,
        serial: str,
        mode: str,
        enabled: bool,
        detailed_diagnostics: bool = False,
    ):
        super().__init__()
        self.session = session
        self.serial = serial
        self.mode = mode
        self.enabled = enabled
        self.detailed_diagnostics = detailed_diagnostics

    def run(self) -> None:
        """执行一次弱网开启或关闭操作。"""
        action = "开启" if self.enabled else "关闭"
        label = "弱网(DROP)" if self.mode == "drop" else "断网(REJECT)"
        try:
            adb = self.session.get_controller(self.serial)
            if self.mode == "drop":
                logs = []
                if self.detailed_diagnostics:
                    logs.append(format_weak_network_diagnostics(adb, f"{action}前"))
                if self.enabled:
                    adb.enable_weak_network(GAME_PACKAGE_NAME)
                    message = "弱网(DROP)已开启"
                else:
                    adb.disable_weak_network(GAME_PACKAGE_NAME)
                    message = "弱网(DROP)已关闭"
                if self.detailed_diagnostics:
                    logs.append(format_weak_network_diagnostics(adb, f"{action}后"))
            elif self.mode == "reject":
                logs = []
                if self.detailed_diagnostics:
                    logs.append(format_reject_network_diagnostics(adb, f"{action}前"))
                if self.enabled:
                    adb.enable_reject_network(GAME_PACKAGE_NAME)
                    message = "断网(REJECT)已开启"
                else:
                    adb.disable_reject_network(GAME_PACKAGE_NAME)
                    message = "断网(REJECT)已关闭"
                if self.detailed_diagnostics:
                    logs.append(format_reject_network_diagnostics(adb, f"{action}后"))
            else:
                raise ValueError(f"不支持的操作模式: {self.mode}")
            self.finished.emit(True, message, "\n".join(logs))
        except Exception as exc:
            self.finished.emit(False, f"{label}操作失败: {exc}", "")


class WeakNetworkWindow(QMainWindow):
    """弱网调试窗口，提供手动开启和关闭按钮。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BBMA 弱网调试工具")
        self.resize(520, 320)

        self._thread: QThread | None = None
        self._worker: WeakNetworkWorker | None = None

        self.serial_edit = QLineEdit(ADB_SERIAL)
        self.serial_edit.setPlaceholderText("例如 127.0.0.1:5555 或 emulator-5554")
        self.diagnostics_checkbox = QCheckBox("记录完整规则诊断（较慢）")
        self.status_label = QLabel("当前未执行操作")
        self.open_button = QPushButton("开启弱网(DROP)")
        self.close_button = QPushButton("关闭弱网(DROP)")
        self.reject_open_button = QPushButton("开启断网(REJECT)")
        self.reject_close_button = QPushButton("关闭断网(REJECT)")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)

        self.open_button.clicked.connect(lambda: self._run_operation("drop", True))
        self.close_button.clicked.connect(lambda: self._run_operation("drop", False))
        self.reject_open_button.clicked.connect(lambda: self._run_operation("reject", True))
        self.reject_close_button.clicked.connect(lambda: self._run_operation("reject", False))

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.close_button)
        reject_button_layout = QHBoxLayout()
        reject_button_layout.addWidget(self.reject_open_button)
        reject_button_layout.addWidget(self.reject_close_button)

        layout = QVBoxLayout()
        serial_layout = QHBoxLayout()
        serial_layout.addWidget(QLabel("模拟器 ADB 地址:"))
        serial_layout.addWidget(self.serial_edit)
        layout.addLayout(serial_layout)
        layout.addWidget(QLabel(f"目标包名: {GAME_PACKAGE_NAME}"))
        layout.addWidget(self.diagnostics_checkbox)
        layout.addLayout(button_layout)
        layout.addLayout(reject_button_layout)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_text)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self._append_log("工具已启动")
        self._append_log(f"专用日志: {GUI_LOG_FILE}")

    def _run_operation(self, mode: str, enabled: bool) -> None:
        """启动后台弱网操作。"""
        if self._thread is not None:
            QMessageBox.information(self, "操作进行中", "请等待当前弱网操作完成")
            return

        action = "开启" if enabled else "关闭"
        label = "弱网(DROP)" if mode == "drop" else "断网(REJECT)"
        serial = self.serial_edit.text().strip()
        if not serial:
            QMessageBox.warning(self, "设备地址无效", "请填写模拟器 ADB 地址")
            return
        self.status_label.setText(f"正在{action}{label}...")
        self._append_log(f"开始{action}{label}: {serial}")
        write_gui_log(f"开始{action}{label}: {serial}")
        self._set_buttons_enabled(False)

        self._thread = QThread(self)
        self._worker = WeakNetworkWorker(
            _adb_session,
            serial,
            mode,
            enabled,
            self.diagnostics_checkbox.isChecked(),
        )
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
        """统一设置所有调试按钮的可用状态。"""
        self.open_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)
        self.reject_open_button.setEnabled(enabled)
        self.reject_close_button.setEnabled(enabled)
        self.serial_edit.setEnabled(enabled)
        self.diagnostics_checkbox.setEnabled(enabled)

    def _append_log(self, message: str) -> None:
        """向窗口日志追加一行状态。"""
        now = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"{now}  {message}")

    def closeEvent(self, event) -> None:
        """关闭窗口时关闭弱网和断网。"""
        self.status_label.setText("正在关闭弱网和断网并退出...")
        self._append_log("窗口关闭，正在关闭弱网和断网")
        write_gui_log("窗口关闭，正在关闭弱网和断网")
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
