"""Token prices used for the AI cost ledger and the daily budget cap.

Prices are USD per million tokens, from
https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-11-20).
Update this table when prices change; unknown models are refused rather than
silently costed at zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from worldscope.ai.providers.base import Usage

MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    # Some models charge more when one prompt exceeds a token threshold.
    long_prompt_threshold: int | None = None
    long_input_per_mtok: float | None = None
    long_output_per_mtok: float | None = None


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        input_per_mtok=0.10,
        output_per_mtok=0.50,
        long_prompt_threshold=100_000,
        long_input_per_mtok=0.50,
        long_output_per_mtok=2.50,
    ),
    "claude-sonnet-5-5": Price(input_per_mtok=2.00, output_per_mtok=10.00),
    "claude-opus-5-5": Price(input_per_mtok=4.00, output_per_mtok=20.00),
}

BATCH_DISCOUNT = 0.5


class UnknownModelPriceError(KeyError):
    pass


def cost_usd(model: str, usage: Usage, *, batch: bool = False) -> float:
    try:
        price = PRICES[model]
    except KeyError as exc:
        raise UnknownModelPriceError(f"No price configured for model {model!r}") from exc

    in_rate, out_rate = price.input_per_mtok, price.output_per_mtok
    if price.long_prompt_threshold is not None and usage.input_tokens > price.long_prompt_threshold:
        in_rate = price.long_input_per_mtok or in_rate
        out_rate = price.long_output_per_mtok or out_rate

    total = (usage.input_tokens * in_rate + usage.output_tokens * out_rate) / MILLION
    if batch:
        total *= BATCH_DISCOUNT
    return round(total, 6)
