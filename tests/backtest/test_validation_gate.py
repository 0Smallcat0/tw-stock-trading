from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.backtest import (
    BacktestError,
    BacktestParameters,
    HoldoutViolationError,
    ValidationInputError,
    append_trial,
    config_hash_for,
    deflated_sharpe_ratio,
    initialize_holdout,
    load_holdout,
    load_trials,
    probability_of_backtest_overfitting,
    require_data_allowed,
    run_registered_backtest,
    sharpe_ratio,
    spend_holdout,
    trial_count,
)
from src.domain import Candle, Symbol, Timeframe

_BASE_OPEN_TIME = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _returns_with_drift() -> list[float]:
    return [0.01 if index % 2 == 0 else -0.004 for index in range(120)]


def test_sharpe_ratio_of_constant_returns_is_zero() -> None:
    assert sharpe_ratio([0.01] * 50) == 0.0


def test_deflated_sharpe_shrinks_as_trials_accumulate() -> None:
    returns = _returns_with_drift()

    single_trial = deflated_sharpe_ratio(returns, trial_sharpe_variance=0.0, effective_trials=1)
    many_trials = deflated_sharpe_ratio(returns, trial_sharpe_variance=0.05, effective_trials=200)

    assert single_trial.expected_max_sharpe == 0.0
    assert many_trials.expected_max_sharpe > 0.0
    assert many_trials.deflated_sharpe_ratio < single_trial.deflated_sharpe_ratio


def test_deflated_sharpe_rejects_bad_inputs() -> None:
    with pytest.raises(ValidationInputError):
        deflated_sharpe_ratio([0.01, 0.02], trial_sharpe_variance=0.0, effective_trials=1)
    with pytest.raises(ValidationInputError):
        deflated_sharpe_ratio(_returns_with_drift(), trial_sharpe_variance=-0.1, effective_trials=1)
    with pytest.raises(ValidationInputError):
        deflated_sharpe_ratio(_returns_with_drift(), trial_sharpe_variance=0.0, effective_trials=0)


def test_pbo_is_zero_when_one_strategy_dominates_everywhere() -> None:
    rows = []
    for index in range(80):
        wiggle = 0.001 if index % 2 == 0 else -0.001
        rows.append([0.02 + wiggle, wiggle, wiggle - 0.0005])
    result = probability_of_backtest_overfitting(rows, block_count=8)

    assert result.pbo == 0.0
    assert result.combinations_evaluated == math.comb(8, 4)


def test_pbo_detects_regime_flipping_strategies() -> None:
    # Two strategies that alternate dominance between the halves of the sample:
    # whatever wins in-sample tends to lose out-of-sample.
    rows = []
    for index in range(80):
        if index < 40:
            rows.append([0.01, -0.01])
        else:
            rows.append([-0.01, 0.01])
    result = probability_of_backtest_overfitting(rows, block_count=8)

    assert result.pbo >= 0.4


def test_pbo_with_sixteen_blocks_evaluates_all_symmetric_splits() -> None:
    rows = [[0.01 * ((index + column) % 3 - 1) for column in range(2)] for index in range(16)]
    result = probability_of_backtest_overfitting(rows, block_count=16)

    assert result.combinations_evaluated == math.comb(16, 8)


def test_pbo_rejects_bad_inputs() -> None:
    with pytest.raises(ValidationInputError):
        probability_of_backtest_overfitting([[0.01, 0.02]] * 4, block_count=7)
    with pytest.raises(ValidationInputError):
        probability_of_backtest_overfitting([[0.01]] * 32, block_count=8)
    with pytest.raises(ValidationInputError):
        probability_of_backtest_overfitting([[0.01, 0.02]] * 4, block_count=8)


def test_trial_registry_assigns_monotonic_ids_and_round_trips(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.jsonl"

    first = append_trial(
        registry_path,
        recorded_at=_NOW,
        config_hash="hash-a",
        code_version="abc1234",
        strategy_id="daily_trend_ensemble",
        parameters={"lookbacks": "20,65,150,200"},
        universe=("BTCUSDT", "ETHUSDT"),
        data_start=_NOW - timedelta(days=400),
        data_end=_NOW - timedelta(days=10),
        cost_assumptions={"fee_bps": "10"},
        metrics={"annualized_sharpe": "1.1"},
        operator_note="first",
    )
    second = append_trial(
        registry_path,
        recorded_at=_NOW,
        config_hash="hash-a",
        code_version="abc1234",
        strategy_id="daily_trend_ensemble",
        parameters={"lookbacks": "20,65,150,200"},
        universe=("BTCUSDT", "ETHUSDT"),
        data_start=_NOW - timedelta(days=400),
        data_end=_NOW - timedelta(days=10),
        cost_assumptions={"fee_bps": "20"},
        metrics={"annualized_sharpe": "0.9"},
        operator_note="cost stress",
    )

    assert (first.trial_id, second.trial_id) == (1, 2)
    assert trial_count(registry_path) == 2
    loaded = load_trials(registry_path)
    assert loaded == (first, second)


def test_config_hash_is_deterministic_and_content_sensitive() -> None:
    snapshot_a = {"strategy": {"name": "daily_trend_ensemble"}, "version": "1"}
    snapshot_b = {"version": "1", "strategy": {"name": "daily_trend_ensemble"}}
    snapshot_c = {"strategy": {"name": "large_liquid_trend_15"}, "version": "1"}

    assert config_hash_for(snapshot_a) == config_hash_for(snapshot_b)
    assert config_hash_for(snapshot_a) != config_hash_for(snapshot_c)


def test_holdout_lock_is_single_use(tmp_path: Path) -> None:
    lock_path = tmp_path / "holdout.json"
    holdout_start = _NOW - timedelta(days=365)

    state = initialize_holdout(lock_path, holdout_start=holdout_start, locked_at=_NOW)
    assert state.spent is False

    # Regular runs must stay strictly before the holdout.
    require_data_allowed(lock_path, data_end=holdout_start - timedelta(days=1))
    with pytest.raises(HoldoutViolationError, match="locked holdout"):
        require_data_allowed(lock_path, data_end=holdout_start + timedelta(days=1))

    spent = spend_holdout(lock_path, spent_at=_NOW)
    assert spent.spent is True
    with pytest.raises(HoldoutViolationError, match="already spent"):
        spend_holdout(lock_path, spent_at=_NOW + timedelta(hours=1))
    # Even after the spend, holdout data stays off-limits to regular runs.
    with pytest.raises(HoldoutViolationError, match="SPENT"):
        require_data_allowed(lock_path, data_end=holdout_start + timedelta(days=1))

    # Re-initializing never resets an existing lock.
    again = initialize_holdout(lock_path, holdout_start=_NOW - timedelta(days=30), locked_at=_NOW)
    assert again.holdout_start == holdout_start
    assert again.spent is True


def _flat_universe(days: int) -> dict[str, tuple[Candle, ...]]:
    def candles(symbol: Symbol) -> tuple[Candle, ...]:
        result = []
        for index in range(days):
            open_time = _BASE_OPEN_TIME + timedelta(days=index)
            price = Decimal("100")
            result.append(
                Candle(
                    symbol=symbol,
                    timeframe=Timeframe("1d"),
                    open_time=open_time,
                    close_time=open_time + timedelta(days=1) - timedelta(milliseconds=1),
                    open_price=price,
                    high_price=price + Decimal("1"),
                    low_price=price - Decimal("1"),
                    close_price=price,
                    volume=Decimal("1000"),
                    is_closed=True,
                )
            )
        return tuple(result)

    return {
        "BTCUSDT": candles(Symbol(value="BTCUSDT", base_asset="BTC", quote_asset="USDT")),
        "ETHUSDT": candles(Symbol(value="ETHUSDT", base_asset="ETH", quote_asset="USDT")),
    }


def _parameters() -> BacktestParameters:
    return BacktestParameters(
        risk_budgets={"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal("0.5")},
        initial_cash=Decimal("1000"),
        account_id="bt-test",
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        quantity_step=Decimal("0.000001"),
        price_tick=Decimal("0.01"),
        min_notional_usdt=Decimal("10"),
        max_drawdown_fraction=Decimal("0.20"),
        daily_loss_pause_fraction=Decimal("0.05"),
        disaster_single_day_drop_fraction=Decimal("0.20"),
        stale_data_max_age_seconds=129600,
    )


def test_registered_run_locks_holdout_trims_data_and_registers_trials(
    tmp_path: Path,
) -> None:
    universe = _flat_universe(620)
    registry_path = tmp_path / "registry.jsonl"
    holdout_path = tmp_path / "holdout.json"
    reports_dir = tmp_path / "reports"

    first = run_registered_backtest(
        universe,
        parameters=_parameters(),
        config_hash="hash-a",
        code_version="abc1234",
        registry_path=registry_path,
        holdout_path=holdout_path,
        reports_directory=reports_dir,
        recorded_at=_NOW,
        operator_note="first registered run",
    )

    lock = load_holdout(holdout_path)
    assert lock is not None
    data_end = max(candles[-1].close_time for candles in universe.values())
    assert lock.holdout_start == data_end - timedelta(days=365)
    # The run never saw holdout data.
    assert first.report.data_end < lock.holdout_start
    assert first.trial.trial_id == 1
    assert first.report_path.exists()

    second = run_registered_backtest(
        universe,
        parameters=_parameters(),
        config_hash="hash-a",
        code_version="abc1234",
        registry_path=registry_path,
        holdout_path=holdout_path,
        reports_directory=reports_dir,
        recorded_at=_NOW + timedelta(hours=1),
        operator_note="second registered run",
    )
    assert second.trial.trial_id == 2
    assert trial_count(registry_path) == 2


def test_holdout_spend_run_is_single_use_and_marked_in_registry(tmp_path: Path) -> None:
    universe = _flat_universe(620)
    registry_path = tmp_path / "registry.jsonl"
    holdout_path = tmp_path / "holdout.json"
    reports_dir = tmp_path / "reports"

    qualification = run_registered_backtest(
        universe,
        parameters=_parameters(),
        config_hash="hash-a",
        code_version="abc1234",
        registry_path=registry_path,
        holdout_path=holdout_path,
        reports_directory=reports_dir,
        recorded_at=_NOW,
        operator_note="goal O qualification",
        spend_holdout_single_use=True,
    )

    assert qualification.holdout.spent is True
    assert qualification.trial.parameters["holdout_spend"] == "True"
    # The spend run sees the full data range.
    data_end = max(candles[-1].close_time for candles in universe.values())
    assert qualification.report.data_end == data_end

    with pytest.raises(HoldoutViolationError, match="already spent"):
        run_registered_backtest(
            universe,
            parameters=_parameters(),
            config_hash="hash-a",
            code_version="abc1234",
            registry_path=registry_path,
            holdout_path=holdout_path,
            reports_directory=reports_dir,
            recorded_at=_NOW + timedelta(hours=2),
            spend_holdout_single_use=True,
        )


def test_non_annualized_sharpe_variance_deannualizes_registry_values() -> None:
    from src.backtest import non_annualized_sharpe_variance

    annualized = [1.02, 0.96, -0.37]
    variance = non_annualized_sharpe_variance(annualized)

    import statistics

    assert variance == pytest.approx(statistics.pvariance(annualized) / 365)
    assert non_annualized_sharpe_variance([1.0]) == 0.0


def test_dsr_raises_instead_of_fabricating_certainty_on_extreme_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With population moments the Mertens term is bounded below by
    # (1 - skew*SR/2)^2 >= 0, so a breakdown needs a broken estimator; the
    # guard is defense-in-depth and must raise rather than clamp silently.
    from src.backtest import validation

    monkeypatch.setattr(validation, "_kurtosis", lambda _values: 0.0)
    with pytest.raises(ValidationInputError, match="Mertens"):
        deflated_sharpe_ratio(
            [0.02, 0.019, 0.021, 0.02, 0.018, 0.022] * 10,
            trial_sharpe_variance=0.0,
            effective_trials=1,
        )


def test_future_dated_candles_cannot_anchor_the_holdout(tmp_path: Path) -> None:
    universe = _flat_universe(620)
    with pytest.raises(BacktestError, match="future-dated"):
        run_registered_backtest(
            universe,
            parameters=_parameters(),
            config_hash="hash-a",
            code_version="abc1234",
            registry_path=tmp_path / "registry.jsonl",
            holdout_path=tmp_path / "holdout.json",
            reports_directory=tmp_path / "reports",
            # recorded_at BEFORE the data ends: the data is "from the future".
            recorded_at=_BASE_OPEN_TIME + timedelta(days=100),
        )


def test_registered_run_persists_a_durable_returns_series(tmp_path: Path) -> None:
    import json as json_module

    universe = _flat_universe(620)
    registry_path = tmp_path / "registry.jsonl"
    result = run_registered_backtest(
        universe,
        parameters=_parameters(),
        config_hash="hash-a",
        code_version="abc1234",
        registry_path=registry_path,
        holdout_path=tmp_path / "holdout.json",
        reports_directory=tmp_path / "reports",
        recorded_at=_NOW,
    )

    returns_path = tmp_path / "trial_returns" / f"trial-{result.trial.trial_id:06d}.json"
    assert returns_path.exists()
    payload = json_module.loads(returns_path.read_text(encoding="utf-8"))
    assert payload["trial_id"] == result.trial.trial_id
    assert len(payload["daily_returns"]) == result.report.metrics.observation_days - 1


def test_holdout_spend_registers_isolated_holdout_segment_metrics(tmp_path: Path) -> None:
    universe = _flat_universe(620)
    qualification = run_registered_backtest(
        universe,
        parameters=_parameters(),
        config_hash="hash-a",
        code_version="abc1234",
        registry_path=tmp_path / "registry.jsonl",
        holdout_path=tmp_path / "holdout.json",
        reports_directory=tmp_path / "reports",
        recorded_at=_NOW,
        spend_holdout_single_use=True,
    )

    metrics = qualification.trial.metrics
    assert "holdout_observation_days" in metrics
    assert int(metrics["holdout_observation_days"]) > 300
    assert "holdout_annualized_sharpe" in metrics
    assert "holdout_max_drawdown_fraction" in metrics
