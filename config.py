from pathlib import Path
from typing import Final

# Base 路径，指向项目根目录
BASE_DIR = Path(__file__).resolve().parent

# ADB 连接的默认设备 IP 地址
ADB_SERIAL = "127.0.0.1:5555"
ADB_EXECUTABLE = "adb"

# 默认控制的游戏包名
GAME_PACKAGE_NAME = "com.supercell.boombeach"

# 模板图片目录和截图保存目录
TEMPLATE_DIR = BASE_DIR / "template"
SCREENSHOT_DIR = BASE_DIR / "_debug" / "screenshots"
LOG_DIR = BASE_DIR / "_debug" / "logs"
LOG_FILE = LOG_DIR / "bbma.log"
OUTPUT_DIR = BASE_DIR / "outputs"

# 声呐活动入口：默认识别活动卡片，不依赖它在列表中的排序。
# 模板应为当前分辨率下活动卡片内的固定图案，避免倒计时和文字。
SONAR_ENTRY_USE_IMAGE = True
SONAR_ENTRY_TEMPLATE = TEMPLATE_DIR / "sonar_pic_alive.png"
SONAR_ENTRY_THRESHOLD = 0.85
# 相对于潜艇图片中心，点击右下方绿色箭头：正 x 向右，正 y 向下。
# 按参考截图比例换算到现有 286x131 模板的估计值，需实机微调。
SONAR_ENTRY_CLICK_OFFSET = (110, 95)
# 每次手指向上移动的距离（列表向下翻），以及每个方向最多滑动次数。
ACTIVITY_SCROLL_DISTANCE = 150
ACTIVITY_SCROLL_MAX_SWIPES = 6
ACTIVITY_SCROLL_START = (1000, 660)
# 每次滚动（含微调和反向搜索）后等待列表惯性停止，再截图识别，单位秒。
ACTIVITY_SCROLL_SETTLE_SECONDS = 1.2
# 活动列表实际可见的上下边界（1280x720 下的初始估计，按界面校准）。
ACTIVITY_LIST_VISIBLE_Y = (80, 700)
# 箭头中心离可见边缘至少留出这些像素，避免按钮只露出一部分。
ACTIVITY_BUTTON_MARGIN = 25
# 图片已找到但按钮未露出时，小幅滚动并重新识别，最多尝试 3 次。
ACTIVITY_ALIGN_DISTANCE = 60
ACTIVITY_ALIGN_MAX_SWIPES = 3
# 关闭图片识别时使用的旧坐标方案。
ACTIVITY_DETAIL_POINT = (1205, 644)
ACTIVITY_LIST_SWIPE = (1000, 660, 1000, 590)


# 目前支持的最大关卡
MAX_LEVEL: Final[int] = 50

# 第 10 海域及以上关卡使用的默认潜艇长度列表
DEFAULT_SUBMARINES: Final[tuple[int, ...]] = (2, 2, 3, 4, 5)

# 固定关卡对应的潜艇长度列表，供前 10 个关卡使用
SPECIAL_SUBMARINES: Final[dict[int, tuple[int, ...]]] = {
    1:  (3,),
    2:  (2, 2),
    3:  (2, 2, 3),
    4:  (2, 3, 4),
    5:  (2, 3, 3, 4),
    6:  (2, 2, 3, 3, 5),
    7:  (2, 2, 3, 3, 4, 5),
    8:  (2, 2, 3, 3, 4, 4, 5),
    9:  (2, 3, 3, 4, 4, 5),
    10: (2, 2, 3, 4, 4, 5),
}

# 固定关卡对应的菱形网格边长
LEVEL_GRID_SIZES: Final[dict[int, int]] = {
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
    **{
        level: 10
        for level in range(11, MAX_LEVEL + 1)
    },

}

# Level 对应的潜艇长度列表
SUBMARINES: Final[dict[int, tuple[int, ...]]] = {
    **SPECIAL_SUBMARINES,
    **{
        level: DEFAULT_SUBMARINES
        for level in range(11, MAX_LEVEL + 1)
    },
}


# 是否优先使用人工校准后的固定点位
USE_SAVED_POINTS = True
SAVED_POINTS_FILE = BASE_DIR / "save_points" / "points.json"

# 默认的截图文件名和模板匹配的默认阈值
DEFAULT_SCREENSHOT_NAME = "screen.png"
DEFAULT_MATCH_THRESHOLD = 0.85
DEFAULT_TEMPLATE_SHAPE_WEIGHT = 0.9
DEFAULT_TEMPLATE_SHAPE_POWER = 3.0

# 日志级别，可选 DEBUG、INFO、WARNING、ERROR
LOG_LEVEL = "INFO"
