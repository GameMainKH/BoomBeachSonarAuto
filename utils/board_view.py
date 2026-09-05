"""把海域视角调整到人工校准模板的位置。"""

from save_points.points import SHARED_POINT_LEVEL


BOARD_VIEW_SWIPE = (1000, 500, 1000, 545)
BOARD_VIEW_SETTLE = 1.5


def prepare_board_view(adb, level: int) -> None:
    """仅在刚进入海域后调用一次；第 8 关起下移视角并等待落稳。"""
    if level < SHARED_POINT_LEVEL:
        return
    adb.delay(BOARD_VIEW_SETTLE)
    adb.swipe(*BOARD_VIEW_SWIPE)
    adb.delay(BOARD_VIEW_SETTLE)
