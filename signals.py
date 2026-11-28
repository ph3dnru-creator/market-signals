"""Чистая логика ценовых наблюдений для бота «Капитал».

Порог сам по себе не является поручением купить или продать актив.
Недельные закрытия, ETF, Strategy и BitMine здесь намеренно не оцениваются:
текущие источники не дают надёжной автоматической проверки их составных условий.
"""

import json
import math
from datetime import date, datetime
from pathlib import Path

ACTION_PLAN = Path(__file__).with_name("signal_actions.json")
STRATEGY = Path(__file__).with_name("video_strategy.json")


def load_plan():
    plan = json.loads(STRATEGY.read_text(encoding="utf-8"))["plan"]
    rows = plan["rows"]
    if not plan.get("version") or not isinstance(plan["broker_ready"], bool):
        raise ValueError("Invalid plan version or broker gate")
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate plan rows")
    for row in rows:
        budget, tranche = row["budget_rub"], row["first_tranche_rub"]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (budget, tranche)) or not 0 <= tranche <= budget:
            raise ValueError("Invalid plan budget")
        if row.get("symbol") and not 0 < row["min_price"] <= row["max_price"]:
            raise ValueError("Invalid entry range")
    risk_budget = sum(r["budget_rub"] for r in rows if r.get("symbol"))
    if not 0 <= risk_budget <= plan["theme_cap_rub"] <= plan["free_capital_rub"]:
        raise ValueError("Plan exceeds available budget")
    date.fromisoformat(plan["review_by"])
    return plan


def plan_snapshot(plan):
    # Dates, market quotes and source prose alone do not change an action.
    fields = ("id", "asset", "side", "budget_rub", "first_tranche_rub", "price", "when", "wait_for", "status")
    return {"rows": [{k: r[k] for k in fields} for r in plan["rows"]],
            "broker_ready": plan["broker_ready"], "review_by": plan["review_by"],
            "free_capital_rub": plan["free_capital_rub"], "obligations": plan["obligations"]}


def rub(value):
    return format(value, ",.0f").replace(",", " ") + " ₽"


def format_plan_change(plan, previous):
    current = plan_snapshot(plan)
    if previous == current:
        return None
    if previous is None:
        return ("📋 Капитал · обновлён план\n\n"
                "Сейчас: новых рискованных покупок 0 ₽.\n"
                f"Сохраняем ликвидно: {rub(plan['free_capital_rub'])}.\n\n"
                "Neo: первый транш 100 000 ₽ только при C$18–19.\n"
                "MP: первый транш 50 000 ₽ только при $22–25.\n"
                "До подтверждения брокера заявки не ставим. Общий лимит темы 450 000 ₽ включён в 3 млн ₽.\n\n"
                "Старые покупки валюты на 22 млн ₽ и крипты на 10,7 млн ₽ сняты с исполнения.\n"
                "Новые видео без конкретного изменения плана — без уведомлений.\n\n" + plan["dashboard_url"])
    old = {r["id"]: r for r in previous.get("rows", [])}
    changed = [r for r in current["rows"] if old.get(r["id"]) != r]
    lines = ["📋 Капитал · изменился план", ""]
    for row in changed:
        before = old.get(row["id"])
        lines += [row["asset"], "Было: " + (f"{before['side']}; первый транш {rub(before['first_tranche_rub'])}; {before['price']}" if before else "строки не было"),
                  f"Теперь: {row['side']}; первый транш {rub(row['first_tranche_rub'])}; {row['price']}",
                  "Ждём: " + row["wait_for"], ""]
    if previous.get("broker_ready") != current["broker_ready"]:
        lines += ["Брокер: " + ("маршрут подтверждён; дополнительно требуется цена входа." if plan["broker_ready"] else "покупки заблокированы до подтверждения маршрута."), ""]
    if previous.get("free_capital_rub") != current["free_capital_rub"]:
        lines += ["Свободный бюджет: " + rub(plan["free_capital_rub"]), ""]
    if previous.get("review_by") != current["review_by"]:
        lines += ["Срок пересмотра: " + plan["review_by"], ""]
    if previous.get("obligations") != current["obligations"]:
        lines += ["Обновлён график обязательных платежей — проверь резерв в плане.", ""]
    # Whole rows may exceed Telegram's limit; the dashboard holds the full diff.
    message = "\n".join(lines)
    return message[:3400] + "\n\n" + plan["dashboard_url"]


def evaluate_plan(plan, quotes, previous, now):
    statuses, events = {}, []
    for row in plan["rows"]:
        if not row.get("symbol"):
            continue
        quote = quotes.get(row["id"])
        if now.date() > date.fromisoformat(plan["review_by"]):
            status = "expired"
        elif not plan["broker_ready"]:
            status = "blocked"
        elif quote is None:
            status = "unknown"
        else:
            price = quote["price"]
            age = now.timestamp() - quote["timestamp"]
            if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0 or not -300 <= age <= 3 * 86400 or quote["currency"] != row["currency"]:
                status = "unknown"
            else:
                status = "ready" if row["min_price"] <= price <= row["max_price"] else "wait"
        # Missing/stale quotes never manufacture an exit from an entry condition.
        if status == "unknown":
            statuses[row["id"]] = previous.get(row["id"], "unknown")
            continue
        statuses[row["id"]] = status
        if status == "ready" and previous.get(row["id"]) != "ready":
            events.append({"id": row["id"], "row": row, "quote": quote})
    return statuses, events


def format_plan_entry(event):
    row, quote = event["row"], event["quote"]
    return (f"🔔 Капитал · выполнено ценовое условие\n\n{row['asset']}\n"
            f"Действие: проверить свежие новости и подготовить покупку на {rub(row['first_tranche_rub'])}.\n"
            f"Лимит цены: {row['price']}.\n"
            f"Цена источника: {quote['price']:.2f} {quote['currency']}.\n\n"
            f"Почему: {row['reason']}\n"
            "Остальные транши не покупать. Сделка автоматически не совершается; брокерская цена и комиссии проверяются перед заявкой.")

RULES = (
    ("btc_down", "BTC ниже $57 735", "btc", lambda q: q["btc"] < 57735,
     "Проверить сценарий второй волны; решение о траншах — после сверки плана."),
    ("btc_reserve", "BTC в зоне $48–53 тыс.", "btc", lambda q: 48000 <= q["btc"] <= 53000,
     "Проверить резерв, ликвидность и ограничения до любого решения о покупке."),
    ("eth_pause", "ETH ниже $1 750", "eth", lambda q: q["eth"] < 1750,
     "Пересмотреть график ETH-траншей."),
    ("eth_reserve", "ETH в зоне $1 200–1 500", "eth", lambda q: 1200 <= q["eth"] <= 1500,
     "Проверить резерв, ликвидность и ограничения до любого решения о покупке."),
    ("ethbtc", "ETH/BTC выше 0,031", "ethbtc", lambda q: q["eth"] / q["btc"] > 0.031,
     "Пересмотреть долю ETH в крипто-ядре; автоматически не ускорять транши."),
    ("usd_low", "USD/RUB ниже 79", "usd", lambda q: q["usd"] < 79,
     "Пересмотреть график валютных траншей после проверки доступного маршрута."),
    ("usd_high", "USD/RUB выше 84", "usd", lambda q: q["usd"] > 84,
     "Проверить паузу валютных траншей; сверить с полной политикой из разбора 05.08."),
    ("jpy", "USD/JPY ниже 150", "jpy", lambda q: q["jpy"] < 150,
     "Проверить риск carry-trade; одного курса недостаточно для вывода о ликвидациях."),
    ("ndx", "Nasdaq ниже пика на 20%", "ndx_drawdown",
     lambda q: q["ndx_drawdown"] is not None and q["ndx_drawdown"] <= -20,
     "Пересмотреть стресс-сценарий и длительность крипто-траншей."),
)


def evaluate(quotes, previous):
    if quotes["btc"] <= 0 or quotes["eth"] <= 0 or quotes["usd"] <= 0 or quotes["jpy"] <= 0:
        raise ValueError("Некорректная цена в источнике")
    values = dict(quotes)
    values["ethbtc"] = quotes["eth"] / quotes["btc"]
    statuses = {}
    events = []
    for key, title, field, predicate, action in RULES:
        if field == "ndx_drawdown" and values[field] is None:
            statuses[key] = previous.get(key, "unknown")
            continue
        status = "active" if predicate(values) else "inactive"
        old = previous.get(key)
        if key == "ethbtc" and old in ("active", "inactive"):
            # Dead band: tiny fluctuations must not reverse a confirmed state.
            ratio = values[field]
            status = ("active" if ratio >= 0.0313 else "inactive" if ratio <= 0.0307 else old)
        statuses[key] = status
        if old is not None and old not in ("unknown", status):
            events.append({"key": key, "title": title, "field": field,
                           "value": values[field], "old": old, "status": status,
                           "action": action})
    return statuses, events


def _value(field, value):
    if field in ("btc", "eth"):
        return "$%s" % format(value, ",.0f").replace(",", " ")
    if field == "ethbtc":
        return "%.6f" % value
    if field == "ndx_drawdown":
        return "%.1f%% от пика" % value
    return "%.2f" % value


def format_event(event, now):
    """Only approved, quantified actions warrant a Telegram notification."""
    plan = json.loads(ACTION_PLAN.read_text(encoding="utf-8"))
    action = plan.get(event["key"], {}).get(event["status"])
    if not action:
        return None
    required = ("side", "asset", "quantity", "price", "reason")
    if any(not isinstance(action.get(k), str) or not action[k].strip() for k in required):
        raise ValueError("Incomplete approved signal action")
    if action["side"] not in ("Купить", "Продать", "Приостановить покупки"):
        raise ValueError("Invalid signal action")
    return (f"🔔 Капитал · {action['asset']}\n\n"
            f"Действие: {action['side']}\n"
            f"Объём: {action['quantity']}\n"
            f"Цена / условие: {action['price']}\n\n"
            f"Почему: {action['reason']}\n"
            f"Рынок: {_value(event['field'], event['value'])}\n"
            f"{now.strftime('%d.%m.%Y %H:%M')} МСК")


def format_status(quotes, statuses, now):
    active = [title for key, title, _, _, _ in RULES if statuses.get(key) == "active"]
    return ("📍 Капитал · %s МСК\nBTC %s · ETH %s\nUSD/RUB %.2f · USD/JPY %.2f\n"
            "Активные ценовые условия: %s\n"
            "Недельные закрытия, ETF и корпоративные события требуют отдельной проверки."
            % (now.strftime("%d.%m.%Y %H:%M"), _value("btc", quotes["btc"]),
               _value("eth", quotes["eth"]), quotes["usd"], quotes["jpy"],
               ", ".join(active) if active else "нет"))
