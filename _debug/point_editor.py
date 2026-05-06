from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QToolBar,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR, LEVEL_GRID_SIZES, SAVED_POINTS_FILE
from save_points.points import (
    IMAGES_DIR,
    detect_level_entry,
    load_points_data,
    make_level_entry,
    points_from_quad,
    save_points_data,
)
from utils.diamond_centers import read_image


class PointCanvas(QWidget):
    """显示关卡图片，并支持拖动中心点和外层四角。"""

    dataChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(760, 520)
        self.setMouseTracking(True)

        self._image_bgr = None
        self._image: QImage | None = None
        self._points: list[tuple[int, int]] = []
        self._quad: list[tuple[int, int]] = []
        self._drag_kind: str | None = None
        self._drag_index: int | None = None
        self._selected_kind: str | None = None
        self._selected_index: int | None = None

    def set_level_data(
        self,
        image_bgr,
        points: list[tuple[int, int]],
        quad: list[tuple[int, int]],
    ) -> None:
        """设置当前图片和可编辑点位。"""
        self._image_bgr = image_bgr
        self._image = self._cv_to_qimage(image_bgr)
        self._points = list(points)
        self._quad = list(quad)
        self._drag_kind = None
        self._drag_index = None
        self._selected_kind = None
        self._selected_index = None
        self.update()

    def current_points(self) -> list[tuple[int, int]]:
        """返回当前中心点坐标。"""
        return list(self._points)

    def current_quad(self) -> list[tuple[int, int]]:
        """返回当前外层四角坐标。"""
        return list(self._quad)

    def replace_points(self, points: list[tuple[int, int]]) -> None:
        """替换当前中心点并刷新画布。"""
        self._points = list(points)
        self._selected_kind = None
        self._selected_index = None
        self.update()
        self.dataChanged.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#202124"))

        if self._image is None:
            painter.setPen(QColor("#c9d1d9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无点位图片")
            return

        image_rect = self._display_rect()
        painter.drawImage(image_rect, self._image)
        self._draw_quad(painter)
        self._draw_points(painter)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._image is None:
            return

        hit = self._find_nearest_handle(event.position().toPoint())
        if hit is None:
            self._selected_kind = None
            self._selected_index = None
            self.update()
            return

        self._drag_kind, self._drag_index = hit
        self._selected_kind, self._selected_index = hit
        self._move_selected_to(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_kind is None or self._drag_index is None:
            return
        self._move_selected_to(event.position().toPoint())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_kind = None
        self._drag_index = None

    def _draw_points(self, painter: QPainter) -> None:
        """绘制所有小菱形中心点和编号。"""
        for index, point in enumerate(self._points):
            widget_point = self._image_to_widget_point(point)
            if widget_point is None:
                continue

            selected = self._selected_kind == "point" and self._selected_index == index
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QColor("#ffcc00") if selected else QColor("#ff3b30"))
            painter.drawEllipse(widget_point, 5 if selected else 4, 5 if selected else 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(widget_point + QPoint(7, -7), str(index + 1))

    def _draw_quad(self, painter: QPainter) -> None:
        """绘制外层大菱形四角和连线。"""
        if len(self._quad) != 4:
            return

        widget_points = [self._image_to_widget_point(point) for point in self._quad]
        if any(point is None for point in widget_points):
            return

        points = [point for point in widget_points if point is not None]
        painter.setPen(QPen(QColor("#00c8ff"), 2))
        for index in range(4):
            painter.drawLine(points[index], points[(index + 1) % 4])

        names = ["top", "right", "bottom", "left"]
        for index, point in enumerate(points):
            selected = self._selected_kind == "quad" and self._selected_index == index
            painter.setPen(QPen(QColor("#001f2a"), 1))
            painter.setBrush(QColor("#ffcc00") if selected else QColor("#00c8ff"))
            painter.drawRect(point.x() - 6, point.y() - 6, 12, 12)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(point + QPoint(8, -8), names[index])

    def _move_selected_to(self, point: QPoint) -> None:
        """把当前拖动对象移动到鼠标所在的图片坐标。"""
        image_point = self._map_widget_to_image(point, clamp=True)
        if image_point is None or self._drag_index is None:
            return

        if self._drag_kind == "point":
            self._points[self._drag_index] = image_point
        elif self._drag_kind == "quad":
            self._quad[self._drag_index] = image_point

        self.update()
        self.dataChanged.emit()

    def _find_nearest_handle(self, point: QPoint) -> tuple[str, int] | None:
        """查找鼠标附近最近的可拖动点。"""
        best: tuple[str, int] | None = None
        best_dist = 13.0

        for index, image_point in enumerate(self._quad):
            widget_point = self._image_to_widget_point(image_point)
            if widget_point is None:
                continue
            dist = _distance(point, widget_point)
            if dist <= best_dist:
                best = ("quad", index)
                best_dist = dist

        for index, image_point in enumerate(self._points):
            widget_point = self._image_to_widget_point(image_point)
            if widget_point is None:
                continue
            dist = _distance(point, widget_point)
            if dist <= best_dist:
                best = ("point", index)
                best_dist = dist

        return best

    def _display_rect(self) -> QRect:
        """计算图片在控件中的显示区域。"""
        if self._image is None:
            return QRect()

        scaled_size = self._image.size()
        scaled_size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled_size.width()) // 2
        y = (self.height() - scaled_size.height()) // 2
        return QRect(x, y, scaled_size.width(), scaled_size.height())

    def _image_to_widget_point(self, point: tuple[int, int]) -> QPoint | None:
        """把图片坐标转换为控件坐标。"""
        if self._image is None:
            return None

        image_rect = self._display_rect()
        if image_rect.isEmpty():
            return None

        x, y = point
        widget_x = int(image_rect.left() + x * image_rect.width() / self._image.width())
        widget_y = int(image_rect.top() + y * image_rect.height() / self._image.height())
        return QPoint(widget_x, widget_y)

    def _map_widget_to_image(self, point: QPoint, *, clamp: bool = False) -> tuple[int, int] | None:
        """把控件坐标映射成图片坐标。"""
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

    @staticmethod
    def _cv_to_qimage(image_bgr) -> QImage:
        """把 OpenCV BGR 图片转换为 Qt 图片。"""
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        height, width, channels = image_rgb.shape
        bytes_per_line = channels * width
        return QImage(
            image_rgb.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        ).copy()


class PointEditorWindow(QMainWindow):
    """人工校准固定关卡菱形点位的主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BBMA Point Editor")
        self.resize(1180, 760)

        self._data = load_points_data()
        self._entries: dict[int, dict[str, Any]] = {}
        self._dirty_levels: set[int] = set()
        self._current_level: int | None = None

        self.level_list = QListWidget(self)
        self.canvas = PointCanvas(self)
        self.canvas.dataChanged.connect(self._on_canvas_changed)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.level_list)
        splitter.addWidget(self.canvas)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self._load_entries()
        self._populate_level_list()
        self.level_list.currentItemChanged.connect(self._on_level_changed)

        if self.level_list.count() > 0:
            self.level_list.setCurrentRow(0)
        else:
            self.statusBar().showMessage("未找到 save_points/imgs 中的关卡图片")

    def _build_toolbar(self) -> None:
        """创建顶部工具栏按钮。"""
        toolbar = QToolBar("点位工具", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.reset_action = QAction("自动识别重置当前图", self)
        self.reset_action.triggered.connect(self.reset_current_level)
        toolbar.addAction(self.reset_action)

        self.rebuild_action = QAction("按四角重算", self)
        self.rebuild_action.triggered.connect(self.rebuild_points_from_quad)
        toolbar.addAction(self.rebuild_action)

        toolbar.addSeparator()

        self.save_current_action = QAction("保存当前图", self)
        self.save_current_action.triggered.connect(self.save_current_level)
        toolbar.addAction(self.save_current_action)

        self.save_all_action = QAction("保存全部", self)
        self.save_all_action.triggered.connect(self.save_all_levels)
        toolbar.addAction(self.save_all_action)

    def _load_entries(self) -> None:
        """读取已有 JSON，并为缺失关卡生成内存中的初始点位。"""
        levels = self._data.setdefault("levels", {})
        for image_path in _iter_images():
            level = int(image_path.stem)
            n = LEVEL_GRID_SIZES.get(level)
            if n is None:
                continue

            saved = levels.get(str(level))
            if isinstance(saved, dict):
                self._entries[level] = saved
                continue

            self._entries[level] = detect_level_entry(image_path, n)

    def _populate_level_list(self) -> None:
        """刷新左侧关卡列表。"""
        self.level_list.clear()
        for level in sorted(self._entries):
            item = QListWidgetItem(f"第 {level} 关")
            item.setData(Qt.ItemDataRole.UserRole, level)
            self.level_list.addItem(item)

    def _on_level_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        """切换左侧关卡时刷新画布。"""
        if current is None:
            return

        if self._current_level in self._dirty_levels:
            self._sync_current_entry()

        level = int(current.data(Qt.ItemDataRole.UserRole))
        self._current_level = level
        entry = self._entries[level]
        image_bgr = read_image(BASE_DIR / entry["image"])
        points = [_to_point(point) for point in entry["points"]]
        quad = [_to_point(point) for point in entry["quad"]]
        self.canvas.set_level_data(image_bgr, points, quad)
        self._update_status()

    def reset_current_level(self) -> None:
        """用自动识别结果重置当前关卡。"""
        if self._current_level is None:
            return

        image_path = IMAGES_DIR / f"{self._current_level}.png"
        if not image_path.exists():
            QMessageBox.warning(self, "重置失败", f"未找到图片：{image_path}")
            return

        try:
            entry = detect_level_entry(image_path, LEVEL_GRID_SIZES[self._current_level])
        except Exception as exc:
            QMessageBox.warning(self, "重置失败", str(exc))
            return

        self._entries[self._current_level] = entry
        self._dirty_levels.add(self._current_level)
        self._reload_current_canvas()
        self._update_status("已使用自动识别重置当前图")

    def rebuild_points_from_quad(self) -> None:
        """根据当前四角重新生成所有中心点。"""
        if self._current_level is None:
            return

        n = LEVEL_GRID_SIZES[self._current_level]
        points = points_from_quad(self.canvas.current_quad(), n)
        self.canvas.replace_points(points)
        self._dirty_levels.add(self._current_level)
        self._update_status("已按四角重算中心点")

    def save_current_level(self) -> None:
        """保存当前关卡到点位 JSON。"""
        if self._current_level is None:
            return

        self._sync_current_entry()
        self._data.setdefault("levels", {})[str(self._current_level)] = self._entries[self._current_level]
        save_points_data(self._data, SAVED_POINTS_FILE)
        self._dirty_levels.discard(self._current_level)
        self._update_status("当前关卡已保存")

    def save_all_levels(self) -> None:
        """保存全部已加载关卡到点位 JSON。"""
        if self._current_level in self._dirty_levels:
            self._sync_current_entry()

        levels = self._data.setdefault("levels", {})
        for level, entry in self._entries.items():
            levels[str(level)] = entry

        save_points_data(self._data, SAVED_POINTS_FILE)
        self._dirty_levels.clear()
        self._update_status("全部关卡已保存")

    def _on_canvas_changed(self) -> None:
        """画布拖动后记录当前关卡有未保存修改。"""
        if self._current_level is None:
            return
        self._dirty_levels.add(self._current_level)
        self._update_status("有未保存修改")

    def _sync_current_entry(self) -> None:
        """把画布上的当前点位同步回内存结构。"""
        if self._current_level is None:
            return

        old_entry = self._entries[self._current_level]
        self._entries[self._current_level] = make_level_entry(
            image_path=BASE_DIR / old_entry["image"],
            n=old_entry["n"],
            image_size=old_entry["image_size"],
            quad=self.canvas.current_quad(),
            points=self.canvas.current_points(),
            source="manual",
        )

    def _reload_current_canvas(self) -> None:
        """从内存数据重新加载当前关卡画布。"""
        if self._current_level is None:
            return
        entry = self._entries[self._current_level]
        image_bgr = read_image(BASE_DIR / entry["image"])
        points = [_to_point(point) for point in entry["points"]]
        quad = [_to_point(point) for point in entry["quad"]]
        self.canvas.set_level_data(image_bgr, points, quad)

    def _update_status(self, prefix: str | None = None) -> None:
        """刷新底部状态栏。"""
        if self._current_level is None:
            return

        entry = self._entries[self._current_level]
        dirty = "未保存" if self._current_level in self._dirty_levels else "已保存"
        text = f"第 {self._current_level} 关：n={entry['n']} points={len(entry['points'])}，{dirty}"
        if prefix:
            text = f"{prefix}。{text}"
        self.statusBar().showMessage(text)


def _iter_images() -> list[Path]:
    """按关卡编号返回 save_points/imgs 中的图片。"""
    if not IMAGES_DIR.exists():
        return []

    paths = [
        path
        for path in IMAGES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.stem.isdigit()
    ]
    return sorted(paths, key=lambda item: int(item.stem))


def _to_point(point: Any) -> tuple[int, int]:
    """把 JSON 点坐标转换为整数元组。"""
    return int(point[0]), int(point[1])


def _distance(a: QPoint, b: QPoint) -> float:
    """计算两个控件坐标点的距离。"""
    return ((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2) ** 0.5


def main() -> int:
    """启动人工点位校准工具。"""
    app = QApplication(sys.argv)
    window = PointEditorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
