from pathlib import Path

import cv2
import numpy as np

from utils.diamond_centers import write_image


def _build_cell_polygons(quad: np.ndarray, n: int) -> list[list[np.ndarray]]:
    """根据外层菱形四角生成每个方格的四边形坐标。"""
    src = np.array(
        [
            [0, 0],
            [n, 0],
            [n, n],
            [0, n],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    polygons: list[list[np.ndarray]] = []

    for row in range(n):
        polygon_row: list[np.ndarray] = []
        for col in range(n):
            cell = np.array(
                [
                    [[col, row]],
                    [[col + 1, row]],
                    [[col + 1, row + 1]],
                    [[col, row + 1]],
                ],
                dtype=np.float32,
            )
            projected = cv2.perspectiveTransform(cell, matrix).reshape(4, 2)
            polygon_row.append(np.round(projected).astype(np.int32))
        polygons.append(polygon_row)

    return polygons


def save_hit_map_image(
    base_img: np.ndarray,
    quad: np.ndarray,
    hit_map: list[list[int]],
    out_path: str | Path,
) -> None:
    """把命中结果叠加绘制到游戏截图上并保存。"""
    n = len(hit_map)
    if n == 0 or any(len(row) != n for row in hit_map):
        raise ValueError("hit_map 必须是非空的 N x N 列表")

    out = base_img.copy()
    overlay = out.copy()
    polygons = _build_cell_polygons(quad, n)

    for row in range(n):
        for col in range(n):
            if hit_map[row][col] == 1:
                cv2.fillConvexPoly(
                    overlay,
                    polygons[row][col],
                    (0, 0, 255),
                    lineType=cv2.LINE_AA,
                )

    out = cv2.addWeighted(overlay, 0.38, out, 0.62, 0)

    for row in range(n):
        for col in range(n):
            is_hit_cell = hit_map[row][col] == 1
            cv2.polylines(
                out,
                [polygons[row][col]],
                True,
                (0, 0, 255) if is_hit_cell else (255, 255, 255),
                3 if is_hit_cell else 1,
                cv2.LINE_AA,
            )

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    write_image(out_path, out)


__all__ = ["save_hit_map_image"]
