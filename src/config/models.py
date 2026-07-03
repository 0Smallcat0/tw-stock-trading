"""Typed configuration models for the TW stock signal MVP."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain import Signal
from src.tw_public_hosts import (
    FINMIND_API_BASE_URL,
    TWSE_OPENAPI_BASE_URL,
    TWSE_RWD_BASE_URL,
)

_TW_SYMBOL_PATTERN = re.compile(r"^[0-9][0-9A-Z]{3,5}$")
_TAIPEI_TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


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


def _require_tw_symbol(name: str, value: str) -> str:
    _require_non_empty_string(name, value)
    if not _TW_SYMBOL_PATTERN.fullmatch(value):
        msg = f"{name} must be a TWSE security code such as 0050 or 2330"
        raise ValueError(msg)
    return value


class DataSourceConfig(CoreConfigModel):
    """Public Taiwan market data configuration.

    All sources are keyless public endpoints. Daily OHLCV is final on TWSE
    around 17:30 Taipei (odd-lot and block volume included), so the daily job
    runs no earlier than 18:00 Taipei. TWSE rate limits are undocumented but
    aggressive scraping earns IP bans: requests stay sequential and spaced.
    """

    provider: Literal["twse_public"] = "twse_public"
    symbols: tuple[str, ...] = ("0050",)
    timeframe: Literal["1d"] = "1d"
    twse_rwd_base_url: str = TWSE_RWD_BASE_URL
    twse_openapi_base_url: str = TWSE_OPENAPI_BASE_URL
    finmind_api_base_url: str = FINMIND_API_BASE_URL
    backfill_provider: Literal["finmind_public"] = "finmind_public"
    daily_job_time_taipei: str = "18:00"
    min_request_interval_seconds: Decimal = Decimal("3")
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
            _require_tw_symbol("symbol", symbol)
        return value

    @field_validator("twse_rwd_base_url", "twse_openapi_base_url", "finmind_api_base_url")
    @classmethod
    def _validate_public_base_urls(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "public base URL"))
        _require_non_empty_string(field_name, value)
        if not value.startswith("https://"):
            msg = f"{field_name} must start with https://"
            raise ValueError(msg)
        return value

    @field_validator("daily_job_time_taipei")
    @classmethod
    def _validate_daily_job_time(cls, value: str) -> str:
        if not _TAIPEI_TIME_PATTERN.fullmatch(value):
            msg = "daily_job_time_taipei must use 24h HH:MM format"
            raise ValueError(msg)
        return value

    @field_validator("min_request_interval_seconds", "timeout_seconds")
    @classmethod
    def _validate_positive_seconds(cls, value: Decimal, info: object) -> Decimal:
        field_name = str(getattr(info, "field_name", "data source seconds"))
        return _require_positive_decimal(field_name, value)

    @field_validator("private_api_enabled", "api_key_required")
    @classmethod
    def _reject_private_data_flags(cls, value: bool, info: object) -> bool:
        field_name = getattr(info, "field_name", "data source safety flag")
        return _reject_enabled_flag(str(field_name), value)


class StrategyConfig(CoreConfigModel):
    """Strategy selection configuration.

    The Daily Trend Ensemble has no tunable parameters by contract
    (docs/contracts/STRATEGY_DAILY_TREND_ENSEMBLE.md): the SMA lookbacks
    20/65/150/200 are fixed to keep the registered trial count minimal.
    """

    name: Literal["daily_trend_ensemble"] = "daily_trend_ensemble"
    allowed_signals: tuple[Signal, ...] = (Signal.LONG, Signal.FLAT)
    allow_short: bool = False

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
    return {"0050": Decimal("1")}


class PortfolioConfig(CoreConfigModel):
    """Long-only portfolio target configuration.

    ``risk_budgets`` defines the decision universe: only budgeted symbols
    reach the strategy layer. The TW MVP universe is 0050 alone at 100%;
    2330 single-stock timing is excluded by evidence and any second asset
    requires its own gate pass (docs/contracts/UNIVERSE_CONTRACT.md).
    """

    max_active_positions: int = 1
    max_symbol_weight: Decimal = Decimal("1.0")
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
            _require_tw_symbol("risk budget symbol", symbol)
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

    TW recalibration (docs/research/TW_SIGNAL_DESIGN_RESEARCH.md):

    - Disaster: under the ±10% daily price limit a -20% single-day close is
      impossible; the single-day disaster trigger sits at -9% (limit-down
      territory, e.g. 2025-04-07). A multi-session trigger lands with the
      TW risk gate retrofit (Goal TW-D).
    - Drawdown pause 0.40: the trend ladder's expected max drawdown band is
      15-25% versus 0050 buy-and-hold's worst -58% (2008); the pause is a
      disaster brake above the expected band, low enough to matter. The
      crypto lesson stands: a pause inside the normal band locks the
      strategy out permanently.
    - Stale data: seconds are an interim unit; weekends and holidays make
      wall-clock staleness wrong for TW, so Goal TW-D replaces this with a
      trading-day rule over the exchange calendar. 96h survives a normal
      weekend but NOT Lunar New Year; the runtime does not go live before
      the trading-day rule exists.
    """

    min_notional_twd: Decimal = Decimal("10000")
    stale_data_max_age_seconds: int = 345600
    max_drawdown_fraction: Decimal = Decimal("0.40")
    daily_loss_pause_fraction: Decimal = Decimal("0.095")
    disaster_single_day_drop_fraction: Decimal = Decimal("0.09")
    short_exposure_enabled: bool = False
    margin_enabled: bool = False
    leverage_enabled: bool = False

    @field_validator("min_notional_twd")
    @classmethod
    def _validate_min_notional(cls, value: Decimal) -> Decimal:
        return _require_positive_decimal("min_notional_twd", value)

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
    """Paper execution cost and rounding configuration.

    Interim flat-bps cost model: the full TW model (per-side commission with
    broker discount and minimum fee, sell-only securities transaction tax by
    instrument type, bracketed tick table) lands in Goal TW-D. Until then
    fee_bps 13.5 approximates a 0.0855% commission per side plus the 0.1%
    ETF sell tax averaged across the round trip; quantity_step 1 is odd-lot
    share granularity; price_tick 0.05 is the ETF bracket at 0050's current
    price (>= NT$50).
    """

    mode: Literal["paper"] = "paper"
    fee_bps: Decimal = Decimal("13.5")
    slippage_bps: Decimal = Decimal("5")
    quantity_step: Decimal = Decimal("1")
    price_tick: Decimal = Decimal("0.05")
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
    initial_cash: Decimal = Decimal("100000")
    quote_asset: Literal["TWD"] = "TWD"

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
    decision_timeframe: Literal["1d"] = "1d"
    config_snapshot_required: bool = True
    halt_on_stale_data: bool = True
    idempotency_key_namespace: str = "tw-paper-runtime"
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
    """PostgreSQL-compatible runtime storage plus research artifact paths.

    Host port 54321 keeps the TW database from colliding with the crypto
    sibling project's local TimescaleDB on 54320.
    """

    backend: Literal["postgresql"] = "postgresql"
    host: str = "localhost"
    port: int = 54321
    database: str = "tw_quant"
    username: str = "tw"
    password: str = "tw_dev_only"
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
    port: int = 8001
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
