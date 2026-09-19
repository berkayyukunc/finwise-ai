"""
FinWise-AI: Denetimsiz Öğrenme ile Harcama Arketipleri (K-Means & PCA)
=====================================================================
Kullanıcının ekstresinden 8 boyutlu davranış vektörü çıkarır, K-Means++ (k=6) kümesine
atar ve PCA ile 3B koordinata indirger.

Kritik Düzeltme (Mülakat İçin):
K-Means küme kimlikleri KEYFİDİR: "küme 0" her çalıştırmada başka bir davranışa denk gelebilir.
Eski sürüm `ARCHETYPE_NAMES[cluster_id]` ile sabit isim atıyordu ve 6 arketipin 6'sı da yanlış
etiketleniyordu. Artık her küme merkezi (centroid), arketip prototip vektörleriyle ölçeklenmiş
uzayda karşılaştırılır ve Macar algoritması (Hungarian / linear_sum_assignment) ile bire bir,
toplam mesafeyi en küçükleyen eşleme yapılır. Eşleme kalitesi (`mapping_cost`, saflık, silhouette)
raporlanır.

Dürüstlük Notu:
Popülasyon SENTETİKTİR: 6 prototip etrafında üretilmiş 1200 sanal kullanıcıdır; K-Means'in bu
kümeleri "keşfetmesi" bu nedenle döngüseldir. Bu modül gerçek bir segmentasyon bulgusu değil,
kümeleme + boyut indirgeme + centroid etiketleme hattının doğru kurulmuş bir gösterimidir.
Gerçek kullanıcı verisiyle k, silhouette / gap istatistiğiyle yeniden seçilmelidir.
"""

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

FEATURE_NAMES = ["food_ratio", "market_ratio", "tech_ratio", "clothing_ratio", "sub_ratio", "weekend_ratio", "installment_ratio", "basket_norm"]
MIN_TRANSACTIONS = 5
_BASE = 0.10  # prototiplerde "nötr" öznitelik düzeyi


def _prototype(**boosts: float) -> np.ndarray:
    vec = np.full(len(FEATURE_NAMES), _BASE)
    for name, value in boosts.items():
        vec[FEATURE_NAMES.index(name)] = value
    return vec


# Arketip tanımı = isim + açıklama + davranış prototipi. Eşleme bu prototiplere göre yapılır.
ARCHETYPES: List[Dict[str, Any]] = [
    {"key": "caffeine", "title": "Kafein Filozofu ☕", "desc": "Bütçenin önemli kısmı kahvecilere ve dışarıda yemeğe gidiyor.",
     "prototype": _prototype(food_ratio=0.50)},
    {"key": "installment", "title": "Taksit Mimarı 💳", "desc": "Harcamalarının çoğunu taksite bölerek nakit akışını yöneten stratejist.",
     "prototype": _prototype(installment_ratio=0.60, basket_norm=0.40)},
    {"key": "subscription", "title": "Abonelik Koleksiyoncusu 🎬", "desc": "Netflix, Spotify, bulut gibi dijital servislere sadık dijital göçebe.",
     "prototype": _prototype(sub_ratio=0.55)},
    {"key": "weekend", "title": "Hafta Sonu Canavarı 🚀", "desc": "Hafta içi tutumlu, hafta sonu bütçeyi eriten sosyal kelebek.",
     "prototype": _prototype(weekend_ratio=0.65, food_ratio=0.35)},
    {"key": "tech", "title": "Teknoloji Gurusu ⚡", "desc": "En büyük harcama kalemleri elektronik ve donanım olan teknofil.",
     "prototype": _prototype(tech_ratio=0.55, basket_norm=0.50)},
    {"key": "saver", "title": "Tasarruf Ninjası 🥋", "desc": "Ağırlıkla market ve temel ihtiyaç harcayan, bütçe disiplini yüksek profil.",
     "prototype": _prototype(market_ratio=0.60, food_ratio=0.05, installment_ratio=0.05)},
]


class SpendingArchetypeClusterer:
    """8 boyutlu harcama davranışı kümeleyicisi."""

    def __init__(self, random_state: int = 42, population_size: int = 1200):
        self.random_state = random_state
        self.scaler = StandardScaler()
        self.kmeans = KMeans(n_clusters=len(ARCHETYPES), init="k-means++", random_state=random_state, n_init=10)
        self.pca = PCA(n_components=3, random_state=random_state)
        self.cluster_to_archetype: Dict[int, int] = {}
        self.quality: Dict[str, float] = {}
        self._fit_population_baseline(population_size)

    # ------------------------------------------------------------ popülasyon
    def _fit_population_baseline(self, n: int) -> None:
        rng = np.random.default_rng(self.random_state)  # global np.random durumuna dokunmaz
        true_profile = rng.integers(0, len(ARCHETYPES), size=n)
        X = np.stack([ARCHETYPES[p]["prototype"] for p in true_profile])
        X = X + rng.uniform(-0.05, 0.05, size=X.shape) + rng.normal(0, 0.02, size=X.shape)
        X = np.clip(X, 0.01, 1.0)

        X_scaled = self.scaler.fit_transform(X)
        self.kmeans.fit(X_scaled)
        self.pca.fit(X_scaled)
        self.population_pca = self.pca.transform(X_scaled)
        self.population_clusters = self.kmeans.labels_

        # Küme merkezi -> arketip: toplam mesafeyi en küçükleyen bire bir eşleme
        prototypes_scaled = self.scaler.transform(np.stack([a["prototype"] for a in ARCHETYPES]))
        cost = np.linalg.norm(self.kmeans.cluster_centers_[:, None, :] - prototypes_scaled[None, :, :], axis=2)
        rows, cols = linear_sum_assignment(cost)
        self.cluster_to_archetype = {int(r): int(c) for r, c in zip(rows, cols)}

        mapped = np.array([self.cluster_to_archetype[c] for c in self.population_clusters])
        self.quality = {
            "mapping_cost_mean": float(cost[rows, cols].mean()),
            "purity": float((mapped == true_profile).mean()),
            "silhouette": float(silhouette_score(X_scaled, self.population_clusters, sample_size=600, random_state=self.random_state)),
        }
        logger.info("Arketip eşlemesi: %s | kalite: %s", self.cluster_to_archetype, self.quality)

    def archetype_for_cluster(self, cluster_id: int) -> Dict[str, Any]:
        return ARCHETYPES[self.cluster_to_archetype[int(cluster_id)]]

    # --------------------------------------------------------- öznitelik çıkarımı
    def extract_features_from_statement(self, transactions_df: pd.DataFrame) -> np.ndarray:
        """İşlemlerden 8 boyutlu davranış vektörü üretir. Oranlar pozitif harcamalar üzerinden hesaplanır."""
        df = transactions_df.copy()
        defaults = {"amount": 0.0, "category": "Diğer", "date": pd.NaT, "installment_no": None}
        for col, default in defaults.items():
            if col not in df.columns:
                df[col] = default
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df = df[df["amount"] > 0]
        if df.empty:
            return np.zeros((1, len(FEATURE_NAMES)))

        total = float(df["amount"].sum())
        category = df["category"].astype(str)

        def ratio(pattern: str) -> float:
            return float(df.loc[category.str.contains(pattern, case=False, na=False, regex=True), "amount"].sum() / total)

        weekend = pd.to_datetime(df["date"], errors="coerce").dt.dayofweek >= 5
        return np.array([[
            ratio("Restoran|Yeme"),
            ratio("Market|Bakkal"),
            ratio("Elektronik|Teknoloji"),
            ratio("Giyim|Aksesuar"),
            ratio("Abonelik|Dijital"),
            float(df.loc[weekend.fillna(False), "amount"].sum() / total),
            float(df["installment_no"].notna().mean()),
            min(float(df["amount"].mean()) / 5000.0, 1.0),
        ]])

    # ------------------------------------------------------------- sınıflandırma
    def classify_user_archetype(self, transactions_df: pd.DataFrame, n_population_samples: int = 250) -> Dict[str, Any]:
        """
        Returns:
            Dict: status ('ok' | 'insufficient_data'), cluster_id, archetype_key/title/desc, user_coords,
                  centroid_distance, feature_vector, explained_variance_ratio, population_samples, quality.
            Yetersiz veride arketip ATANMAZ (boş ekstreye "Taksit Mimarı" denmez).
        """
        positive = pd.to_numeric(transactions_df.get("amount", pd.Series(dtype=float)), errors="coerce")
        n_spend = int((positive > 0).sum())

        rng = np.random.default_rng(self.random_state)  # deterministik örneklem: grafik her yenilemede aynı
        sample_idx = rng.choice(len(self.population_pca), size=min(n_population_samples, len(self.population_pca)), replace=False)
        population = [
            {"x": float(self.population_pca[i, 0]), "y": float(self.population_pca[i, 1]), "z": float(self.population_pca[i, 2]),
             "cluster": int(self.population_clusters[i]), "archetype": self.archetype_for_cluster(self.population_clusters[i])["title"]}
            for i in sample_idx
        ]
        common = {
            "explained_variance_ratio": [float(v) for v in self.pca.explained_variance_ratio_],
            "population_samples": population,
            "quality": self.quality,
            "population_is_synthetic": True,
        }

        if n_spend < MIN_TRANSACTIONS:
            return {
                "status": "insufficient_data", "cluster_id": None, "archetype_key": None,
                "archetype_title": "Henüz belirlenemedi",
                "archetype_desc": f"Harcama kişiliği için en az {MIN_TRANSACTIONS} harcama işlemi gerekir (bulunan: {n_spend}).",
                "user_coords": None, "centroid_distance": None, "feature_vector": None, **common,
            }

        features = self.extract_features_from_statement(transactions_df)
        scaled = self.scaler.transform(features)
        cluster_id = int(self.kmeans.predict(scaled)[0])
        archetype = self.archetype_for_cluster(cluster_id)
        coords = self.pca.transform(scaled)[0]
        return {
            "status": "ok",
            "cluster_id": cluster_id,
            "archetype_key": archetype["key"],
            "archetype_title": archetype["title"],
            "archetype_desc": archetype["desc"],
            "user_coords": {"x": float(coords[0]), "y": float(coords[1]), "z": float(coords[2])},
            "centroid_distance": float(np.linalg.norm(scaled[0] - self.kmeans.cluster_centers_[cluster_id])),
            "feature_vector": dict(zip(FEATURE_NAMES, (float(v) for v in features[0]))),
            **common,
        }
