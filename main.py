import atexit
import signal
import time
from pathlib import Path
from time import sleep

import cv2
import numpy as np

from config import (
    GAME_PACKAGE_NAME,
    LEVEL_GRID_SIZES,
    OUTPUT_DIR,
    SUBMARINES,
    USE_SAVED_POINTS,
)
from save_points.points import read_saved_points, read_saved_quad
from utils import AdbController, MatchResult, find_template, get_logger, is_diamond_hit
from utils.diamond_centers import detect_diamond_centers, write_image
from utils.submarine_strategy import Cell, SubmarineStrategy, get_configured_submarines

logger = get_logger(__name__)
adb = AdbController()
_weak_network_cleanup_done = False
    

def enable_weak_network(second: float = 0) -> None:
    """开启游戏弱网，并按需等待网络状态生效。"""
    adb.enable_weak_network(GAME_PACKAGE_NAME)
    if second > 0:
        sleep(second)

def disable_weak_network(second: float = 0) -> None:
    """关闭游戏弱网，并按需等待网络状态恢复。"""
    adb.disable_weak_network(GAME_PACKAGE_NAME)
    if second > 0:
        sleep(second)

def cleanup_weak_network(reason: str = "脚本退出") -> None:
    """脚本退出时关闭游戏弱网，防止影响游戏正常运行"""
    global _weak_network_cleanup_done
    if _weak_network_cleanup_done:
        return

    _weak_network_cleanup_done = True
    try:
        logger.info("%s，正在关闭弱网", reason)
        disable_weak_network()
    except Exception as exc:
        logger.error("关闭弱网失败: %s", exc)

def cleanup_reject_network(reason: str = "脚本退出") -> None:
    """关闭游戏 REJECT 断网残留，避免影响本次或下次运行。"""
    try:
        logger.info("%s，正在清理 REJECT 断网", reason)
        adb.disable_reject_network(GAME_PACKAGE_NAME)
    except Exception as exc:
        logger.error("清理 REJECT 断网失败: %s", exc)

def handle_exit_signal(signum: int, _frame) -> None:
    """收到退出信号时先关闭弱网再退出。"""
    cleanup_weak_network(f"收到退出信号 {signum}")
    raise SystemExit(128 + signum)

def register_exit_cleanup() -> None:
    """注册脚本退出清理，尽量避免弱网规则残留。"""
    atexit.register(cleanup_weak_network)
    for signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            signal.signal(signum, handle_exit_signal)

def enter_activity(re_enter: bool = False, max_retries: int = 5) -> None:
    if max_retries <= 0:
        raise ValueError(f"max_retries 必须大于 0: {max_retries}")

    last_failure = "进入活动失败"
    for attempt in range(1, max_retries + 1):
        adb.delay(0.5)
        res = wait_until_occur("./template/activity_button.png", timeout=20)
        if res is None:
            last_failure = "未找到活动按钮"
            logger.warning(
                "%s，无法进入活动界面，正在重试 (%s/%s)",
                last_failure,
                attempt,
                max_retries,
            )
            _restart_game_for_activity_retry()
            continue

        adb.click(*res.center) # 点击活动按钮进入活动界面
        if not re_enter:
            enable_weak_network(0.2)
            adb.delay(0.4).swipe(1000, 660, 1000, 180) # 上滑展示全部选项（仅第一次进入需要）
            adb.delay(0.2).swipe(1000, 660, 1000, 180)

        adb.delay(0.7).click(1205, 644) # 点击进入活动详情界面
        if wait_until_occur("./template/quit_activity.png", timeout=15) is not None:
            return

        last_failure = "进入活动详情界面失败"
        logger.warning(
            "%s，正在重试进入活动 (%s/%s)",
            last_failure,
            attempt,
            max_retries,
        )
        _restart_game_for_activity_retry()

    message = f"{last_failure}，已达到最大重试次数 {max_retries}"
    logger.error(message)
    raise RuntimeError(message)


def _restart_game_for_activity_retry() -> None:
    adb.close_app(GAME_PACKAGE_NAME)
    adb.delay(1.5).open_app(GAME_PACKAGE_NAME)
    login_img = wait_until_occur("./template/login.png", timeout=30)
    if login_img is None:
        logger.warning("重新启动游戏后未找到登录按钮，继续下一次进入尝试")
        return
    adb.click(*login_img.center) # 点击登录按钮
    

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


def get_level_grid_size(level: int) -> int:
    """读取指定关卡的菱形网格边长。"""
    if level not in LEVEL_GRID_SIZES:
        raise ValueError(f"未配置第 {level} 关的网格边长")
    return LEVEL_GRID_SIZES[level]


def get_click_points(level: int, grid_img: np.ndarray) -> tuple[list[tuple[int, int]], np.ndarray]:
    """按配置读取人工点位，失败时回退到自动识别。"""
    grid_size = get_level_grid_size(level)

    if USE_SAVED_POINTS:
        try:
            saved_points = read_saved_points(level, expected_n=grid_size)
            saved_quad = read_saved_quad(level)
        except Exception as exc:
            logger.warning("读取第 %s 关人工点位失败，回退自动识别：%s", level, exc)
        else:
            if saved_points is not None and saved_quad is not None:
                logger.info("第 %s 关使用人工校准点位：%s 个", level, len(saved_points))
                return saved_points, saved_quad
            logger.warning("第 %s 关人工点位不存在或数量不正确，回退自动识别", level)

    grid_result = detect_diamond_centers(grid_img, grid_size)
    logger.info("第 %s 关使用自动识别点位：%s 个", level, len(grid_result.points))
    return grid_result.points, grid_result.global_quad


def handle_game_level(level: int, hit_map: list[list[int]]) -> tuple[np.ndarray, np.ndarray]:
    """处理单个关卡：有潜艇配置时策略选点，缺少配置时回退逐格扫描。"""
    # 获取当前关卡的棱形方格中心坐标列表
    adb.delay(1.5)
    # adb.pinch_in(distance=10, duration_ms=200) # 缩小视野，目前不需要
    grid_img = adb.read_screenshot()
    click_points, grid_quad = get_click_points(level, grid_img)

    submarines = get_configured_submarines(level, SUBMARINES)
    if submarines is None:
        message = f"第 {level} 关缺少潜艇长度配置，回退逐格扫描"
        logger.warning(message)
        _scan_level_by_grid_order(level, hit_map, click_points)
    else:
        _scan_level_by_strategy(level, hit_map, click_points, submarines)

    return grid_img, grid_quad


def _scan_level_by_grid_order(
    level: int,
    hit_map: list[list[int]],
    click_points: list[tuple[int, int]],
    skip_cells: set[Cell] | None = None,
) -> None:
    """按行优先顺序逐格探测，可跳过策略阶段已获得真实反馈的格子。"""
    grid_size = get_level_grid_size(level)
    skip_cells = skip_cells or set()
    for index, point in enumerate(click_points): # 遍历每个方格中心坐标
        cell = (index // grid_size, index % grid_size)
        if cell in skip_cells:
            continue
        _probe_cell(level, hit_map, cell, point, index)


def _scan_level_by_strategy(
    level: int,
    hit_map: list[list[int]],
    click_points: list[tuple[int, int]],
    submarines: list[int],
) -> None:
    """使用潜艇策略选择探测格；策略无法完成时回退扫描剩余未探测格。"""
    grid_size = get_level_grid_size(level)
    strategy = SubmarineStrategy(grid_size, submarines)
    max_attempts = grid_size * grid_size
    attempts = 0

    logger.info("第 %s 关启用潜艇策略：grid=%s submarines=%s", level, grid_size, submarines)

    while not strategy.done and attempts < max_attempts:
        cell = strategy.choose_next_cell()
        if cell is None:
            logger.warning("第 %s 关策略已无可选方格，提前结束", level)
            break

        row, col = cell
        index = row * grid_size + col
        hit = _probe_cell(level, hit_map, cell, click_points[index], index)
        attempts += 1

        if hit is None:
            continue

        strategy.report_result(cell, hit)

    if strategy.done:
        logger.info("第 %s 关策略已确认全部潜艇，探测次数：%s", level, attempts)
    else:
        logger.warning("第 %s 关策略未能确认全部潜艇，回退逐格扫描未探测方格", level)
        _scan_level_by_grid_order(level, hit_map, click_points, skip_cells=set(strategy.shots))


def _probe_cell(
    level: int,
    hit_map: list[list[int]],
    cell: Cell,
    point: tuple[int, int],
    index: int,
) -> bool | None:
    """执行一次单格探测，保持原自动化顺序；命中返回 True，未命中返回 False，页面异常返回 None。"""
    x, y = point
    # if i != 0:
    #     adb.pinch_in(distance=10, duration_ms=200)
    if wait_until_occur("./template/quit_activity.png", timeout=6) is None:
        logger.warning("点击方格前不在活动详情界面，重新进入活动后跳过本次点击")
        enter_activity()
        return None

    before_img = adb.read_screenshot("./_debug/screenshots/run_debug/debug_before.png") # 点击前截图
    adb.click(x, y)
    adb.delay(0.3)
    if not click_template("./template/quit_activity.png", "./_debug/screenshots/run_debug/debug_quit1.png"):
        logger.warning("点击方格后未找到退出按钮，当前页面可能已离开活动详情界面")
        enter_activity()
        return None

    enter_activity(re_enter=True) # 重新进入活动界面
    # adb.pinch_in(distance=10, duration_ms=200)
    after_img = adb.delay(1).read_screenshot("./_debug/screenshots/run_debug/debug_after.png")
    if is_diamond_hit(before_img, after_img, (x, y)):
        row, col = cell
        hit_map[row][col] = 1
        logger.info("第 %s 关，点击方格 %s 结果：击中！", level, index)
        hit = True
    else:
        logger.info("第 %s 关，点击方格 %s 结果：未击中", level, index)
        hit = False

    adb.enable_reject_network(GAME_PACKAGE_NAME)
    retry = wait_until_occur("./template/retry.png", timeout=20)
    adb.disable_reject_network(GAME_PACKAGE_NAME)
    adb.delay(0.8).click(*retry.center) # 点击重试按钮

    restart_process()
    return hit
        
def restart_process():
    disable_weak_network()
    enter_activity()
        
def wait_until_occur(template_path: str, timeout: float = 30.0) -> MatchResult | None:
    """等待直到指定模板出现，返回匹配结果或 None（超时）。"""
    logger.info("正在等待模板 '%s' 出现，超时时间 %s 秒...", template_path, timeout)
    start_time = time.time()
    while time.time() - start_time < timeout:
        screenshot = adb.read_screenshot()
        match_result = find_template(screenshot, template_path)
        if match_result is not None:
            return match_result
        sleep(0.5)  # 每隔 0.5 秒检查一次
    logger.warning("等待模板 '%s' 超时 (%s 秒)", template_path, timeout)
    return None

def click_template(template_path: str, screenshot_path: str | None = None, threshold: float = 0.85) -> bool:
    """查找模板并点击中心点，找不到时返回 False。"""
    img = adb.read_screenshot(screenshot_path)
    match_result = find_template(img, template_path, threshold=threshold)
    if match_result is None:
        return False

    adb.delay(0.5).click(*match_result.center)
    return True

def main(level: int):
    grid_size = get_level_grid_size(level)
    hit_map = [[0 for i in range(grid_size)] for j in range(grid_size)]
    disable_weak_network()
    
    if not find_template(adb.read_screenshot(), "./template/activity_button.png"):
        logger.error("当前不在海岛主界面，无法启动脚本")
        return
    
    enter_activity()
    base_img, quad = handle_game_level(level, hit_map)
    out_path = OUTPUT_DIR / f"hit_map_level_{level}.png"
    save_hit_map_image(base_img, quad, hit_map, out_path)
    logger.info("命中矩阵：%s", hit_map)
    logger.info("命中可视化图片已保存：%s", out_path)
    

if __name__ == "__main__":
    register_exit_cleanup()
    level = 1
    try:
        adb.ensure_root_shell()
        cleanup_reject_network("主流程启动")
        main(level)
    finally:
        cleanup_weak_network("主流程结束")
        cleanup_reject_network("主流程结束")
    
