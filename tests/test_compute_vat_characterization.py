"""Tests de caractérisation de ``engine.compute_vat`` (filet avant découpage).

Principe : on fige, sur le code D'ORIGINE, le résultat complet (scénario, pays,
taux, montant, collecteur, canal, note) d'une grille combinatoire d'entrées et
de cas ciblés. Un refactor doit reproduire la référence à l'identique.

* Déterministe et sans réseau : ``engine.vat_rate`` est remplacé par le taux
  statique ``rates.vat_rate`` (historique par date) ; toute vente a une date.
* Référence : ``tests/golden/compute_vat_grid.json`` (régénérable
  volontairement via ``UPDATE_COMPUTE_VAT_GOLDEN=1`` — jamais pour un simple
  refactor).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tva_intracom import engine, rates
from tva_intracom.models import BuyerType, Sale

_GOLDEN = Path(__file__).parent / "golden" / "compute_vat_grid.json"

BUYERS = [("FR", ""), ("DE", ""), ("ES", ""), ("IT", ""), ("PL", ""), ("MC", ""),
          ("ES", "35001"), ("GB", ""), ("US", "")]
STOCKS = ["FR", "DE", "ES", "MC", "PL", "US"]
SELLERS = ["FR", "DE", "US"]
# (buyer_type, buyer_vat_valid, buyer_vat_number)
BUYER_KINDS = [(BuyerType.B2C, False, ""), (BuyerType.B2C, False, "X1234567"),
               (BuyerType.B2B, True, "DE123456789"), (BuyerType.B2B, False, "DE123456789")]
AMOUNTS = ["-40.00", "100.00", "500.00"]
# (ioss_number, ioss_own_number_active)
IOSS = [("", False), ("IM1234567890", False), ("IM1234567890", True)]
CATEGORIES = ["STANDARD", "BOOKS"]


def _sale(**kw) -> Sale:
    base = dict(sale_id="S", amount_ht=Decimal("100.00"), buyer_type=BuyerType.B2C,
                stock_country="FR", buyer_country="DE", seller_country="FR",
                transaction_date="2026-03-15")
    base.update(kw)
    return Sale(**base)


def _snap(res) -> dict:
    return {
        "scenario": str(res.scenario), "vat_country": res.vat_country,
        "vat_rate": str(res.vat_rate), "vat_amount": str(res.vat_amount),
        "collector": str(res.collector), "channel": str(res.channel),
        "note": res.note,
    }


def _grid_cases():
    for (bc, pc), sc, sl, (bt, valid, num), amt, (ioss, act), imp, cat in itertools.product(
            BUYERS, STOCKS, SELLERS, BUYER_KINDS, AMOUNTS, IOSS, (False, True), CATEGORIES):
        key = "|".join([bc, pc, sc, sl, bt.name, str(valid), num, amt, ioss, str(act), str(imp), cat])
        sale = _sale(buyer_country=bc, arrival_post_code=pc, stock_country=sc, seller_country=sl,
                     buyer_type=bt, buyer_vat_valid=valid, buyer_vat_number=num,
                     amount_ht=Decimal(amt), ioss_number=ioss, seller_is_importer=imp)
        yield key, dict(sale=sale, marketplace_name="Amazon", product_category=cat,
                        lang="fr", ioss_own_number_active=act)


def _targeted_cases():
    c = {}
    # Catégorie : paramètre > champ Sale > STANDARD ; OUT_OF_SCOPE court-circuite tout
    c["oos_param"] = dict(sale=_sale(buyer_country="MC"), product_category="out_of_scope", lang="fr")
    c["oos_field"] = dict(sale=_sale(product_category="OUT_OF_SCOPE", buyer_country="US"), lang="en")
    c["cat_field_books"] = dict(sale=_sale(product_category="BOOKS"), lang="fr")
    c["cat_param_overrides_field"] = dict(sale=_sale(product_category="BOOKS"), product_category="STANDARD", lang="fr")
    c["cat_blank_spaces"] = dict(sale=_sale(), product_category="  books ", lang="fr")
    # Dates : historique de taux, avoir différé (order_date), date malformée, tx_date injectée
    for cc in ("EE", "RO", "FR"):
        for d in ("2025-06-30", "2025-07-01", "2025-07-31", "2025-08-01", "2026-03-15"):
            c[f"hist_{cc}_{d}"] = dict(sale=_sale(buyer_country=cc, transaction_date=d), lang="fr")
    c["credit_note_order_date"] = dict(
        sale=_sale(buyer_country="EE", amount_ht=Decimal("-50.00"), transaction_date="2026-03-15",
                   order_date="2025-06-10"), lang="fr")
    c["positive_ignores_order_date"] = dict(
        sale=_sale(buyer_country="EE", transaction_date="2026-03-15", order_date="2025-06-10"), lang="fr")
    c["credit_note_no_order_date"] = dict(
        sale=_sale(buyer_country="EE", amount_ht=Decimal("-50.00"), transaction_date="2025-06-10"), lang="fr")
    c["malformed_date"] = dict(sale=_sale(transaction_date="2026-13-45"), lang="fr")
    c["datetime_string"] = dict(sale=_sale(transaction_date="2025-06-10T12:30:00Z", buyer_country="EE"), lang="fr")
    c["tx_date_injected"] = dict(sale=_sale(buyer_country="EE", transaction_date="2026-03-15"),
                                 tx_date=date(2025, 6, 10), lang="fr")
    c["monaco_hist_tx_date"] = dict(sale=_sale(buyer_country="MC", transaction_date="2025-06-10"), lang="fr")
    # lang=None (résolution locale) et date de transaction vide (taux courant, pays au taux stable)
    c["lang_none"] = dict(sale=_sale(), lang=None)
    c["empty_date_de"] = dict(sale=_sale(transaction_date=""), lang="fr")
    c["empty_date_monaco"] = dict(sale=_sale(transaction_date="", buyer_country="MC"), lang="fr")
    # Langues / marketplace
    for lg in ("fr", "en", "de", "es", "it", "pl", "pt"):
        c[f"lang_{lg}_oss"] = dict(sale=_sale(), lang=lg)
        c[f"lang_{lg}_deemed"] = dict(sale=_sale(seller_country="US", stock_country="DE"), lang=lg,
                                      marketplace_name="eBay")
        c[f"lang_{lg}_monaco"] = dict(sale=_sale(buyer_country="MC", stock_country="DE"), lang=lg)
    # Bornes du seuil IOSS (150 €) et sous-cas DDP
    for amt in ("149.99", "150.00", "150.01"):
        c[f"ioss_{amt}_deemed"] = dict(sale=_sale(stock_country="US", amount_ht=Decimal(amt)), lang="fr")
        c[f"ioss_{amt}_direct"] = dict(sale=_sale(stock_country="US", amount_ht=Decimal(amt),
                                                  ioss_number="IM1"), ioss_own_number_active=True, lang="fr")
    c["ddp_home"] = dict(sale=_sale(stock_country="US", buyer_country="FR", amount_ht=Decimal("300"),
                                    seller_is_importer=True), lang="fr")
    c["ddp_foreign"] = dict(sale=_sale(stock_country="US", buyer_country="DE", amount_ht=Decimal("300"),
                                       seller_is_importer=True), lang="fr")
    # Autoliquidation domestique hors FR (DOMESTIC_REVERSE_CHARGE_COUNTRIES) : NIF sur B2C
    for cc in sorted(rates.DOMESTIC_REVERSE_CHARGE_COUNTRIES):
        c[f"rc_dom_{cc}_b2b"] = dict(sale=_sale(stock_country=cc, buyer_country=cc, seller_country="FR",
                                                buyer_type=BuyerType.B2B), lang="fr")
        c[f"rc_dom_{cc}_b2c_nif"] = dict(sale=_sale(stock_country=cc, buyer_country=cc, seller_country="FR",
                                                   buyer_vat_number="NIF123"), lang="fr")
        c[f"b2b_novies_{cc}"] = dict(sale=_sale(stock_country="FR", buyer_country=cc,
                                                buyer_type=BuyerType.B2B, buyer_vat_number="X"), lang="fr")
        c[f"b2b_novies_{cc}_stock_foreign"] = dict(sale=_sale(stock_country="DE", seller_country="FR",
                                                              buyer_country=cc, buyer_type=BuyerType.B2B), lang="fr")
    # Territoires exclus (art. 6) et stocks Monaco
    for cc, pc in (("ES", "38001"), ("DE", "27498"), ("FI", "22100"), ("FR", "97100"), ("IT", "23030")):
        c[f"excl_{cc}_{pc}"] = dict(sale=_sale(buyer_country=cc, arrival_post_code=pc), lang="fr")
    for bc in ("FR", "DE", "MC", "US", "ES"):
        for bt, v in ((BuyerType.B2C, False), (BuyerType.B2B, True), (BuyerType.B2B, False)):
            for sl in ("FR", "MC", "DE"):
                c[f"mc_stock_{bc}_{bt.name}_{v}_{sl}"] = dict(
                    sale=_sale(stock_country="MC", buyer_country=bc, buyer_type=bt, buyer_vat_valid=v,
                               seller_country=sl), lang="fr")
    return c


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


@pytest.fixture(autouse=True)
def _static_rates(monkeypatch):
    monkeypatch.setattr(engine, "vat_rate", rates.vat_rate)
    yield


def _run_all() -> dict:
    notes: dict[str, str] = {}
    rows: dict[str, list] = {}
    errors: dict[str, str] = {}
    for key, kw in itertools.chain(_grid_cases(), (("T:" + k, v) for k, v in _targeted_cases().items())):
        try:
            s = _snap(engine.compute_vat(**kw))
        except Exception as exc:  # exceptions aussi figées (type + message)
            errors[key] = f"{type(exc).__name__}: {exc}"
            continue
        note = s.pop("note")
        nh = _h(note)
        notes[nh] = note
        rows[key] = [s["scenario"], s["vat_country"], s["vat_rate"], s["vat_amount"],
                     s["collector"], s["channel"], nh]
    return {"rows": rows, "notes": notes, "errors": errors}


def _load_or_update() -> dict:
    snap = _run_all()
    if os.environ.get("UPDATE_COMPUTE_VAT_GOLDEN") == "1":
        _GOLDEN.write_text(json.dumps(snap, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
        pytest.skip("golden régénéré")
    assert _GOLDEN.exists(), "référence manquante (UPDATE_COMPUTE_VAT_GOLDEN=1 pour la créer)"
    return snap


def test_compute_vat_matches_golden():
    snap = _load_or_update()
    exp = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert set(snap["rows"]) == set(exp["rows"])
    diffs = [k for k in exp["rows"] if snap["rows"][k] != exp["rows"][k]]
    assert not diffs, f"{len(diffs)} divergences, ex. {diffs[:5]} : " + \
        "; ".join(f"{k}: attendu {exp['rows'][k]} obtenu {snap['rows'][k]}" for k in diffs[:3])
    assert snap["notes"] == exp["notes"]
    assert snap["errors"] == exp["errors"]


def test_grid_covers_every_scenario():
    exp = json.loads(_GOLDEN.read_text(encoding="utf-8")) if _GOLDEN.exists() else _run_all()
    seen = {r[0] for r in exp["rows"].values()}
    from tva_intracom.models import Scenario
    assert {str(s) for s in Scenario} <= seen, {str(s) for s in Scenario} - seen
