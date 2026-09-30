"""Non-régression — correctifs du 2026-09-30.

1. Clés i18n utilisées dans le code mais absentes des TOML (bloc dégradation AIC
   du rapport CA3 affichait les clés brutes).
2. `detect_format3_grouped_risk` : plantage sur valeurs None + faux positifs sur
   les commandes multi-articles.
3. `build_oss_excel` : signature d'origine conservée (pas de paramètres morts).
"""
from __future__ import annotations

import ast
import inspect
import re
from decimal import Decimal
from pathlib import Path

import pytest
import toml

from tva_intracom.parsers.amazon.detect import detect_format3_grouped_risk

_ROOT = Path(__file__).resolve().parent.parent
_I18N = _ROOT / "tva_intracom" / "i18n"
_LANGS = ("fr", "en", "es", "de", "it", "pt", "pl")
_KEY_RE = re.compile(r"""\b(?:_|i18n_|tr|t)\(\s*["']([a-zA-Z0-9_.]+)["']""")
_AMT = "total_activity_value_amt_vat_excl"


# ---------------------------------------------------------------------------
# 1. i18n
# ---------------------------------------------------------------------------

def _flat(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        if isinstance(v, dict):
            out |= _flat(v, prefix + k + ".")
        else:
            out.add(prefix + k)
    return out


def _source_files() -> list[Path]:
    files = [p for p in (_ROOT / "tva_intracom").rglob("*.py")]
    files.append(_ROOT / "app.py")
    return files


def test_every_key_used_in_code_exists_in_fr():
    """Garde-fou global : aucun appel `_("clé")` ne doit pointer vers une clé absente."""
    fr_keys = _flat(toml.load(_I18N / "fr.toml"))
    missing: dict[str, str] = {}
    for f in _source_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in _KEY_RE.finditer(text):
            key = m.group(1)
            if key not in fr_keys:
                missing.setdefault(key, f.name)
    assert not missing, f"Clés i18n utilisées mais absentes de fr.toml : {missing}"


@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.parametrize("key", [
    "ca3_aic_degradation_warning",
    "ca3_aic_degradation_qty",
    "ca3_aic_degradation_asin",
    "amazon_format3_grouped_warning",
])
def test_new_keys_present_in_all_locales(lang, key):
    data = toml.load(_I18N / f"{lang}.toml")
    assert key in data and data[key].strip()


@pytest.mark.parametrize("lang", _LANGS)
def test_degradation_count_placeholder(lang):
    data = toml.load(_I18N / f"{lang}.toml")
    for key in ("ca3_aic_degradation_qty", "ca3_aic_degradation_asin"):
        assert "{count}" in data[key], (lang, key)


def test_ca3_report_shows_degradation_note_translated():
    """Le rapport HTML doit afficher le texte traduit (et non la clé brute)."""
    from tva_intracom import ca3_report

    src = inspect.getsource(ca3_report.generate_ca3_html_report_v2)
    # Pas de guillemets doubles imbriqués dans une f-string (exige Python >= 3.12)
    tree = ast.parse(inspect.getsource(ca3_report))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    seg = ast.get_source_segment(inspect.getsource(ca3_report), v.value) or ""
                    assert '_("ca3_aic_degradation' not in seg
    assert "ca3_aic_degradation_warning" in src


# ---------------------------------------------------------------------------
# 2. detect_format3_grouped_risk
# ---------------------------------------------------------------------------

def test_format3_multi_item_order_is_not_a_false_positive():
    rows = [
        {"order_id": "A", "asin": "B1", _AMT: "10.00"},
        {"order_id": "A", "asin": "B2", _AMT: "25.00"},
    ]
    assert detect_format3_grouped_risk(rows) is False


def test_format3_detects_multiple_of_unit_price():
    rows = [{"asin": "B1", _AMT: "10.00"}, {"asin": "B1", _AMT: "30.00"}]
    assert detect_format3_grouped_risk(rows) is True


def test_format3_ordinary_price_variation_is_ignored():
    rows = [{"asin": "B1", _AMT: "10.00"}, {"asin": "B1", _AMT: "12.50"}]
    assert detect_format3_grouped_risk(rows) is False


def test_format3_refund_amounts_use_absolute_value():
    rows = [{"asin": "B1", _AMT: "-10"}, {"asin": "B1", _AMT: "20"}]
    assert detect_format3_grouped_risk(rows) is True


def test_format3_decimal_comma():
    rows = [{"asin": "B1", _AMT: "9,90"}, {"asin": "B1", _AMT: "29,70"}]
    assert detect_format3_grouped_risk(rows) is True


@pytest.mark.parametrize("rows", [
    [],
    [{}],
    [{"order_id": None, "asin": None, _AMT: None}],
    [{"asin": "B1", _AMT: ""}, {"asin": "B1", _AMT: "abc"}],
    [{"asin": "B1", _AMT: Decimal("0")}],
])
def test_format3_never_crashes_on_malformed_rows(rows):
    assert detect_format3_grouped_risk(rows) is False


# ---------------------------------------------------------------------------
# 3. build_oss_excel
# ---------------------------------------------------------------------------

def test_build_oss_excel_signature_has_no_dead_params():
    from tva_intracom.oss_export import build_oss_excel

    params = list(inspect.signature(build_oss_excel).parameters)
    assert params == ["results", "output_path", "period", "data"]


# ---------------------------------------------------------------------------
# 4. Docstrings fiscales (art. 5 bis Règl. 2020/194)
# ---------------------------------------------------------------------------

def test_closing_rate_docstring_keeps_forward_search_semantics():
    from tva_intracom import ecb_rates

    doc = ecb_rates.get_closing_rate.__doc__ or ""
    assert "5 bis" in doc and "EN AVANT" in doc


def test_aggregate_oss_docstring_keeps_oss_ioss_separation():
    from tva_intracom.oss_export import aggregate_oss_results

    doc = aggregate_oss_results.__doc__ or ""
    assert "IOSS" in doc and "trimestrielle" in doc
