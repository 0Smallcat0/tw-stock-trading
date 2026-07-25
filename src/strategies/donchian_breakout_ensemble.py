"""Donchian Breakout Ensemble (experiment 7, backtest-only).

Pre-registered in docs/research/GOALP_EXPERIMENT7_PREREGISTRATION.md. Four
channel windows form a 5-rung exposure ladder: each window is an
independent state machine (breakout above the prior w-day close high turns
it ON; the exit rule turns it OFF), and the exposure fraction is the
ON-count over four. The live qualification runtime never runs this; its
contract is frozen on the original ensemble.
"""

from __future__ import annotations

from decimal import Decimal

from src.domain import Candle
from src.strategies.types import StrategyValidationError

EXIT_HALF_LOW = "half_low"
EXIT_MID_CHANNEL = "mid_channel"
EXIT_ATR_CHANNEL = "atr_channel"
EXIT_MODES = (EXIT_HALF_LOW, EXIT_MID_CHANNEL, EXIT_ATR_CHANNEL)


def average_true_range(candles: tuple[Candle, ...], index: int, window: int) -> Decimal | None:
    """Wilder true range averaged over the trailing window, ending at index.

    True range uses the prior close, so the first usable index is 1; None
    means warmup (callers must not substitute a guessed level).
    """

    start = index - window + 1
    if window < 1 or start < 1:
        return None
    total = Decimal("0")
    for position in range(start, index + 1):
        candle = candles[position]
        previous_close = candles[position - 1].close_price
        total += max(
            candle.high_price - candle.low_price,
            abs(candle.high_price - previous_close),
            abs(candle.low_price - previous_close),
        )
    return total / Decimal(window)


def evaluate_donchian_ensemble(
    candles: tuple[Candle, ...],
    index: int,
    *,
    windows: tuple[int, ...],
    exit_mode: str,
    previous_states: tuple[bool, ...] | None = None,
    atr_window: int = 14,
    atr_multiple: Decimal = Decimal("3"),
) -> tuple[Decimal, tuple[str, ...], tuple[bool, ...]]:
    """One decision from closes up to and including the decision close.

    Per window w: OFF->ON when close strictly exceeds the max close of the
    prior w days; ON->OFF when close falls below the exit level (min close
    of the prior ceil(w/2) days for half_low; midpoint of the prior w-day
    close range for mid_channel). Warmup (fewer than w prior closes) keeps
    a window OFF. State is explicit and carried by the caller, preserving
    replay determinism.
    """

    if exit_mode not in EXIT_MODES:
        msg = f"unknown donchian exit_mode: {exit_mode}"
        raise StrategyValidationError(msg)
    if len(windows) != 4:
        msg = "donchian ensemble needs exactly four windows"
        raise StrategyValidationError(msg)
    states = previous_states if previous_states is not None else (False,) * 4
    if len(states) != 4:
        msg = "previous_states must carry four window states"
        raise StrategyValidationError(msg)

    close = candles[index].close_price
    new_states: list[bool] = []
    for window, was_on in zip(windows, states):
        if index < window:
            new_states.append(False)
            continue
        prior_closes = [candles[i].close_price for i in range(index - window, index)]
        if was_on:
            if exit_mode == EXIT_HALF_LOW:
                half = (window + 1) // 2
                exit_level = min(candles[i].close_price for i in range(index - half, index))
            elif exit_mode == EXIT_ATR_CHANNEL:
                # Chandelier off the channel high: give the trend room
                # proportional to how noisy the tape currently is. Warmup
                # falls back to the mid-channel level rather than guessing.
                atr = average_true_range(candles, index, atr_window)
                if atr is None:
                    exit_level = (max(prior_closes) + min(prior_closes)) / Decimal("2")
                else:
                    exit_level = max(prior_closes) - atr_multiple * atr
            else:
                exit_level = (max(prior_closes) + min(prior_closes)) / Decimal("2")
            new_states.append(close >= exit_level)
        else:
            new_states.append(close > max(prior_closes))

    on_count = sum(new_states)
    fraction = Decimal(on_count) / Decimal("4")
    reason_codes = (
        "DONCHIAN_ENSEMBLE",
        f"WINDOWS_ON_{on_count}_OF_4",
        f"EXIT_{exit_mode.upper()}",
    )
    return fraction, reason_codes, tuple(new_states)
