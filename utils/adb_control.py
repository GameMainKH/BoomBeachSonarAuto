import re
import subprocess
from pathlib import Path
from time import sleep

import cv2

from config import ADB_SERIAL, DEFAULT_SCREENSHOT_NAME, SCREENSHOT_DIR
from utils.logger import get_logger


logger = get_logger(__name__)


class AdbCommandError(RuntimeError):
    """  adb 命令执行失败时抛出，包含命令和结果信息。"""

    def __init__(self, command: list[str], result: subprocess.CompletedProcess[str]):
        self.command = command
        self.result = result
        message = result.stderr.strip() or result.stdout.strip() or "adb command failed"
        super().__init__(f"{' '.join(command)}: {message}")


class AdbController:

    def __init__(self, serial: str = ADB_SERIAL, auto_connect: bool = True):
        self.serial = serial
        self._touch_device_info: tuple[str, int, int, int, int] | None = None
        self._next_touch_tracking_id = 100
        if auto_connect:
            self.connect()
        logger.info("adb 控制器已初始化: %s", self.serial)

    def _run(self, args: list[str], *, device: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
        ''' 执行 adb 命令，自动添加设备参数。 '''
        command = ["adb"]
        if device:
            command.extend(["-s", self.serial])
        command.extend(args)

        logger.debug("执行 adb 命令: %s", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True)
        if check and result.returncode != 0:
            logger.error(
                "adb 命令失败: command=%s returncode=%s stdout=%r stderr=%r",
                " ".join(command),
                result.returncode,
                _limit_text(result.stdout),
                _limit_text(result.stderr),
            )
            raise AdbCommandError(command, result)
        return result

    @property
    def ip(self) -> str:
        return self.serial

    def get_screen_size(self) -> tuple[int, int]:
        """获取系统报告的屏幕大小，返回宽度和高度。"""
        result = self._run(["shell", "wm", "size"])
        return self._parse_wm_size(result.stdout)

    def get_screenshot_size(self) -> tuple[int, int]:
        """根据当前截图返回实际画面宽度和高度。"""
        screen = self.read_screenshot()
        height, width = screen.shape[:2]
        return width, height

    def get_orientation(self) -> str:
        """根据截图判断当前画面方向。"""
        width, height = self.get_screenshot_size()
        return "landscape" if width > height else "portrait"

    def take_screenshot(self, output_path: str | Path | None = None) -> Path:
        """使用 adb 截图并保存到本地，返回截图路径。"""
        path = Path(output_path) if output_path else SCREENSHOT_DIR / DEFAULT_SCREENSHOT_NAME
        path.parent.mkdir(parents=True, exist_ok=True)

        remote_path = "/sdcard/_bbma_screen.png"
        self._run(["shell", "screencap", "-p", remote_path])
        self._run(["pull", remote_path, str(path)])
        logger.info("截图已保存: %s", path)
        return path

    def read_screenshot(self, output_path: str | Path | None = None):
        """截图并读取为 OpenCV 图像对象。"""
        path = self.take_screenshot(output_path)
        screen = cv2.imread(str(path))
        if screen is None:
            logger.error("截图读取失败: %s", path)
            raise RuntimeError(f"failed to read screenshot: {path}")
        return screen

    def is_landscape_by_screenshot(self) -> bool:
        """根据截图判断屏幕是否为横屏。"""
        return self.get_orientation() == "landscape"

    def click(self, x: int, y: int) -> None:
        """点击屏幕坐标。"""
        self._run(["shell", "input", "tap", str(x), str(y)])
        logger.info("点击屏幕坐标: (%s, %s)", x, y)

    def back(self) -> None:
        """触发安卓返回键。"""
        self._run(["shell", "input", "keyevent", "KEYCODE_BACK"])
        logger.info("已触发返回键")

    def go_home(self) -> None:
        """触发安卓主页键，回到系统主页。"""
        self._run(["shell", "input", "keyevent", "KEYCODE_HOME"])
        logger.info("已回到系统主页")

    def open_app(self, package_name: str) -> None:
        """通过包名启动 APP。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        self._run([
            "shell",
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ])
        logger.info("已通过包名启动 APP: %s", package_name)

    def close_app(self, package_name: str) -> None:
        """通过包名强制停止 APP。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        self._run(["shell", "am", "force-stop", package_name])
        logger.info("已通过包名关闭 APP: %s", package_name)

    def swipe(
        self,
        direction: str | int,
        distance: int,
        duration_ms: int = 300,
        start: tuple[int, int] | int | None = None,
    ):
        """按方向距离或四坐标方式滑动屏幕。"""
        if not isinstance(direction, str):
            if start is None:
                _raise_value_error("坐标滑动需要提供 start_x、start_y、end_x、end_y")
            start_x = _to_int("start_x", direction)
            start_y = _to_int("start_y", distance)
            end_x = _to_int("end_x", duration_ms)
            end_y = _to_int("end_y", start)
            self.drag(start_x, start_y, end_x, end_y, 300)
            return self

        direction = direction.lower()
        if direction not in {"up", "down", "left", "right"}:
            _raise_value_error(f"不支持的滑动方向: {direction}")
        distance = _validate_positive("distance", distance)
        duration_ms = _validate_duration(duration_ms)

        if start is None:
            width, height = self.get_screenshot_size()
            start_x, start_y = width // 2, height // 2
        else:
            start_x = _to_int("start[0]", start[0])
            start_y = _to_int("start[1]", start[1])
            width, height = self.get_screenshot_size()

        end_x, end_y = _calculate_swipe_end(start_x, start_y, direction, distance)
        start_x, start_y = _clamp_point(start_x, start_y, width, height)
        end_x, end_y = _clamp_point(end_x, end_y, width, height)

        self.drag(start_x, start_y, end_x, end_y, duration_ms)
        logger.info(
            "滑动屏幕: direction=%s distance=%s start=(%s, %s) end=(%s, %s) duration_ms=%s",
            direction,
            distance,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms,
        )
        return self

    def pinch_in(
        self,
        center: tuple[int, int] | None = None,
        distance: int = 300,
        duration_ms: int = 300,
    ) -> None:
        """双指向内划，常用于缩小地图。"""
        center_x, center_y, width, height = self._resolve_gesture_center(center)
        distance = _validate_positive("distance", distance)
        duration_ms = _validate_duration(duration_ms)
        inner_offset = _calculate_pinch_inner_offset()

        left_start = _clamp_point(center_x - inner_offset - distance, center_y, width, height)
        left_end = _clamp_point(center_x - inner_offset, center_y, width, height)
        right_start = _clamp_point(center_x + inner_offset + distance, center_y, width, height)
        right_end = _clamp_point(center_x + inner_offset, center_y, width, height)

        self._run_two_finger_swipe(left_start, left_end, right_start, right_end, duration_ms, (width, height))
        logger.info(
            "双指向内划: center=(%s, %s) distance=%s duration_ms=%s",
            center_x,
            center_y,
            distance,
            duration_ms,
        )

    def pinch_out(
        self,
        center: tuple[int, int] | None = None,
        distance: int = 300,
        duration_ms: int = 300,
    ) -> None:
        """双指向外划，常用于放大地图。"""
        center_x, center_y, width, height = self._resolve_gesture_center(center)
        distance = _validate_positive("distance", distance)
        duration_ms = _validate_duration(duration_ms)
        inner_offset = _calculate_pinch_inner_offset()

        left_start = _clamp_point(center_x - inner_offset, center_y, width, height)
        left_end = _clamp_point(center_x - inner_offset - distance, center_y, width, height)
        right_start = _clamp_point(center_x + inner_offset, center_y, width, height)
        right_end = _clamp_point(center_x + inner_offset + distance, center_y, width, height)

        self._run_two_finger_swipe(left_start, left_end, right_start, right_end, duration_ms, (width, height))
        logger.info(
            "双指向外划: center=(%s, %s) distance=%s duration_ms=%s",
            center_x,
            center_y,
            distance,
            duration_ms,
        )

    def input_text(self, text: str) -> None:
        """输入指定字符串，适合英文、数字和简单符号。"""
        escaped_text = _escape_input_text(text)
        self._run(["shell", "input", "text", escaped_text])
        logger.info("输入文本: %s", text)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 800) -> None:
        """从指定起点拖动到指定终点。"""
        duration_ms = _validate_duration(duration_ms)
        start_x = _to_int("start_x", start_x)
        start_y = _to_int("start_y", start_y)
        end_x = _to_int("end_x", end_x)
        end_y = _to_int("end_y", end_y)

        self._run([
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration_ms),
        ])
        logger.info(
            "拖动屏幕: start=(%s, %s) end=(%s, %s) duration_ms=%s",
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms,
        )

    def connect(self) -> None:
        """连接 adb 设备。"""
        logger.info("连接 adb 设备: %s", self.serial)
        self._run(["connect", self.serial], device=False)

    def adb_restart(self) -> None:
        """重启 adb 服务。"""
        logger.warning("重启 adb 服务")
        self._run(["kill-server"], device=False)
        self._run(["start-server"], device=False)
        self.connect()
        
    def delay(self, seconds: float):
        """等待指定秒数，方便在自动化步骤之间插入延迟。"""
        seconds = float(seconds)
        if seconds < 0:
            _raise_value_error(f"seconds 不能小于 0: {seconds}")
        sleep(seconds)
        return self

    @staticmethod
    def _parse_wm_size(output: str) -> tuple[int, int]:
        ''' 解析 adb shell wm size 输出，返回宽度和高度。 '''
        size_str = output.strip().split()[-1]
        width, height = map(int, size_str.split("x"))
        return width, height

    def _resolve_gesture_center(self, center: tuple[int, int] | None) -> tuple[int, int, int, int]:
        """解析手势中心点，并返回中心坐标和截图尺寸。"""
        width, height = self.get_screenshot_size()
        if center is None:
            return width // 2, height // 2, width, height
        return _to_int("center[0]", center[0]), _to_int("center[1]", center[1]), width, height

    def _run_two_finger_swipe(
        self,
        first_start: tuple[int, int],
        first_end: tuple[int, int],
        second_start: tuple[int, int],
        second_end: tuple[int, int],
        duration_ms: int,
        screen_size: tuple[int, int],
    ) -> None:
        """使用底层触摸事件执行双指滑动。"""
        touch_device_info = self._get_touch_device_info()
        first_tracking_id, second_tracking_id = self._next_touch_tracking_ids()
        script = _build_two_finger_sendevent_script(
            touch_device_info,
            first_start,
            first_end,
            second_start,
            second_end,
            duration_ms,
            screen_size,
            first_tracking_id,
            second_tracking_id,
        )
        self._run(["shell", "sh", "-c", script])

    def _next_touch_tracking_ids(self) -> tuple[int, int]:
        """获取本次双指手势使用的唯一触点 ID。"""
        if self._next_touch_tracking_id > 60000:
            self._next_touch_tracking_id = 100
        first_tracking_id = self._next_touch_tracking_id
        second_tracking_id = first_tracking_id + 1
        self._next_touch_tracking_id += 2
        return first_tracking_id, second_tracking_id

    def _get_touch_device_info(self) -> tuple[str, int, int, int, int]:
        """探测支持多点触控的输入设备和坐标范围。"""
        if self._touch_device_info is not None:
            return self._touch_device_info

        result = self._run(["shell", "getevent", "-pl"])
        touch_device_info = _parse_touch_device_info(result.stdout)
        if touch_device_info is None:
            _raise_value_error("未找到支持多点触控的输入设备")
        self._touch_device_info = touch_device_info
        return touch_device_info


def _limit_text(text: str, limit: int = 500) -> str:
    """限制日志中的命令输出长度，避免单条日志过长。"""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "...(truncated)"


def _raise_value_error(message: str) -> None:
    """记录参数错误并抛出 ValueError。"""
    logger.error(message)
    raise ValueError(message)


def _to_int(name: str, value: int) -> int:
    """把参数转换为整数，失败时记录日志。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        _raise_value_error(f"{name} 必须是整数: {value}")


def _validate_positive(name: str, value: int) -> int:
    """校验参数必须大于 0。"""
    int_value = _to_int(name, value)
    if int_value <= 0:
        _raise_value_error(f"{name} 必须大于 0: {value}")
    return int_value


def _validate_duration(duration_ms: int) -> int:
    """校验 adb 手势持续时间。"""
    int_value = _to_int("duration_ms", duration_ms)
    if int_value < 0:
        _raise_value_error(f"duration_ms 不能小于 0: {duration_ms}")
    return int_value


def _calculate_swipe_end(start_x: int, start_y: int, direction: str, distance: int) -> tuple[int, int]:
    """根据方向和距离计算滑动终点。"""
    if direction == "up":
        return start_x, start_y - distance
    if direction == "down":
        return start_x, start_y + distance
    if direction == "left":
        return start_x - distance, start_y
    return start_x + distance, start_y


def _calculate_pinch_inner_offset() -> int:
    """计算双指手势靠近中心时固定保留的半间隔。"""
    return 40


def _parse_touch_device_info(output: str) -> tuple[str, int, int, int, int] | None:
    """从 getevent 输出中解析多点触控设备和坐标范围。"""
    for block in _split_getevent_device_blocks(output):
        if not all(name in block for name in ("ABS_MT_SLOT", "ABS_MT_TRACKING_ID", "ABS_MT_POSITION_X", "ABS_MT_POSITION_Y")):
            continue

        device_match = re.search(r"add device \d+:\s+(\S+)", block)
        x_range = _parse_abs_range(block, "ABS_MT_POSITION_X")
        y_range = _parse_abs_range(block, "ABS_MT_POSITION_Y")
        if device_match and x_range and y_range:
            return device_match.group(1), x_range[0], x_range[1], y_range[0], y_range[1]
    return None


def _split_getevent_device_blocks(output: str) -> list[str]:
    """按输入设备拆分 getevent -pl 输出。"""
    blocks = []
    current_lines = []
    for line in output.splitlines():
        if line.startswith("add device "):
            if current_lines:
                blocks.append("\n".join(current_lines))
            current_lines = [line]
        elif current_lines:
            current_lines.append(line)
    if current_lines:
        blocks.append("\n".join(current_lines))
    return blocks


def _parse_abs_range(block: str, abs_name: str) -> tuple[int, int] | None:
    """解析 ABS 轴的最小值和最大值。"""
    match = re.search(rf"{abs_name}\s*:.*?min\s+(-?\d+),\s+max\s+(-?\d+)", block)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _clamp_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把坐标限制在屏幕范围内。"""
    return (
        min(max(int(x), 0), max(int(width) - 1, 0)),
        min(max(int(y), 0), max(int(height) - 1, 0)),
    )


def _build_two_finger_sendevent_script(
    touch_device_info: tuple[str, int, int, int, int],
    first_start: tuple[int, int],
    first_end: tuple[int, int],
    second_start: tuple[int, int],
    second_end: tuple[int, int],
    duration_ms: int,
    screen_size: tuple[int, int],
    first_tracking_id: int,
    second_tracking_id: int,
) -> str:
    """生成双指滑动的 sendevent 脚本。"""
    device, min_x, max_x, min_y, max_y = touch_device_info
    screen_width, screen_height = screen_size
    steps = max(4, min(duration_ms // 16, 30))
    sleep_seconds = max(duration_ms / steps / 1000, 0.01)
    commands = []

    first_touch_start = _screen_to_touch_point(first_start, screen_width, screen_height, min_x, max_x, min_y, max_y)
    second_touch_start = _screen_to_touch_point(second_start, screen_width, screen_height, min_x, max_x, min_y, max_y)
    _append_release_touch_slots(commands, device)
    commands.append("sleep 0.050")

    _append_touch_down(commands, device, 0, first_tracking_id, first_touch_start)
    _append_touch_down(commands, device, 1, second_tracking_id, second_touch_start)
    _append_sendevent(commands, device, 1, 330, 1)
    _append_syn(commands, device)

    for step in range(1, steps + 1):
        first_point = _interpolate_point(first_start, first_end, step, steps)
        second_point = _interpolate_point(second_start, second_end, step, steps)
        first_touch_point = _screen_to_touch_point(first_point, screen_width, screen_height, min_x, max_x, min_y, max_y)
        second_touch_point = _screen_to_touch_point(second_point, screen_width, screen_height, min_x, max_x, min_y, max_y)
        _append_touch_move(commands, device, 0, first_touch_point)
        _append_touch_move(commands, device, 1, second_touch_point)
        _append_syn(commands, device)
        if step != steps:
            commands.append(f"sleep {sleep_seconds:.3f}")

    _append_release_touch_slots(commands, device)
    return "; ".join(commands)


def _screen_to_touch_point(
    point: tuple[int, int],
    screen_width: int,
    screen_height: int,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
) -> tuple[int, int]:
    """把截图坐标转换为触摸设备坐标。"""
    x, y = point
    touch_x = _scale_axis(x, screen_width, min_x, max_x)
    touch_y = _scale_axis(y, screen_height, min_y, max_y)
    return touch_x, touch_y


def _scale_axis(value: int, screen_size: int, touch_min: int, touch_max: int) -> int:
    """按屏幕尺寸缩放单个坐标轴。"""
    if screen_size <= 1:
        return touch_min
    ratio = int(value) / (screen_size - 1)
    return int(round(touch_min + ratio * (touch_max - touch_min)))


def _interpolate_point(start: tuple[int, int], end: tuple[int, int], step: int, steps: int) -> tuple[int, int]:
    """按进度计算滑动路径中的坐标。"""
    start_x, start_y = start
    end_x, end_y = end
    return (
        int(round(start_x + (end_x - start_x) * step / steps)),
        int(round(start_y + (end_y - start_y) * step / steps)),
    )


def _append_touch_down(commands: list[str], device: str, slot: int, tracking_id: int, point: tuple[int, int]) -> None:
    """追加单根手指按下事件。"""
    x, y = point
    _append_sendevent(commands, device, 3, 47, slot)
    _append_sendevent(commands, device, 3, 57, tracking_id)
    _append_sendevent(commands, device, 3, 53, x)
    _append_sendevent(commands, device, 3, 54, y)
    _append_sendevent(commands, device, 3, 58, 1)


def _append_touch_move(commands: list[str], device: str, slot: int, point: tuple[int, int]) -> None:
    """追加单根手指移动事件。"""
    x, y = point
    _append_sendevent(commands, device, 3, 47, slot)
    _append_sendevent(commands, device, 3, 53, x)
    _append_sendevent(commands, device, 3, 54, y)


def _append_touch_up(commands: list[str], device: str, slot: int) -> None:
    """追加单根手指抬起事件。"""
    _append_sendevent(commands, device, 3, 47, slot)
    _append_sendevent(commands, device, 3, 57, -1)


def _append_release_touch_slots(commands: list[str], device: str) -> None:
    """释放当前使用的触摸 slot，避免连续手势残留状态。"""
    _append_touch_up(commands, device, 0)
    _append_touch_up(commands, device, 1)
    _append_sendevent(commands, device, 1, 330, 0)
    _append_syn(commands, device)


def _append_syn(commands: list[str], device: str) -> None:
    """追加同步事件。"""
    _append_sendevent(commands, device, 0, 0, 0)


def _append_sendevent(commands: list[str], device: str, event_type: int, event_code: int, value: int) -> None:
    """追加一条 sendevent 命令。"""
    commands.append(f"sendevent {device} {event_type} {event_code} {value}")


def _escape_input_text(text: str) -> str:
    """转义 adb input text 使用的简单文本。"""
    escaped_chars = []
    special_chars = set("&<>|;()")
    for char in text:
        if char == " ":
            escaped_chars.append("%s")
        elif char in special_chars:
            escaped_chars.append("\\" + char)
        else:
            escaped_chars.append(char)
    return "".join(escaped_chars)


if __name__ == '__main__':
    adb = AdbController()
    adb.adb_restart()
    print(adb.get_screen_size())
    adb.take_screenshot()
    print(adb.is_landscape_by_screenshot())
