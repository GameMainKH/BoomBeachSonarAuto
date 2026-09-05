"""以人工模板为基准测量页面平移；不改变校准数据或网络状态。"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import cv2
import numpy as np

from save_points.points import SHARED_POINT_LEVEL, IMAGES_DIR, read_saved_points
from utils.diamond_centers import read_image


BOARD_VIEW_SWIPE = (1000, 500, 1000, 545)
BOARD_VIEW_SETTLE = 1.5


class BoardAlignmentError(RuntimeError):
    """页面位置不能可靠确定；禁止使用上一次的视角继续点击。"""


class BoardVictoryVisible(RuntimeError):
    def __init__(self, match):
        super().__init__("对齐采样期间出现胜利页")
        self.match = match


@dataclass(frozen=True)
class PatchMatch:
    row: int
    col: int
    dx: int
    dy: int
    score: float


@dataclass(frozen=True)
class BoardView:
    level: int
    entry_id: int
    dx: int = 0
    dy: int = 0
    matches: tuple[tuple[PatchMatch, ...], ...] = ()

    def point(self, point):
        return int(point[0]) + self.dx, int(point[1]) + self.dy

    def quad(self, quad):
        return np.asarray(quad, dtype=np.float32) + (self.dx, self.dy)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class BoardFrame:
    raw: np.ndarray
    view: BoardView

    def reference_image(self):
        """仅供格子识别使用；UI 识别和磁盘证据仍使用原图。"""
        dx, dy = self.view.dx, self.view.dy
        if dx == 0 and dy == 0:
            return self.raw
        h, w = self.raw.shape[:2]
        matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
        return cv2.warpAffine(self.raw, matrix, (w, h), flags=cv2.INTER_NEAREST)


def estimate_board_translation(reference, image, points):
    """九块局部匹配形成整体平移共识，不用潜艇或某一目标格决定偏移。"""
    if reference.shape != image.shape or image.shape != (720, 1280, 3):
        raise BoardAlignmentError("视角对齐要求模板与截图均为 1280×720")
    if len(points) != 100:
        raise BoardAlignmentError("共享校准必须包含 100 个点位")
    ref = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    matches = []
    for row in (2, 5, 7):
        for col in (2, 5, 7):
            x, y = points[row * 10 + col]
            if not (82 <= x < 1280 - 82 and 66 <= y < 720 - 66):
                raise BoardAlignmentError("校准点位的对齐采样范围越界")
            needle = ref[y-48:y+49, x-64:x+65]
            haystack = gray[y-66:y+67, x-82:x+83]
            if float(np.std(needle)) < 1.0:
                continue
            scores = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(scores)
            if np.isfinite(score) and score >= 0.60:
                matches.append(PatchMatch(row, col, location[0]-18, location[1]-18, score))
    candidates = []
    for dx in range(-18, 19):
        for dy in range(-18, 19):
            inliers = tuple(m for m in matches if max(abs(m.dx-dx), abs(m.dy-dy)) <= 3)
            if len(inliers) < 5 or len({m.row for m in inliers}) < 2 or len({m.col for m in inliers}) < 2:
                continue
            residual = sum(abs(m.dx-dx)+abs(m.dy-dy) for m in inliers)
            candidates.append(((len(inliers), sum(m.score for m in inliers), -residual), dx, dy, inliers))
    if not candidates:
        raise BoardAlignmentError(f"视角匹配缺少一致证据：合格区域={len(matches)}")
    best = max(candidates, key=lambda c: c[0])
    _, dx, dy, inliers = best
    # A separate competing consensus must not silently choose another lattice cell.
    if any(c[0][0] == best[0][0] and abs(c[0][1]-best[0][1]) < 0.05
           and max(abs(c[1]-dx), abs(c[2]-dy)) > 6 for c in candidates):
        raise BoardAlignmentError("视角匹配存在多个相互冲突的平移解释")
    if max(abs(dx), abs(dy)) > 16 or any(abs(m.dx) == 18 or abs(m.dy) == 18 for m in inliers):
        raise BoardAlignmentError(f"视角偏移超出安全范围：({dx}, {dy})")
    return dx, dy, inliers


class BoardViewTracker:
    """每个 worker 独立持有，只有新页面完整对齐后才发布当前视角。"""

    def __init__(self):
        self.current = None
        self.entry_id = 0

    def invalidate(self):
        self.current = None

    def snapshot(self, level):
        if level < SHARED_POINT_LEVEL:
            return BoardView(level, self.entry_id)
        if self.current is None or self.current.level != level:
            raise BoardAlignmentError(f"第 {level} 关缺少当前页面的有效对齐记录")
        return self.current

    def frame(self, image, level, path=None):
        view = self.snapshot(level)
        if path is not None and level >= SHARED_POINT_LEVEL:
            metadata = Path(str(path) + ".view.json")
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text(json.dumps(view.to_dict(), indent=2)+"\n", encoding="utf-8")
        return BoardFrame(image, view)

    def establish(self, adb, level, evidence_root, *, validate_frame=None):
        self.invalidate()
        self.entry_id += 1
        if level < SHARED_POINT_LEVEL:
            self.current = BoardView(level, self.entry_id)
            return self.current
        directory = Path(evidence_root) / f"level_{level:02d}" / f"entry_{self.entry_id:04d}"
        directory.mkdir(parents=True, exist_ok=True)
        records = []
        try:
            reference = read_image(IMAGES_DIR / f"{SHARED_POINT_LEVEL}.png")
            points = read_saved_points(level, expected_n=10)
            if points is None:
                raise BoardAlignmentError("共享人工校准不存在")
            for index in range(2):
                if index:
                    adb.delay(0.5)
                frame = adb.read_screenshot(directory / f"frame_{index+1:02d}.png")
                if validate_frame is not None:
                    validate_frame(frame)
                records.append(estimate_board_translation(reference, frame, points))
            first, last = records
            if max(abs(first[0]-last[0]), abs(first[1]-last[1])) > 2:
                raise BoardAlignmentError("拖动后的两帧视角仍不稳定")
            view = BoardView(level, self.entry_id, last[0], last[1], tuple(r[2] for r in records))
            (directory / "alignment.json").write_text(json.dumps(view.to_dict(), indent=2)+"\n", encoding="utf-8")
            self.current = view
            return view
        except Exception as exc:
            (directory / "failure.json").write_text(json.dumps({
                "level": level, "entry_id": self.entry_id, "error": str(exc),
                "measurements": [{"dx": r[0], "dy": r[1], "matches": [asdict(m) for m in r[2]]} for r in records],
            }, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            raise


def prepare_board_view(adb, level: int) -> None:
    """仅在刚进入海域后调用一次；第 8 关起下移视角并等待落稳。"""
    if level < SHARED_POINT_LEVEL:
        return
    adb.delay(BOARD_VIEW_SETTLE)
    adb.swipe(*BOARD_VIEW_SWIPE)
    adb.delay(BOARD_VIEW_SETTLE)
