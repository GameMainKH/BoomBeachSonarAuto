import logging

from config import LOG_FILE, LOG_LEVEL


_LOGGING_READY = False
_RESET = "\033[0m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",    # 青色
    logging.INFO: "\033[32m",     # 绿色
    logging.WARNING: "\033[33m",  # 黄色
    logging.ERROR: "\033[31m",    # 红色
    logging.CRITICAL: "\033[35m", # 紫色
}


class ColorFormatter(logging.Formatter):
    """控制台彩色日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        time_text = self.formatTime(record, self.datefmt)
        level_text = f"{color}{record.levelname:<8}{_RESET}"
        name_text = f"{_DIM}{record.name}{_RESET}"
        message = record.getMessage()

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return f"{_DIM}{time_text}{_RESET} {level_text} {name_text} | {message}"


def setup_logging(level: str | int | None = None) -> None:
    """初始化项目日志，默认同时输出到控制台和文件。"""
    global _LOGGING_READY

    if _LOGGING_READY:
        if level is None:
            return
        log_level = _normalize_level(level)
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        for handler in root_logger.handlers:
            handler.setLevel(log_level)
        return

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_level = _normalize_level(level or LOG_LEVEL)

    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = ColorFormatter(datefmt="%H:%M:%S")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_level)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    _LOGGING_READY = True


def get_logger(name: str) -> logging.Logger:
    """获取项目 logger，首次调用时自动初始化日志系统。"""
    setup_logging()
    return logging.getLogger(name)


def _normalize_level(level: str | int) -> int:
    """把配置中的日志级别转换为 logging 模块使用的整数级别。"""
    if isinstance(level, int):
        return level

    normalized = level.upper()
    log_level = logging.getLevelName(normalized)
    if not isinstance(log_level, int):
        raise ValueError(f"不支持的日志级别: {level}")
    return log_level
