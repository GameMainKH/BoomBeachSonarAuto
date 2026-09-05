# 从刚进入、尚未拖动的海域详情页采集点位模板。
from utils import AdbController
from utils.board_view import prepare_board_view
from save_points.points import IMAGES_DIR, point_template_level

level = 8  # 前七关填写实际关卡；第 8 关及以后共用 8.png。
adb = AdbController()
prepare_board_view(adb, level)
adb.take_screenshot(IMAGES_DIR / f"{point_template_level(level)}.png")
