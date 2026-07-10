from dataclasses import dataclass
from enum import Enum, auto

from utils.logger import get_logger
from utils.submarine_strategy import Cell


logger = get_logger(__name__)


class ProbeProtocolError(RuntimeError):
    """探测事务进入不安全或无法确认的状态。"""


class ProbeNotReadyError(RuntimeError):
    """点击发生前页面未准备好，可以安全恢复后重试当前格。"""


class ProbePhase(Enum):
    """单次弱网探测事务的关键阶段。"""

    PREPARING = auto()
    REQUEST_PENDING = auto()
    RESULT_VISIBLE = auto()
    RESULT_RECORDED = auto()
    REQUEST_DISCARDED = auto()
    LOGIN_RECOVERING = auto()
    COMPLETE = auto()


_ALLOWED_TRANSITIONS: dict[ProbePhase, set[ProbePhase]] = {
    ProbePhase.PREPARING: {ProbePhase.REQUEST_PENDING},
    ProbePhase.REQUEST_PENDING: {ProbePhase.RESULT_VISIBLE},
    ProbePhase.RESULT_VISIBLE: {ProbePhase.RESULT_RECORDED},
    ProbePhase.RESULT_RECORDED: {ProbePhase.REQUEST_DISCARDED},
    ProbePhase.REQUEST_DISCARDED: {ProbePhase.LOGIN_RECOVERING},
    ProbePhase.LOGIN_RECOVERING: {ProbePhase.COMPLETE},
    ProbePhase.COMPLETE: set(),
}


@dataclass
class ProbeTransaction:
    """记录一次只能串行执行的单格弱网探测。"""

    level: int
    cell: Cell
    index: int
    phase: ProbePhase = ProbePhase.PREPARING
    hit: bool | None = None

    @property
    def request_may_be_pending(self) -> bool:
        """客户端是否仍可能保存尚未丢弃的验证请求。"""
        return self.phase in {
            ProbePhase.REQUEST_PENDING,
            ProbePhase.RESULT_VISIBLE,
            ProbePhase.RESULT_RECORDED,
        }

    def advance(self, phase: ProbePhase) -> None:
        """按固定协议顺序推进事务，拒绝非法状态跳转。"""
        allowed = _ALLOWED_TRANSITIONS[self.phase]
        if phase not in allowed:
            raise ProbeProtocolError(
                f"非法探测状态转换: {self.phase.name} -> {phase.name}"
            )

        logger.info(
            "第 %s 关格子 %s 探测状态：%s -> %s",
            self.level,
            self.index,
            self.phase.name,
            phase.name,
        )
        self.phase = phase


__all__ = [
    "ProbeNotReadyError",
    "ProbePhase",
    "ProbeProtocolError",
    "ProbeTransaction",
]
