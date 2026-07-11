from __future__ import annotations

from dataclasses import dataclass


def format_elapsed(seconds: float) -> str:
    """把运行秒数格式化为 HH:MM:SS。"""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def render_progress_bar(current: int, total: int, *, width: int = 20) -> str:
    """生成不依赖终端控制符的 ASCII 进度条。"""
    if total <= 0:
        raise ValueError(f"进度总数必须大于 0: {total}")
    if width <= 0:
        raise ValueError(f"进度条宽度必须大于 0: {width}")

    current = min(max(int(current), 0), int(total))
    ratio = current / total
    filled = min(width, int(ratio * width))
    percent = int(ratio * 100)
    return f"[{'#' * filled}{'-' * (width - filled)}] {percent:3d}%"


@dataclass(frozen=True)
class SearchProgress:
    """生成一关探索阶段的可记录进度信息。"""

    level: int
    max_probes: int
    started_at: float
    total_ship_cells: int | None = None
    total_ships: int | None = None
    width: int = 20

    def strategy_message(
        self,
        *,
        attempts: int,
        hit_cells: int,
        confirmed_lengths: list[int],
        remaining_lengths: list[int],
        now: float,
    ) -> str:
        """输出有舰队配置时的命中进度和最坏探测上界。"""
        if self.total_ship_cells is None or self.total_ships is None:
            raise ValueError("策略进度需要潜艇总格数和潜艇总数")

        attempts = max(0, int(attempts))
        confirmed_count = len(confirmed_lengths)
        worst_remaining = max(0, self.max_probes - attempts)
        bar = render_progress_bar(
            hit_cells,
            self.total_ship_cells,
            width=self.width,
        )
        return (
            f"第 {self.level} 关探索 {bar} | "
            f"已找到潜艇格 {hit_cells}/{self.total_ship_cells} | "
            f"已确认潜艇 {confirmed_count}/{self.total_ships} {sorted(confirmed_lengths)} | "
            f"探测 {attempts}/{self.max_probes}（最坏还需 {worst_remaining} 次） | "
            f"剩余舰长 {sorted(remaining_lengths)} | "
            f"总运行 {format_elapsed(now - self.started_at)}"
        )

    def grid_message(
        self,
        *,
        completed: int,
        total: int,
        now: float,
    ) -> str:
        """输出无完整策略证明时的逐格扫描进度。"""
        bar = render_progress_bar(completed, total, width=self.width)
        remaining = max(0, total - completed)
        return (
            f"第 {self.level} 关逐格扫描 {bar} | "
            f"已扫描 {completed}/{total}（还需 {remaining} 次） | "
            f"总运行 {format_elapsed(now - self.started_at)}"
        )


__all__ = ["SearchProgress", "format_elapsed", "render_progress_bar"]
