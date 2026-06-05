from pathlib import Path
from typing import Final

# Base 路径，指向项目根目录
BASE_DIR = Path(__file__).resolve().parent

# ADB 连接的默认设备 IP 地址
ADB_SERIAL = "127.0.0.1:5555"

# 默认控制的游戏包名
GAME_PACKAGE_NAME = "com.supercell.boombeach"

# 模板图片目录和截图保存目录
TEMPLATE_DIR = BASE_DIR / "template"
SCREENSHOT_DIR = BASE_DIR / "_debug" / "screenshots"
LOG_DIR = BASE_DIR / "_debug" / "logs"
LOG_FILE = LOG_DIR / "bbma.log"
OUTPUT_DIR = BASE_DIR / "outputs"


# 目前支持的最大关卡
MAX_LEVEL: Final[int] = 36

# 第 10 海域及以上关卡使用的默认潜艇长度列表
DEFAULT_SUBMARINES: Final[tuple[int, ...]] = (2, 2, 3, 4, 5)

# 固定关卡对应的潜艇长度列表，供前 10 个关卡使用
SPECIAL_SUBMARINES: Final[dict[int, tuple[int, ...]]] = {
    1: (3,),
    2: (2, 2),
    3: (2, 2, 3),
    4: (2, 3, 4),
    5: (2, 3, 3, 4),
    6: (2, 2, 3, 3, 5),
    7: (2, 2, 3, 3, 4, 5),
    8: (2, 2, 3, 3, 4, 4, 5),
    9: (2, 3, 3, 4, 4, 5),
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
    **{level: 10 for level in range(11, MAX_LEVEL + 1)},
}

# Level 对应的潜艇长度列表
SUBMARINES: Final[dict[int, tuple[int, ...]]] = {
    **SPECIAL_SUBMARINES,
    **{level: DEFAULT_SUBMARINES for level in range(11, MAX_LEVEL + 1)},
}


# 是否优先使用人工校准后的固定点位
USE_SAVED_POINTS = True
SAVED_POINTS_FILE = BASE_DIR / "save_points" / "points.json"

# 是否为国际版游戏（国际版不需要点击登录）
IS_INTERNATIONAL_VERSION = True

# 默认的截图文件名和模板匹配的默认阈值
DEFAULT_SCREENSHOT_NAME = "screen.png"
DEFAULT_MATCH_THRESHOLD = 0.85
DEFAULT_TEMPLATE_SHAPE_WEIGHT = 0.9
DEFAULT_TEMPLATE_SHAPE_POWER = 3.0

# 日志级别，可选 DEBUG、INFO、WARNING、ERROR
LOG_LEVEL = "INFO"
