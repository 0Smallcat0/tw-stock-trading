"""The registry row must describe the run that produced it."""

from __future__ import annotations

from decimal import Decimal

from src.backtest.runner import registry_parameters
from src.backtest.types import BacktestParameters


def _parameters(**overrides: object) -> BacktestParameters:
    base: dict[str, object] = {
        "risk_budgets": {"0050": Decimal("1")},
        "initial_cash": Decimal("100000"),
        "account_id": "paper-main",
        "commission_bps": Decimal("8.55"),
        "min_fee": Decimal("20"),
        "sell_tax_bps_etf": Decimal("10"),
        "sell_tax_bps_stock": Decimal("30"),
        "slippage_bps": Decimal("5"),
        "quantity_step": Decimal("1"),
        "min_notional_twd": Decimal("10000"),
        "max_drawdown_fraction": Decimal("0.40"),
        "daily_loss_pause_fraction": Decimal("0.095"),
        "disaster_single_day_drop_fraction": Decimal("0.09"),
        "disaster_multi_session_count": 3,
        "disaster_multi_session_drop_fraction": Decimal("0.15"),
        "stale_data_max_age_seconds": 345600,
    }
    base.update(overrides)
    return BacktestParameters(**base)  # type: ignore[arg-type]


def test_a_donchian_run_records_its_own_channels_not_sma_lookbacks() -> None:
    recorded = registry_parameters(
        _parameters(
            strategy_name="donchian_breakout_ensemble",
            dc_windows=(10, 20, 55, 110),
            dc_exit="mid_channel",
            dc_atr_window=14,
            dc_atr_multiple=Decimal("3"),
        )
    )

    assert recorded["dc_windows"] == "10,20,55,110"
    assert recorded["dc_exit"] == "mid_channel"
    # The defect this guards: trials 23 and 24 are on record claiming the SMA
    # ensemble's lookbacks while actually running Donchian channels.
    assert "lookbacks" not in recorded
    assert "ladder" not in recorded


def test_the_sma_ensemble_still_records_its_fixed_contract() -> None:
    recorded = registry_parameters(_parameters())

    assert recorded["lookbacks"] == "20,65,150,200"
    assert recorded["ladder"] == "0,0.25,0.5,0.75,1"
    assert "dc_windows" not in recorded
