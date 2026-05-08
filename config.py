from pathlib import Path

# Base 路径，指向项目根目录
BASE_DIR = Path(__file__).resolve().parent

# ADB 连接的默认设备 IP 地址
ADB_SERIAL = "127.0.0.1:5555"

# 默认控制的游戏包名
GAME_PACKAGE_NAME = "com.tencent.tmgp.supercell.boombeach"

# 模板图片目录和截图保存目录
TEMPLATE_DIR = BASE_DIR / "template"
SCREENSHOT_DIR = BASE_DIR / "_debug" / "screenshots"
LOG_DIR = BASE_DIR / "_debug" / "logs"
LOG_FILE = LOG_DIR / "bbma.log"
OUTPUT_DIR = BASE_DIR / "outputs"

# 固定关卡对应的菱形网格边长
LEVEL_GRID_SIZES = {
    1: 3,    2: 4,
    3: 5,    4: 6,
    5: 7,    6: 8,
    7: 9,    8: 10,
    9: 10,   10: 10,
    11: 10,  12: 10,
    13: 10,  14: 10,
    15: 10,  16: 10,
    17: 10,  18: 10,
    19: 10,  20: 10,
    21: 10,  22: 10,
    23: 10,  24: 10,
    25: 10,  26: 10,
    27: 10,  28: 10,
    29: 10,  30: 10,
    31: 10,  32: 10,
    33: 10,  34: 10,
    35: 10,  36: 10,

}

# Level 对应的潜艇长度列表
SUBMARINES = {
    1: [3],
    2: [2, 2],
    3: [2, 2, 3],
    4: [2, 3, 4],
    5: [2, 3, 3, 4],
    6: [2, 2, 3, 3, 5],
    7: [2, 2, 3, 3, 4, 5],
    8: [2, 2, 3, 3, 4, 4, 5],
    9: [2, 3, 3, 4, 4, 5],
    10:[2, 2, 3, 4, 4, 5],
    11:[2, 2, 3, 4, 5],
    12:[2, 2, 3, 4, 5],
    13:[2, 2, 3, 4, 5],
    14:[2, 2, 3, 4, 5],
    15:[2, 2, 3, 4, 5],
    16:[2, 2, 3, 4, 5],
    17:[2, 2, 3, 4, 5],
    18:[2, 2, 3, 4, 5],
    19:[2, 2, 3, 4, 5],
    20:[2, 2, 3, 4, 5],
    21:[2, 2, 3, 4, 5],
    22:[2, 2, 3, 4, 5],
    23:[2, 2, 3, 4, 5],
    24:[2, 2, 3, 4, 5],
    25:[2, 2, 3, 4, 5],
    26:[2, 2, 3, 4, 5],
    27:[2, 2, 3, 4, 5],
    28:[2, 2, 3, 4, 5],
    29:[2, 2, 3, 4, 5],
    30:[2, 2, 3, 4, 5],
    31:[2, 2, 3, 4, 5],
    32:[2, 2, 3, 4, 5],
    33:[2, 2, 3, 4, 5],
    34:[2, 2, 3, 4, 5],
    35:[2, 2, 3, 4, 5],
    36:[2, 2, 3, 4, 5],
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
