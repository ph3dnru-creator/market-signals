import unittest

from signals import evaluate, format_event
from datetime import datetime


BASE = {"btc": 86000, "eth": 2700, "usd": 83, "jpy": 157,
        "ndx": 26000, "ndx_drawdown": -3}


class SignalsTest(unittest.TestCase):
    def test_only_transitions_emit_events(self):
        initial, events = evaluate(BASE, {})
        self.assertEqual(events, [])
        same, events = evaluate(BASE, initial)
        self.assertEqual(events, [])
        changed = dict(BASE, usd=84.2)
        active, events = evaluate(changed, same)
        self.assertEqual([(x["key"], x["status"]) for x in events], [("usd_high", "active")])
        _, events = evaluate(BASE, active)
        self.assertEqual([(x["key"], x["status"]) for x in events], [("usd_high", "inactive")])

    def test_missing_optional_index_does_not_clear_signal(self):
        active, _ = evaluate(dict(BASE, ndx_drawdown=-21), {})
        next_state, events = evaluate(dict(BASE, ndx=None, ndx_drawdown=None), active)
        self.assertEqual(next_state["ndx"], "active")
        self.assertEqual(events, [])

    def test_ratio_does_not_flap_near_threshold(self):
        state, _ = evaluate(dict(BASE, eth=BASE["btc"] * .0306), {})
        for ratio in (.03101, .03099, .0311, .0309):
            state, events = evaluate(dict(BASE, eth=BASE["btc"] * ratio), state)
            self.assertFalse(any(e["key"] == "ethbtc" for e in events))
            self.assertEqual(state["ethbtc"], "inactive")
        state, events = evaluate(dict(BASE, eth=BASE["btc"] * .0314), state)
        self.assertEqual(state["ethbtc"], "active")
        self.assertTrue(any(e["key"] == "ethbtc" for e in events))

    def test_no_approved_action_means_no_message(self):
        self.assertIsNone(format_event({"key": "ethbtc", "status": "active"}, datetime.now()))

    def test_bad_quote_is_not_inactive_signal(self):
        with self.assertRaises(ValueError):
            evaluate(dict(BASE, btc=0), {})


if __name__ == "__main__":
    unittest.main()
