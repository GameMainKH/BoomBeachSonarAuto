import unittest

from utils.submarine_strategy import (
    SubmarineStrategy,
    get_configured_submarines,
    play_with_strategy,
)


def play_board(n, submarines, ships):
    ship_cells = {cell for ship in ships for cell in ship}
    strategy = SubmarineStrategy(n, submarines)
    steps = 0

    while not strategy.done and steps < n * n:
        cell = strategy.choose_next_cell()
        if cell is None:
            break

        strategy.report_result(cell, cell in ship_cells)
        steps += 1

    return strategy, steps


class SubmarineStrategyTest(unittest.TestCase):
    def test_finds_all_ships_before_full_scan(self):
        ships = [
            [(0, 0), (0, 1), (0, 2), (0, 3)],
            [(1, 7), (2, 7), (3, 7)],
            [(7, 3), (7, 4)],
        ]

        strategy, steps = play_board(8, [2, 3, 4], ships)

        self.assertTrue(strategy.done)
        self.assertLess(steps, 64)
        self.assertEqual(
            sorted(ship.length for ship in strategy.get_confirmed_ships()),
            [2, 3, 4],
        )

    def test_repeated_lengths_are_counted_independently(self):
        ships = [
            [(0, 0), (0, 1), (0, 2)],
            [(3, 6), (4, 6)],
            [(6, 0), (6, 1)],
        ]

        strategy, _ = play_board(7, [2, 2, 3], ships)

        self.assertTrue(strategy.done)
        self.assertEqual(
            sorted(ship.length for ship in strategy.get_confirmed_ships()),
            [2, 2, 3],
        )

    def test_confirmed_safety_area_is_not_selected_again(self):
        strategy = SubmarineStrategy(5, [2, 2])
        strategy.report_result((0, 0), True)
        strategy.report_result((0, 1), True)

        confirmed = strategy.get_confirmed_ships()
        self.assertEqual(len(confirmed), 1)

        safety = confirmed[0].safety_area
        for _ in range(10):
            cell = strategy.choose_next_cell()
            self.assertIsNotNone(cell)
            self.assertNotIn(cell, safety)
            strategy.report_result(cell, False)

    def test_repeated_result_is_idempotent_and_conflict_errors(self):
        strategy = SubmarineStrategy(4, [2])
        strategy.report_result((1, 1), True)
        strategy.report_result((1, 1), True)

        with self.assertRaises(ValueError):
            strategy.report_result((1, 1), False)

    def test_missing_level_config_returns_none_for_fallback(self):
        self.assertEqual(get_configured_submarines(1, {1: [2, 3]}), [2, 3])
        self.assertIsNone(get_configured_submarines(9, {1: [2, 3]}))

    def test_play_with_strategy_uses_callback(self):
        ships = [[(0, 0), (0, 1)]]
        ship_cells = {cell for ship in ships for cell in ship}

        confirmed = play_with_strategy(3, [2], lambda cell: cell in ship_cells)

        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].cells, ((0, 0), (0, 1)))


if __name__ == "__main__":
    unittest.main()
