from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterable, Mapping, Optional, Sequence


Cell = tuple[int, int]


@dataclass(frozen=True)
class Placement:
    """一艘潜艇在当前信息下的一个可能摆放方案。"""

    length: int
    direction: str
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class ConfirmedShip:
    """已经根据命中反馈唯一确认的潜艇及其安全区。"""

    length: int
    direction: str
    cells: tuple[Cell, ...]
    safety_area: FrozenSet[Cell]


def get_configured_submarines(
    level: int,
    configs: Mapping[int, Sequence[int]],
) -> list[int] | None:
    """读取关卡潜艇长度配置；缺少配置时返回 None 供主流程回退逐格扫描。"""
    submarines = configs.get(int(level))
    if submarines is None:
        return None
    return [int(length) for length in submarines]


class SubmarineStrategy:
    """根据命中/未命中反馈选择下一格，尽量减少实际探测次数。"""

    def __init__(
        self,
        n: int,
        submarines: Sequence[int],
        use_safety_rule: bool = True,
    ) -> None:
        """初始化 N x N 棋盘策略，并记录待确认的潜艇长度计数。"""
        if n <= 0:
            raise ValueError("n must be positive")

        lengths = [int(length) for length in submarines]
        if not lengths:
            raise ValueError("submarines cannot be empty")
        if any(length <= 0 for length in lengths):
            raise ValueError("submarine lengths must be positive")

        self.n = int(n)
        self.remaining = Counter(lengths)
        self.use_safety_rule = use_safety_rule
        self.shots: dict[Cell, bool] = {}
        self.confirmed_ships: list[ConfirmedShip] = []
        self.blocked_cells: set[Cell] = set()
        self._hunt_residue_cache: dict[int, int] = {}

    @property
    def done(self) -> bool:
        """是否已经确认配置中的全部潜艇。"""
        return sum(self.remaining.values()) == 0

    def report_result(self, cell: Cell, hit: bool) -> None:
        """记录一次真实探测反馈，并尝试根据新信息确认潜艇。"""
        self._validate_cell(cell)

        if cell in self.shots:
            old = self.shots[cell]
            if old != hit:
                raise ValueError(f"conflicting result for cell {cell}: old={old}, new={hit}")
            return

        self.shots[cell] = bool(hit)
        self._try_confirm_ships()

    def choose_next_cell(self) -> Optional[Cell]:
        """返回下一次建议探测的格子；全部潜艇确认后返回 None。"""
        self._try_confirm_ships()

        if self.done:
            return None

        target = self._choose_target_cell()
        if target is not None:
            return target

        return self._choose_hunt_cell()

    def get_confirmed_ships(self) -> list[ConfirmedShip]:
        """返回已经确认完整位置的潜艇列表副本。"""
        return list(self.confirmed_ships)

    def get_debug_board(self) -> list[str]:
        """生成文本调试棋盘，用于观察命中、未命中、已确认和安全区状态。"""
        confirmed_cells = set()
        for ship in self.confirmed_ships:
            confirmed_cells.update(ship.cells)

        board = []
        for row in range(self.n):
            chars = []
            for col in range(self.n):
                cell = (row, col)
                if cell in confirmed_cells:
                    chars.append("S")
                elif self.shots.get(cell) is True:
                    chars.append("X")
                elif self.shots.get(cell) is False:
                    chars.append(".")
                elif cell in self.blocked_cells:
                    chars.append("-")
                else:
                    chars.append("?")
            board.append("".join(chars))
        return board

    def _validate_cell(self, cell: Cell) -> None:
        """校验格子坐标是否位于当前棋盘内。"""
        row, col = cell
        if not (0 <= row < self.n and 0 <= col < self.n):
            raise ValueError(f"cell out of bounds: {cell}")

    def _inside(self, cell: Cell) -> bool:
        """判断格子坐标是否在当前棋盘范围内。"""
        row, col = cell
        return 0 <= row < self.n and 0 <= col < self.n

    def _neighbors4(self, cell: Cell) -> Iterable[Cell]:
        """枚举一个格子的上下左右四连通邻居。"""
        row, col = cell
        for next_cell in (
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ):
            if self._inside(next_cell):
                yield next_cell

    def _unconfirmed_hit_cells(self) -> set[Cell]:
        """返回已命中但尚未归属到确认潜艇的格子集合。"""
        confirmed_cells = set()
        for ship in self.confirmed_ships:
            confirmed_cells.update(ship.cells)

        return {
            cell
            for cell, hit in self.shots.items()
            if hit and cell not in confirmed_cells
        }

    def _miss_cells(self) -> set[Cell]:
        """返回所有已探测且判定为未命中的格子。"""
        return {cell for cell, hit in self.shots.items() if not hit}

    def _get_hit_clusters(self) -> list[set[Cell]]:
        """按四连通关系聚合未确认命中格，较大的命中簇优先处理。"""
        hits = self._unconfirmed_hit_cells()
        visited: set[Cell] = set()
        clusters: list[set[Cell]] = []

        for start in hits:
            if start in visited:
                continue

            queue = deque([start])
            visited.add(start)
            cluster = {start}

            while queue:
                current = queue.popleft()
                for next_cell in self._neighbors4(current):
                    if next_cell in hits and next_cell not in visited:
                        visited.add(next_cell)
                        cluster.add(next_cell)
                        queue.append(next_cell)

            clusters.append(cluster)

        clusters.sort(key=lambda item: -len(item))
        return clusters

    def _all_placements(self, length: int) -> list[Placement]:
        """生成指定长度潜艇在当前已知信息下仍可能存在的全部位置。"""
        invalid = self._miss_cells() | self.blocked_cells
        result: list[Placement] = []
        seen: set[tuple[Cell, ...]] = set()

        for row in range(self.n):
            for col_start in range(self.n - length + 1):
                cells = tuple((row, col) for col in range(col_start, col_start + length))
                if any(cell in invalid for cell in cells):
                    continue
                if cells not in seen:
                    seen.add(cells)
                    result.append(Placement(length=length, direction="H", cells=cells))

        for col in range(self.n):
            for row_start in range(self.n - length + 1):
                cells = tuple((row, col) for row in range(row_start, row_start + length))
                if any(cell in invalid for cell in cells):
                    continue
                if cells not in seen:
                    seen.add(cells)
                    result.append(Placement(length=length, direction="V", cells=cells))

        return result

    def _candidate_placements_for_cluster(self, cluster: set[Cell]) -> list[Placement]:
        """找出所有能覆盖指定命中簇的剩余潜艇摆放方案。"""
        candidates: list[Placement] = []
        cluster_cells = frozenset(cluster)

        for length, count in self.remaining.items():
            if count <= 0:
                continue

            for placement in self._all_placements(length):
                if cluster_cells.issubset(frozenset(placement.cells)):
                    candidates.append(placement)

        return candidates

    def _try_confirm_ships(self) -> None:
        """当某个命中簇只剩唯一完整解释时，确认潜艇并屏蔽安全区。"""
        changed = True

        while changed:
            changed = False
            for cluster in self._get_hit_clusters():
                candidates = self._candidate_placements_for_cluster(cluster)
                if len(candidates) != 1:
                    continue

                placement = candidates[0]
                if not all(self.shots.get(cell) is True for cell in placement.cells):
                    continue
                if self.remaining[placement.length] <= 0:
                    continue

                safety = self._calc_safety_area(placement)
                self.confirmed_ships.append(
                    ConfirmedShip(
                        length=placement.length,
                        direction=placement.direction,
                        cells=placement.cells,
                        safety_area=frozenset(safety),
                    )
                )

                self.remaining[placement.length] -= 1
                if self.remaining[placement.length] == 0:
                    del self.remaining[placement.length]

                if self.use_safety_rule:
                    self.blocked_cells.update(safety)
                else:
                    self.blocked_cells.update(placement.cells)

                changed = True
                break

    def _calc_safety_area(self, placement: Placement) -> set[Cell]:
        """按上浮规则计算潜艇周围一圈安全区，并裁剪到棋盘范围内。"""
        rows = [row for row, _ in placement.cells]
        cols = [col for _, col in placement.cells]
        area: set[Cell] = set()

        for row in range(min(rows) - 1, max(rows) + 2):
            for col in range(min(cols) - 1, max(cols) + 2):
                cell = (row, col)
                if self._inside(cell):
                    area.add(cell)

        return area

    def _choose_target_cell(self) -> Optional[Cell]:
        """命中后进入追击模式，优先选择能最快确认方向和长度的邻近格。"""
        clusters = self._get_hit_clusters()
        if not clusters:
            return None

        best_cell: Optional[Cell] = None
        best_score = -1.0

        for cluster in clusters:
            candidates = self._candidate_placements_for_cluster(cluster)
            if not candidates:
                continue

            freq: Counter[Cell] = Counter()
            for placement in candidates:
                for cell in placement.cells:
                    if cell in self.shots or cell in self.blocked_cells:
                        continue
                    freq[cell] += 1

            if not freq:
                continue

            frontier = {
                next_cell
                for hit_cell in cluster
                for next_cell in self._neighbors4(hit_cell)
                if next_cell in freq
            }
            selectable = frontier if frontier else set(freq.keys())

            for cell in selectable:
                score = float(freq[cell])
                if cell in frontier:
                    score += 100.0
                score += self._center_bonus(cell)

                if score > best_score:
                    best_score = score
                    best_cell = cell

        return best_cell

    def _choose_hunt_cell(self) -> Optional[Cell]:
        """未处于追击模式时，用候选段热力图和最短潜艇跳格规则巡航。"""
        if not self.remaining:
            return None

        heat: Counter[Cell] = Counter()
        for length, count in self.remaining.items():
            for placement in self._all_placements(length):
                for cell in placement.cells:
                    if cell not in self.shots and cell not in self.blocked_cells:
                        heat[cell] += count

        if not heat:
            return self._fallback_unshot_cell()

        min_len = min(self.remaining.keys())
        if min_len not in self._hunt_residue_cache:
            residue_scores = Counter()
            for cell, value in heat.items():
                row, col = cell
                residue_scores[(row + col) % min_len] += value
            self._hunt_residue_cache[min_len] = residue_scores.most_common(1)[0][0]

        residue = self._hunt_residue_cache[min_len]
        best_cell: Optional[Cell] = None
        best_score = -1.0

        for cell, value in heat.items():
            row, col = cell
            if (row + col) % min_len != residue:
                continue

            score = float(value) + self._center_bonus(cell)
            if score > best_score:
                best_score = score
                best_cell = cell

        if best_cell is not None:
            return best_cell

        for cell, value in heat.items():
            score = float(value) + self._center_bonus(cell)
            if score > best_score:
                best_score = score
                best_cell = cell

        return best_cell

    def _center_bonus(self, cell: Cell) -> float:
        """给靠近中心的格子极小加权，用于同分时减少边界优先级。"""
        row, col = cell
        center = (self.n - 1) / 2
        distance = abs(row - center) + abs(col - center)
        return -distance * 0.001

    def _fallback_unshot_cell(self) -> Optional[Cell]:
        """热力图无解时，按行优先返回第一个未探测且未屏蔽格子。"""
        for row in range(self.n):
            for col in range(self.n):
                cell = (row, col)
                if cell not in self.shots and cell not in self.blocked_cells:
                    return cell
        return None


def play_with_strategy(
    n: int,
    submarines: Sequence[int],
    fire_once: Callable[[Cell], bool],
    max_steps: int | None = None,
) -> list[ConfirmedShip]:
    """用回调执行完整策略循环，主要供纯逻辑测试或外部接入复用。"""
    strategy = SubmarineStrategy(n=n, submarines=submarines)
    limit = max_steps if max_steps is not None else n * n

    for _ in range(limit):
        if strategy.done:
            break

        cell = strategy.choose_next_cell()
        if cell is None:
            break

        strategy.report_result(cell, fire_once(cell))

    return strategy.get_confirmed_ships()
