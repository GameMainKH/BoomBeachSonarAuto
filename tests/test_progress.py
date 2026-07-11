import unittest

from utils.progress import SearchProgress, format_elapsed, render_progress_bar


class ProgressTest(unittest.TestCase):
    def test_format_elapsed_supports_long_runs(self):
        self.assertEqual(format_elapsed(0), "00:00:00")
        self.assertEqual(format_elapsed(3661.9), "01:01:01")
        self.assertEqual(format_elapsed(-1), "00:00:00")

    def test_render_progress_bar_clamps_values(self):
        self.assertEqual(render_progress_bar(5, 10, width=10), "[#####-----]  50%")
        self.assertEqual(render_progress_bar(20, 10, width=10), "[##########] 100%")
        self.assertEqual(render_progress_bar(-1, 10, width=10), "[----------]   0%")

    def test_strategy_message_contains_completion_and_time_estimate(self):
        progress = SearchProgress(
            level=11,
            max_probes=100,
            total_ship_cells=16,
            total_ships=5,
            started_at=100,
        )

        message = progress.strategy_message(
            attempts=12,
            hit_cells=4,
            confirmed_lengths=[2],
            remaining_lengths=[5, 2, 4, 3],
            now=165,
        )

        self.assertIn("[#####---------------]  25%", message)
        self.assertIn("已找到潜艇格 4/16", message)
        self.assertIn("已确认潜艇 1/5 [2]", message)
        self.assertIn("最坏还需 88 次", message)
        self.assertIn("剩余舰长 [2, 3, 4, 5]", message)
        self.assertIn("总运行 00:01:05", message)

    def test_grid_message_contains_remaining_count(self):
        progress = SearchProgress(level=3, max_probes=25, started_at=10)
        message = progress.grid_message(completed=3, total=12, now=20)
        self.assertIn("[#####---------------]  25%", message)
        self.assertIn("已扫描 3/12（还需 9 次）", message)
        self.assertIn("总运行 00:00:10", message)


if __name__ == "__main__":
    unittest.main()
