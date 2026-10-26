"""Pure payroll math. Every rate and threshold comes from payroll_rules.params, never from code.

Money is integer VND. Rates are parsed as Decimal (never float) and rounded half-up once per
line item.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


def _d(value) -> Decimal:
    return Decimal(str(value))


def _vnd(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def progressive_tax(taxable: int, brackets: list) -> int:
    """Marginal tax. `brackets` = [[upper_bound | None, rate], ...] in ascending order."""
    tax = Decimal(0)
    lower = 0
    for upper, rate in brackets:
        if taxable <= lower:
            break
        top = taxable if upper is None else min(taxable, upper)
        tax += (top - lower) * _d(rate)
        if upper is None:
            break
        lower = upper
    return _vnd(tax)


def insurance(base_salary: int, params: dict) -> tuple[int, int, int]:
    """(social, health, unemployment), all computed on the base salary."""
    return (
        _vnd(base_salary * _d(params["social_insurance_rate"])),
        _vnd(base_salary * _d(params["health_insurance_rate"])),
        _vnd(base_salary * _d(params["unemployment_insurance_rate"])),
    )


def standard_minutes(params: dict) -> int:
    return int(params["standard_days"]) * int(params["hours_per_day"]) * 60


@dataclass(frozen=True)
class ApprovedMinutes:
    """Approved requests of one employee on one day."""

    adjustment: int = 0  # minutes claimed by approved 'adjustment' requests (max of them)
    ot: int = 0  # minutes of approved 'ot' requests (sum)
    leave: bool = False  # an approved 'leave' counts as a full standard day


def effective_minutes(daily: tuple[int, int] | None, approved: ApprovedMinutes | None, params: dict) -> tuple[int, int]:
    """(worked, ot) for one day: attendance_daily combined with approved requests.

    Approved requests never edit attendance_events (NV-5); they are applied here instead.
    The larger of event-based and approved minutes wins, so nothing is counted twice.
    """
    worked, ot = daily or (0, 0)
    if approved is not None:
        worked = max(worked, approved.adjustment, int(params["hours_per_day"]) * 60 if approved.leave else 0)
        ot = max(ot, approved.ot)
    return worked, ot


def compute_payslip(
    params: dict, base_salary: int, allowance: int, dependents: int, worked_minutes: int, ot_minutes: int
) -> dict:
    std = standard_minutes(params)
    paid_minutes = min(worked_minutes, std)  # pay never exceeds the standard month
    base_pay = _vnd(Decimal(base_salary) * paid_minutes / std)
    ot_pay = _vnd(Decimal(base_salary) * ot_minutes / std * _d(params["ot_multiplier"]))
    gross = base_pay + ot_pay + allowance
    si, hi, ui = insurance(base_salary, params)
    taxable = max(
        0,
        gross - si - hi - ui - int(params["personal_deduction"]) - dependents * int(params["dependent_deduction"]),
    )
    tax = progressive_tax(taxable, params["tax_brackets"])
    return {
        "worked_minutes": worked_minutes,
        "ot_minutes": ot_minutes,
        "base_pay": base_pay,
        "ot_pay": ot_pay,
        "allowance": allowance,
        "gross": gross,
        "social_insurance": si,
        "health_insurance": hi,
        "unemployment_insurance": ui,
        "taxable_income": taxable,
        "income_tax": tax,
        # Insurance is charged on the base salary regardless of attendance, so a month with
        # almost no work could go negative; the payslips CHECK forbids that, floor at zero.
        "net_pay": max(0, gross - si - hi - ui - tax),
    }
