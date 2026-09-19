"""
FinWise-AI: Finansal Copilot — Kural Tabanlı Niyet Yönlendirici + Deterministik Araçlar
=======================================================================================
Dürüst İsimlendirme:
Bu modül bir LLM, RAG ya da öğrenilmiş "tool-calling" ajanı DEĞİLDİR. Üç katmandan oluşur:

    soru ──> [1] IntentRouter (Türkçe katlama + kök eşleme) ──> niyet + slotlar
         ──> [2] Araç (saf Pandas fonksiyonu)                ──> ToolResult (sayılar burada üretilir)
         ──> [3] Şablon (render)                             ──> yanıt metni

Garanti: Yanıttaki her TL tutarı [2]'deki ToolResult'tan gelir; şablon katmanı aritmetik yapmaz
(bkz. tests/test_copilot.py::test_every_amount_in_answer_comes_from_tool_result).
Anlaşılmayan ya da veriyle yanıtlanamayan sorular (ör. "geçen aya göre") genel özete
DÜŞÜRÜLMEZ; sistem neyi yanıtlayamadığını ve neleri yanıtlayabildiğini açıkça söyler.

Genişleme Noktası: Katman [1] bir LLM ile değiştirilebilir (LLM yalnızca {intent, slots} JSON'u
üretir; sayılar yine [2]'den gelir). Bu, endüstrideki "LLM yönlendirir, kod hesaplar" kalıbıdır.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor
from src.predictive.forecasting import SpendingForecaster
from src.predictive.wealth_simulator import DISCLAIMER, WealthSimulator

fold = TurkishFinancialNLPPreprocessor.fold


def tl(value: float) -> str:
    """1234.5 -> '1.234,50 TL' (Türkçe biçim)."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


@dataclass
class ToolResult:
    tool: str
    data: Dict[str, Any] = field(default_factory=dict)

    def amounts(self) -> List[float]:
        """Sonuçtaki tüm sayısal değerler (yanıt doğrulaması için)."""
        found: List[float] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, bool):
                return
            if isinstance(obj, (int, float)):
                found.append(float(obj))
            elif isinstance(obj, dict):
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    walk(v)
        walk(self.data)
        return found


# Kategori sözlüğü: (ad, ikon, ekstredeki kategori adında aranacak kökler, soruda aranacak regex kökleri)
CATEGORY_LEXICON: List[Tuple[str, str, List[str], List[str]]] = [
    ("Market / Gıda", "🛒", ["market", "bakkal"], ["market", "bakkal", "gida", "mutfak"]),
    ("Restoran / Yeme-İçme", "☕", ["restoran", "yeme"], ["restoran", "yemek", "yemeg", "yedim", "kahve", "kafe", "disari", "lokanta"]),
    ("Akaryakıt / Ulaşım", "⛽", ["akaryakit", "ulasim"], ["akaryakit", "benzin", "yakit", "ulasim", r"taksi(?!t)", "ucak", "otopark", "mazot"]),
    ("Giyim / Aksesuar", "👕", ["giyim", "aksesuar", "moda"], ["giyim", "kiyafet", "ayakkabi", "elbise"]),
    ("Elektronik / Teknoloji", "💻", ["elektronik", "teknoloji"], ["elektronik", "teknoloji", "bilgisayar"]),
    ("Dijital Servis / Abonelik", "🎬", ["abonelik", "dijital"], ["abonelik", "aboneli", "uyelik", "streaming"]),
    ("Sağlık / Kozmetik", "💊", ["saglik", "kozmetik"], ["saglik", "eczane", "hastane", "kozmetik", "doktor"]),
    ("Ev / Yaşam / Fatura", "🏠", ["fatura", "yasam"], ["fatura", "elektrik", "dogalgaz", "mobilya", "kira", "aidat"]),
    ("Eğlence / Hobi / Kitap", "🎭", ["eglence", "hobi", "kitap"], ["eglence", "sinema", "kitap", "hobi", "konser", "tiyatro"]),
    ("Finans / Vergi / Masraf", "🏦", ["finans", "vergi", "masraf"], ["faiz", "vergi", "masraf", "komisyon", "kkdf", "bsmv"]),
    ("E-Ticaret / Pazaryeri", "📦", ["e-ticaret", "pazaryeri"], ["e-ticaret", "eticaret", "pazaryeri", "online alisveris", "internet alisveris"]),
]

INTENT_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("unsupported_temporal", ["gecen ay", "onceki ay", "gecen yil", "gecen hafta", "artis", "artti", "azaldi", "azalis", "kiyasla"]),
    ("anomalies", ["anomali", "supheli", "olagandisi", "mukerrer", "cift cekim", "dolandiricilik", "tanimadigim"]),
    ("refunds", ["iade", "iptal"]),
    ("obligations", ["taksit", "gelecek ay", "onumuzdeki ay", "sonraki ay", "odeyecegim", "tahmin", "ongoru", "beklenen"]),
    ("savings", ["tasarruf", "biriktir", "birikim", "oneri", "tavsiye", "kisabilir", "kisarim"]),
    ("top_merchants", ["en cok", "en fazla", "nereye", "hangi magaza", "hangi isyeri", "lider", "nerelere"]),
    ("summary", ["ozet", "toplam", "genel", "borc", "asgari", "ekstre", "durum"]),
]

MERCHANT_STOPWORDS = {"market", "magaza", "magazacilik", "ticaret", "kahve", "gida", "istanbul", "ankara", "izmir", "sanayi", "perakende", "hizmetleri", "elektronik", "giyim", "<num>", "<date>", "<cur>"}

SUPPORTED_EXAMPLES = [
    "Markete ne kadar harcadım?", "Starbucks'a ne kadar gitti?", "Markete mi restorana mı daha çok harcadım?",
    "En çok nereye harcadım?", "Gelecek ay taksitlerim ne kadar?", "Şüpheli işlem var mı?", "İadelerim ne kadar?", "Nasıl tasarruf ederim?",
]


class FinancialCopilot:
    """Ekstre verisi üzerinde deterministik soru-cevap."""

    def __init__(self, transactions_df: pd.DataFrame, total_debt: float, bank_name: str = "Banka", min_payment: Optional[float] = None):
        self.df = transactions_df.copy()
        self.total_debt = float(total_debt or 0.0)
        self.min_payment = min_payment
        self.bank_name = bank_name
        defaults = {"amount": 0.0, "category": "Diğer / Belirsiz", "transaction_type": "EXPENSE"}
        for col, default in defaults.items():
            if col not in self.df.columns:
                self.df[col] = default
        if "clean_description" not in self.df.columns:
            self.df["clean_description"] = self.df["raw_description"] if "raw_description" in self.df.columns else "Bilinmeyen İşlem"
        self.df["amount"] = pd.to_numeric(self.df["amount"], errors="coerce").fillna(0.0)
        self.df["category"] = self.df["category"].fillna("Diğer / Belirsiz").astype(str)
        self.df["_cat_folded"] = self.df["category"].map(fold)
        self.df["_merchant_folded"] = self.df["clean_description"].astype(str).map(TurkishFinancialNLPPreprocessor.clean_pos_text)

        self.spend = self.df[(self.df["amount"] > 0) & ~self.df["transaction_type"].astype(str).isin(["PAYMENT", "POINT_REDEMPTION"])]
        self.refunds = self.df[(self.df["amount"] < 0) & (self.df["transaction_type"].astype(str) != "PAYMENT")]
        self.last_result: Optional[ToolResult] = None

        self._tools: Dict[str, Callable[..., ToolResult]] = {
            "summary": self.tool_summary, "category_spend": self.tool_category_spend, "compare_categories": self.tool_compare_categories,
            "merchant_spend": self.tool_merchant_spend, "top_merchants": self.tool_top_merchants, "anomalies": self.tool_anomalies,
            "refunds": self.tool_refunds, "obligations": self.tool_obligations, "savings": self.tool_savings,
        }

    # =========================================================== [1] yönlendirme
    def _merchant_tokens(self) -> Dict[str, str]:
        tokens: Dict[str, str] = {}
        for merchant in self.spend["_merchant_folded"].unique():
            for tok in merchant.split():
                if len(tok) >= 4 and tok not in MERCHANT_STOPWORDS:
                    tokens.setdefault(tok, merchant)
        return tokens

    def route(self, question: str) -> Tuple[str, Dict[str, Any]]:
        """Soru -> (niyet, slotlar). Saf fonksiyondur; test edilebilir."""
        q = fold(question)
        if not q.strip():
            return "unknown", {}

        def has(stems: List[str]) -> bool:
            # Kökler kelime BAŞINDA aranır ('markete', 'marketten' eşleşir) ve regex parçası olabilir:
            # r"taksi(?!t)" -> 'taksiye' eşleşir, 'taksitlerim' eşleşmez.
            return any(re.search(rf"(?<![^\W\d_]){k}", q) for k in stems)

        if has(dict(INTENT_KEYWORDS)["unsupported_temporal"]):
            return "unsupported_temporal", {}

        categories = [i for i, (_, _, _, q_stems) in enumerate(CATEGORY_LEXICON) if has(q_stems)]
        if len(categories) >= 2:
            return "compare_categories", {"category_ids": categories}
        if len(categories) == 1:
            return "category_spend", {"category_id": categories[0]}

        for tok in self._merchant_tokens():
            if re.search(rf"(?<![^\W\d_]){re.escape(tok)}", q):
                return "merchant_spend", {"merchant_token": tok}

        for intent, keywords in INTENT_KEYWORDS[1:]:
            if has(keywords):
                return intent, {}
        return "unknown", {}

    # ============================================================== [2] araçlar
    def _category_rows(self, frame: pd.DataFrame, category_id: int) -> pd.DataFrame:
        stems = CATEGORY_LEXICON[category_id][2]
        # astype(bool) şart: boş tabloda object tipli maske, pandas tarafından kolon seçimi sanılır
        return frame.loc[frame["_cat_folded"].map(lambda c: any(s in c for s in stems)).astype(bool)]

    def _total_spend(self) -> float:
        return round(float(self.spend["amount"].sum()), 2)

    def _top(self, frame: pd.DataFrame, n: int) -> List[Dict[str, Any]]:
        grouped = frame.groupby("clean_description")["amount"].agg(["sum", "size"]).sort_values("sum", ascending=False).head(n)
        return [{"merchant": str(m), "amount": round(float(r["sum"]), 2), "count": int(r["size"])} for m, r in grouped.iterrows()]

    def tool_summary(self) -> ToolResult:
        by_cat = self.spend.groupby("category")["amount"].sum().sort_values(ascending=False).head(4)
        return ToolResult("summary", {
            "total_spend": self._total_spend(), "total_refunds": round(float(-self.refunds["amount"].sum()), 2),
            "total_debt": round(self.total_debt, 2), "min_payment": self.min_payment, "transaction_count": int(len(self.df)),
            "top_categories": [{"category": str(c), "amount": round(float(a), 2)} for c, a in by_cat.items()],
        })

    def tool_category_spend(self, category_id: int) -> ToolResult:
        rows, refunds = self._category_rows(self.spend, category_id), self._category_rows(self.refunds, category_id)
        gross, total = round(float(rows["amount"].sum()), 2), self._total_spend()
        refund = round(float(-refunds["amount"].sum()), 2)
        return ToolResult("category_spend", {
            "category": CATEGORY_LEXICON[category_id][0], "icon": CATEGORY_LEXICON[category_id][1], "gross_spend": gross, "refunds": refund,
            "net_spend": round(gross - refund, 2), "transaction_count": int(len(rows)),
            "share_pct": round(100.0 * gross / total, 1) if total > 0 else 0.0, "top_merchants": self._top(rows, 3),
        })

    def tool_compare_categories(self, category_ids: List[int]) -> ToolResult:
        items = sorted((self.tool_category_spend(c).data for c in category_ids), key=lambda d: d["gross_spend"], reverse=True)
        diff = round(items[0]["gross_spend"] - items[1]["gross_spend"], 2)
        return ToolResult("compare_categories", {"items": items, "difference_top_two": diff})

    def tool_merchant_spend(self, merchant_token: str) -> ToolResult:
        rows = self.spend.loc[self.spend["_merchant_folded"].str.contains(rf"(?<![^\W\d_]){re.escape(merchant_token)}", regex=True).astype(bool)]
        return ToolResult("merchant_spend", {
            "merchant_token": merchant_token, "gross_spend": round(float(rows["amount"].sum()), 2), "transaction_count": int(len(rows)),
            "transactions": [{"date": str(r["date"]) if "date" in rows.columns else "", "merchant": str(r["clean_description"]), "amount": round(float(r["amount"]), 2)} for _, r in rows.head(5).iterrows()],
        })

    def tool_top_merchants(self, n: int = 5) -> ToolResult:
        top, total = self._top(self.spend, n), self._total_spend()
        covered = round(sum(t["amount"] for t in top), 2)
        return ToolResult("top_merchants", {"top_merchants": top, "covered_amount": covered, "covered_share_pct": round(100.0 * covered / total, 1) if total > 0 else 0.0})

    def tool_anomalies(self) -> ToolResult:
        if "is_anomaly" not in self.df.columns:
            return ToolResult("anomalies", {"available": False, "items": []})
        rows = self.df[self.df["is_anomaly"].astype(bool)]
        return ToolResult("anomalies", {"available": True, "items": [
            {"merchant": str(r["clean_description"]), "amount": round(float(r["amount"]), 2), "reason": str(r.get("anomaly_reason", ""))} for _, r in rows.iterrows()]})

    def tool_refunds(self) -> ToolResult:
        return ToolResult("refunds", {"total_refunds": round(float(-self.refunds["amount"].sum()), 2), "items": [
            {"merchant": str(r["clean_description"]), "amount": round(float(-r["amount"]), 2)} for _, r in self.refunds.iterrows()]})

    def tool_obligations(self) -> ToolResult:
        fc = SpendingForecaster(self.df.drop(columns=["_cat_folded", "_merchant_folded"])).forecast_next_30_days()
        ob = fc["obligations"]
        return ToolResult("obligations", {
            "installments": ob["installments"], "subscriptions": ob["subscriptions"], "installments_total": round(ob["installments_total"], 2),
            "subscriptions_total": round(ob["subscriptions_total"], 2), "committed_total": round(ob["installments_total"] + ob["subscriptions_total"], 2),
            "forecast_status": fc["status"], "method": fc["method_used"],
            "expected_total": round(fc["total_expected_monthly"], 2) if fc["total_expected_monthly"] is not None else None,
            "upper_95": round(fc["risk_upper_monthly"], 2) if fc["risk_upper_monthly"] is not None else None,
        })

    def tool_savings(self, cut_ratio: float = 0.20, horizon_years: int = 3) -> ToolResult:
        discretionary = self.spend.loc[~self.spend["_cat_folded"].str.contains("finans|vergi|fatura|diger", regex=True).astype(bool)]
        if discretionary.empty:
            return ToolResult("savings", {"available": False})
        by_cat = discretionary.groupby("category")["amount"].sum().sort_values(ascending=False)
        top_cat, top_amount = str(by_cat.index[0]), round(float(by_cat.iloc[0]), 2)
        monthly_saving = round(top_amount * cut_ratio, 2)
        sim = WealthSimulator.simulate_dca(monthly_saving, horizon_years)
        conservative = sim["results_by_asset"]["Para Piyasası / Mevduat Fonu"]
        subs = self._category_rows(self.spend, 5)
        return ToolResult("savings", {
            "available": True, "top_category": top_cat, "top_amount": top_amount, "cut_pct": round(cut_ratio * 100), "monthly_saving": monthly_saving,
            "horizon_years": horizon_years, "total_invested": round(sim["total_invested_principal"], 2), "final_real_value": conservative["final_real_value"],
            "inflation_pct": round(sim["inflation_rate_pct"]), "nominal_return_pct": round(conservative["nominal_annual_return_pct"]),
            "subscription_total": round(float(subs["amount"].sum()), 2), "subscription_count": int(len(subs)),
        })

    # ============================================================== [3] şablonlar
    def ask(self, question: str) -> str:
        intent, slots = self.route(question)
        if intent in self._tools:
            self.last_result = self._tools[intent](**slots)
            return getattr(self, f"_render_{intent}")(self.last_result.data)
        self.last_result = ToolResult(intent)
        examples = "\n".join(f"- {e}" for e in SUPPORTED_EXAMPLES)
        if intent == "unsupported_temporal":
            return ("⏳ **Bu soruyu yanıtlayamıyorum:** yalnızca yüklediğiniz tek ekstre dönemini görüyorum; önceki dönemlerle "
                    f"karşılaştırma yapmak için veri yok. Bu ekstre hakkında şunları sorabilirsiniz:\n{examples}")
        return f"🤔 **Bu soruyu anlayamadım.** Tahmin yürütmek yerine söylemeyi tercih ederim. Şunları yanıtlayabilirim:\n{examples}"

    @staticmethod
    def _merchant_list(items: List[Dict[str, Any]]) -> str:
        return ", ".join(f"{i['merchant']} ({tl(i['amount'])})" for i in items) or "kayıt yok"

    def _render_summary(self, d: Dict[str, Any]) -> str:
        cats = ", ".join(f"{c['category']} ({tl(c['amount'])})" for c in d["top_categories"]) or "yok"
        minimum = f"\n- Asgari Ödeme: **{tl(d['min_payment'])}**" if d["min_payment"] else ""
        return (f"💳 **{self.bank_name} Ekstre Özeti**\n- Toplam Harcama: **{tl(d['total_spend'])}** ({d['transaction_count']} işlem)\n"
                f"- İadeler: **{tl(d['total_refunds'])}**\n- Dönem Borcu: **{tl(d['total_debt'])}**{minimum}\n- En Yoğun Kategoriler: {cats}")

    def _render_category_spend(self, d: Dict[str, Any]) -> str:
        refund = f"\n- İadeler: {tl(d['refunds'])} → Net: **{tl(d['net_spend'])}**" if d["refunds"] > 0 else ""
        return (f"{d['icon']} **{d['category']}**\n- Harcama: **{tl(d['gross_spend'])}** ({d['transaction_count']} işlem, toplam harcamanın %{d['share_pct']}'i){refund}\n"
                f"- Öne çıkan işyerleri: {self._merchant_list(d['top_merchants'])}")

    def _render_compare_categories(self, d: Dict[str, Any]) -> str:
        lines = "\n".join(f"{i + 1}. {it['icon']} **{it['category']}**: {tl(it['gross_spend'])} ({it['transaction_count']} işlem)" for i, it in enumerate(d["items"]))
        return f"⚖️ **Kategori Karşılaştırması**\n{lines}\n\nİlk iki kalem arasındaki fark: **{tl(d['difference_top_two'])}**"

    def _render_merchant_spend(self, d: Dict[str, Any]) -> str:
        rows = "\n".join(f"- {t['date']} · {t['merchant']}: {tl(t['amount'])}" for t in d["transactions"])
        return f"🏪 **'{d['merchant_token']}' içeren işyerleri**\n- Toplam: **{tl(d['gross_spend'])}** ({d['transaction_count']} işlem)\n{rows}"

    def _render_top_merchants(self, d: Dict[str, Any]) -> str:
        rows = "\n".join(f"- **{t['merchant']}**: {tl(t['amount'])} ({t['count']} işlem)" for t in d["top_merchants"])
        return f"📊 **En Çok Harcama Yapılan {len(d['top_merchants'])} İşyeri**\n{rows}\n\n💡 Bu işyerleri toplam harcamanızın %{d['covered_share_pct']}'ini ({tl(d['covered_amount'])}) oluşturuyor."

    def _render_anomalies(self, d: Dict[str, Any]) -> str:
        if not d["available"]:
            return "🚨 Bu veri üzerinde anomali taraması çalıştırılmamış."
        if not d["items"]:
            return "✅ Bu ekstrede olağandışı bir işlem saptanmadı."
        rows = "\n".join(f"- **{i['merchant']}** ({tl(i['amount'])}): {i['reason']}" for i in d["items"])
        return f"🚨 **İncelemenizi önerdiğim {len(d['items'])} işlem:**\n{rows}"

    def _render_refunds(self, d: Dict[str, Any]) -> str:
        if not d["items"]:
            return "↩️ Bu ekstrede iade / iptal işlemi yok."
        return f"↩️ **İadeler: {tl(d['total_refunds'])}**\n" + "\n".join(f"- {i['merchant']}: {tl(i['amount'])}" for i in d["items"])

    def _render_obligations(self, d: Dict[str, Any]) -> str:
        inst = "\n".join(f"- {i['description']}: {tl(i['amount'])} ({i['next_installment']}/{i['installment_total'] or '?'})" for i in d["installments"]) or "- yok"
        subs = "\n".join(f"- {s['description']}: {tl(s['amount'])}" for s in d["subscriptions"]) or "- yok"
        text = (f"📅 **Gelecek 30 Günün Bilinen Yükümlülükleri: {tl(d['committed_total'])}**\n**Taksitler ({tl(d['installments_total'])})**\n{inst}\n"
                f"**Abonelikler ({tl(d['subscriptions_total'])})**\n{subs}")
        if d["expected_total"] is not None:
            text += f"\n\nİsteğe bağlı harcamalarla birlikte beklenen toplam: **{tl(d['expected_total'])}** (%95 üst sınır: {tl(d['upper_95'])}; yöntem: {d['method']})."
        else:
            text += "\n\nİsteğe bağlı harcama tahmini için yeterli veri yok; yalnızca kesinleşmiş kalemler gösterildi."
        return text

    def _render_savings(self, d: Dict[str, Any]) -> str:
        if not d["available"]:
            return "🎯 Tasarruf önerisi üretebilmek için kategorilendirilmiş harcama bulunamadı."
        subs = (f"\n2. **Abonelikler:** {d['subscription_count']} işlemde {tl(d['subscription_total'])} ödüyorsunuz; kullanmadıklarınızı gözden geçirin."
                if d["subscription_count"] else "")
        return (f"🎯 **Verinize Dayalı Tasarruf Senaryosu**\n1. **En yüksek isteğe bağlı kalem: {d['top_category']}** ({tl(d['top_amount'])}). "
                f"%{d['cut_pct']} kısmak ayda **{tl(d['monthly_saving'])}** bırakır.{subs}\n"
                f"3. Bu tutarı {d['horizon_years']} yıl boyunca para piyasası fonunda biriktirirseniz ({tl(d['total_invested'])} anapara; varsayım: yıllık %{d['nominal_return_pct']} "
                f"nominal getiri, %{d['inflation_pct']} enflasyon) bugünün parasıyla yaklaşık **{tl(d['final_real_value'])}** eder.\n\n_{DISCLAIMER}_")
