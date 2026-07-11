import atexit
import signal
from pathlib import Path
from time import monotonic, sleep

import numpy as np

from config import (
    GAME_PACKAGE_NAME,
    LEVEL_GRID_SIZES,
    OUTPUT_DIR,
    SCREENSHOT_DIR,
    SUBMARINES,
    TEMPLATE_DIR,
    USE_SAVED_POINTS,
)
from save_points.points import read_saved_points, read_saved_quad
from utils import AdbController, MatchResult, find_template, get_logger, is_diamond_hit
from utils.diamond_centers import detect_diamond_centers
from utils.hit_map import save_hit_map_image
from utils.probe_protocol import (
    ProbeNotReadyError,
    ProbePhase,
    ProbeProtocolError,
    ProbeTransaction,
)
from utils.submarine_strategy import Cell, SubmarineStrategy, get_configured_submarines

logger = get_logger(__name__)
adb = AdbController()

ACTIVITY_BUTTON_TEMPLATE = TEMPLATE_DIR / "activity_button.png"
LOGIN_TEMPLATE = TEMPLATE_DIR / "login.png"
QUIT_ACTIVITY_TEMPLATE = TEMPLATE_DIR / "quit_activity.png"
RETRY_TEMPLATE = TEMPLATE_DIR / "retry.png"

ACTIVITY_DETAIL_POINT = (1205, 644)
ACTIVITY_LIST_SWIPE = (1000, 660, 1000, 180)
RUN_DEBUG_DIR = SCREENSHOT_DIR / "run_debug"

_weak_network_cleanup_done = False
_active_probe: "ProbeTransaction | None" = None


def _has_pending_probe_request() -> bool:
    return _active_probe is not None and _active_probe.request_may_be_pending


def enable_weak_network(second: float = 0) -> None:
    """开启游戏弱网，并按需等待网络状态生效。"""
    adb.enable_weak_network(GAME_PACKAGE_NAME)
    if second > 0:
        sleep(second)


def disable_weak_network(second: float = 0) -> None:
    """安全关闭游戏弱网；存在待丢弃请求时拒绝恢复网络。"""
    if _has_pending_probe_request():
        transaction = _active_probe
        raise ProbeProtocolError(
            "客户端仍可能保存待发送请求，拒绝关闭 DROP 弱网："
            f"cell={transaction.cell if transaction else None} "
            f"phase={transaction.phase.name if transaction else None}"
        )
    adb.disable_weak_network(GAME_PACKAGE_NAME)
    if second > 0:
        sleep(second)


def cleanup_weak_network(reason: str = "脚本退出") -> None:
    """仅在不存在待发送探测请求时关闭 DROP 弱网。"""
    global _weak_network_cleanup_done
    if _weak_network_cleanup_done:
        return

    if _has_pending_probe_request():
        transaction = _active_probe
        logger.critical(
            "%s，但格子 %s 的探测处于 %s；为避免暂存请求补发，保留 DROP 弱网",
            reason,
            transaction.cell if transaction else None,
            transaction.phase.name if transaction else None,
        )
        return

    try:
        logger.info("%s，正在关闭弱网", reason)
        disable_weak_network()
    except Exception as exc:
        logger.error("关闭弱网失败: %s", exc)
    else:
        _weak_network_cleanup_done = True


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
    """进入活动详情页。

    ``re_enter=False`` 用于没有待验证请求的普通进入，允许重启恢复；
    ``re_enter=True`` 用于点击后的第二次进入，此时 DROP 下可能仍有暂存请求，
    任何失败都必须立即中止，不能复用会关闭弱网的普通恢复流程。
    """
    if max_retries <= 0:
        raise ValueError(f"max_retries 必须大于 0: {max_retries}")

    last_failure = "进入活动失败"
    for attempt in range(1, max_retries + 1):
        adb.delay(0.5)
        res = wait_until_occur(ACTIVITY_BUTTON_TEMPLATE, timeout=20)
        if res is None:
            last_failure = "未找到活动按钮"
            if re_enter:
                raise ProbeProtocolError(
                    f"第二次进入活动时{last_failure}；保留 DROP 弱网并中止探测"
                )
            logger.warning(
                "%s，无法进入活动界面，正在重试 (%s/%s)",
                last_failure,
                attempt,
                max_retries,
            )
            _restart_game_for_activity_retry()
            continue

        adb.click(*res.center)  # 点击活动按钮进入活动界面
        if not re_enter:
            enable_weak_network(0.2)
            adb.delay(0.4).swipe(*ACTIVITY_LIST_SWIPE)  # 首次进入需要展示全部选项
            adb.delay(0.2).swipe(*ACTIVITY_LIST_SWIPE)

        adb.delay(0.7).click(*ACTIVITY_DETAIL_POINT)
        if wait_until_occur(QUIT_ACTIVITY_TEMPLATE, timeout=15) is not None:
            return

        last_failure = "进入活动详情界面失败"
        if re_enter:
            raise ProbeProtocolError(
                f"第二次进入活动时{last_failure}；保留 DROP 弱网并中止探测"
            )
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
    """在没有待验证请求的普通进入阶段重启游戏。"""
    if _has_pending_probe_request():
        raise ProbeProtocolError("存在待发送探测请求，禁止通过重启游戏恢复活动入口")

    adb.close_app(GAME_PACKAGE_NAME)
    adb.disable_reject_network(GAME_PACKAGE_NAME)
    disable_weak_network()
    adb.delay(1.5).open_app(GAME_PACKAGE_NAME)
    login_img = wait_until_occur(LOGIN_TEMPLATE, timeout=30)
    if login_img is None:
        logger.warning("重新启动游戏后未找到登录按钮，继续下一次进入尝试")
        return
    adb.click(*login_img.center)  # 点击登录按钮


def get_level_grid_size(level: int) -> int:
    """读取指定关卡的菱形网格边长。"""
    if level not in LEVEL_GRID_SIZES:
        raise ValueError(f"未配置第 {level} 关的网格边长")
    return LEVEL_GRID_SIZES[level]


def get_click_points(
    level: int, grid_img: np.ndarray
) -> tuple[list[tuple[int, int]], np.ndarray]:
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


def handle_game_level(
    level: int, hit_map: list[list[int]]
) -> tuple[np.ndarray, np.ndarray]:
    """处理单个关卡：有潜艇配置时策略选点，缺少配置时回退逐格扫描。"""
    adb.delay(1.5)
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
    for index, point in enumerate(click_points):
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

    logger.info(
        "第 %s 关启用潜艇策略：grid=%s submarines=%s", level, grid_size, submarines
    )

    while not strategy.done and attempts < max_attempts:
        cell = strategy.choose_next_cell()
        if cell is None:
            logger.warning("第 %s 关策略已无可选方格，提前结束", level)
            break

        row, col = cell
        index = row * grid_size + col
        hit = _probe_cell(level, hit_map, cell, click_points[index], index)
        attempts += 1
        strategy.report_result(cell, hit)

    if strategy.done:
        logger.info("第 %s 关策略已确认全部潜艇，探测次数：%s", level, attempts)
    else:
        logger.warning("第 %s 关策略未能确认全部潜艇，回退逐格扫描未探测方格", level)
        known_cells = set(strategy.shots) | strategy.blocked_cells
        _scan_level_by_grid_order(
            level,
            hit_map,
            click_points,
            skip_cells=known_cells,
        )


def _probe_cell(
    level: int,
    hit_map: list[list[int]],
    cell: Cell,
    point: tuple[int, int],
    index: int,
) -> bool:
    """准备页面并执行一次完整探测；点击前异常只重试当前格。"""
    max_preflight_retries = 3
    for attempt in range(1, max_preflight_retries + 1):
        try:
            return _execute_probe_transaction(level, hit_map, cell, point, index)
        except ProbeNotReadyError as exc:
            if attempt >= max_preflight_retries:
                raise ProbeProtocolError(
                    f"格子 {cell} 在点击前连续 {max_preflight_retries} 次未准备好"
                ) from exc
            logger.warning(
                "格子 %s 点击前页面未准备好，恢复后重试同一格 (%s/%s)：%s",
                cell,
                attempt,
                max_preflight_retries,
                exc,
            )
            enter_activity()

    raise AssertionError("探测重试循环意外结束")


def _execute_probe_transaction(
    level: int,
    hit_map: list[list[int]],
    cell: Cell,
    point: tuple[int, int],
    index: int,
) -> bool:
    """按固定 DROP/二次进入/REJECT/登录顺序执行单格探测事务。"""
    global _active_probe

    if _active_probe is not None:
        raise ProbeProtocolError(
            f"上一轮探测尚未结束，禁止开始格子 {cell}: "
            f"cell={_active_probe.cell} phase={_active_probe.phase.name}"
        )

    if wait_until_occur(QUIT_ACTIVITY_TEMPLATE, timeout=6) is None:
        raise ProbeNotReadyError("当前不在活动详情界面")

    transaction = ProbeTransaction(level=level, cell=cell, index=index)
    _active_probe = transaction
    x, y = point

    try:
        before_img = adb.read_screenshot(RUN_DEBUG_DIR / "debug_before.png")

        # 点击命令一旦发出，就保守地认为客户端可能已经暂存验证请求。
        transaction.advance(ProbePhase.REQUEST_PENDING)
        adb.click(x, y)
        adb.delay(0.3)

        if not click_template(
            QUIT_ACTIVITY_TEMPLATE,
            RUN_DEBUG_DIR / "debug_quit1.png",
        ):
            raise ProbeProtocolError(
                "点击格子后未找到退出按钮；待发送请求状态未知，保留 DROP 弱网"
            )

        enter_activity(re_enter=True, max_retries=1)
        after_img = adb.delay(1).read_screenshot(RUN_DEBUG_DIR / "debug_after.png")
        transaction.advance(ProbePhase.RESULT_VISIBLE)

        hit = is_diamond_hit(before_img, after_img, (x, y))
        transaction.hit = hit
        transaction.advance(ProbePhase.RESULT_RECORDED)

        if hit:
            row, col = cell
            hit_map[row][col] = 1
            logger.info("第 %s 关，点击方格 %s 结果：击中！", level, index)
        else:
            logger.info("第 %s 关，点击方格 %s 结果：未击中", level, index)

        _discard_pending_request_and_prepare_next_probe(transaction)
        return hit
    finally:
        if transaction.phase in {ProbePhase.PREPARING, ProbePhase.COMPLETE}:
            _active_probe = None
        elif transaction.request_may_be_pending:
            logger.critical(
                "格子 %s 的探测中断于 %s；客户端可能仍有暂存请求，"
                "退出清理将保留 DROP 弱网",
                transaction.cell,
                transaction.phase.name,
            )


def _discard_pending_request_and_prepare_next_probe(
    transaction: ProbeTransaction,
) -> None:
    """通过 REJECT 丢弃暂存请求，恢复登录并准备下一轮。"""
    adb.enable_reject_network(GAME_PACKAGE_NAME)
    retry = wait_until_occur(RETRY_TEMPLATE, timeout=20)
    if retry is None:
        raise ProbeProtocolError(
            "REJECT 后未出现重试按钮；无法确认暂存请求已丢弃，保留网络阻断"
        )

    # retry 出现表示客户端已确认网络失败并丢弃本轮暂存请求。
    transaction.advance(ProbePhase.REQUEST_DISCARDED)
    adb.disable_reject_network(GAME_PACKAGE_NAME)
    adb.delay(0.8).click(*retry.center)
    transaction.advance(ProbePhase.LOGIN_RECOVERING)

    restart_process()
    transaction.advance(ProbePhase.COMPLETE)


def restart_process() -> None:
    """在请求确认丢弃后恢复网络登录，并进入下一轮探测页面。"""
    disable_weak_network()
    enter_activity()


def wait_until_occur(
    template_path: str | Path,
    timeout: float = 30.0,
) -> MatchResult | None:
    """等待直到指定模板出现，返回匹配结果或 None（超时）。"""
    logger.info("正在等待模板 '%s' 出现，超时时间 %s 秒...", template_path, timeout)
    start_time = monotonic()
    while monotonic() - start_time < timeout:
        screenshot = adb.read_screenshot()
        match_result = find_template(screenshot, template_path)
        if match_result is not None:
            return match_result
        sleep(0.5)  # 每隔 0.5 秒检查一次
    logger.warning("等待模板 '%s' 超时 (%s 秒)", template_path, timeout)
    return None


def click_template(
    template_path: str | Path,
    screenshot_path: str | Path | None = None,
    threshold: float = 0.85,
) -> bool:
    """查找模板并点击中心点，找不到时返回 False。"""
    img = adb.read_screenshot(screenshot_path)
    match_result = find_template(img, template_path, threshold=threshold)
    if match_result is None:
        return False

    adb.delay(0.5).click(*match_result.center)
    return True


def main(level: int) -> Path | None:
    """执行指定关卡的逻辑探测并输出命中图。"""
    grid_size = get_level_grid_size(level)
    hit_map = [[0] * grid_size for _ in range(grid_size)]
    disable_weak_network()

    if find_template(adb.read_screenshot(), ACTIVITY_BUTTON_TEMPLATE) is None:
        logger.error("当前不在海岛主界面，无法启动脚本")
        return None

    enter_activity()
    base_img, quad = handle_game_level(level, hit_map)
    out_path = OUTPUT_DIR / f"hit_map_level_{level}.png"
    save_hit_map_image(base_img, quad, hit_map, out_path)
    logger.info("命中矩阵：%s", hit_map)
    logger.info("命中可视化图片已保存：%s", out_path)
    return out_path


if __name__ == "__main__":
    register_exit_cleanup()
    level = 2
    try:
        adb.ensure_root_shell()
        cleanup_reject_network("主流程启动")
        main(level)
    finally:
        cleanup_weak_network("主流程结束")
        cleanup_reject_network("主流程结束")
