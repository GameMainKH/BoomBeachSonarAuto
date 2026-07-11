import unittest

from utils.probe_protocol import (
    ProbePhase,
    ProbeProtocolError,
    ProbeTransaction,
)


class ProbeProtocolTest(unittest.TestCase):
    def test_transaction_accepts_only_the_verified_protocol_order(self):
        transaction = ProbeTransaction(level=1, cell=(0, 0), index=0)

        for phase in (
            ProbePhase.REQUEST_PENDING,
            ProbePhase.RESULT_VISIBLE,
            ProbePhase.RESULT_RECORDED,
            ProbePhase.REQUEST_DISCARDED,
            ProbePhase.LOGIN_RECOVERING,
            ProbePhase.COMPLETE,
        ):
            transaction.advance(phase)

        self.assertEqual(transaction.phase, ProbePhase.COMPLETE)
        self.assertFalse(transaction.request_may_be_pending)

    def test_transaction_rejects_skipping_request_discard(self):
        transaction = ProbeTransaction(level=1, cell=(0, 0), index=0)
        transaction.advance(ProbePhase.REQUEST_PENDING)
        transaction.advance(ProbePhase.RESULT_VISIBLE)
        transaction.advance(ProbePhase.RESULT_RECORDED)

        with self.assertRaisesRegex(ProbeProtocolError, "非法探测状态转换"):
            transaction.advance(ProbePhase.LOGIN_RECOVERING)

        self.assertTrue(transaction.request_may_be_pending)


if __name__ == "__main__":
    unittest.main()
