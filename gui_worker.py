"""独立任务进程；运行主流程并转发人工恢复确认。"""
import sys
import subprocess
import faulthandler
import importlib
import traceback
import json
from time import monotonic


def load_main():
    """逐项显示加载进度；阻塞时输出真实调用栈，不猜测卡点。"""
    print(f"Python：{sys.executable} ({sys.version.split()[0]})", flush=True)
    # C 层定时转储在 Python 导入或原生依赖阻塞时也能输出定位信息。
    faulthandler.dump_traceback_later(20, repeat=True, file=sys.stderr)
    try:
        module = None
        for name, label in (("numpy", "NumPy"), ("cv2", "OpenCV"),
                            ("tqdm", "进度组件"), ("main", "主流程及 ADB 初始化")):
            print(f"正在加载 {label}；若等待超过 20 秒，将自动输出诊断堆栈…", flush=True)
            started = monotonic()
            module = importlib.import_module(name)
            print(f"{label} 加载完成（{monotonic() - started:.1f} 秒）", flush=True)
        return module
    finally:
        faulthandler.cancel_dump_traceback_later()


def check_device():
    """在导入主流程前检查连接，避免无期限等待且没有启动反馈。"""
    from config import ADB_EXECUTABLE, ADB_SERIAL

    def command(*args):
        try:
            result = subprocess.run(
                [ADB_EXECUTABLE, *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("找不到 adb，请安装 ADB 并确保它在 PATH 中。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ADB 检查超过 15 秒，请检查模拟器和设备地址。") from exc
        return result

    print(f"检查 ADB 设备：{ADB_SERIAL}", flush=True)
    if ":" in ADB_SERIAL:
        result = command("connect", ADB_SERIAL)
        print((result.stdout + result.stderr).strip(), flush=True)
    result = command("-s", ADB_SERIAL, "get-state")
    if result.returncode != 0 or result.stdout.strip() != "device":
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"设备未就绪：{detail}。请确认模拟器已启动、ADB 调试已开启、设备地址正确。")
    print("设备已连接，正在加载探索程序…", flush=True)


def run_worker(level: int) -> int:
    def confirm_recovery():
        print("\n@@BBMA_CONFIRM_RECOVERY@@", flush=True)
        return sys.stdin.readline().strip() == "recover_yes"

    try:
        print("任务进程就绪。", flush=True)
        check_device()
        main = load_main()
        from utils.progress import set_progress_listener
        set_progress_listener(lambda data: print(
            "@@BBMA_PROGRESS@@" + json.dumps(data, ensure_ascii=False), flush=True,
        ))
        main._show_manual_network_recovery_dialog = confirm_recovery
        print("主程序已就绪，开始检查 root 权限并执行探索。", flush=True)
        try:
            result = main.run(level)
        finally:
            set_progress_listener(None)
        return 0 if result is not None else 1
    except Exception as exc:
        print(f"任务启动或运行失败：{exc}", flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(run_worker(int(sys.argv[1])))
