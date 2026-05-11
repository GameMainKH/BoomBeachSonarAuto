from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QImage, QIntValidator, QPainter, QPen
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

from config import ADB_SERIAL, SCREENSHOT_DIR, TEMPLATE_DIR
from utils.adb_control import AdbCommandError, AdbController


CLICK_DRAG_THRESHOLD_PX = 4


class ScreenshotView(QWidget):
    """显示模拟器截图，并把鼠标手势转换为原始截图坐标。"""

    mousePositionChanged = pyqtSignal(object, object)
    markerAddRequested = pyqtSignal(int, int)
    markerInspectRequested = pyqtSignal(int)
    markerDeleteRequested = pyqtSignal(int)
    roiSelected = pyqtSignal(int, int, int, int)
    roiClearRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._image: QImage | None = None
        self._press_widget_point: QPoint | None = None
        self._selection_start: tuple[int, int] | None = None
        self._selection_current: tuple[int, int] | None = None
        self._markers: list[tuple[int, int]] = []
        self._selected_marker_index: int | None = None
        self._roi: tuple[int, int, int, int] | None = None

    def setImage(self, image: QImage | None) -> None:
        """设置当前显示的截图。"""
        self._image = image
        self._press_widget_point = None
        self._selection_start = None
        self._selection_current = None
        self._roi = None
        self.update()

    def setMarkers(self, markers: list[tuple[int, int]], selected_index: int | None) -> None:
        """更新需要绘制的坐标标记。"""
        self._markers = list(markers)
        if selected_index is not None and 0 <= selected_index < len(self._markers):
            self._selected_marker_index = selected_index
        else:
            self._selected_marker_index = None
        self.update()

    def setRoi(self, roi: tuple[int, int, int, int] | None) -> None:
        """更新当前 ROI 选区。"""
        self._roi = roi
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#202124"))

        if self._image is None:
            painter.setPen(QColor("#c9d1d9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无截图，请点击“连接/刷新截图”")
            return

        image_rect = self._display_rect()
        painter.drawImage(image_rect, self._image)

        if self._roi is not None:
            self._draw_selection(painter, self._roi, QColor("#47d16c"))

        selection = self._normalized_selection()
        if selection is not None and self._is_dragging():
            self._draw_selection(painter, selection, QColor("#47d16c"))

        for index, marker in enumerate(self._markers):
            self._draw_marker(painter, index, marker)

    def mousePressEvent(self, event) -> None:
        if self._image is None:
            return

        widget_point = event.position().toPoint()

        if event.button() == Qt.MouseButton.RightButton:
            marker_index = self._hit_marker(widget_point)
            if marker_index is not None:
                self.markerDeleteRequested.emit(marker_index)
            else:
                self.roiClearRequested.emit()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        image_point = self._map_widget_to_image(widget_point)
        if image_point is None:
            return

        self._press_widget_point = widget_point
        self._selection_start = image_point
        self._selection_current = image_point
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._image is None:
            self.mousePositionChanged.emit(None, None)
            return

        widget_point = event.position().toPoint()
        image_point = self._map_widget_to_image(widget_point)
        if image_point is None and self._selection_start is not None:
            image_point = self._map_widget_to_image(widget_point, clamp=True)

        if image_point is None:
            self.mousePositionChanged.emit(None, None)
        else:
            self.mousePositionChanged.emit(*image_point)

        if self._selection_start is None:
            return

        self._selection_current = self._map_widget_to_image(widget_point, clamp=True)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._image is None:
            return

        if self._selection_start is None or self._press_widget_point is None:
            self._clear_pending_selection()
            return

        widget_point = event.position().toPoint()
        release_point = self._map_widget_to_image(widget_point, clamp=True)
        selection = self._normalized_selection()
        is_dragging = self._is_dragging(widget_point)

        self._clear_pending_selection()

        if release_point is None:
            self.update()
            return

        if not is_dragging:
            marker_index = self._hit_marker(widget_point)
            if marker_index is not None:
                self.markerInspectRequested.emit(marker_index)
            else:
                self.markerAddRequested.emit(*release_point)
            self.update()
            return

        if selection is None:
            self.update()
            return

        x, y, width, height = selection
        if width >= 2 and height >= 2:
            self._roi = selection
            self.roiSelected.emit(x, y, width, height)

        self.update()

    def leaveEvent(self, event) -> None:
        self.mousePositionChanged.emit(None, None)
        super().leaveEvent(event)

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

        left = image_rect.left()
        top = image_rect.top()
        right = left + image_rect.width() - 1
        bottom = top + image_rect.height() - 1

        if clamp:
            px = min(max(point.x(), left), right)
            py = min(max(point.y(), top), bottom)
        elif point.x() < left or point.x() > right or point.y() < top or point.y() > bottom:
            return None
        else:
            px = point.x()
            py = point.y()

        image_x = self._scale_closed_interval(px - left, image_rect.width(), self._image.width())
        image_y = self._scale_closed_interval(py - top, image_rect.height(), self._image.height())
        return image_x, image_y

    def _map_image_to_widget(self, point: tuple[int, int]) -> QPoint:
        """把原始截图坐标映射为控件坐标。"""
        if self._image is None:
            return QPoint()

        image_rect = self._display_rect()
        x, y = point
        widget_x = image_rect.left() + self._scale_closed_interval(x, self._image.width(), image_rect.width())
        widget_y = image_rect.top() + self._scale_closed_interval(y, self._image.height(), image_rect.height())
        return QPoint(widget_x, widget_y)

    @staticmethod
    def _scale_closed_interval(value: int, source_size: int, target_size: int) -> int:
        """按闭区间缩放坐标，保证两端像素能互相映射。"""
        if source_size <= 1 or target_size <= 1:
            return 0
        return int(round(value * (target_size - 1) / (source_size - 1)))

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
        x, y, width, height = selection
        top_left = self._map_image_to_widget((x, y))
        bottom_right = self._map_image_to_widget((x + width - 1, y + height - 1))
        return QRect(top_left, bottom_right).normalized()

    def _draw_selection(self, painter: QPainter, selection: tuple[int, int, int, int], color: QColor) -> None:
        rect = self._image_selection_to_widget_rect(selection)
        painter.setPen(QPen(QColor("#111827"), 4, Qt.PenStyle.SolidLine))
        painter.drawRect(rect)
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine))
        painter.drawRect(rect)

    def _draw_marker(self, painter: QPainter, index: int, marker: tuple[int, int]) -> None:
        widget_point = self._map_image_to_widget(marker)
        selected = index == self._selected_marker_index
        marker_color = QColor("#ffd60a") if selected else QColor("#ff453a")
        radius = 8 if selected else 6
        cross = 13 if selected else 10

        painter.setPen(QPen(QColor("#111827"), 5, Qt.PenStyle.SolidLine))
        painter.drawLine(widget_point.x() - cross, widget_point.y(), widget_point.x() + cross, widget_point.y())
        painter.drawLine(widget_point.x(), widget_point.y() - cross, widget_point.x(), widget_point.y() + cross)
        painter.drawEllipse(widget_point, radius, radius)

        painter.setPen(QPen(marker_color, 2, Qt.PenStyle.SolidLine))
        painter.drawLine(widget_point.x() - cross, widget_point.y(), widget_point.x() + cross, widget_point.y())
        painter.drawLine(widget_point.x(), widget_point.y() - cross, widget_point.x(), widget_point.y() + cross)
        painter.drawEllipse(widget_point, radius, radius)

        label = f"{index + 1}: ({marker[0]}, {marker[1]})"
        metrics = painter.fontMetrics()
        text_rect = QRect(
            widget_point.x() + 12,
            widget_point.y() - metrics.height() - 8,
            metrics.horizontalAdvance(label) + 8,
            metrics.height() + 4,
        )
        painter.fillRect(text_rect, QColor(17, 24, 39, 210))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

    def _hit_marker(self, point: QPoint) -> int | None:
        """返回被点击的标记索引。"""
        if self._image is None:
            return None

        hit_radius = 12
        for index in reversed(range(len(self._markers))):
            marker_point = self._map_image_to_widget(self._markers[index])
            dx = point.x() - marker_point.x()
            dy = point.y() - marker_point.y()
            if dx * dx + dy * dy <= hit_radius * hit_radius:
                return index
        return None

    def _is_dragging(self, current_point: QPoint | None = None) -> bool:
        if self._press_widget_point is None:
            return False
        if current_point is None:
            if self._selection_current is None:
                return False
            current_point = self._map_image_to_widget(self._selection_current)
        dx = current_point.x() - self._press_widget_point.x()
        dy = current_point.y() - self._press_widget_point.y()
        return dx * dx + dy * dy > CLICK_DRAG_THRESHOLD_PX * CLICK_DRAG_THRESHOLD_PX

    def _clear_pending_selection(self) -> None:
        self._press_widget_point = None
        self._selection_start = None
        self._selection_current = None


class DebugMainWindow(QMainWindow):
    """ADB 调试窗口，负责截图刷新、手势状态和图片保存。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BBMA Debug GUI")
        self.resize(1100, 760)

        self._adb: AdbController | None = None
        self._screen_bgr = None
        self._markers: list[tuple[int, int]] = []
        self._selected_marker_index: int | None = None
        self._current_roi: tuple[int, int, int, int] | None = None
        self._last_mouse_position: tuple[int, int] | None = None

        self.view = ScreenshotView(self)
        self.setCentralWidget(self.view)
        self.view.mousePositionChanged.connect(self._on_mouse_position_changed)
        self.view.markerAddRequested.connect(self._add_marker)
        self.view.markerInspectRequested.connect(self._inspect_marker)
        self.view.markerDeleteRequested.connect(self._delete_marker)
        self.view.roiSelected.connect(self._on_roi_selected)
        self.view.roiClearRequested.connect(self._clear_roi)

        self._build_toolbar()
        self._build_status_bar()
        self.statusBar().showMessage("点击“连接/刷新截图”后即可查看坐标、标点或框选 ROI")
        self._set_tools_enabled(False)
        self._update_status_labels()

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

        self.save_full_action = QAction("保存完整截图", self)
        self.save_full_action.triggered.connect(self._save_full_screenshot)
        toolbar.addAction(self.save_full_action)
        toolbar.addSeparator()

        int_validator = QIntValidator(0, 99999, self)
        toolbar.addWidget(QLabel("X: ", self))
        self.x_edit = QLineEdit(self)
        self.x_edit.setValidator(int_validator)
        self.x_edit.setFixedWidth(64)
        self.x_edit.setPlaceholderText("x")
        self.x_edit.returnPressed.connect(self._add_marker_from_inputs)
        toolbar.addWidget(self.x_edit)

        toolbar.addWidget(QLabel("Y: ", self))
        self.y_edit = QLineEdit(self)
        self.y_edit.setValidator(int_validator)
        self.y_edit.setFixedWidth(64)
        self.y_edit.setPlaceholderText("y")
        self.y_edit.returnPressed.connect(self._add_marker_from_inputs)
        toolbar.addWidget(self.y_edit)

        self.jump_marker_action = QAction("跳转标记", self)
        self.jump_marker_action.triggered.connect(self._add_marker_from_inputs)
        toolbar.addAction(self.jump_marker_action)
        toolbar.addSeparator()

        self.save_roi_action = QAction("保存 ROI", self)
        self.save_roi_action.triggered.connect(self._save_roi)
        toolbar.addAction(self.save_roi_action)

    def _build_status_bar(self) -> None:
        """创建底部固定信息区。"""
        status_bar = self.statusBar()
        self.mouse_label = QLabel("坐标: --, --", self)
        self.roi_label = QLabel("ROI: --", self)
        self.marker_label = QLabel("标记: 0", self)
        self.hint_label = QLabel("操作: 左键标点/拖拽 ROI，右键删除点/清除 ROI", self)
        status_bar.addPermanentWidget(self.mouse_label)
        status_bar.addPermanentWidget(self.roi_label)
        status_bar.addPermanentWidget(self.marker_label)
        status_bar.addPermanentWidget(self.hint_label)

    def refresh_screenshot(self) -> None:
        """连接设备并刷新当前截图。"""
        serial = self.serial_edit.text().strip() or ADB_SERIAL
        self.refresh_action.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            self._adb = AdbController(serial)
            self._screen_bgr = self._adb.read_screenshot()
            self._clear_overlay_state()
            self.view.setImage(self._cv_to_qimage(self._screen_bgr))
            self.view.setMarkers(self._markers, self._selected_marker_index)
            self.view.setRoi(self._current_roi)
            self._set_tools_enabled(True)

            height, width = self._screen_bgr.shape[:2]
            self.statusBar().showMessage(f"截图已刷新: {width}x{height}")
        except (AdbCommandError, RuntimeError) as exc:
            self.statusBar().showMessage(f"截图刷新失败: {exc}")
            QMessageBox.warning(self, "截图刷新失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_action.setEnabled(True)
            self._sync_action_states()

    def _set_tools_enabled(self, enabled: bool) -> None:
        """根据是否已有截图启用或禁用画布工具。"""
        self.view.setEnabled(enabled)
        self.save_full_action.setEnabled(enabled)
        self.x_edit.setEnabled(enabled)
        self.y_edit.setEnabled(enabled)
        self.jump_marker_action.setEnabled(enabled)
        self._sync_action_states()

    def _sync_action_states(self) -> None:
        has_screen = self._screen_bgr is not None
        has_roi = self._current_roi is not None
        self.save_roi_action.setEnabled(has_screen and has_roi)

    def _clear_overlay_state(self) -> None:
        self._markers.clear()
        self._selected_marker_index = None
        self._current_roi = None
        self._last_mouse_position = None
        self._update_status_labels()

    def _on_mouse_position_changed(self, x: int | None, y: int | None) -> None:
        if x is None or y is None:
            self._last_mouse_position = None
        else:
            self._last_mouse_position = (int(x), int(y))
        self._update_status_labels()

    def _add_marker(self, x: int, y: int) -> None:
        self._markers.append((x, y))
        self._selected_marker_index = len(self._markers) - 1
        self.x_edit.setText(str(x))
        self.y_edit.setText(str(y))
        self.view.setMarkers(self._markers, self._selected_marker_index)
        self.statusBar().showMessage(f"已添加标记: ({x}, {y})")
        self._update_status_labels()

    def _inspect_marker(self, index: int) -> None:
        if not 0 <= index < len(self._markers):
            return

        self._selected_marker_index = index
        x, y = self._markers[index]
        self.x_edit.setText(str(x))
        self.y_edit.setText(str(y))
        self.view.setMarkers(self._markers, self._selected_marker_index)
        self.statusBar().showMessage(f"标记 #{index + 1}: ({x}, {y})")
        self._update_status_labels()

    def _delete_marker(self, index: int) -> None:
        if not 0 <= index < len(self._markers):
            return

        removed = self._markers.pop(index)
        if self._markers:
            self._selected_marker_index = min(index, len(self._markers) - 1)
        else:
            self._selected_marker_index = None
        self.view.setMarkers(self._markers, self._selected_marker_index)
        self.statusBar().showMessage(f"已删除标记: ({removed[0]}, {removed[1]})")
        self._update_status_labels()

    def _clear_roi(self) -> None:
        if self._current_roi is None:
            self.statusBar().showMessage("当前没有 ROI 可清除")
            return

        self._current_roi = None
        self.view.setRoi(None)
        self.statusBar().showMessage("已清除 ROI")
        self._update_status_labels()
        self._sync_action_states()

    def _add_marker_from_inputs(self) -> None:
        point = self._read_coordinate_inputs()
        if point is None:
            return

        self._add_marker(*point)

    def _read_coordinate_inputs(self) -> tuple[int, int] | None:
        if self._screen_bgr is None:
            QMessageBox.warning(self, "无法标记坐标", "请先连接并刷新截图。")
            return None

        try:
            x = int(self.x_edit.text().strip())
            y = int(self.y_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "坐标无效", "请输入整数 x 和 y 坐标。")
            return None

        height, width = self._screen_bgr.shape[:2]
        if not (0 <= x < width and 0 <= y < height):
            QMessageBox.warning(
                self,
                "坐标越界",
                f"当前截图尺寸为 {width}x{height}，坐标范围是 x=0..{width - 1}, y=0..{height - 1}。",
            )
            return None

        return x, y

    def _on_roi_selected(self, x: int, y: int, width: int, height: int) -> None:
        self._current_roi = (x, y, width, height)
        self.view.setRoi(self._current_roi)
        self.statusBar().showMessage(self._format_roi_message("ROI 已选中", self._current_roi))
        self._update_status_labels()
        self._sync_action_states()

    def _save_full_screenshot(self) -> None:
        if self._screen_bgr is None:
            QMessageBox.warning(self, "无法保存截图", "请先连接并刷新截图。")
            return

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        default_path = SCREENSHOT_DIR / f"full_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        save_path = self._ask_save_path("保存完整截图", default_path)
        if save_path is None:
            self.statusBar().showMessage("已取消保存完整截图")
            return

        if not self._write_png(save_path, self._screen_bgr):
            QMessageBox.warning(self, "保存失败", f"无法保存完整截图: {save_path}")
            self.statusBar().showMessage("完整截图保存失败")
            return

        height, width = self._screen_bgr.shape[:2]
        self.statusBar().showMessage(f"完整截图已保存: {save_path} ({width}x{height})")

    def _save_roi(self) -> None:
        if self._screen_bgr is None:
            QMessageBox.warning(self, "保存 ROI", "请先连接并刷新截图。")
            return
        if self._current_roi is None:
            QMessageBox.warning(self, "保存 ROI", "请先左键拖拽选择 ROI 区域。")
            return

        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"roi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        save_path = self._ask_save_path("保存 ROI", TEMPLATE_DIR / default_name)
        if save_path is None:
            self.statusBar().showMessage("已取消保存 ROI")
            return

        x, y, width, height = self._current_roi
        crop = self._screen_bgr[y : y + height, x : x + width]
        if not self._write_png(save_path, crop):
            QMessageBox.warning(self, "保存失败", f"无法保存图片: {save_path}")
            self.statusBar().showMessage("ROI 保存失败")
            return

        self.statusBar().showMessage(f"ROI 已保存: {save_path} ({width}x{height})")

    def _ask_save_path(self, title: str, default_path: Path) -> Path | None:
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(default_path),
            "PNG 图片 (*.png)",
        )
        if not save_path:
            return None

        path = Path(save_path)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        return path

    @staticmethod
    def _write_png(path: Path, image) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        return image.size > 0 and cv2.imwrite(str(path), image)

    def _update_status_labels(self) -> None:
        if self._last_mouse_position is None:
            self.mouse_label.setText("坐标: --, --")
        else:
            x, y = self._last_mouse_position
            self.mouse_label.setText(f"坐标: {x}, {y}")

        if self._current_roi is None:
            self.roi_label.setText("ROI: --")
        else:
            x, y, width, height = self._current_roi
            self.roi_label.setText(f"ROI: x={x}, y={y}, w={width}, h={height}, 右下=({x + width - 1}, {y + height - 1})")

        if self._selected_marker_index is None:
            self.marker_label.setText(f"标记: {len(self._markers)}")
        else:
            self.marker_label.setText(f"标记: {len(self._markers)}，当前 #{self._selected_marker_index + 1}")

    @staticmethod
    def _format_roi_message(prefix: str, roi: tuple[int, int, int, int]) -> str:
        x, y, width, height = roi
        return f"{prefix}: x={x}, y={y}, w={width}, h={height}, 右下=({x + width - 1}, {y + height - 1})"

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
