import time
from pathlib import Path
from time import sleep

import cv2
import numpy as np

from config import OUTPUT_DIR
from utils import AdbController, MatchResult, find_template, get_logger, is_diamond_hit
from utils.diamond_centers import detect_diamond_centers, write_image

logger = get_logger(__name__)
adb = AdbController()

LEVELDICT = {
    1: 3,
    2: 4,
    3: 5,
    4: 6,
    5: 7,
    6: 8,
    7: 9,
    8: 10,
    9: 10,
    10: 10,
    11: 10,
    12: 10,
    13: 10,
    14: 10,
    15: 10,
    16: 10,
}

def set_qnet_and_start():
    adb.open_app("com.tencent.wanluo.qnet")
    adb.delay(2).click(321, 482) # 点击配置按钮
    adb.delay(0.4).swipe(360, 1040, 360, 540) # 上滑展示全部选项
    
    adb.click(252, 600) # 单击 outloss
    adb.delay(0.2).click(106, 660) # 点击输入框
    adb.input_text("10")
    adb.delay(0.2).click(585, 765) # 点击确定
    
    adb.click(252, 1072) # 单击 inloss
    adb.delay(0.2).click(106, 660) # 点击输入框
    adb.input_text("10")
    adb.delay(0.2).click(585, 765) # 点击确定
    
    adb.delay(0.2).click(354, 1228) # 单击保存
    adb.delay(0.2).click(341, 465) # 单击配置
    adb.delay(0.2).click(354, 1228) # 单击开始测试
    
    
def enter_account(img_path: str):
    # adb.delay(2).click(99, 30) # 暂时关闭弱网
    pass

def enter_activity():
    res = wait_until_occur("./template/activity_button.png", timeout=15)
    if res is None:
        logger.error("未找到活动按钮，无法进入活动界面，重试中...")
        adb.close_app("com.tencent.tmgp.supercell.boombeach")
        adb.delay(1.5).open_app("com.tencent.tmgp.supercell.boombeach")
        login_img = wait_until_occur("./template/login.png", timeout=15)
        adb.click(*login_img.center) # 点击登录按钮
        return enter_activity()

    adb.click(*res.center) # 点击活动按钮进入活动界面
    adb.delay(0.5).swipe(1000, 660, 1000, 180) # 上滑展示全部选项
    adb.delay(0.5).click(1205, 644) # 点击进入活动详情界面
    if wait_until_occur("./template/quit_activity.png", timeout=5) is None:
        logger.warning("进入活动详情界面失败，重新尝试进入活动")
        return enter_activity()

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


def handle_game_level(level: int, hit_map: list[list[int]]) -> tuple[np.ndarray, np.ndarray]:
    # 获取当前关卡的棱形方格中心坐标列表
    adb.delay(1.5)
    grid_img = adb.read_screenshot()
    grid_result = detect_diamond_centers(grid_img, LEVELDICT[level])
    click_points = grid_result.points
    
    for i, (x, y) in enumerate(click_points): # 遍历每个方格中心坐标
        check_qnet()
        if wait_until_occur("./template/quit_activity.png", timeout=3) is None:
            logger.warning("点击方格前不在活动详情界面，重新进入活动后跳过本次点击")
            enter_activity()
            continue

        before_img = adb.read_screenshot("debug_before.png") # 点击前截图
        adb.click(x, y)
        adb.delay(0.5)
        if not click_template("./template/quit_activity.png", "debug_quit1.png"):
            logger.warning("点击方格后未找到退出按钮，当前页面可能已离开活动详情界面")
            enter_activity()
            continue
        
        enter_activity() # 重新进入活动界面
        after_img = adb.delay(1.5).read_screenshot("debug_after.png")
        if is_diamond_hit(before_img, after_img, (x, y)):
            square_size = LEVELDICT[level]
            hit_map[i//square_size][i%square_size] = 1
            logger.info("第 %s 关，点击方格 %s 结果：击中！", level, i)
        else:
            logger.info("第 %s 关，点击方格 %s 结果：未击中", level, i)
            
        if not click_template("./template/quit_activity.png", "debug_quit2.png"):
            logger.warning("判断结果后未找到退出按钮，重新进入下一轮")
            enter_activity()
            continue
        
        adb.delay(1.5)
        if not click_template("./template/ship.png", threshold=0.9):
            logger.warning("未找到船图标，重新进入下一轮")
            enter_activity()
            continue
        
        adb.go_home()
        restart_game()

    return grid_img, grid_result.global_quad
        
def restart_game():
    adb.open_app("com.tencent.tmgp.supercell.boombeach")
    adb.delay(3).click(1182, 35) # 打开弱网
    enter_activity()
    
def is_hit():
    img = adb.read_screenshot()
    return find_template(img, "./template/hit.png", threshold=0.9) is not None  

def check_qnet():
    """ 检查 Qnet 是否开启，如果未开启则点击开启。"""
    screenshot = adb.read_screenshot()
    match_result = find_template(screenshot, "./template/qnet_button_off.png", threshold=0.85)
    if match_result is not None:
        logger.info("检测到 Qnet 未开启，正在开启...")
        adb.click(*match_result.center)
        sleep(2)  # 等待状态更新
    else:
        logger.info("Qnet 已经处于开启状态")
        
def wait_until_occur(template_path: str, timeout: float = 30.0) -> MatchResult | None:
    """等待直到指定模板出现，返回匹配结果或 None（超时）。"""
    logger.info("正在等待模板 '%s' 出现，超时时间 %s 秒...", template_path, timeout)
    start_time = time.time()
    while time.time() - start_time < timeout:
        screenshot = adb.read_screenshot()
        match_result = find_template(screenshot, template_path)
        if match_result is not None:
            return match_result
        sleep(1)  # 每隔 1 秒检查一次
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
    hit_map = [[0 for i in range(LEVELDICT[level])] for j in range(LEVELDICT[level])]
    print(hit_map)
    enter_activity()
    base_img, quad = handle_game_level(level, hit_map)
    out_path = OUTPUT_DIR / f"hit_map_level_{level}.png"
    save_hit_map_image(base_img, quad, hit_map, out_path)
    print(hit_map)
    print(f"命中可视化图片已保存：{out_path}")
    

if __name__ == "__main__":
    # set_qnet_and_start()
    level = 1
    main(level)
