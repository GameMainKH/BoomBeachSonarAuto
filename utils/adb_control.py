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


def _clamp_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把坐标限制在屏幕范围内。"""
    return (
        min(max(int(x), 0), max(int(width) - 1, 0)),
        min(max(int(y), 0), max(int(height) - 1, 0)),
    )


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
