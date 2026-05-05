from pathlib import Path

# Base 路径，指向项目根目录
BASE_DIR = Path(__file__).resolve().parent

# ADB 连接的默认设备 IP 地址
ADB_SERIAL = "127.0.0.1:5555"

# 模板图片目录和截图保存目录
TEMPLATE_DIR = BASE_DIR / "template"
SCREENSHOT_DIR = BASE_DIR / "_debug" / "screenshots"
LOG_DIR = BASE_DIR / "_debug" / "logs"
LOG_FILE = LOG_DIR / "bbma.log"
OUTPUT_DIR = BASE_DIR / "outputs"

# 默认的截图文件名和模板匹配的默认阈值
DEFAULT_SCREENSHOT_NAME = "screen.png"
DEFAULT_MATCH_THRESHOLD = 0.85
DEFAULT_TEMPLATE_SHAPE_WEIGHT = 0.9
DEFAULT_TEMPLATE_SHAPE_POWER = 3.0

# 日志级别，可选 DEBUG、INFO、WARNING、ERROR
LOG_LEVEL = "INFO"
