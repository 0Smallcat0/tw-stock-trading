"""Map exposure-ladder strategy decisions to long-only portfolio targets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from src.domain import Symbol
from src.portfolio.types import (
    PortfolioTarget,
    PortfolioTargetSet,
    PortfolioValidationError,
)

LADDER_TARGET_BUILT = "LADDER_TARGET_BUILT"
NO_LONG_EXPOSURE = "NO_LONG_EXPOSURE"
TARGETS_BUILT = "TARGETS_BUILT"
CASH_HELD = "CASH_HELD"


@runtime_checkable
class LadderDecisionLike(Protocol):
    """Strategy output fields needed by the ladder target builder."""

    @property
    def symbol(self) -> Symbol: ...

    @property
    def exposure_fraction(self) -> Decimal: ...

    @property
    def reason_codes(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LadderPortfolioParameters:
    """Per-asset risk budgets for the exposure-ladder portfolio."""

    risk_budgets: Mapping[str, Decimal]
    max_gross_exposure: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not isinstance(self.risk_budgets, Mapping) or not self.risk_budgets:
            msg = "risk_budgets must be a non-empty mapping"
            raise PortfolioValidationError(msg)
        copied_budgets = dict(self.risk_budgets)
        total_budget = Decimal("0")
        for symbol_value, budget in copied_budgets.items():
            if not isinstance(symbol_value, str) or not symbol_value.strip():
                msg = "risk budget symbols must be non-empty strings"
                raise PortfolioValidationError(msg)
            if "/" in symbol_value:
                msg = "risk budget symbols must use exchange-native codes, for example 0050"
                raise PortfolioValidationError(msg)
            if not isinstance(budget, Decimal) or not budget.is_finite():
                msg = f"risk budget for {symbol_value} must be a finite Decimal"
                raise PortfolioValidationError(msg)
            if budget <= Decimal("0") or budget > Decimal("1"):
                msg = f"risk budget for {symbol_value} must be greater than 0 and at most 1"
                raise PortfolioValidationError(msg)
            total_budget += budget
        if (
            not isinstance(self.max_gross_exposure, Decimal)
            or not self.max_gross_exposure.is_finite()
        ):
            msg = "max_gross_exposure must be a finite Decimal"
            raise PortfolioValidationError(msg)
        if self.max_gross_exposure <= Decimal("0") or self.max_gross_exposure > Decimal("1"):
            msg = "max_gross_exposure must be greater than 0 and at most 1"
            raise PortfolioValidationError(msg)
        if total_budget > self.max_gross_exposure:
            msg = "risk budgets must not exceed max_gross_exposure in total"
            raise PortfolioValidationError(msg)
        object.__setattr__(self, "risk_budgets", MappingProxyType(copied_budgets))


def build_ladder_targets(
    decisions: Iterable[LadderDecisionLike],
    *,
    parameters: LadderPortfolioParameters,
) -> PortfolioTargetSet:
    """Convert ladder decisions into target weights inside per-asset risk budgets."""

    decision_list = tuple(decisions)
    if not decision_list:
        msg = "ladder decisions must not be empty"
        raise PortfolioValidationError(msg)
    if any(not isinstance(decision, LadderDecisionLike) for decision in decision_list):
        msg = "decisions must contain ladder decision values"
        raise PortfolioValidationError(msg)
    if not isinstance(parameters, LadderPortfolioParameters):
        msg = "parameters must be LadderPortfolioParameters"
        raise PortfolioValidationError(msg)

    seen_symbols: set[str] = set()
    targets: list[PortfolioTarget] = []
    for decision in decision_list:
        symbol_value = decision.symbol.value
        if symbol_value in seen_symbols:
            msg = f"duplicate ladder decision symbol: {symbol_value}"
            raise PortfolioValidationError(msg)
        seen_symbols.add(symbol_value)

        budget = parameters.risk_budgets.get(symbol_value)
        if budget is None:
            msg = f"no risk budget configured for symbol: {symbol_value}"
            raise PortfolioValidationError(msg)
        target_weight = decision.exposure_fraction * budget
        if target_weight <= Decimal("0"):
            continue
        targets.append(
            PortfolioTarget(
                symbol=decision.symbol,
                target_weight=target_weight,
                source_score=decision.exposure_fraction,
                reason_codes=decision.reason_codes + (LADDER_TARGET_BUILT,),
            )
        )

    gross_target_weight = sum((target.target_weight for target in targets), Decimal("0"))
    cash_weight = Decimal("1") - gross_target_weight
    if not targets:
        return PortfolioTargetSet(
            targets=(),
            cash_weight=Decimal("1"),
            reason_codes=(NO_LONG_EXPOSURE, CASH_HELD),
        )

    reason_codes = [TARGETS_BUILT]
    if cash_weight > Decimal("0"):
        reason_codes.append(CASH_HELD)
    return PortfolioTargetSet(
        targets=tuple(targets),
        cash_weight=cash_weight,
        reason_codes=tuple(reason_codes),
    )
