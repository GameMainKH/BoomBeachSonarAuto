from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Iterator, TextIO

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


def format_elapsed(seconds: float) -> str:
    """把运行秒数格式化为 HH:MM:SS。"""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@contextmanager
def fixed_progress_bar(
    *,
    total: int,
    description: str,
    unit: str,
    file: TextIO | None = None,
    disable: bool | None = None,
) -> Iterator[tqdm]:
    """创建固定底栏，并让控制台日志通过 tqdm 安全输出。"""
    if total <= 0:
        raise ValueError(f"进度总数必须大于 0: {total}")

    bar = tqdm(
        total=total,
        desc=description,
        unit=unit,
        ascii=True,
        dynamic_ncols=True,
        leave=True,
        file=file,
        disable=disable,
        bar_format=(
            "{desc} {percentage:3.0f}%|{bar:20}| "
            "{n_fmt}/{total_fmt}{unit} | {postfix}"
        ),
    )
    # 非交互输出不创建动态栏，也不需要改写现有日志 handler。
    log_context = logging_redirect_tqdm() if not bar.disable else nullcontext()
    try:
        with log_context:
            yield bar
    finally:
        bar.close()


def update_fixed_progress(bar: tqdm, current: int, postfix: str) -> None:
    """把底栏更新到绝对进度，避免重复反馈导致累计误差。"""
    bar.n = min(max(int(current), 0), int(bar.total))
    bar.set_postfix_str(postfix, refresh=True)


@dataclass(frozen=True)
class SearchProgress:
    """生成探索进度条右侧的动态状态。"""

    level: int
    max_probes: int
    started_at: float
    total_ship_cells: int | None = None
    total_ships: int | None = None

    def strategy_postfix(
        self,
        *,
        attempts: int,
        confirmed_lengths: list[int],
        remaining_lengths: list[int],
        now: float,
    ) -> str:
        """显示策略确认度、最坏探测上界和脚本累计时间。"""
        if self.total_ship_cells is None or self.total_ships is None:
            raise ValueError("策略进度需要潜艇总格数和潜艇总数")

        attempts = max(0, int(attempts))
        worst_remaining = max(0, self.max_probes - attempts)
        return (
            f"确认 {len(confirmed_lengths)}/{self.total_ships} "
            f"{sorted(confirmed_lengths)} | "
            f"探测 {attempts}/{self.max_probes} | "
            f"最坏剩余 {worst_remaining} | "
            f"剩余舰长 {sorted(remaining_lengths)} | "
            f"总运行 {format_elapsed(now - self.started_at)}"
        )

    def grid_postfix(
        self,
        *,
        completed: int,
        total: int,
        now: float,
    ) -> str:
        """显示逐格扫描剩余次数和脚本累计时间。"""
        remaining = max(0, total - completed)
        return (
            f"还需 {remaining} 次 | "
            f"总运行 {format_elapsed(now - self.started_at)}"
        )


__all__ = [
    "SearchProgress",
    "fixed_progress_bar",
    "format_elapsed",
    "update_fixed_progress",
]
