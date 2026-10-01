"""Script Streamlit exécuté par AppTest (voir test_telechargements_characterization.py).

Rend `render_telechargements()` avec un TabContext factice et toutes les
dépendances lourdes (exports Excel/XML/PDF, BCE, paywall) remplacées par des
doublures déterministes qui JOURNALISENT leurs appels. Pilotage par
st.session_state["_scn"]. Ne pas importer ailleurs.
"""
import hashlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import streamlit as st

from tva_intracom.models import BuyerType, Channel, Collector, Sale, Scenario, VatResult
from tva_intracom import local_vat_report as _lvr, rates as _rates
from tva_intracom.ui.tabs import telechargements as tl

scn = st.session_state["_scn"]
LOG = st.session_state.setdefault("_calls_log", [])


def _sale(i, amount, country, buyer_type=BuyerType.B2C):
    return Sale(sale_id=f"S{i}", display_id=f"D{i}", amount_ht=Decimal(amount), buyer_type=buyer_type,
                stock_country="FR", buyer_country=country, seller_country="FR",
                transaction_date="2026-01-1%d" % (i % 9))


def _res(i, scenario, channel, country, rate, amount="100.00", collector=Collector.SELLER,
         buyer_type=BuyerType.B2C):
    amt = Decimal(amount)
    return VatResult._new_unchecked(
        sale=_sale(i, amount, country, buyer_type), scenario=scenario, vat_country=country,
        vat_rate=Decimal(rate), vat_amount=(amt * Decimal(rate) / 100).quantize(Decimal("0.01")),
        collector=collector, channel=channel, note="n")


results = []
n = 0
if scn.get("oss", True):
    results += [_res(n := n + 1, Scenario.OSS_B2C, Channel.OSS, "DE", "19")]
if scn.get("ioss"):
    results += [_res(n := n + 1, Scenario.IOSS_DIRECT, Channel.IOSS, "DE", "19")]
if scn.get("b2b"):
    results += [_res(n := n + 1, Scenario.B2B_REVERSE_CHARGE, Channel.EXONERATION, "", "0", "250.00",
                     Collector.BUYER, BuyerType.B2B)]
for cc in scn.get("local", []):
    rate = {"PL": "23", "DE": "19", "ES": "21", "FR": "20"}.get(cc, "20")
    results += [_res(n := n + 1, Scenario.DOMESTIC, Channel.LOCAL_REGISTRATION, cc, rate),
                _res(n := n + 1, Scenario.DOMESTIC, Channel.LOCAL_REGISTRATION, cc, "10", "40.00")]
results += [_res(n := n + 1, Scenario.DOMESTIC, Channel.FR_DOMESTIC, "FR", "20")]
refunds = [_res(n := n + 1, Scenario.OSS_B2C, Channel.OSS, "DE", "19", "-30.00")] if scn.get("refunds") else []


def _gated(label, data, file_name, mime, **kw):
    if isinstance(data, (bytes, bytearray)) and (mime in ("text/csv",) or b"" == data):
        shown = bytes(data).decode("utf-8", "replace")
    else:
        shown = "sha1:" + hashlib.sha1(bytes(data)).hexdigest()[:10] + f"/len:{len(data)}"
    LOG.append(["gated_download", label, file_name, mime, shown, sorted(kw)])
    key = f"dl_{len([x for x in LOG if x[0] == 'gated_download'])}"
    st.download_button(label, data=data, file_name=file_name, mime=mime, key=key)


summary = SimpleNamespace(
    reverse_charge_ht=Decimal("250.00"), net_fr_domestic_vat=Decimal("20.00"),
    net_local_by_country={"PL": Decimal("23.00"), "DE": Decimal("19.00")})
vies = SimpleNamespace(total_not_auto_verified=scn.get("vies_unverified", 0),
                       vies_affected_sale_ids={"S1"}) if scn.get("vies", True) else None
ctx = SimpleNamespace(
    results=results, refund_results=refunds, summary=summary, vies_summary=vies,
    period_label=scn.get("period", "2026-Q1"), period_detected_range=scn.get("range"),
    can_export=scn.get("can_export", True), billing_ok=scn.get("billing_ok", True),
    sub_status=scn.get("sub_status", "active"), gated_download=_gated,
    unlock_label_suffix=" (9 €)", vies_scope_id="scope1", nom_entreprise="ACME SAS",
    siren_entreprise="111222333", tva_fr="FR11111222333", countries_with_vat=["FR", "DE"],
    local_vat_numbers=scn.get("local_vat_numbers", {"DE": "DE999888777"}),
    all_fc_transfers=[], all_invoice_credit_notes=[], all_sales=[SimpleNamespace(asin="A", amount_ht="1")] * 3,
    home_country=scn.get("home", "FR"), target_currency="EUR",
    amazon_format=scn.get("amazon_format", 0),
    oss_tva_net_total=scn.get("oss_net", Decimal("12.34")) if not scn.get("oss_net_none") else None,
    calc_key=scn.get("calc_key", "ck1"), lock_message="🔒 verrouillé")
st.session_state["_tab_ctx"] = ctx

# État préchargé : appliqué UNE SEULE FOIS (au 1er passage), sinon chaque rerun ré-injecterait
# ce que l'application vient de purger / remplacer et fausserait la caractérisation.
if not st.session_state.get("_preset_done"):
    for k, v in scn.get("preset_state", {}).items():
        st.session_state.setdefault(k, v)
    st.session_state["_preset_done"] = True
if scn.get("detailed"):
    st.session_state["display_mode"] = "detaille"


def _wbytes(tag):
    def f(*args, **kw):
        path = args[1] if len(args) > 1 else None
        LOG.append([tag, len(args[0]) if args and hasattr(args[0], "__len__") else None,
                    sorted(k for k in kw), kw.get("period"), kw.get("regime_periodicite")])
        if path:
            with open(path, "wb") as fh:
                fh.write(f"{tag}-bytes".encode())
    return f


def _ret(tag, value=None, **fixed):
    def f(*args, **kw):
        LOG.append([tag, sorted(kw), {k: (v if isinstance(v, (str, int, bool, type(None))) else repr(v))
                                        for k, v in kw.items() if k in ("period", "seller_vat", "period_label", "vat_country",
                                        "confirm_corrections", "regime_periodicite", "piece_ref", "ecriture_date", "scope_id")}])
        return value if value is not None else f"{tag}-content"
    return f


fallback = scn.get("fallback")  # dict devise -> n
xml_error = scn.get("xml_error")
sugg = []
if scn.get("neg_matched") or scn.get("neg_unmatched"):
    sugg = [SimpleNamespace(
        bucket=SimpleNamespace(departure="FR", arrival="DE", vat_rate=Decimal("19")),
        matched=[SimpleNamespace(origin_period="2025-Q4")] if scn.get("neg_matched") else [],
        unmatched_count=2 if scn.get("neg_unmatched") else 0, unmatched_ht=Decimal("1234.5"))]


if scn.get("neg_mixed"):
    sugg = [
        SimpleNamespace(bucket=SimpleNamespace(departure="FR", arrival="DE", vat_rate=Decimal("19")),
                        matched=[SimpleNamespace(origin_period="2025-Q4"), SimpleNamespace(origin_period="2025-Q3")],
                        unmatched_count=0, unmatched_ht=Decimal("0")),
        SimpleNamespace(bucket=SimpleNamespace(departure="FR", arrival="PL", vat_rate=Decimal("23")),
                        matched=[], unmatched_count=1, unmatched_ht=Decimal("10")),
    ]


def _xml(*a, **kw):
    LOG.append(["generate_oss_xml", len(kw["results"]), kw["seller_vat"], kw["period"],
                kw["local_vat_numbers"], kw["confirm_corrections"]])
    if xml_error:
        raise ValueError(xml_error)
    return b"<xml/>"


def _agg(res, period=None):
    LOG.append(["aggregate_oss_results", len(res), period])
    return "AGG"


_real_rerun = st.rerun


def _rerun(scope="app"):
    # AppTest ne sait pas faire de rerun de fragment : on journalise le scope
    # demandé (comportement à figer) puis on relance le script complet.
    LOG.append(["st.rerun", scope])
    _real_rerun()


patches = [
    patch.object(st, "rerun", side_effect=_rerun),
    # taux statiques : local_standard_rate_timeline() ne doit jamais appeler TEDB (réseau)
    patch.object(_lvr, "vat_rate", _rates.vat_rate),
    patch.object(tl, "export_xlsx", side_effect=_wbytes("export_xlsx")),
    patch.object(tl, "build_oss_excel", side_effect=_wbytes("build_oss_excel")),
    patch.object(tl, "build_ioss_excel", side_effect=_wbytes("build_ioss_excel")),
    patch.object(tl, "build_b2b_excel", side_effect=_wbytes("build_b2b_excel")),
    patch.object(tl, "generate_oss_xml", side_effect=_xml),
    patch.object(tl, "aggregate_oss_results", side_effect=_agg),
    patch.object(tl, "find_oss_negative_buckets", side_effect=lambda agg: [1] if (scn.get("neg_matched") or scn.get("neg_unmatched") or scn.get("neg_mixed")) else []),
    patch.object(tl, "preview_negative_bucket_suggestions", side_effect=lambda r, p: (LOG.append(["preview_neg", len(r), p]), sugg)[1]),
    patch.object(tl, "generate_ca3_html_report_v2", side_effect=_ret("ca3_html", "<html>ca3</html>")),
    patch.object(tl, "generate_ca3_edi_preparation_csv", side_effect=_ret("ca3_edi", b"a;b\n1;2\n")),
    patch.object(tl, "generate_local_vat_html_report", side_effect=_ret("local_html", "<html>local</html>")),
    patch.object(tl, "generate_fec_bytes", side_effect=lambda *a, **kw: (LOG.append(["fec", len(a[0]), sorted(kw), kw.get("period"), kw.get("ecriture_date"), kw.get("piece_ref")]), b"fec")[1]),
    patch.object(tl, "build_rates_evidence_xlsx", side_effect=lambda r, p, **kw: (LOG.append(["evidence_xlsx", len(r), p, sorted(k for k in kw if k != "translator")]), b"xlsx-ev")[1]),
    patch.object(tl, "generate_rates_evidence_pdf", side_effect=lambda r, p, **kw: (LOG.append(["evidence_pdf", len(r), p, sorted(k for k in kw if k != "translator"), kw.get("xlsx_bytes")]), b"%PDF-ev")[1]),
    patch.object(tl, "reset_oss_rate_fallback_stats", side_effect=lambda: LOG.append(["reset_fallback"])),
    patch.object(tl, "get_oss_rate_fallback_stats", side_effect=lambda: (LOG.append(["get_fallback"]), dict(fallback or {}))[1]),
    patch.object(tl, "detect_format3_grouped_risk",
                 side_effect=lambda s: (_ for _ in ()).throw(RuntimeError("boom")) if scn.get("format3_raises") else bool(scn.get("format3_risk"))),
]
_real_remove = tl.os.remove
_leaked = []


def _failing_remove(path):
    _leaked.append(path)
    LOG.append(["os.remove_failed"])
    raise OSError("locked")


if scn.get("remove_fails"):
    patches.append(patch.object(tl.os, "remove", side_effect=_failing_remove))
if scn.get("empty_box_mapping"):
    patches.append(patch.object(tl, "LOCAL_VAT_BOX_CODES", {"PL": (["A", "B", "C", "D", "E"], {})}))
for p in patches:
    p.start()
try:
    tl.render_telechargements()
finally:
    for p in patches:
        p.stop()
    for _p in _leaked:
        try:
            _real_remove(_p)
        except OSError:
            pass
    st.session_state["_keys"] = sorted(
        k for k in st.session_state.keys()
        if not str(k).startswith(("_scn", "_calls_log", "_keys", "$$", "_tab_ctx", "_preset_done")))
