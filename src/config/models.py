"""Typed configuration models for the Core MVP paper-trading system."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.binance_public_hosts import (
    BINANCE_PUBLIC_REST_BASE_URL_CANDIDATES,
    BINANCE_PUBLIC_WS_STREAM_BASE_URL_CANDIDATES,
)
from src.domain import Signal


class ConfigLoadError(ValueError):
    """Raised when a config file cannot be loaded as a mapping."""


class CoreConfigModel(BaseModel):
    """Base model for strict, immutable config contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _reject_enabled_flag(name: str, value: bool) -> bool:
    if value:
        msg = f"{name} is not allowed in Core MVP paper mode"
        raise ValueError(msg)
    return value


def _require_true_flag(name: str, value: bool) -> bool:
    if not value:
        msg = f"{name} must stay enabled in Core MVP"
        raise ValueError(msg)
    return value


def _require_non_empty_string(name: str, value: str) -> str:
    if not value.strip():
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    return value


def _require_positive_int(name: str, value: int) -> int:
    if value <= 0:
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return value


def _require_positive_decimal(name: str, value: Decimal) -> Decimal:
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return value


def _require_non_negative_decimal(name: str, value: Decimal) -> Decimal:
    if value < Decimal("0"):
        msg = f"{name} must not be negative"
        raise ValueError(msg)
    return value


def _require_fraction(name: str, value: Decimal) -> Decimal:
    _require_positive_decimal(name, value)
    if value > Decimal("1"):
        msg = f"{name} must be at most 1"
        raise ValueError(msg)
    return value


class DataSourceConfig(CoreConfigModel):
    """Public market data configuration."""

    provider: Literal["binance_spot_public"] = "binance_spot_public"
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    timeframe: Literal["1d", "15m"] = "1d"
    rest_base_url_candidates: tuple[str, ...] = BINANCE_PUBLIC_REST_BASE_URL_CANDIDATES
    ws_stream_base_url_candidates: tuple[str, ...] = BINANCE_PUBLIC_WS_STREAM_BASE_URL_CANDIDATES
    timeout_seconds: Decimal = Decimal("10")
    private_api_enabled: bool = False
    api_key_required: bool = False

    @field_validator("symbols")
    @classmethod
    def _validate_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            msg = "symbols must not be empty"
            raise ValueError(msg)
        for symbol in value:
            _require_non_empty_string("symbol", symbol)
            if "/" in symbol:
                msg = "symbols must use Binance-native format, for example BTCUSDT"
                raise ValueError(msg)
        return value

    @field_validator("rest_base_url_candidates", "ws_stream_base_url_candidates")
    @classmethod
    def _validate_public_base_url_candidates(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if not value:
            msg = "public base URL candidates must not be empty"
            raise ValueError(msg)
        field_name = getattr(info, "field_name", "")
        expected_scheme = "https://" if field_name == "rest_base_url_candidates" else "wss://"
        for url in value:
            _require_non_empty_string("public base URL", url)
            if not url.startswith(expected_scheme):
                msg = f"{field_name} values must start with {expected_scheme}"
                raise ValueError(msg)
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_public_data_timeout(cls, value: Decimal) -> Decimal:
        return _require_positive_decimal("timeout_seconds", value)

    @field_validator("private_api_enabled", "api_key_required")
    @classmethod
    def _reject_private_data_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "data source safety flag")
        return _reject_enabled_flag(str(field_name), value)


class StrategyParametersConfig(CoreConfigModel):
    """Readable parameters for the first Core MVP strategy contract."""

    momentum_lookback_candles: int = 12
    trend_lookback_candles: int = 48
    breakout_lookback_candles: int = 96
    volume_lookback_candles: int = 96
    volatility_lookback_candles: int = 48
    minimum_entry_score: Decimal = Decimal("0.70")
    exit_score: Decimal = Decimal("0.40")

    @field_validator(
        "momentum_lookback_candles",
        "trend_lookback_candles",
        "breakout_lookback_candles",
        "volume_lookback_candles",
        "volatility_lookback_candles",
    )
    @classmethod
    def _validate_positive_windows(cls, value: int, info: object) -> int:
        field_name = getattr(info, "field_name", "strategy lookback")
        return _require_positive_int(str(field_name), value)

    @field_validator("minimum_entry_score", "exit_score")
    @classmethod
    def _validate_score_fraction(cls, value: Decimal, info: object) -> Decimal:
        field_name = getattr(info, "field_name", "strategy score")
        return _require_fraction(str(field_name), value)

    @model_validator(mode="after")
    def _exit_score_must_not_exceed_entry_score(self) -> StrategyParametersConfig:
        if self.exit_score > self.minimum_entry_score:
            msg = "exit_score must not exceed minimum_entry_score"
            raise ValueError(msg)
        return self


class StrategyConfig(CoreConfigModel):
    """Strategy selection and parameter configuration.

    The Daily Trend Ensemble has no tunable parameters by contract
    (docs/contracts/STRATEGY_DAILY_TREND_ENSEMBLE.md); the ``parameters``
    block only applies to the superseded ``large_liquid_trend_15`` strategy.
    """

    name: Literal["daily_trend_ensemble", "large_liquid_trend_15"] = "daily_trend_ensemble"
    allowed_signals: tuple[Signal, ...] = (Signal.LONG, Signal.FLAT)
    allow_short: bool = False
    parameters: StrategyParametersConfig = Field(default_factory=StrategyParametersConfig)

    @field_validator("allowed_signals")
    @classmethod
    def _reject_non_mvp_signals(cls, value: tuple[Signal, ...]) -> tuple[Signal, ...]:
        if set(value) != {Signal.LONG, Signal.FLAT}:
            msg = "allowed_signals must be exactly LONG and FLAT"
            raise ValueError(msg)
        return value

    @field_validator("allow_short")
    @classmethod
    def _reject_short_strategy(cls, value: bool) -> bool:
        return _reject_enabled_flag("allow_short", value)


def _default_risk_budgets() -> dict[str, Decimal]:
    return {"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal("0.5")}


class PortfolioConfig(CoreConfigModel):
    """Long-only portfolio target configuration.

    ``risk_budgets`` defines the v0.9 decision universe: only budgeted symbols
    reach the strategy layer. SOLUSDT stays out until it independently passes
    the validation gate (docs/contracts/UNIVERSE_CONTRACT.md).
    """

    max_active_positions: int = 3
    max_symbol_weight: Decimal = Decimal("0.35")
    max_gross_exposure: Decimal = Decimal("1.0")
    cash_allowed: bool = True
    cooldown_enabled: bool = True
    allow_short: bool = False
    risk_budgets: dict[str, Decimal] = Field(default_factory=_default_risk_budgets)

    @field_validator("max_active_positions")
    @classmethod
    def _validate_max_active_positions(cls, value: int) -> int:
        return _require_positive_int("max_active_positions", value)

    @field_validator("risk_budgets")
    @classmethod
    def _validate_risk_budgets(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value:
            msg = "risk_budgets must not be empty"
            raise ValueError(msg)
        total_budget = Decimal("0")
        for symbol, budget in value.items():
            _require_non_empty_string("risk budget symbol", symbol)
            if "/" in symbol:
                msg = "risk budget symbols must use Binance-native format, for example BTCUSDT"
                raise ValueError(msg)
            _require_fraction(f"risk budget for {symbol}", budget)
            total_budget += budget
        if total_budget > Decimal("1"):
            msg = "risk_budgets must not exceed 1 in total"
            raise ValueError(msg)
        return value

    @field_validator("max_symbol_weight", "max_gross_exposure")
    @classmethod
    def _validate_exposure_fraction(cls, value: Decimal, info: object) -> Decimal:
        field_name = getattr(info, "field_name", "portfolio exposure")
        return _require_fraction(str(field_name), value)

    @field_validator("cash_allowed")
    @classmethod
    def _require_cash_allowed(cls, value: bool) -> bool:
        return _require_true_flag("cash_allowed", value)

    @field_validator("allow_short")
    @classmethod
    def _reject_short_portfolio(cls, value: bool) -> bool:
        return _reject_enabled_flag("allow_short", value)


class RiskConfig(CoreConfigModel):
    """Risk gate thresholds and safety flags.

    The stale-data default fits the daily decision cadence: a daily close
    older than 36 hours means the feed is broken, so new exposure halts.

    The drawdown pause measures from the all-time equity peak and blocks new
    buys; verified research expects 50-60% drawdowns as NORMAL for the daily
    trend ensemble (docs/research/SIGNAL_DESIGN_RESEARCH.md section 4), so the
    pause sits above that band as a disaster brake, not inside it. Trial 1
    proved the old 20% default from the 15m era locks the strategy out
    permanently after the first bear market.
    """

    min_notional_usdt: Decimal = Decimal("10")
    stale_data_max_age_seconds: int = 129600
    max_drawdown_fraction: Decimal = Decimal("0.65")
    daily_loss_pause_fraction: Decimal = Decimal("0.10")
    disaster_single_day_drop_fraction: Decimal = Decimal("0.20")
    short_exposure_enabled: bool = False
    margin_enabled: bool = False
    leverage_enabled: bool = False

    @field_validator("min_notional_usdt")
    @classmethod
    def _validate_min_notional(cls, value: Decimal) -> Decimal:
        return _require_positive_decimal("min_notional_usdt", value)

    @field_validator("stale_data_max_age_seconds")
    @classmethod
    def _validate_stale_data_window(cls, value: int) -> int:
        return _require_positive_int("stale_data_max_age_seconds", value)

    @field_validator(
        "max_drawdown_fraction",
        "daily_loss_pause_fraction",
        "disaster_single_day_drop_fraction",
    )
    @classmethod
    def _validate_risk_fraction(cls, value: Decimal, info: object) -> Decimal:
        field_name = getattr(info, "field_name", "risk fraction")
        return _require_fraction(str(field_name), value)

    @field_validator("short_exposure_enabled", "margin_enabled", "leverage_enabled")
    @classmethod
    def _reject_forbidden_risk_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "risk safety flag")
        return _reject_enabled_flag(str(field_name), value)


class ExecutionConfig(CoreConfigModel):
    """Paper execution cost and rounding configuration."""

    mode: Literal["paper"] = "paper"
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    quantity_step: Decimal = Decimal("0.000001")
    price_tick: Decimal = Decimal("0.01")
    real_orders_enabled: bool = False
    private_api_enabled: bool = False
    margin_enabled: bool = False
    leverage_enabled: bool = False

    @field_validator("fee_bps", "slippage_bps")
    @classmethod
    def _validate_cost_bps(cls, value: Decimal, info: object) -> Decimal:
        field_name = getattr(info, "field_name", "execution cost bps")
        return _require_non_negative_decimal(str(field_name), value)

    @field_validator("quantity_step", "price_tick")
    @classmethod
    def _validate_rounding_step(cls, value: Decimal, info: object) -> Decimal:
        field_name = getattr(info, "field_name", "execution rounding step")
        return _require_positive_decimal(str(field_name), value)

    @field_validator(
        "real_orders_enabled",
        "private_api_enabled",
        "margin_enabled",
        "leverage_enabled",
    )
    @classmethod
    def _reject_forbidden_execution_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "execution safety flag")
        return _reject_enabled_flag(str(field_name), value)


class VirtualAccountConfig(CoreConfigModel):
    """Initial virtual account configuration."""

    account_id: str = "paper-main"
    initial_cash: Decimal = Decimal("1000")
    quote_asset: Literal["USDT"] = "USDT"

    @field_validator("account_id")
    @classmethod
    def _validate_account_id(cls, value: str) -> str:
        return _require_non_empty_string("account_id", value)

    @field_validator("initial_cash")
    @classmethod
    def _validate_initial_cash(cls, value: Decimal) -> Decimal:
        return _require_positive_decimal("initial_cash", value)


class RuntimeConfig(CoreConfigModel):
    """Runtime mode, cadence, and restart behavior configuration."""

    mode: Literal["paper"] = "paper"
    decision_timeframe: Literal["1d", "15m"] = "1d"
    config_snapshot_required: bool = True
    halt_on_stale_data: bool = True
    idempotency_key_namespace: str = "paper-runtime"
    real_trading_enabled: bool = False
    private_api_enabled: bool = False

    @field_validator("config_snapshot_required", "halt_on_stale_data")
    @classmethod
    def _require_runtime_safety_switches(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "runtime safety switch")
        return _require_true_flag(str(field_name), value)

    @field_validator("idempotency_key_namespace")
    @classmethod
    def _validate_idempotency_namespace(cls, value: str) -> str:
        return _require_non_empty_string("idempotency_key_namespace", value)

    @field_validator("real_trading_enabled", "private_api_enabled")
    @classmethod
    def _reject_forbidden_runtime_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "runtime safety flag")
        return _reject_enabled_flag(str(field_name), value)


class StorageConfig(CoreConfigModel):
    """PostgreSQL-compatible runtime storage plus research artifact paths."""

    backend: Literal["postgresql"] = "postgresql"
    host: str = "localhost"
    port: int = 54320
    database: str = "crypto_quant"
    username: str = "crypto"
    password: str = "crypto_dev_only"
    snapshot_directory: str = "docs/reports/config-snapshots"
    trial_registry_path: str = "docs/reports/research/trial_registry.jsonl"
    holdout_lock_path: str = "docs/reports/research/holdout_lock.json"
    backtest_reports_directory: str = "docs/reports/backtests"
    candle_files_directory: str = "data/candles"
    runtime_events_path: str = "data/runtime/events.jsonl"

    @field_validator(
        "host",
        "database",
        "username",
        "password",
        "snapshot_directory",
        "trial_registry_path",
        "holdout_lock_path",
        "backtest_reports_directory",
        "candle_files_directory",
        "runtime_events_path",
    )
    @classmethod
    def _validate_storage_strings(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "storage string")
        return _require_non_empty_string(str(field_name), value)

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        return _require_positive_int("port", value)


class NotificationConfig(CoreConfigModel):
    """Advisory notification delivery configuration.

    Notifications are persisted before delivery and are never execution
    instructions; the webhook channel is config-gated and https-only.
    """

    enabled: bool = True
    channel: Literal["log", "webhook"] = "log"
    webhook_url: str = ""

    @model_validator(mode="after")
    def _webhook_channel_requires_https_url(self) -> NotificationConfig:
        if self.channel == "webhook":
            if not self.webhook_url.startswith("https://"):
                msg = "webhook channel requires an https webhook_url"
                raise ValueError(msg)
        elif self.webhook_url:
            msg = "webhook_url is only allowed when channel is webhook"
            raise ValueError(msg)
        return self


class ApiDashboardConfig(CoreConfigModel):
    """Read-only dashboard/API configuration."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    read_only: bool = True
    manual_orders_enabled: bool = False
    risk_limit_mutation_enabled: bool = False
    private_account_access_enabled: bool = False

    @field_validator("enabled", "read_only")
    @classmethod
    def _require_read_only_api(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "API safety flag")
        return _require_true_flag(str(field_name), value)

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        return _require_non_empty_string("host", value)

    @field_validator("port")
    @classmethod
    def _validate_api_port(cls, value: int) -> int:
        return _require_positive_int("port", value)

    @field_validator(
        "manual_orders_enabled",
        "risk_limit_mutation_enabled",
        "private_account_access_enabled",
    )
    @classmethod
    def _reject_forbidden_api_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "API safety flag")
        return _reject_enabled_flag(str(field_name), value)


class AppConfig(CoreConfigModel):
    """Full Core MVP configuration loaded once per run."""

    version: Literal["1"] = "1"
    data_source: DataSourceConfig = Field(default_factory=DataSourceConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    account: VirtualAccountConfig = Field(default_factory=VirtualAccountConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    api_dashboard: ApiDashboardConfig = Field(default_factory=ApiDashboardConfig)


def load_config(path: str | Path) -> AppConfig:
    """Load a YAML config file through the typed Core MVP config model."""

    config_path = Path(path)
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        msg = f"{config_path} must contain a YAML mapping"
        raise ConfigLoadError(msg)
    return AppConfig.model_validate(raw_config)


def config_snapshot(config: AppConfig) -> dict[str, object]:
    """Return a JSON-serializable config snapshot for run artifacts."""

    return config.model_dump(mode="json")


def write_config_snapshot(config: AppConfig, path: str | Path) -> Path:
    """Write a deterministic JSON config snapshot for a run."""

    snapshot_path = Path(path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = config_snapshot(config)
    snapshot_text = json.dumps(snapshot, indent=2, sort_keys=True)
    snapshot_path.write_text(f"{snapshot_text}\n", encoding="utf-8")
    return snapshot_path
