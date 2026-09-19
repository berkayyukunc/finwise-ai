"""Tema ve grafik paleti kuralları: renk kararları koda bağlandığı için testle korunur."""

import glob
import re
from pathlib import Path

import pandas as pd
import pytest

from src.ui import theme

PAGE_FILES = ["app.py", *sorted(glob.glob("pages/*.py"))]


@pytest.fixture(scope="module")
def page_sources() -> dict:
    return {p: Path(p).read_text(encoding="utf-8") for p in PAGE_FILES}


def test_no_dark_template_or_stray_hex_left_in_pages(page_sources):
    """Renkler tek kaynaktan (src/ui/theme.py) gelir; sayfalarda elle yazılmış palet olmamalı."""
    for path, src in page_sources.items():
        assert "plotly_dark" not in src, f"{path}: koyu şablon kalmış"
        stray = {h for h in re.findall(r"#[0-9a-fA-F]{6}", src) if h.lower() not in {"#ffffff", "#c24a1c", "#57544e"}}
        assert not stray, f"{path}: temaya bağlanmamış renk(ler): {sorted(stray)}"


def test_every_plotly_figure_uses_the_shared_template(page_sources):
    for path, src in page_sources.items():
        layouts = src.count("update_layout(")
        assert src.count("template=theme.TEMPLATE") >= layouts, f"{path}: bazı grafikler şablonsuz"


def test_palette_has_no_more_slots_than_validated():
    """Doğrulanan 7 slot: fazlası için 8. renk uydurulmaz, nötr kovaya düşer."""
    assert len(theme.CATEGORICAL) == theme.MAX_CATEGORIES == 7
    assert len(set(theme.CATEGORICAL)) == 7
    assert theme.OTHER_COLOR not in theme.CATEGORICAL


def test_category_colors_are_assigned_in_fixed_order_never_cycled():
    labels = ["Market / Bakkal", "Restoran / Yeme-İçme", "Giyim / Aksesuar"]
    assert theme.category_colors(labels) == theme.CATEGORICAL[:3]
    # Aynı sıradaki etiket her zaman aynı rengi alır (renk sıraya bağlıdır, etiket adına değil)
    assert theme.category_colors(labels[::-1]) == theme.CATEGORICAL[:3]


def test_other_bucket_is_neutral_and_does_not_consume_a_slot():
    labels = ["Market / Bakkal", f"{theme.OTHER_LABEL} (3)", "Restoran / Yeme-İçme"]
    colors = theme.category_colors(labels)
    assert colors == [theme.CATEGORICAL[0], theme.OTHER_COLOR, theme.CATEGORICAL[1]]


def test_model_other_class_is_a_real_category_not_the_bucket():
    """Modelin 'Diğer / Belirsiz' sınıfı gerçek bir kategoridir; nötr kovayla karıştırılmamalı."""
    assert theme.category_colors(["Diğer / Belirsiz"]) == [theme.CATEGORICAL[0]]


@pytest.mark.parametrize("n_categories,expected_rows", [(3, 3), (7, 7), (12, 8)])
def test_top_n_with_other_buckets_the_tail(n_categories, expected_rows):
    series = pd.Series({f"Kategori {i}": 100 - i for i in range(n_categories)})
    out = theme.top_n_with_other(series)
    assert len(out) == expected_rows
    assert out.sum() == pytest.approx(series.sum()), "Kovaya alma toplamı değiştirmemeli"
    assert list(out.values) == sorted(out.values[:theme.MAX_CATEGORIES], reverse=True) + list(out.values[theme.MAX_CATEGORIES:])
    if n_categories > theme.MAX_CATEGORIES:
        assert out.index[-1].startswith(theme.OTHER_LABEL)


def test_diverging_pair_is_not_red_green():
    """Yeşil/kırmızı ikilisi en yaygın renk körlüğü türünde ayırt edilemez; ıraksak çift kırmızı/mavidir."""
    pos, neg = theme.DIVERGING_POS.lower(), theme.DIVERGING_NEG.lower()
    assert pos != neg
    red, green, blue = (int(neg[i:i + 2], 16) for i in (1, 3, 5))
    assert blue > red and blue > green, "Negatif kutup mavi olmalı"


def test_shap_html_uses_the_diverging_pair_not_green():
    from src.nlp.explainer import POSShapExplainer
    html = POSShapExplainer.render_html([("migros", 2.0), ("levent", -1.5), ("tic", 0.01)])
    assert "208, 59, 59" in html and "42, 120, 214" in html
    assert "34, 197, 94" not in html, "Yeşil/kırmızı ikilisine geri dönülmüş"
    assert html.count("title='SHAP") == 2, "Renk tek başına anlam taşımamalı: değer ipucunda olmalı"


def test_plotly_template_registers_once_and_is_light():
    theme.register_plotly_template()
    theme.register_plotly_template()
    import plotly.io as pio
    tpl = pio.templates[theme.TEMPLATE]
    assert tpl.layout.paper_bgcolor == theme.SURFACE == "#fdfaf6"
    assert list(tpl.layout.colorway) == theme.CATEGORICAL
    assert tpl.layout.xaxis.automargin and tpl.layout.yaxis.automargin, "Uzun Türkçe etiketler kırpılmamalı"


def test_streamlit_config_matches_theme_module():
    """config.toml ile theme.py aynı renkleri söylemeli; ikisi ayrışırsa arayüz iki başlı görünür."""
    config = Path(".streamlit/config.toml").read_text(encoding="utf-8")
    assert 'base = "light"' in config
    assert f'primaryColor = "{theme.BRAND}"' in config
    assert f'backgroundColor = "{theme.SURFACE}"' in config
