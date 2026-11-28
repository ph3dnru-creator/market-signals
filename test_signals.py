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

class PlanSignalsTest(unittest.TestCase):
    def setUp(self):
        from signals import load_plan
        from zoneinfo import ZoneInfo
        self.plan = load_plan()
        self.now = datetime(2026, 11, 28, 10, tzinfo=ZoneInfo('Europe/Moscow'))
        self.quote = {'neo': {'price': 18.5, 'currency': 'CAD', 'timestamp': self.now.timestamp()}}

    def test_broker_gate_blocks_even_at_entry_price(self):
        from signals import evaluate_plan
        statuses, events = evaluate_plan(self.plan, self.quote, {}, self.now)
        self.assertEqual(statuses['neo'], 'blocked')
        self.assertEqual(events, [])

    def test_fresh_price_enters_once_and_stale_does_not_reverse(self):
        from signals import evaluate_plan
        import copy
        plan = copy.deepcopy(self.plan); plan['broker_ready'] = True
        statuses, events = evaluate_plan(plan, self.quote, {}, self.now)
        self.assertEqual([e['id'] for e in events], ['neo'])
        _, events = evaluate_plan(plan, self.quote, statuses, self.now)
        self.assertEqual(events, [])
        stale = {'neo': dict(self.quote['neo'], timestamp=self.now.timestamp()-4*86400, price=22)}
        next_state, events = evaluate_plan(plan, stale, statuses, self.now)
        self.assertEqual(next_state['neo'], 'ready')
        self.assertEqual(events, [])

    def test_bad_currency_nan_and_expired_conditions_never_buy(self):
        from signals import evaluate_plan
        import copy
        plan = copy.deepcopy(self.plan); plan['broker_ready'] = True
        for quote in (dict(self.quote['neo'], currency='USD'), dict(self.quote['neo'], price=float('nan'))):
            _, events = evaluate_plan(plan, {'neo':quote}, {}, self.now)
            self.assertEqual(events, [])
        plan['review_by']='2026-11-27'
        _, events = evaluate_plan(plan, self.quote, {}, self.now)
        self.assertEqual(events, [])

    def test_quotes_and_review_prose_do_not_make_plan_diff(self):
        from signals import plan_snapshot, format_plan_change
        import copy
        snapshot = plan_snapshot(self.plan)
        plan = copy.deepcopy(self.plan); plan['rows'][1]['reference_price']=99
        plan['review_register'].append({'title':'another opinion'})
        self.assertIsNone(format_plan_change(plan, snapshot))
        plan['rows'][1]['first_tranche_rub']=50000
        self.assertIn('50 000 ₽',format_plan_change(plan,snapshot))
