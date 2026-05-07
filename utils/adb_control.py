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
        self._root_shell_ready = False
        self._package_uid_cache: dict[str, int] = {}
        self._ip6tables_available: bool | None = None
        self._weak_network_enabled_uids: set[int] = set()
        self._reject_network_enabled_uids: set[int] = set()
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
        if check and device and result.returncode != 0 and self._is_recoverable_adb_error(result):
            logger.warning(
                "ADB 连接异常，正在重连并重试: command=%s stdout=%r stderr=%r",
                " ".join(command),
                _limit_text(result.stdout),
                _limit_text(result.stderr),
            )
            self._recover_connection()
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

    def enable_weak_network(self, package_name: str) -> None:
        """通过包名开启弱网，阻断该 APP 的出站网络。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        self._set_weak_network_rule(uid, enabled=True)
        logger.info("已开启 APP 弱网: package=%s uid=%s", package_name, uid)

    def disable_weak_network(self, package_name: str) -> None:
        """通过包名关闭弱网，恢复该 APP 的出站网络。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        self._set_weak_network_rule(uid, enabled=False)
        logger.info("已关闭 APP 弱网: package=%s uid=%s", package_name, uid)

    def enable_reject_network(self, package_name: str) -> None:
        """通过包名开启 REJECT 断网，独立于 DROP 弱网规则。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        self._set_reject_network_rule(uid, enabled=True)
        logger.info("已开启 APP REJECT 断网: package=%s uid=%s", package_name, uid)

    def disable_reject_network(self, package_name: str) -> None:
        """通过包名关闭 REJECT 断网，不影响 DROP 弱网规则。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        self._set_reject_network_rule(uid, enabled=False)
        logger.info("已关闭 APP REJECT 断网: package=%s uid=%s", package_name, uid)

    def get_weak_network_diagnostics(self, package_name: str) -> str:
        """读取当前弱网规则和计数器，方便排查脚本运行时的真实状态。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        script = _build_weak_network_diagnostics_script(uid)
        result = self._run_privileged_script(script, check=False)
        output = result.stdout.strip()
        error = result.stderr.strip()
        sections = [f"package={package_name}", f"uid={uid}", f"returncode={result.returncode}"]
        if output:
            sections.append(output)
        if error:
            sections.append(f"stderr={_limit_text(error, 1200)}")
        return "\n".join(sections)

    def get_reject_network_diagnostics(self, package_name: str) -> str:
        """读取当前 REJECT 断网规则和计数器，方便排查残留规则。"""
        package_name = package_name.strip()
        if not package_name:
            _raise_value_error("包名不能为空")

        uid = self._get_package_uid(package_name)
        script = _build_reject_network_diagnostics_script(uid)
        result = self._run_privileged_script(script, check=False)
        output = result.stdout.strip()
        error = result.stderr.strip()
        sections = [f"package={package_name}", f"uid={uid}", f"returncode={result.returncode}"]
        if output:
            sections.append(output)
        if error:
            sections.append(f"stderr={_limit_text(error, 1200)}")
        return "\n".join(sections)

    def ensure_root_shell(self) -> None:
        """确保 adb shell 已经以 root 身份运行，避免 su 授权弹窗。"""
        if self._root_shell_ready:
            return
        if self._is_root_shell():
            self._root_shell_ready = True
            logger.info("adb shell 已是 root")
            return

        logger.warning("adb shell 不是 root，正在执行 adb root 并重连")
        root_result = self._run(["root"], check=False)
        logger.info(
            "adb root 结果: returncode=%s stdout=%r stderr=%r",
            root_result.returncode,
            _limit_text(root_result.stdout),
            _limit_text(root_result.stderr),
        )
        sleep(1.5)
        self.connect()
        sleep(0.5)
        if not self._is_root_shell():
            raise RuntimeError("当前设备无法通过 adb root 获得 root shell，弱网控制会触发授权弹窗，已中止")

        self._root_shell_ready = True
        logger.info("adb shell root 已准备就绪")

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

    def _recover_connection(self) -> None:
        """恢复异常的 adb 连接，并清理依赖设备状态的缓存。"""
        self._touch_device_info = None
        self._root_shell_ready = False
        disconnect_command = ["adb", "disconnect", self.serial]
        logger.info("断开 adb 设备连接: %s", self.serial)
        subprocess.run(disconnect_command, capture_output=True, text=True)
        sleep(0.5)
        self.connect()
        sleep(1.0)

    @staticmethod
    def _is_recoverable_adb_error(result: subprocess.CompletedProcess[str]) -> bool:
        """判断 adb 失败是否属于可通过重连恢复的连接错误。"""
        output = f"{result.stdout}\n{result.stderr}".lower()
        recoverable_messages = [
            "error: closed",
            "device offline",
            "no devices/emulators found",
            "device not found",
            "cannot connect",
            "failed to connect",
            "unable to connect",
        ]
        return any(message in output for message in recoverable_messages)

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
        self._run(["shell", "sh", "-c", _quote_shell_arg(script)])

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

    def _get_package_uid(self, package_name: str) -> int:
        """读取指定包名对应的安卓 UID。"""
        if package_name in self._package_uid_cache:
            return self._package_uid_cache[package_name]

        result = self._run(["shell", "cmd", "package", "list", "packages", "-U", package_name])
        uid_match = re.search(rf"package:{re.escape(package_name)}\s+uid:(\d+)", result.stdout)
        if uid_match is None:
            _raise_value_error(f"未找到包名对应的 UID: {package_name}")
        uid = int(uid_match.group(1))
        self._package_uid_cache[package_name] = uid
        return uid

    def _set_weak_network_rule(self, uid: int, *, enabled: bool) -> None:
        """配置 IPv4/IPv6 的按 UID 弱网规则。"""
        action = "开启" if enabled else "关闭"
        if enabled and uid in self._weak_network_enabled_uids:
            if self._is_weak_network_rule_active(uid):
                logger.info("APP 弱网已开启，跳过重复设置: uid=%s", uid)
                return
            logger.warning("本地缓存显示弱网已开启，但规则不存在，正在重新设置: uid=%s", uid)
            self._weak_network_enabled_uids.discard(uid)

        self._run_privileged_script(_build_weak_network_script("iptables", uid, enabled))
        if self._is_ip6tables_available() is False:
            logger.warning("ip6tables 不可用，仅%s IPv4 弱网规则: uid=%s", action, uid)
            self._update_weak_network_state(uid, enabled)
            return

        ip6_result = self._run_privileged_script(_build_weak_network_script("ip6tables", uid, enabled), check=False)
        if ip6_result.returncode != 0:
            logger.warning(
                "ip6tables %s弱网规则失败，已忽略 IPv6: uid=%s stdout=%r stderr=%r",
                action,
                uid,
                _limit_text(ip6_result.stdout),
                _limit_text(ip6_result.stderr),
            )

        self._update_weak_network_state(uid, enabled)

    def _set_reject_network_rule(self, uid: int, *, enabled: bool) -> None:
        """配置 IPv4/IPv6 的按 UID REJECT 断网规则。"""
        action = "开启" if enabled else "关闭"
        if enabled and uid in self._reject_network_enabled_uids:
            if self._is_reject_network_rule_active(uid):
                logger.info("APP REJECT 断网已开启，跳过重复设置: uid=%s", uid)
                return
            logger.warning("本地缓存显示 REJECT 断网已开启，但规则不存在，正在重新设置: uid=%s", uid)
            self._reject_network_enabled_uids.discard(uid)

        self._run_privileged_script(_build_reject_network_script("iptables", uid, enabled))
        if self._is_ip6tables_available() is False:
            logger.warning("ip6tables 不可用，仅%s IPv4 REJECT 断网规则: uid=%s", action, uid)
            self._update_reject_network_state(uid, enabled)
            return

        ip6_result = self._run_privileged_script(_build_reject_network_script("ip6tables", uid, enabled), check=False)
        if ip6_result.returncode != 0:
            logger.warning(
                "ip6tables %s REJECT 断网规则失败，已忽略 IPv6: uid=%s stdout=%r stderr=%r",
                action,
                uid,
                _limit_text(ip6_result.stdout),
                _limit_text(ip6_result.stderr),
            )

        self._update_reject_network_state(uid, enabled)

    def _is_ip6tables_available(self) -> bool:
        """检查并缓存 ip6tables 是否可用。"""
        if self._ip6tables_available is not None:
            return self._ip6tables_available

        result = self._run_privileged_script("command -v ip6tables >/dev/null", check=False)
        self._ip6tables_available = result.returncode == 0
        return self._ip6tables_available

    def _update_weak_network_state(self, uid: int, enabled: bool) -> None:
        """更新本地弱网状态缓存。"""
        if enabled:
            self._weak_network_enabled_uids.add(uid)
        else:
            self._weak_network_enabled_uids.discard(uid)

    def _update_reject_network_state(self, uid: int, enabled: bool) -> None:
        """更新本地 REJECT 断网状态缓存。"""
        if enabled:
            self._reject_network_enabled_uids.add(uid)
        else:
            self._reject_network_enabled_uids.discard(uid)

    def _is_weak_network_rule_active(self, uid: int) -> bool:
        """确认当前 iptables 中是否存在指定 UID 的弱网规则。"""
        script = f"iptables -C OUTPUT -m owner --uid-owner {uid} -j BBMA_WEAKNET"
        result = self._run_privileged_script(script, check=False)
        return result.returncode == 0

    def _is_reject_network_rule_active(self, uid: int) -> bool:
        """确认当前 iptables 中是否存在指定 UID 的 REJECT 断网规则。"""
        script = f"iptables -C OUTPUT -m owner --uid-owner {uid} -j BBMA_REJECTNET"
        result = self._run_privileged_script(script, check=False)
        return result.returncode == 0

    def _run_privileged_script(self, script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        """使用 root adb shell 执行特权脚本。"""
        self.ensure_root_shell()
        return self._run(["shell", "sh", "-c", _quote_shell_arg(script)], check=check)

    def _is_root_shell(self) -> bool:
        """检查当前 adb shell 是否已经是 root。"""
        result = self._run(["shell", "id", "-u"], check=False)
        return result.returncode == 0 and result.stdout.strip() == "0"


def _limit_text(text: str, limit: int = 500) -> str:
    """限制日志中的命令输出长度，避免单条日志过长。"""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "...(truncated)"


def _build_weak_network_script(command: str, uid: int, enabled: bool) -> str:
    """生成按 UID 控制弱网的 iptables 脚本。"""
    chain = "BBMA_WEAKNET"
    if enabled:
        return (
            f"{command} -N {chain} 2>/dev/null || true; "
            f"{command} -C {chain} -j DROP 2>/dev/null || {command} -A {chain} -j DROP; "
            f"{command} -C OUTPUT -m owner --uid-owner {uid} -j {chain} 2>/dev/null "
            f"|| {command} -I OUTPUT -m owner --uid-owner {uid} -j {chain}"
        )
    return (
        f"while {command} -C OUTPUT -m owner --uid-owner {uid} -j {chain} 2>/dev/null; "
        f"do {command} -D OUTPUT -m owner --uid-owner {uid} -j {chain}; done"
    )


def _build_reject_network_script(command: str, uid: int, enabled: bool) -> str:
    """生成按 UID 控制 REJECT 断网的 iptables 脚本。"""
    chain = "BBMA_REJECTNET"
    if enabled:
        reject_with = "icmp6-port-unreachable" if command == "ip6tables" else "icmp-port-unreachable"
        return (
            f"{command} -N {chain} 2>/dev/null || true; "
            f"{command} -F {chain}; "
            f"{command} -A {chain} -p tcp -j REJECT --reject-with tcp-reset; "
            f"{command} -A {chain} -j REJECT --reject-with {reject_with}; "
            f"{command} -C OUTPUT -m owner --uid-owner {uid} -j {chain} 2>/dev/null "
            f"|| {command} -I OUTPUT -m owner --uid-owner {uid} -j {chain}"
        )
    return (
        f"while {command} -C OUTPUT -m owner --uid-owner {uid} -j {chain} 2>/dev/null; "
        f"do {command} -D OUTPUT -m owner --uid-owner {uid} -j {chain}; done; "
        f"{command} -F {chain} 2>/dev/null || true; "
        f"{command} -X {chain} 2>/dev/null || true"
    )


def _build_weak_network_diagnostics_script(uid: int) -> str:
    """生成弱网状态诊断脚本，只读取规则和计数器，不修改设备状态。"""
    chain = "BBMA_WEAKNET"
    return (
        f"echo ipv4_rule=$([ $(iptables -C OUTPUT -m owner --uid-owner {uid} -j {chain} "
        f"2>/dev/null; echo $?) -eq 0 ] && echo 1 || echo 0); "
        f"if command -v ip6tables >/dev/null 2>&1; then "
        f"echo ipv6_rule=$([ $(ip6tables -C OUTPUT -m owner --uid-owner {uid} -j {chain} "
        f"2>/dev/null; echo $?) -eq 0 ] && echo 1 || echo 0); "
        f"else echo ipv6_rule=unavailable; fi; "
        f"echo '[iptables OUTPUT]'; iptables -L OUTPUT -v -n 2>&1; "
        f"echo '[iptables {chain}]'; iptables -L {chain} -v -n 2>&1; "
        f"if command -v ip6tables >/dev/null 2>&1; then "
        f"echo '[ip6tables OUTPUT]'; ip6tables -L OUTPUT -v -n 2>&1; "
        f"echo '[ip6tables {chain}]'; ip6tables -L {chain} -v -n 2>&1; fi"
    )


def _build_reject_network_diagnostics_script(uid: int) -> str:
    """生成 REJECT 断网状态诊断脚本，只读取规则和计数器。"""
    chain = "BBMA_REJECTNET"
    return (
        f"echo ipv4_rule=$([ $(iptables -C OUTPUT -m owner --uid-owner {uid} -j {chain} "
        f"2>/dev/null; echo $?) -eq 0 ] && echo 1 || echo 0); "
        f"if command -v ip6tables >/dev/null 2>&1; then "
        f"echo ipv6_rule=$([ $(ip6tables -C OUTPUT -m owner --uid-owner {uid} -j {chain} "
        f"2>/dev/null; echo $?) -eq 0 ] && echo 1 || echo 0); "
        f"else echo ipv6_rule=unavailable; fi; "
        f"echo '[iptables OUTPUT]'; iptables -L OUTPUT -v -n 2>&1; "
        f"echo '[iptables {chain}]'; iptables -L {chain} -v -n 2>&1; "
        f"if command -v ip6tables >/dev/null 2>&1; then "
        f"echo '[ip6tables OUTPUT]'; ip6tables -L OUTPUT -v -n 2>&1; "
        f"echo '[ip6tables {chain}]'; ip6tables -L {chain} -v -n 2>&1; fi"
    )


def _quote_shell_arg(text: str) -> str:
    """把字符串包成 shell 单引号参数。"""
    return "'" + text.replace("'", "'\\''") + "'"


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
