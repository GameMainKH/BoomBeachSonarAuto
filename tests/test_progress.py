import io
import unittest

from utils.progress import (
    SearchProgress,
    fixed_progress_bar,
    format_elapsed,
    update_fixed_progress,
)


class ProgressTest(unittest.TestCase):
    def test_format_elapsed_supports_long_runs(self):
        self.assertEqual(format_elapsed(0), "00:00:00")
        self.assertEqual(format_elapsed(3661.9), "01:01:01")
        self.assertEqual(format_elapsed(-1), "00:00:00")

    def test_strategy_postfix_contains_completion_and_time_estimate(self):
        progress = SearchProgress(
            level=11,
            max_probes=100,
            total_ship_cells=16,
            total_ships=5,
            started_at=100,
        )

        postfix = progress.strategy_postfix(
            attempts=12,
            confirmed_lengths=[2],
            remaining_lengths=[5, 2, 4, 3],
            now=165,
        )

        self.assertIn("确认 1/5 [2]", postfix)
        self.assertIn("探测 12/100", postfix)
        self.assertIn("最坏剩余 88", postfix)
        self.assertIn("剩余舰长 [2, 3, 4, 5]", postfix)
        self.assertIn("总运行 00:01:05", postfix)

    def test_grid_postfix_contains_remaining_count(self):
        progress = SearchProgress(level=3, max_probes=25, started_at=10)
        postfix = progress.grid_postfix(completed=3, total=12, now=20)
        self.assertIn("还需 9 次", postfix)
        self.assertIn("总运行 00:00:10", postfix)

    def test_fixed_progress_renders_one_dynamic_bar(self):
        output = io.StringIO()
        with fixed_progress_bar(
            total=4,
            description="第 2 关探索",
            unit="格",
            file=output,
            disable=False,
        ) as bar:
            update_fixed_progress(bar, 2, "确认 1/2 | 总运行 00:00:05")

        rendered = output.getvalue()
        self.assertIn("第 2 关探索", rendered)
        self.assertIn("50%", rendered)
        self.assertIn("2/4格", rendered)
        self.assertIn("确认 1/2", rendered)

    def test_fixed_progress_clamps_absolute_updates(self):
        output = io.StringIO()
        with fixed_progress_bar(
            total=2,
            description="测试",
            unit="格",
            file=output,
            disable=False,
        ) as bar:
            update_fixed_progress(bar, 9, "done")
            self.assertEqual(bar.n, 2)


if __name__ == "__main__":
    unittest.main()
