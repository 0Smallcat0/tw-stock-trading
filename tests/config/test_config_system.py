from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.config import AppConfig, config_snapshot, load_config, write_config_snapshot
from src.domain import Signal, Symbol
from src.portfolio import build_portfolio_targets
from src.strategies import StrategyDecision
from src.tw_public_hosts import (
    FINMIND_API_BASE_URL,
    TWSE_OPENAPI_BASE_URL,
    TWSE_RWD_BASE_URL,
)

DEFAULT_CONFIG_PATH = Path("configs/runtime/paper_runtime.yaml")


def _base_config_data() -> dict[str, object]:
    return AppConfig().model_dump(mode="json")


def _with_nested_value(path: tuple[str, ...], value: object) -> dict[str, object]:
    updated = deepcopy(_base_config_data())
    cursor = updated
    for key in path[:-1]:
        next_value = cursor[key]
        assert isinstance(next_value, dict)
        cursor = next_value
    cursor[path[-1]] = value
    return updated


def _symbol(value: str) -> Symbol:
    return Symbol(value=value, base_asset=value, quote_asset="TWD")


def _portfolio_decision(value: str, score: str) -> StrategyDecision:
    generated_at = datetime(2026, 5, 20, 5, 30, tzinfo=UTC)
    return StrategyDecision(
        symbol=_symbol(value),
        signal=Signal.LONG,
        score=Decimal(score),
        reason_codes=("CONFIG_INTEGRATION_TEST",),
        generated_at_bar_close=generated_at,
        executable_from_next_bar=generated_at + timedelta(milliseconds=1),
    )


def test_default_config_model_has_core_mvp_defaults() -> None:
    config = AppConfig()

    assert config.account.initial_cash == Decimal("100000")
    assert config.account.quote_asset == "TWD"
    assert config.data_source.provider == "twse_public"
    assert config.data_source.symbols == ("0050",)
    assert config.data_source.timeframe == "1d"
    assert config.data_source.twse_rwd_base_url == TWSE_RWD_BASE_URL
    assert config.data_source.twse_openapi_base_url == TWSE_OPENAPI_BASE_URL
    assert config.data_source.finmind_api_base_url == FINMIND_API_BASE_URL
    assert config.data_source.backfill_provider == "finmind_public"
    assert config.data_source.daily_job_time_taipei == "18:00"
    assert config.data_source.timeout_seconds == Decimal("10")
    assert config.strategy.name == "daily_trend_ensemble"
    assert config.portfolio.risk_budgets == {"0050": Decimal("1")}
    assert "2330" not in config.portfolio.risk_budgets
    assert config.risk.min_notional_twd == Decimal("10000")
    assert config.risk.disaster_single_day_drop_fraction == Decimal("0.09")
    assert config.risk.max_drawdown_fraction == Decimal("0.40")
    assert config.runtime.mode == "paper"
    assert config.runtime.decision_timeframe == "1d"
    assert config.runtime.idempotency_key_namespace == "tw-paper-runtime"
    assert config.execution.mode == "paper"
    assert config.execution.quantity_step == Decimal("1")
    assert config.storage.database == "tw_quant"
    assert config.storage.port == 54321


def test_default_paper_runtime_config_loads_through_typed_model() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config.account.initial_cash == Decimal("100000")
    assert config.data_source.timeframe == "1d"
    assert config.strategy.name == "daily_trend_ensemble"
    assert config.portfolio.risk_budgets == {"0050": Decimal("1.0")}
    assert config.runtime.mode == "paper"
    assert config.runtime.decision_timeframe == "1d"
    assert config.execution.fee_bps == Decimal("13.5")
    assert config.execution.price_tick == Decimal("0.05")


@pytest.mark.parametrize(
    "risk_budgets",
    (
        {},
        {"0050": "0.7", "0056": "0.4"},
        {"0050/TWD": "0.5"},
        {"BTCUSDT": "0.5"},
        {"0050": "0"},
        {"0050": "1.5"},
    ),
)
def test_invalid_risk_budgets_are_rejected(risk_budgets: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(("portfolio", "risk_budgets"), risk_budgets))


@pytest.mark.parametrize(
    "symbols",
    (
        (),
        ("BTCUSDT",),
        ("50",),
        ("0050/TWD",),
        ("台積電",),
    ),
)
def test_non_tw_symbols_are_rejected(symbols: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(("data_source", "symbols"), list(symbols)))


@pytest.mark.parametrize("symbols", (("0050",), ("2330",), ("006208",), ("00679B",)))
def test_valid_tw_symbols_are_accepted(symbols: tuple[str, ...]) -> None:
    config = AppConfig.model_validate(_with_nested_value(("data_source", "symbols"), list(symbols)))
    assert config.data_source.symbols == symbols


@pytest.mark.parametrize("value", ("25:00", "9:00", "1800", "", "18:60"))
def test_invalid_daily_job_time_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            _with_nested_value(("data_source", "daily_job_time_taipei"), value)
        )


@pytest.mark.parametrize(
    "path",
    (
        ("data_source", "twse_rwd_base_url"),
        ("data_source", "twse_openapi_base_url"),
        ("data_source", "finmind_api_base_url"),
    ),
)
def test_non_https_base_urls_are_rejected(path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(path, "http://insecure.example"))


def test_notification_defaults_are_log_channel() -> None:
    config = AppConfig()

    assert config.notifications.enabled is True
    assert config.notifications.channel == "log"
    assert config.notifications.webhook_url == ""


@pytest.mark.parametrize(
    ("channel", "webhook_url"),
    (
        ("webhook", ""),
        ("webhook", "http://insecure.example/hook"),
        ("log", "https://hook.example/x"),
    ),
)
def test_invalid_notification_configs_are_rejected(channel: str, webhook_url: str) -> None:
    data = _base_config_data()
    data["notifications"] = {"enabled": True, "channel": channel, "webhook_url": webhook_url}

    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_config_file_values_override_model_defaults(tmp_path: Path) -> None:
    data = _base_config_data()
    risk = data["risk"]
    execution = data["execution"]
    runtime = data["runtime"]
    assert isinstance(risk, dict)
    assert isinstance(execution, dict)
    assert isinstance(runtime, dict)

    risk["min_notional_twd"] = "25000"
    execution["fee_bps"] = "7.5"
    runtime["idempotency_key_namespace"] = "tw-paper-runtime-test"

    config_path = tmp_path / "paper_runtime.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)

    assert config.risk.min_notional_twd == Decimal("25000")
    assert config.execution.fee_bps == Decimal("7.5")
    assert config.runtime.idempotency_key_namespace == "tw-paper-runtime-test"


def test_loaded_config_values_drive_portfolio_parameters(tmp_path: Path) -> None:
    data = _base_config_data()
    portfolio = data["portfolio"]
    assert isinstance(portfolio, dict)

    portfolio.update(
        {
            "max_active_positions": 1,
            "max_symbol_weight": "0.20",
            "max_gross_exposure": "0.50",
        }
    )
    config_path = tmp_path / "paper_runtime.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)

    targets = build_portfolio_targets(
        (
            _portfolio_decision("0050", "0.90"),
            _portfolio_decision("0056", "0.80"),
        ),
        parameters=config.portfolio,
    )
    assert [(target.symbol.value, target.target_weight) for target in targets.targets] == [
        ("0050", Decimal("0.20")),
    ]
    assert targets.cash_weight == Decimal("0.80")


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("runtime", "mode"), "live"),
        (("runtime", "real_trading_enabled"), True),
        (("runtime", "private_api_enabled"), True),
        (("execution", "real_orders_enabled"), True),
        (("execution", "private_api_enabled"), True),
        (("data_source", "private_api_enabled"), True),
        (("data_source", "api_key_required"), True),
    ),
)
def test_real_trading_and_private_api_flags_are_rejected(
    path: tuple[str, ...], value: object
) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(path, value))


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("strategy", "allow_short"), True),
        (("portfolio", "allow_short"), True),
        (("risk", "short_exposure_enabled"), True),
        (("risk", "margin_enabled"), True),
        (("risk", "leverage_enabled"), True),
        (("execution", "margin_enabled"), True),
        (("execution", "leverage_enabled"), True),
    ),
)
def test_short_margin_and_leverage_flags_are_rejected(path: tuple[str, ...], value: object) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(path, value))


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("api_dashboard", "read_only"), False),
        (("api_dashboard", "manual_orders_enabled"), True),
        (("api_dashboard", "risk_limit_mutation_enabled"), True),
        (("api_dashboard", "private_account_access_enabled"), True),
    ),
)
def test_api_dashboard_mutation_and_private_account_flags_are_rejected(
    path: tuple[str, ...], value: object
) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(_with_nested_value(path, value))


def test_unknown_config_fields_are_rejected() -> None:
    data = _base_config_data()
    data["live_trading"] = {"enabled": True}

    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_legacy_crypto_config_fields_are_rejected() -> None:
    for path, value in (
        (("data_source", "rest_base_url_candidates"), ["https://api.binance.com"]),
        (("data_source", "ws_stream_base_url_candidates"), ["wss://stream.binance.com"]),
        (("strategy", "parameters"), {"momentum_lookback_candles": 12}),
    ):
        with pytest.raises(ValidationError):
            AppConfig.model_validate(_with_nested_value(path, value))


def test_config_snapshot_is_json_serializable_and_writeable(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    snapshot = config_snapshot(config)

    assert snapshot["version"] == "1"
    assert snapshot["account"] == {
        "account_id": "paper-main",
        "initial_cash": "100000",
        "quote_asset": "TWD",
    }

    snapshot_path = write_config_snapshot(config, tmp_path / "config_snapshot.json")
    assert snapshot_path.read_text(encoding="utf-8").endswith("\n")
    assert '"mode": "paper"' in snapshot_path.read_text(encoding="utf-8")
