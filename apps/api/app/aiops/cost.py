"""Cost of model calls (spec 6.6), from the LLM gateway's price table (`PRICES`), never invented.

A live call records what it cost (`cost_usd`). A mock call records 0 (nothing is billed), which would leave the
AI Ops page and the cost alerts with nothing to show in the demo; for mock traffic the page and the eval reports
therefore use the list-price estimate: the mock's token counts times the price of the model the call was made for
(the registered model of the agent's tier). Every figure says whether it is an estimate.
"""

from __future__ import annotations


def price_of(model: str | None) -> tuple[float, float] | None:
    from app.llm.gateway import PRICES

    return PRICES.get((model or "").removeprefix("mock:"))


def estimate(model: str | None, tokens_in: int, tokens_out: int) -> float:
    price = price_of(model)
    if price is None:
        return 0.0
    return (tokens_in * price[0] + tokens_out * price[1]) / 1e6


def call_cost(mode: str | None, model: str | None, cost_usd: float, tokens_in: int, tokens_out: int) -> tuple[float, bool]:
    """(USD, estimated): what a call cost, or for a mock call its list-price estimate."""
    if mode == "mock":
        return estimate(model, tokens_in, tokens_out), True
    return cost_usd or 0.0, False
