# BoomBeachSonarAuto

基于 **ADB + OpenCV** 的海岛奇兵声呐活动界面自动化与菱形网格识别工具。项目会通过 ADB 获取模拟器截图，使用模板匹配进入活动页面，识别菱形网格中心点，逐格点击并判断命中结果，最后生成命中可视化图片。

> 说明：本项目仅用于图像识别、自动化流程和个人学习研究。使用前请确认不会违反目标应用的用户协议或平台规则。请自觉在24小时后删除。
本项目本项目目前处于试验阶段，经小范围测试可运行，但不保证绝对稳定，建议有python或自动化工具开发基础的人员使用，
使用本软件的风险完全由用户自行承担。作者不对任何直接或间接损失承担责任。


## 功能特性

- 通过 ADB 连接安卓模拟器或设备。
- 使用模板图片识别活动入口、退出按钮、图标等 UI 元素。
- 自动检测菱形网格外框并计算每个格子的中心坐标。
- 输出命中结果图片。
- 提供 PyQt6 调试工具，用于截图取点和裁剪模板图片。

## 项目结构

```text
.
├── main.py                    # 主入口，执行自动化流程
├── config.py                  # 路径、ADB 设备、日志和模板匹配配置
├── requirements.txt           # Python 依赖
├── template/                  # 模板匹配所需图片
├── utils/
│   ├── adb_control.py         # ADB 封装
│   ├── image_match.py         # 模板匹配
│   ├── diamond_centers.py     # 菱形网格检测与中心点计算
│   ├── diamond_hit.py         # 点击前后截图对比与命中判断
│   └── logger.py              # 日志配置
├── _debug/
│   ├── debug_gui.py           # 截图取点/模板裁剪 GUI
│   ├── screenshots/           # 调试截图输出
│   └── logs/                  # 日志文件
└── outputs/                   # 命中可视化结果输出
```

## 环境要求

- Python 3.10 或更高版本。
- 已安装并可在命令行使用的 `adb`。
- 一台已开启 ADB 调试的安卓设备或模拟器。
- 海岛奇兵国服（国际服没测试过，如有需要自行修改包名与流程）和 QNET 已安装在设备中。
- 当前模板图片与设备分辨率、游戏界面语言、UI 状态尽量一致。

## 安装

先进入此项目目录，命令行运行

```powershell
python -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
```

如果在 macOS/Linux 上运行，虚拟环境激活命令通常为：

```bash
source .venv/bin/activate
```

## 配置

打开 `config.py`，按你的设备环境调整配置。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `ADB_SERIAL` | `127.0.0.1:5555` | ADB 设备序列号或模拟器连接地址 |
| `TEMPLATE_DIR` | `template/` | 模板图片目录 |
| `SCREENSHOT_DIR` | `_debug/screenshots/` | 调试截图保存目录 |
| `LOG_FILE` | `_debug/logs/bbma.log` | 日志文件路径 |
| `OUTPUT_DIR` | `outputs/` | 命中可视化图片输出目录 |
| `DEFAULT_MATCH_THRESHOLD` | `0.85` | 默认模板匹配阈值 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

连接设备前可以先检查 ADB：

```powershell
adb devices
adb connect 127.0.0.1:5555
```

如果你的设备不是 `127.0.0.1:5555`，请把 `config.py` 中的 `ADB_SERIAL` 改成 `adb devices` 显示的设备 ID。

## 使用方法

1. 启动安卓模拟器或连接安卓设备，建议使用雷电模拟器并且分辨率调整至 720x1280。
2. 确认设备已登录到游戏主界面并且Qnet的所有悬浮窗移动右上角，且活动入口可见。
3. 确认 `template/` 目录下的模板图片能匹配当前界面。
4. 在 `main.py` 底部修改要运行的Level(一号区域即填数字“1”)

```python
if __name__ == "__main__":
    level = 1
    main(level)
```

1. 运行主程序：

```powershell
python main.py
```

运行结束后，命中可视化图片会保存到：

```text
outputs/hit_map_level_<level>.png
```

日志文件会保存到：

```text
_debug/logs/bbma.log
```

## 图片说明

Qnet配置：
<p align="left"><img src="docs/images/qnet.png" width="400"></p>

启动脚本前应确保Qnet如图所示：
<p align="left"><img src="docs\images\home.png" height="400"></p>

最终输出（红色方框即为潜艇）：
<p align="left"><img src="docs\images\hit_map_level_1.png" height="400"></p>

## 调试工具

项目内置了一个简单的 PyQt6 调试 GUI，可以用于：

- 连接设备并刷新截图。
- 点击截图获取坐标。
- 框选截图区域并保存为模板图片。

启动方式：

```powershell
python _debug/debug_gui.py
```

常见用途：

- 模板匹配失败时，重新裁剪 `template/` 下的图片。
- 自动点击位置不准时，检查截图分辨率和坐标。
- 新设备或新分辨率适配时，更新关键 UI 模板。

## 模板图片说明

当前主流程会使用以下模板：

| 文件 | 用途 |
| --- | --- |
| `template/activity_button.png` | 活动入口按钮 |
| `template/login.png` | 登录按钮 |
| `template/quit_activity.png` | 活动详情页退出按钮 |
| `template/qnet_button_off.png` | QNET 未开启状态按钮 |
| `template/ship.png` | 母舰图标 |

如果界面发生变化、分辨率不同或模板匹配失败，需要重新裁剪对应模板。

## 输出与调试文件

- `outputs/`：主流程结果图。
- `_debug/screenshots/`：运行过程中的截图和中间图。
- `_debug/logs/bbma.log`：日志文件。
- `_debug/screenshots/diamond_hit_debug/`：命中判断的局部调试图。


## 常见问题

### 找不到 ADB 设备

先运行：

```powershell
adb devices
```

如果没有设备，检查模拟器是否开启 ADB，或重新执行：

```powershell
adb connect <设备地址>
```

然后同步修改 `config.py` 中的 `ADB_SERIAL`。

### 模板匹配失败

可能原因：

- 模板图片与当前分辨率不一致。
- 游戏界面语言、缩放或 UI 状态不同。
- 模板区域裁剪过大或包含动态背景。

可以使用 `_debug/debug_gui.py` 重新裁剪模板，并适当调整 `DEFAULT_MATCH_THRESHOLD`。

### 菱形网格识别失败

可能原因：

- 截图中网格区域被遮挡。
- 当前画面不是活动详情页。
- 网格颜色、边框或背景变化明显。

可以查看 `_debug/screenshots/` 下的中间图片，确认程序检测到的外框是否正确。

## License

本项目源码公开，仅允许非商业用途。

未经作者书面授权，禁止将本项目用于商业产品、付费服务、商业自动化、商业代练、商业测试、商业运营、二次售卖或任何直接/间接盈利场景。

详见 [LICENSE](./LICENSE)。
