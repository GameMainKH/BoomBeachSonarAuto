from __future__ import annotations

import sys
from datetime import datetime
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from config import ADB_SERIAL, TEMPLATE_DIR
from utils.adb_control import AdbCommandError, AdbController


class ToolMode(Enum):
    """调试工具模式。"""

    PICK_POINT = "pick_point"
    CROP_TEMPLATE = "crop_template"


class ScreenshotView(QWidget):
    """显示模拟器截图，并把鼠标操作转换为原始截图坐标。"""

    pointPicked = pyqtSignal(int, int)
    templateRegionSelected = pyqtSignal(int, int, int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)

        self._image: QImage | None = None
        self._tool_mode = ToolMode.PICK_POINT
        self._selection_start: tuple[int, int] | None = None
        self._selection_current: tuple[int, int] | None = None

    def setImage(self, image: QImage | None) -> None:
        """设置当前显示的截图。"""
        self._image = image
        self._selection_start = None
        self._selection_current = None
        self.update()

    def setToolMode(self, mode: ToolMode) -> None:
        """切换当前鼠标工具。"""
        self._tool_mode = mode
        self._selection_start = None
        self._selection_current = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202124"))

        if self._image is None:
            painter.setPen(QColor("#c9d1d9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无截图，请点击“连接/刷新截图”")
            return

        image_rect = self._display_rect()
        painter.drawImage(image_rect, self._image)

        selection = self._normalized_selection()
        if selection is not None:
            painter.setPen(QPen(QColor("#00c8ff"), 2, Qt.PenStyle.SolidLine))
            painter.drawRect(self._image_selection_to_widget_rect(selection))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._image is None:
            return

        image_point = self._map_widget_to_image(event.position().toPoint())
        if image_point is None:
            return

        if self._tool_mode == ToolMode.CROP_TEMPLATE:
            self._selection_start = image_point
            self._selection_current = image_point
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._image is None or self._tool_mode != ToolMode.CROP_TEMPLATE:
            return
        if self._selection_start is None:
            return

        self._selection_current = self._map_widget_to_image(event.position().toPoint(), clamp=True)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._image is None:
            return

        if self._tool_mode == ToolMode.PICK_POINT:
            image_point = self._map_widget_to_image(event.position().toPoint())
            if image_point is not None:
                self.pointPicked.emit(*image_point)
            return

        if self._tool_mode == ToolMode.CROP_TEMPLATE and self._selection_start is not None:
            self._selection_current = self._map_widget_to_image(event.position().toPoint(), clamp=True)
            selection = self._normalized_selection()
            self._selection_start = None
            self._selection_current = None
            self.update()

            if selection is None:
                return
            x, y, width, height = selection
            if width >= 2 and height >= 2:
                self.templateRegionSelected.emit(x, y, width, height)

    def _display_rect(self) -> QRect:
        """计算截图在控件中的实际显示区域。"""
        if self._image is None:
            return QRect()

        scaled_size = self._image.size()
        scaled_size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled_size.width()) // 2
        y = (self.height() - scaled_size.height()) // 2
        return QRect(x, y, scaled_size.width(), scaled_size.height())

    def _map_widget_to_image(self, point: QPoint, *, clamp: bool = False) -> tuple[int, int] | None:
        """把控件坐标映射为原始截图坐标。"""
        if self._image is None:
            return None

        image_rect = self._display_rect()
        if image_rect.isEmpty():
            return None

        if clamp:
            px = min(max(point.x(), image_rect.left()), image_rect.left() + image_rect.width() - 1)
            py = min(max(point.y(), image_rect.top()), image_rect.top() + image_rect.height() - 1)
        elif not image_rect.contains(point):
            return None
        else:
            px = point.x()
            py = point.y()

        image_x = int((px - image_rect.left()) * self._image.width() / image_rect.width())
        image_y = int((py - image_rect.top()) * self._image.height() / image_rect.height())
        image_x = min(max(image_x, 0), self._image.width() - 1)
        image_y = min(max(image_y, 0), self._image.height() - 1)
        return image_x, image_y

    def _normalized_selection(self) -> tuple[int, int, int, int] | None:
        """返回标准化后的截图选区。"""
        if self._selection_start is None or self._selection_current is None:
            return None

        start_x, start_y = self._selection_start
        end_x, end_y = self._selection_current
        left = min(start_x, end_x)
        top = min(start_y, end_y)
        right = max(start_x, end_x)
        bottom = max(start_y, end_y)
        return left, top, right - left + 1, bottom - top + 1

    def _image_selection_to_widget_rect(self, selection: tuple[int, int, int, int]) -> QRect:
        """把原始截图选区转换为控件上的显示选区。"""
        if self._image is None:
            return QRect()

        x, y, width, height = selection
        image_rect = self._display_rect()
        scale_x = image_rect.width() / self._image.width()
        scale_y = image_rect.height() / self._image.height()

        return QRect(
            int(image_rect.left() + x * scale_x),
            int(image_rect.top() + y * scale_y),
            max(1, int(width * scale_x)),
            max(1, int(height * scale_y)),
        )


class DebugMainWindow(QMainWindow):
    """ADB 调试窗口，负责截图刷新、工具栏和模板保存。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BBMA Debug GUI")
        self.resize(1000, 720)

        self._adb: AdbController | None = None
        self._screen_bgr = None
        self._tool_mode = ToolMode.PICK_POINT

        self.view = ScreenshotView(self)
        self.setCentralWidget(self.view)
        self.view.pointPicked.connect(self._on_point_picked)
        self.view.templateRegionSelected.connect(self._on_template_region_selected)

        self._build_toolbar()
        self.statusBar().showMessage("请选择工具并点击“连接/刷新截图”")
        self._set_tools_enabled(False)

    def _build_toolbar(self) -> None:
        """创建顶部工具栏。"""
        toolbar = QToolBar("调试工具", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel("设备: ", self))
        self.serial_edit = QLineEdit(ADB_SERIAL, self)
        self.serial_edit.setMinimumWidth(180)
        toolbar.addWidget(self.serial_edit)

        self.refresh_action = QAction("连接/刷新截图", self)
        self.refresh_action.triggered.connect(self.refresh_screenshot)
        toolbar.addAction(self.refresh_action)
        toolbar.addSeparator()

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)

        self.pick_action = QAction("取点工具", self)
        self.pick_action.setCheckable(True)
        self.pick_action.setChecked(True)
        self.pick_action.triggered.connect(lambda: self._set_tool_mode(ToolMode.PICK_POINT))
        self.tool_group.addAction(self.pick_action)
        toolbar.addAction(self.pick_action)

        self.crop_action = QAction("裁剪模板工具", self)
        self.crop_action.setCheckable(True)
        self.crop_action.triggered.connect(lambda: self._set_tool_mode(ToolMode.CROP_TEMPLATE))
        self.tool_group.addAction(self.crop_action)
        toolbar.addAction(self.crop_action)

    def refresh_screenshot(self) -> None:
        """连接设备并刷新当前截图。"""
        serial = self.serial_edit.text().strip() or ADB_SERIAL
        self.refresh_action.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            self._adb = AdbController(serial)
            self._screen_bgr = self._adb.read_screenshot()
            self.view.setImage(self._cv_to_qimage(self._screen_bgr))
            self._set_tools_enabled(True)

            height, width = self._screen_bgr.shape[:2]
            self.statusBar().showMessage(f"截图已刷新: {width}x{height}")
        except (AdbCommandError, RuntimeError) as exc:
            self.statusBar().showMessage(f"截图刷新失败: {exc}")
            QMessageBox.warning(self, "截图刷新失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_action.setEnabled(True)

    def _set_tool_mode(self, mode: ToolMode) -> None:
        """切换当前工具模式。"""
        self._tool_mode = mode
        self.view.setToolMode(mode)
        if mode == ToolMode.PICK_POINT:
            self.statusBar().showMessage("当前工具: 取点工具")
        elif mode == ToolMode.CROP_TEMPLATE:
            self.statusBar().showMessage("当前工具: 裁剪模板工具")

    def _set_tools_enabled(self, enabled: bool) -> None:
        """根据是否已有截图启用或禁用画布工具。"""
        self.pick_action.setEnabled(enabled)
        self.crop_action.setEnabled(enabled)
        self.view.setEnabled(enabled)

    def _on_point_picked(self, x: int, y: int) -> None:
        if self._tool_mode != ToolMode.PICK_POINT:
            return
        self.statusBar().showMessage(f"取点坐标: ({x}, {y})")

    def _on_template_region_selected(self, x: int, y: int, width: int, height: int) -> None:
        if self._tool_mode != ToolMode.CROP_TEMPLATE or self._screen_bgr is None:
            return

        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"template_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        default_path = TEMPLATE_DIR / default_name
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存模板图片",
            str(default_path),
            "PNG 图片 (*.png)",
        )
        if not save_path:
            self.statusBar().showMessage("已取消保存模板")
            return

        path = Path(save_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")

        crop = self._screen_bgr[y : y + height, x : x + width]
        if crop.size == 0 or not cv2.imwrite(str(path), crop):
            QMessageBox.warning(self, "保存失败", f"无法保存模板: {path}")
            self.statusBar().showMessage("模板保存失败")
            return

        self.statusBar().showMessage(f"模板已保存: {path} ({width}x{height})")

    @staticmethod
    def _cv_to_qimage(screen_bgr) -> QImage:
        """把 OpenCV BGR 图片转换为 Qt 可显示的 QImage。"""
        screen_rgb = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2RGB)
        height, width, channels = screen_rgb.shape
        bytes_per_line = channels * width
        return QImage(
            screen_rgb.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        ).copy()


def main() -> int:
    app = QApplication(sys.argv)
    window = DebugMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
