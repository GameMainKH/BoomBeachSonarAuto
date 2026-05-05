from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config import DEFAULT_MATCH_THRESHOLD, DEFAULT_TEMPLATE_SHAPE_POWER, DEFAULT_TEMPLATE_SHAPE_WEIGHT


@dataclass(frozen=True)
class MatchResult:
    ''' 模板匹配结果，包含模板路径、匹配坐标和得分。 '''
    template_path: Path             # 模板图片路径
    top_left: tuple[int, int]       # 模板在截图中的左上角坐标 (x, y)
    bottom_right: tuple[int, int]   # 模板在截图中的右下角坐标 (x, y)
    center: tuple[int, int]         # 模板在截图中的中心坐标 (x, y)
    score: float                    # 模板匹配的得分，范围 [0, 1]，越接近 1 表示匹配越好


def find_template(
    screenshot,
    template_path: str | Path,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    shape_weight: float = DEFAULT_TEMPLATE_SHAPE_WEIGHT,
    shape_power: float = DEFAULT_TEMPLATE_SHAPE_POWER,
) -> MatchResult | None:
    """ 在给定的截图中查找模板图片，返回匹配结果或 None。"""
    path = Path(template_path)
    template, mask = _read_template(path)
    screenshot = _normalize_screenshot(screenshot)
    shape_weight = _normalize_weight(shape_weight)
    shape_power = max(float(shape_power), 1.0)

    if template.shape[0] > screenshot.shape[0] or template.shape[1] > screenshot.shape[1]:
        return None

    # 透明模板使用 alpha 通道作为 mask，并额外叠加 alpha 轮廓分数，降低纯色区域误匹配
    if mask is None:
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    else:
        color_result = cv2.matchTemplate(screenshot, template, cv2.TM_CCORR_NORMED, mask=mask)
        shape_quality = _match_alpha_shape(screenshot, template, mask)
        result = _combine_color_and_shape(color_result, shape_quality, shape_weight, shape_power)
    _, max_score, _, max_loc = cv2.minMaxLoc(result)

    # 匹配分数未达到阈值，视为未找到
    if max_score < threshold:
        return None

    # 计算匹配区域的坐标
    template_height, template_width = template.shape[:2]
    top_left = max_loc
    bottom_right = (top_left[0] + template_width, top_left[1] + template_height)
    center = (top_left[0] + template_width // 2, top_left[1] + template_height // 2)

    return MatchResult(
        template_path=path,
        top_left=top_left,
        bottom_right=bottom_right,
        center=center,
        score=max_score,
    )


def _read_template(path: Path):
    """读取模板图片；透明 PNG 会裁剪到 alpha 有效区域并返回 mask。"""
    raw_template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw_template is None:
        raise FileNotFoundError(f"无法读取模板: {path}")

    if raw_template.ndim == 2:
        return cv2.cvtColor(raw_template, cv2.COLOR_GRAY2BGR), None

    if raw_template.shape[2] != 4:
        return raw_template, None

    template = raw_template[:, :, :3]
    alpha = raw_template[:, :, 3]
    bbox = cv2.boundingRect(alpha)
    x, y, width, height = bbox
    if width == 0 or height == 0:
        raise ValueError(f"模板 alpha 通道为空: {path}")

    cropped_template = template[y : y + height, x : x + width]
    cropped_mask = alpha[y : y + height, x : x + width]
    return cropped_template, cropped_mask


def _normalize_screenshot(screenshot):
    """把截图统一转换为 BGR 三通道图片。"""
    if screenshot is None:
        raise ValueError("截图不能为空")
    if screenshot.ndim == 2:
        return cv2.cvtColor(screenshot, cv2.COLOR_GRAY2BGR)
    if screenshot.ndim == 3 and screenshot.shape[2] == 4:
        return screenshot[:, :, :3]
    return screenshot


def _match_alpha_shape(screenshot, template, mask):
    """用 alpha 轮廓匹配截图边缘，返回形状分数图。"""
    alpha = (mask > 16).astype("uint8") * 255
    if _is_almost_rect_mask(alpha):
        return None

    kernel = np.ones((3, 3), np.uint8)
    template_edges = cv2.morphologyEx(alpha, cv2.MORPH_GRADIENT, kernel)
    template_edges = cv2.dilate(template_edges, kernel, iterations=1)
    if cv2.countNonZero(template_edges) == 0:
        return None

    screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
    screenshot_edges = cv2.Canny(screenshot_gray, 50, 150)
    shape_result = cv2.matchTemplate(screenshot_edges, template_edges, cv2.TM_CCORR_NORMED)
    edge_quality = _normalize_shape_score(shape_result)

    silhouette_quality = _match_solid_color_silhouette(screenshot, template, alpha)
    if silhouette_quality is None:
        return edge_quality
    return np.minimum(edge_quality, silhouette_quality)


def _combine_color_and_shape(color_result, shape_quality, shape_weight: float, shape_power: float):
    """组合颜色分数和形状质量，形状差的候选会被明显压低。"""
    if shape_quality is None or shape_weight <= 0:
        return color_result

    shape_quality = np.power(shape_quality, shape_power)
    return color_result * ((1.0 - shape_weight) + shape_weight * shape_quality)


def _is_almost_rect_mask(alpha) -> bool:
    """判断 alpha 是否接近完整矩形；完整矩形不需要额外形状匹配。"""
    fill_ratio = cv2.countNonZero(alpha) / alpha.size
    return fill_ratio >= 0.92


def _match_solid_color_silhouette(screenshot, template, alpha):
    """单色透明模板使用颜色二值图与 alpha 做 IoU 形状匹配。"""
    foreground = template[alpha > 0]
    if foreground.size == 0:
        return None

    color_std = float(np.mean(np.std(foreground.reshape(-1, 3), axis=0)))
    if color_std > 35:
        return None

    median_color = np.median(foreground.reshape(-1, 3), axis=0)
    tolerance = max(45.0, color_std * 3.0)
    distance = np.linalg.norm(screenshot.astype(np.float32) - median_color.astype(np.float32), axis=2)
    candidate_mask = (distance <= tolerance).astype("uint8")
    alpha_binary = (alpha > 0).astype("uint8")

    intersection = cv2.matchTemplate(candidate_mask, alpha_binary, cv2.TM_CCORR)
    rect_count = cv2.boxFilter(
        candidate_mask.astype("float32"),
        ddepth=-1,
        ksize=(alpha.shape[1], alpha.shape[0]),
        normalize=False,
        anchor=(0, 0),
    )
    rect_count = rect_count[: intersection.shape[0], : intersection.shape[1]]

    alpha_count = float(alpha_binary.sum())
    union = alpha_count + rect_count - intersection
    silhouette_iou = np.divide(
        intersection,
        union,
        out=np.zeros_like(intersection, dtype="float32"),
        where=union > 0,
    )
    return np.clip(silhouette_iou / 0.65, 0.0, 1.0)


def _normalize_shape_score(shape_result):
    """把 Canny 轮廓匹配分数转换为绝对形状质量，不使用全图最大值归一化。"""
    shape_result = np.nan_to_num(shape_result, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(shape_result / 0.45, 0.0, 1.0)


def _normalize_weight(weight: float) -> float:
    """把形状权重限制在 0 到 1。"""
    return min(max(float(weight), 0.0), 1.0)
