"""
FinWise-AI: Bileşik Getiri ve Tasarruf Simülatörü (Wealth & DCA Engine)
=======================================================================
Aylık düzenli alım (DCA) ile birikimin nominal ve enflasyondan arındırılmış (reel) değerini hesaplar.

Fisher Denklemi:  (1 + r_nominal) = (1 + r_reel) * (1 + enflasyon)

Dürüstlük Notu:
Getiri ve enflasyon oranları TAHMİN DEĞİL, kullanıcının değiştirebileceği VARSAYIMLARDIR
(`DEFAULT_ASSUMPTIONS`). Simülasyon deterministiktir; oynaklık, vergi, stopaj ve işlem maliyeti
içermez. Çıktılar yatırım tavsiyesi değildir (bkz. `DISCLAIMER`).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

DISCLAIMER = (
    "Bu hesaplama varsayımsal sabit getiri oranlarına dayanan bir senaryo çalışmasıdır; oynaklık, vergi ve "
    "masrafları içermez. Geçmiş getiriler geleceğin garantisi değildir. Yatırım tavsiyesi değildir."
)


@dataclass(frozen=True)
class MarketAssumptions:
    annual_inflation: float = 0.30
    nominal_annual_returns: Dict[str, float] = field(default_factory=lambda: {
        "BIST 100 Hisse Fonu": 0.52,
        "Gram Altın (Darphane / Fon)": 0.45,
        "Para Piyasası / Mevduat Fonu": 0.40,
        "S&P 500 / Global Teknoloji": 0.35,
    })
    safe_withdrawal_rate: float = 0.03


DEFAULT_ASSUMPTIONS = MarketAssumptions()


class WealthSimulator:
    """Enflasyon düzeltmeli bileşik getiri ve DCA simülasyon motoru."""

    ASSET_CAGR = DEFAULT_ASSUMPTIONS.nominal_annual_returns          # geriye dönük uyumluluk
    ANNUAL_INFLATION_RATE = DEFAULT_ASSUMPTIONS.annual_inflation

    @classmethod
    def calculate_real_rate(cls, nominal_annual_rate: float, inflation: Optional[float] = None) -> float:
        """Fisher Denklemi ile yıllık reel getiri."""
        inflation = cls.ANNUAL_INFLATION_RATE if inflation is None else inflation
        return (1.0 + nominal_annual_rate) / (1.0 + inflation) - 1.0

    @classmethod
    def simulate_dca(
        cls,
        monthly_saving_tl: float,
        horizon_years: int = 3,
        assumptions: MarketAssumptions = DEFAULT_ASSUMPTIONS,
        monthly_expense_tl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Args:
            monthly_saving_tl: Her ay yatırılan tutar (bugünün TL'si). Negatif olamaz.
            horizon_years: Vade (yıl), >= 1.
            monthly_expense_tl: "Özgürlük günü" metriği için aylık yaşam gideri. Verilmezse metrik hesaplanmaz.
        """
        if monthly_saving_tl < 0:
            raise ValueError("monthly_saving_tl negatif olamaz.")
        if horizon_years < 1:
            raise ValueError("horizon_years en az 1 olmalıdır.")

        months = horizon_years * 12
        total_invested = monthly_saving_tl * months
        results_by_asset: Dict[str, Any] = {}

        for asset, nominal in assumptions.nominal_annual_returns.items():
            real = cls.calculate_real_rate(nominal, assumptions.annual_inflation)
            nominal_m = (1.0 + nominal) ** (1.0 / 12.0) - 1.0
            real_m = (1.0 + real) ** (1.0 / 12.0) - 1.0
            nominal_curve, real_curve, cur_nominal, cur_real = [], [], 0.0, 0.0
            for _ in range(months):
                cur_nominal = (cur_nominal + monthly_saving_tl) * (1.0 + nominal_m)
                cur_real = (cur_real + monthly_saving_tl) * (1.0 + real_m)
                nominal_curve.append(round(cur_nominal, 2))
                real_curve.append(round(cur_real, 2))
            results_by_asset[asset] = {
                "nominal_annual_return_pct": nominal * 100,
                "real_annual_return_pct": real * 100,
                "final_nominal_value": nominal_curve[-1],
                "final_real_value": real_curve[-1],
                "nominal_curve": nominal_curve,
                "real_curve": real_curve,
                "profit_nominal": round(nominal_curve[-1] - total_invested, 2),
                "profit_real": round(real_curve[-1] - total_invested, 2),
            }

        best_asset = max(results_by_asset, key=lambda a: results_by_asset[a]["final_real_value"])
        passive_income = results_by_asset[best_asset]["final_real_value"] * assumptions.safe_withdrawal_rate / 12.0
        freedom: Dict[str, Any] = {
            "reference_asset": best_asset,
            "monthly_passive_income_today_tl": round(passive_income, 2),
            "life_days_bought_per_month": None,
            "headline": f"{horizon_years} yıl sonunda birikiminiz, bugünün parasıyla ayda yaklaşık {passive_income:,.0f} TL pasif gelir üretebilir.",
        }
        if monthly_expense_tl and monthly_expense_tl > 0:
            days = min(30.0, passive_income / (monthly_expense_tl / 30.0))
            freedom["life_days_bought_per_month"] = round(days, 1)
            freedom["headline"] = (
                f"{horizon_years} yıl sonunda bu birikimin pasif geliri, aylık {monthly_expense_tl:,.0f} TL'lik "
                f"giderinizin {days:.1f} gününü karşılayabilir."
            )

        return {
            "monthly_deposit_tl": monthly_saving_tl,
            "horizon_years": horizon_years,
            "total_invested_principal": total_invested,
            "inflation_rate_pct": assumptions.annual_inflation * 100,
            "results_by_asset": results_by_asset,
            "freedom_metric": freedom,
            "assumptions": {"annual_inflation": assumptions.annual_inflation, "nominal_annual_returns": dict(assumptions.nominal_annual_returns), "safe_withdrawal_rate": assumptions.safe_withdrawal_rate},
            "disclaimer": DISCLAIMER,
        }
