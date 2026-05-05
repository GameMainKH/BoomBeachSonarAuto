from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np


Point = tuple[int, int]


@dataclass
class DiamondHitConfig:
    """菱形格命中判断配置。"""

    diamond_w: int = 80
    diamond_h: int = 56
    search_radius: int = 14
    inner_scale: float = 0.72
    center_scale: float = 0.52
    diff_threshold: int = 18
    min_changed_ratio: float = 0.08
    gray_s_max: int = 78
    rgb_delta_max: int = 52
    v_min: int = 28
    v_max: int = 230
    min_center_gray_ratio: float = 0.085
    min_gray_excess: float = 0.045
    min_component_ratio: float = 0.026
    min_s_drop: float = 8.0
    min_edge_density: float = 0.014
    hit_score_threshold: float = 0.58
    debug: bool = False
    debug_dir: str = "debug_diamond_pair"


@dataclass
class DiamondHitResult:
    """菱形格命中判断结果，包含调试指标。"""

    state: str
    confidence: float
    score: float
    rough_center: Point
    refined_center: Point
    changed_ratio: float
    center_gray_ratio: float
    ring_gray_ratio: float
    gray_excess: float
    component_ratio: float
    s_center: float
    s_ring: float
    s_drop: float
    edge_density: float


def is_diamond_hit(
    before_screenshot: np.ndarray,
    after_screenshot: np.ndarray,
    center: Point,
    diamond_w: int = 80,
    diamond_h: int = 56,
    search_radius: int = 14,
) -> bool:
    """判断单个菱形格是否命中，未命中或无法确认时返回 False。"""
    config = DiamondHitConfig(
        diamond_w=diamond_w,
        diamond_h=diamond_h,
        search_radius=search_radius,
    )
    result = classify_diamond_hit(
        before_screenshot=before_screenshot,
        after_screenshot=after_screenshot,
        center=center,
        config=config,
    )
    return result.state == "hit"


def classify_diamond_hit(
    before_screenshot: np.ndarray,
    after_screenshot: np.ndarray,
    center: Point,
    config: DiamondHitConfig | None = None,
    index: int = 0,
) -> DiamondHitResult:
    """判断单个菱形格命中状态，并返回评分和调试指标。"""
    _validate_screenshot("before_screenshot", before_screenshot)
    _validate_screenshot("after_screenshot", after_screenshot)

    if before_screenshot.shape[:2] != after_screenshot.shape[:2]:
        raise ValueError("before_screenshot 和 after_screenshot 的图片尺寸必须一致")

    config = config or DiamondHitConfig()
    rough_center = _to_point(center)

    refined_center = refine_center_by_pair(
        before_bgr=before_screenshot,
        after_bgr=after_screenshot,
        rough_center=rough_center,
        config=config,
    )

    crop_w = int(config.diamond_w * 1.7)
    crop_h = int(config.diamond_h * 1.9)

    before_crop, local_center, _ = crop_around(before_screenshot, refined_center, crop_w, crop_h)
    after_crop, _, _ = crop_around(after_screenshot, refined_center, crop_w, crop_h)

    h, w = after_crop.shape[:2]

    inner_mask = make_diamond_mask(
        (h, w),
        local_center,
        config.diamond_w,
        config.diamond_h,
        scale=config.inner_scale,
    )
    center_mask = make_diamond_mask(
        (h, w),
        local_center,
        config.diamond_w,
        config.diamond_h,
        scale=config.center_scale,
    )
    ring_mask = cv2.subtract(inner_mask, center_mask)

    before_gray = cv2.cvtColor(before_crop, cv2.COLOR_BGR2GRAY)
    after_gray = cv2.cvtColor(after_crop, cv2.COLOR_BGR2GRAY)
    diff_gray = cv2.absdiff(before_gray, after_gray)

    changed_mask = (diff_gray >= config.diff_threshold).astype(np.uint8) * 255
    changed_ratio = ratio_in_mask(changed_mask, inner_mask)

    gray_candidate = build_gray_candidate_mask(after_crop, config)

    center_gray_mask = cv2.bitwise_and(gray_candidate, gray_candidate, mask=center_mask)
    ring_gray_mask = cv2.bitwise_and(gray_candidate, gray_candidate, mask=ring_mask)

    center_gray_ratio = ratio_in_mask(center_gray_mask, center_mask)
    ring_gray_ratio = ratio_in_mask(ring_gray_mask, ring_mask)
    gray_excess = center_gray_ratio - ring_gray_ratio

    largest_component = get_largest_component_area(center_gray_mask)
    center_area = max(1, int(np.count_nonzero(center_mask)))
    component_ratio = largest_component / center_area

    after_hsv = cv2.cvtColor(after_crop, cv2.COLOR_BGR2HSV)
    _, s_after, _ = cv2.split(after_hsv)

    s_center = mean_in_mask(s_after, center_mask)
    s_ring = mean_in_mask(s_after, ring_mask)
    s_drop = s_ring - s_center

    blur = cv2.GaussianBlur(after_gray, (3, 3), 0)
    edges = cv2.Canny(blur, 35, 90)
    center_edges = cv2.bitwise_and(edges, edges, mask=center_mask)
    edge_density = ratio_in_mask(center_edges, center_mask)

    score = 0.0
    score += score_piece(center_gray_ratio, config.min_center_gray_ratio, 0.28)
    score += score_piece(max(0.0, gray_excess), config.min_gray_excess, 0.22)
    score += score_piece(component_ratio, config.min_component_ratio, 0.25)
    score += score_piece(max(0.0, s_drop), config.min_s_drop, 0.15)
    score += score_piece(edge_density, config.min_edge_density, 0.10)
    score = max(0.0, min(1.0, score))

    if changed_ratio < config.min_changed_ratio:
        state = "unknown"
        confidence = 1.0 - changed_ratio / max(config.min_changed_ratio, 1e-6)
        confidence = max(0.0, min(1.0, confidence))
    elif score >= config.hit_score_threshold:
        state = "hit"
        confidence = score
    else:
        state = "miss"
        confidence = 1.0 - score

    if config.debug:
        save_debug_images(
            before_crop=before_crop,
            after_crop=after_crop,
            diff_gray=diff_gray,
            gray_candidate=gray_candidate,
            inner_mask=inner_mask,
            center_mask=center_mask,
            ring_mask=ring_mask,
            local_center=local_center,
            result_text=(
                f"{state} score={score:.3f} "
                f"chg={changed_ratio:.3f} "
                f"gray={center_gray_ratio:.3f} "
                f"ex={gray_excess:.3f} "
                f"comp={component_ratio:.3f} "
                f"sdrop={s_drop:.1f} "
                f"edge={edge_density:.3f}"
            ),
            config=config,
            index=index,
        )

    return DiamondHitResult(
        state=state,
        confidence=confidence,
        score=score,
        rough_center=rough_center,
        refined_center=refined_center,
        changed_ratio=changed_ratio,
        center_gray_ratio=center_gray_ratio,
        ring_gray_ratio=ring_gray_ratio,
        gray_excess=gray_excess,
        component_ratio=component_ratio,
        s_center=s_center,
        s_ring=s_ring,
        s_drop=s_drop,
        edge_density=edge_density,
    )


def clamp_int(value: int, low: int, high: int) -> int:
    """把整数限制在指定范围内。"""
    return max(low, min(high, value))


def diamond_points(center: Point, diamond_w: int, diamond_h: int, scale: float = 1.0) -> np.ndarray:
    """根据中心点生成菱形四角坐标。"""
    cx, cy = center
    half_w = diamond_w * scale / 2.0
    half_h = diamond_h * scale / 2.0

    return np.array(
        [
            [int(round(cx)), int(round(cy - half_h))],
            [int(round(cx + half_w)), int(round(cy))],
            [int(round(cx)), int(round(cy + half_h))],
            [int(round(cx - half_w)), int(round(cy))],
        ],
        dtype=np.int32,
    )


def make_diamond_mask(
    shape_hw: tuple[int, int],
    center: Point,
    diamond_w: int,
    diamond_h: int,
    scale: float = 1.0,
) -> np.ndarray:
    """生成指定菱形区域的二值 mask。"""
    h, w = shape_hw
    mask = np.zeros((h, w), dtype=np.uint8)
    pts = diamond_points(center, diamond_w, diamond_h, scale)
    cv2.fillConvexPoly(mask, pts, 255)
    return mask


def crop_around(
    image: np.ndarray,
    center: Point,
    crop_w: int,
    crop_h: int,
) -> tuple[np.ndarray, Point, Point]:
    """围绕中心点裁剪图片，并返回局部中心和原图偏移。"""
    h, w = image.shape[:2]
    cx, cy = center

    x1 = clamp_int(cx - crop_w // 2, 0, w - 1)
    y1 = clamp_int(cy - crop_h // 2, 0, h - 1)
    x2 = clamp_int(cx + crop_w // 2, 0, w)
    y2 = clamp_int(cy + crop_h // 2, 0, h)

    cropped = image[y1:y2, x1:x2].copy()
    local_center = (cx - x1, cy - y1)
    offset = (x1, y1)

    return cropped, local_center, offset


def mean_in_mask(image_2d: np.ndarray, mask: np.ndarray) -> float:
    """计算 mask 区域内的平均值。"""
    values = image_2d[mask > 0]
    if values.size == 0:
        return 0.0
    return float(np.mean(values))


def ratio_in_mask(binary_mask: np.ndarray, area_mask: np.ndarray) -> float:
    """计算二值 mask 在目标区域内的占比。"""
    area = int(np.count_nonzero(area_mask))
    if area <= 0:
        return 0.0
    count = int(np.count_nonzero((binary_mask > 0) & (area_mask > 0)))
    return count / area


def get_largest_component_area(binary_mask: np.ndarray) -> int:
    """返回二值图中最大连通块面积。"""
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary_mask, connectivity=8)

    if num_labels <= 1:
        return 0

    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return 0

    return int(np.max(areas))


def build_gray_candidate_mask(after_bgr: np.ndarray, config: DiamondHitConfig) -> np.ndarray:
    """提取点击后图片中的灰色废墟候选区域。"""
    hsv = cv2.cvtColor(after_bgr, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(after_bgr)
    _, s, v = cv2.split(hsv)

    max_rgb = np.maximum(np.maximum(r, g), b)
    min_rgb = np.minimum(np.minimum(r, g), b)
    rgb_delta = max_rgb.astype(np.int16) - min_rgb.astype(np.int16)

    gray_candidate = (
        (s <= config.gray_s_max)
        & (rgb_delta <= config.rgb_delta_max)
        & (v >= config.v_min)
        & (v <= config.v_max)
    ).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    gray_candidate = cv2.morphologyEx(gray_candidate, cv2.MORPH_OPEN, kernel)
    gray_candidate = cv2.morphologyEx(gray_candidate, cv2.MORPH_CLOSE, kernel)

    return gray_candidate


def refine_center_by_pair(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    rough_center: Point,
    config: DiamondHitConfig,
) -> Point:
    """使用点击前后差异和点击前白色填充特征修正中心点。"""
    crop_w = config.diamond_w + config.search_radius * 2 + 60
    crop_h = config.diamond_h + config.search_radius * 2 + 60

    before_crop, local_center, offset = crop_around(before_bgr, rough_center, crop_w, crop_h)
    after_crop, _, _ = crop_around(after_bgr, rough_center, crop_w, crop_h)

    before_gray = cv2.cvtColor(before_crop, cv2.COLOR_BGR2GRAY)
    after_gray = cv2.cvtColor(after_crop, cv2.COLOR_BGR2GRAY)
    diff_gray = cv2.absdiff(before_gray, after_gray)

    before_hsv = cv2.cvtColor(before_crop, cv2.COLOR_BGR2HSV)
    _, s_before, v_before = cv2.split(before_hsv)

    # 点击前格子常带白色透明填充：低饱和加较高亮度。
    before_white = ((s_before <= 65) & (v_before >= 125)).astype(np.uint8) * 255

    h, w = before_crop.shape[:2]

    best_score = -1e9
    best_center = local_center

    for dy in range(-config.search_radius, config.search_radius + 1):
        for dx in range(-config.search_radius, config.search_radius + 1):
            cand = (local_center[0] + dx, local_center[1] + dy)

            inner_mask = make_diamond_mask(
                (h, w),
                cand,
                config.diamond_w,
                config.diamond_h,
                scale=config.inner_scale,
            )
            full_mask = make_diamond_mask(
                (h, w),
                cand,
                config.diamond_w,
                config.diamond_h,
                scale=1.0,
            )
            outer_big = make_diamond_mask(
                (h, w),
                cand,
                config.diamond_w,
                config.diamond_h,
                scale=1.34,
            )
            outer_ring = cv2.subtract(outer_big, full_mask)

            inner_diff = mean_in_mask(diff_gray, inner_mask)
            outer_diff = mean_in_mask(diff_gray, outer_ring)
            white_ratio = ratio_in_mask(before_white, inner_mask)

            # inner_diff 衡量目标格子变化，outer_diff 用来扣除周围动态海水变化。
            score = inner_diff - 0.35 * outer_diff + 35.0 * white_ratio

            if score > best_score:
                best_score = score
                best_center = cand

    return best_center[0] + offset[0], best_center[1] + offset[1]


def score_piece(value: float, threshold: float, weight: float) -> float:
    """把单项指标转换为加权评分。"""
    if threshold <= 0:
        return 0.0

    if value >= threshold:
        return weight

    return weight * 0.35 * max(0.0, min(1.0, value / threshold))


def save_debug_images(
    before_crop: np.ndarray,
    after_crop: np.ndarray,
    diff_gray: np.ndarray,
    gray_candidate: np.ndarray,
    inner_mask: np.ndarray,
    center_mask: np.ndarray,
    ring_mask: np.ndarray,
    local_center: Point,
    result_text: str,
    config: DiamondHitConfig,
    index: int,
) -> None:
    """保存命中判断过程中的调试图片。"""
    os.makedirs(config.debug_dir, exist_ok=True)

    vis = after_crop.copy()

    red_overlay = np.zeros_like(vis)
    red_overlay[:, :, 2] = cv2.bitwise_and(gray_candidate, gray_candidate, mask=center_mask)
    vis = cv2.addWeighted(vis, 0.78, red_overlay, 0.55, 0)

    inner_pts = diamond_points(local_center, config.diamond_w, config.diamond_h, config.inner_scale)
    center_pts = diamond_points(local_center, config.diamond_w, config.diamond_h, config.center_scale)

    cv2.polylines(vis, [inner_pts], True, (0, 255, 255), 1)
    cv2.polylines(vis, [center_pts], True, (0, 0, 255), 1)

    cv2.putText(
        vis,
        result_text,
        (5, max(18, vis.shape[0] - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.36,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    diff_norm = cv2.normalize(diff_gray, None, 0, 255, cv2.NORM_MINMAX)
    diff_color = cv2.applyColorMap(diff_norm.astype(np.uint8), cv2.COLORMAP_JET)

    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_before_crop.png"), before_crop)
    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_after_overlay.png"), vis)
    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_diff.png"), diff_color)
    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_gray_candidate.png"), gray_candidate)
    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_center_mask.png"), center_mask)
    cv2.imwrite(os.path.join(config.debug_dir, f"{index:02d}_ring_mask.png"), ring_mask)


def classify_diamond_pair(
    before_bgr: np.ndarray,
    after_bgr: np.ndarray,
    rough_center: Point,
    config: "DiamondHitConfig | DiamondPairConfig",
    index: int = 0,
) -> DiamondHitResult:
    """兼容旧名称，内部转调 classify_diamond_hit。"""
    return classify_diamond_hit(before_bgr, after_bgr, rough_center, config, index)


def _validate_screenshot(name: str, screenshot: np.ndarray) -> None:
    """校验截图必须是 OpenCV BGR 彩色图片。"""
    if not isinstance(screenshot, np.ndarray):
        raise TypeError(f"{name} 必须是 OpenCV 图像对象")

    if screenshot.ndim != 3 or screenshot.shape[2] != 3:
        raise ValueError(f"{name} 必须是 BGR 彩色图片")

    if screenshot.size == 0:
        raise ValueError(f"{name} 不能为空")


def _to_point(center: Point) -> Point:
    """把输入中心点转换为整数坐标。"""
    if len(center) != 2:
        raise ValueError(f"center 必须是 (x, y): {center}")

    return int(center[0]), int(center[1])


DiamondPairConfig = DiamondHitConfig
DiamondPairResult = DiamondHitResult


__all__ = [
    "DiamondHitConfig",
    "DiamondHitResult",
    "DiamondPairConfig",
    "DiamondPairResult",
    "classify_diamond_hit",
    "classify_diamond_pair",
    "is_diamond_hit",
]
