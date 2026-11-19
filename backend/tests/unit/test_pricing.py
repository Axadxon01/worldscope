from __future__ import annotations

import pytest

from worldscope.ai.pricing import UnknownModelPriceError, cost_usd
from worldscope.ai.providers.base import Usage


def test_haiku_standard_rate() -> None:
    # 800 in x $0.10/M + 200 out x $0.50/M = $0.00018
    assert cost_usd("claude-haiku-5-5", Usage(input_tokens=800, output_tokens=200)) == 0.00018


def test_haiku_long_prompt_rate_applies_above_100k() -> None:
    usage = Usage(input_tokens=100_001, output_tokens=1_000)
    expected = (100_001 * 0.50 + 1_000 * 2.50) / 1_000_000
    assert cost_usd("claude-haiku-5-5", usage) == round(expected, 6)


def test_sonnet_with_batch_discount() -> None:
    usage = Usage(input_tokens=20_000, output_tokens=1_500)
    assert cost_usd("claude-sonnet-5-5", usage) == 0.055
    assert cost_usd("claude-sonnet-5-5", usage, batch=True) == 0.0275


def test_unknown_model_is_refused_not_costed_at_zero() -> None:
    with pytest.raises(UnknownModelPriceError):
        cost_usd("some-new-model", Usage(input_tokens=1, output_tokens=1))
