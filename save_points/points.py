from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR, LEVEL_GRID_SIZES, SAVED_POINTS_FILE
from utils.diamond_centers import centers_from_quad, detect_diamond_centers, read_image

POINTS_VERSION = 2
SHARED_POINT_LEVEL = 10
IMAGES_DIR = BASE_DIR / "save_points" / "imgs"


def point_template_level(level: int) -> int:
    """第 10 关及以后共用第 10 关的图片、四角和中心点。"""
    return min(int(level), SHARED_POINT_LEVEL)


def _normalize_points_data(data: dict[str, Any]) -> dict[str, Any]:
    """兼容旧的逐关数据，以第 10 关为准收拢重复配置。"""
    result = deepcopy(data)
    levels = result.setdefault("levels", {})
    if not isinstance(levels, dict):
        raise ValueError("点位文件的 levels 必须是字典")
    for key in list(levels):
        if str(key).isdigit() and int(key) > SHARED_POINT_LEVEL:
            del levels[key]
    shared = levels.get(str(SHARED_POINT_LEVEL))
    if isinstance(shared, dict) and isinstance(shared.get("image"), str):
        image = Path(shared["image"])
        if image.stem.isdigit():
            shared["image"] = image.with_name(f"{SHARED_POINT_LEVEL}{image.suffix}").as_posix()
    result["version"] = POINTS_VERSION
    return result


def empty_points_data() -> dict[str, Any]:
    """创建空的点位数据结构。"""
    return {
        "version": POINTS_VERSION,
        "levels": {},
    }


def load_points_data(path: str | Path = SAVED_POINTS_FILE) -> dict[str, Any]:
    """读取点位 JSON，文件不存在或为空时返回空结构。"""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return empty_points_data()

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"点位文件格式错误：{path}")

    return _normalize_points_data(data)


def save_points_data(data: dict[str, Any], path: str | Path = SAVED_POINTS_FILE) -> None:
    """保存完整点位 JSON。"""
    normalized = _normalize_points_data(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(normalized, file, ensure_ascii=False, indent=2)
        file.write("\n")


def detect_level_entry(image_path: str | Path, n: int) -> dict[str, Any]:
    """使用现有自动识别工具生成单关初始点位。"""
    image_path = Path(image_path)
    img = read_image(image_path)
    result = detect_diamond_centers(img, n)
    height, width = img.shape[:2]

    return make_level_entry(
        image_path=image_path,
        n=n,
        image_size=(width, height),
        quad=result.global_quad,
        points=result.points,
        source="auto",
    )


def make_level_entry(
    image_path: str | Path,
    n: int,
    image_size: tuple[int, int] | list[int],
    quad: Any,
    points: list[tuple[int, int]] | list[list[int]],
    source: str = "manual",
) -> dict[str, Any]:
    """把图片、四角和中心点整理成可写入 JSON 的单关数据。"""
    image_path = Path(image_path)
    return {
        "image": _relative_path_text(image_path),
        "n": int(n),
        "image_size": [int(image_size[0]), int(image_size[1])],
        "quad": _round_points(quad),
        "points": _round_points(points),
        "source": source,
    }


def points_from_quad(quad: Any, n: int) -> list[tuple[int, int]]:
    """根据四角重新生成整数中心点。"""
    float_points = centers_from_quad(np.asarray(quad, dtype=np.float32), int(n))
    return [(int(round(x)), int(round(y))) for x, y in float_points]


def save_level_entry(
    level: int,
    entry: dict[str, Any],
    path: str | Path = SAVED_POINTS_FILE,
) -> None:
    """保存关卡模板；保存第 10 关以后任一关即更新共享点位。"""
    data = load_points_data(path)
    data.setdefault("levels", {})[str(point_template_level(level))] = entry
    save_points_data(data, path)


def read_level_entry(
    level: int,
    path: str | Path = SAVED_POINTS_FILE,
) -> dict[str, Any] | None:
    """读取指定关卡的点位数据，不存在时返回 None。"""
    data = load_points_data(path)
    entry = data.get("levels", {}).get(str(point_template_level(level)))
    if not isinstance(entry, dict):
        return None
    return entry


def read_saved_points(
    level: int,
    path: str | Path = SAVED_POINTS_FILE,
    expected_n: int | None = None,
) -> list[tuple[int, int]] | None:
    """读取指定关卡中心点，数量不符合预期时返回 None。"""
    entry = read_level_entry(level, path)
    if entry is None:
        return None

    points = entry.get("points")
    if not isinstance(points, list):
        return None

    parsed = [_to_point(point) for point in points]
    if expected_n is not None and len(parsed) != int(expected_n) * int(expected_n):
        return None

    return parsed


def read_saved_quad(
    level: int,
    path: str | Path = SAVED_POINTS_FILE,
) -> np.ndarray | None:
    """读取指定关卡的大菱形四角。"""
    entry = read_level_entry(level, path)
    if entry is None:
        return None

    quad = entry.get("quad")
    if not isinstance(quad, list) or len(quad) != 4:
        return None

    return np.asarray([_to_point(point) for point in quad], dtype=np.float32)


def generate_points_json(
    images_dir: str | Path = IMAGES_DIR,
    out_path: str | Path = SAVED_POINTS_FILE,
) -> dict[str, Any]:
    """扫描点位图片目录，批量生成初始点位 JSON。"""
    images_dir = Path(images_dir)
    data = empty_points_data()

    for image_path in iter_template_images(images_dir):
        level = int(image_path.stem)
        n = LEVEL_GRID_SIZES.get(level)
        if n is None:
            continue

        data["levels"][str(level)] = detect_level_entry(image_path, n)

    save_points_data(data, out_path)
    return data


def iter_template_images(images_dir: str | Path = IMAGES_DIR) -> list[Path]:
    """仅列出 1–9 关和第 10 关共享模板，忽略旧的重复图片。"""
    images_dir = Path(images_dir)
    if not images_dir.exists():
        return []
    paths = [
        path
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        and path.stem.isdigit() and 1 <= int(path.stem) <= SHARED_POINT_LEVEL
    ]
    return sorted(paths, key=lambda item: int(item.stem))


def _relative_path_text(path: Path) -> str:
    """把项目内路径转换成 JSON 中稳定的相对路径。"""
    try:
        return path.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def _round_points(points: Any) -> list[list[int]]:
    """把点数组统一转换为 JSON 友好的整数列表。"""
    return [
        [int(round(float(point[0]))), int(round(float(point[1])))]
        for point in points
    ]


def _to_point(point: Any) -> tuple[int, int]:
    """把 JSON 中的点坐标转换为整数元组。"""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f"点位格式错误：{point}")
    return int(point[0]), int(point[1])


def main() -> int:
    """命令行入口：生成 save_points/points.json。"""
    data = generate_points_json()
    for level, entry in data["levels"].items():
        print(f"level {level}: n={entry['n']} points={len(entry['points'])}")
    print(f"点位文件已保存：{SAVED_POINTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
