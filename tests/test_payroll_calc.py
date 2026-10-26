from app.modules.payroll.calculator import (
    ApprovedMinutes, compute_payslip, effective_minutes, insurance, progressive_tax,
)
from scripts.seed import PAYROLL_PARAMS as P

B = P["tax_brackets"]
FULL = 26 * 8 * 60


def test_progressive_tax_brackets():
    assert progressive_tax(0, B) == 0
    assert progressive_tax(5_000_000, B) == 250_000
    assert progressive_tax(10_000_000, B) == 500_000
    assert progressive_tax(20_000_000, B) == 1_500_000
    assert progressive_tax(70_000_000, B) == 11_500_000
    assert progressive_tax(120_000_000, B) == 27_500_000


def test_insurance_on_base_salary():
    assert insurance(10_000_000, P) == (800_000, 150_000, 100_000)


def test_payslip_below_tax_threshold():
    s = compute_payslip(P, 10_000_000, 1_000_000, 0, FULL, 0)
    assert (s["base_pay"], s["gross"], s["taxable_income"], s["income_tax"], s["net_pay"]) == (
        10_000_000, 11_000_000, 0, 0, 9_950_000)


def test_payslip_with_tax_and_dependents():
    s = compute_payslip(P, 50_000_000, 0, 2, FULL, 0)
    assert s["taxable_income"] == 50_000_000 - 5_250_000 - 15_500_000 - 12_400_000
    assert s["income_tax"] == 500_000 + 685_000
    assert s["net_pay"] == 50_000_000 - 5_250_000 - 1_185_000


def test_payslip_overtime_and_partial_month():
    assert compute_payslip(P, 26_000_000, 0, 0, FULL, 120)["ot_pay"] == 375_000
    assert compute_payslip(P, 26_000_000, 0, 0, FULL // 2, 0)["base_pay"] == 13_000_000
    # pay is capped at the standard month
    assert compute_payslip(P, 10_000_000, 0, 0, FULL * 2, 0)["base_pay"] == 10_000_000


def test_params_drive_the_result():
    p = {**P, "social_insurance_rate": 0.1, "ot_multiplier": 2}
    assert insurance(10_000_000, p)[0] == 1_000_000
    assert compute_payslip(p, 26_000_000, 0, 0, FULL, 120)["ot_pay"] == 500_000


def test_effective_minutes_merges_approved_requests():
    assert effective_minutes((300, 0), None, P) == (300, 0)
    assert effective_minutes(None, ApprovedMinutes(adjustment=480), P) == (480, 0)
    assert effective_minutes((300, 0), ApprovedMinutes(adjustment=200), P) == (300, 0)  # larger wins
    assert effective_minutes(None, ApprovedMinutes(leave=True), P) == (480, 0)
    assert effective_minutes((500, 20), ApprovedMinutes(ot=120), P) == (500, 120)


def test_net_pay_never_negative():
    s = compute_payslip(P, 10_000_000, 1_000_000, 0, 0, 0)
    assert s["gross"] == 1_000_000 and s["net_pay"] == 0
