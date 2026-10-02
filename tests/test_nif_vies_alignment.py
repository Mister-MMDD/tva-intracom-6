"""Alignement NIF / VIES (2026-10-02).

Un NIF (identifiant fiscal national, jamais soumis à VIES) ne doit PAS apparaître dans
`vies_affected_sale_ids` (onglet « Risque VIES » / Excel) : il a son propre ensemble
`nif_affected_sale_ids` et sa propre phrase explicative (note moteur, onglet VIES, Excel).
"""
from tva_intracom.i18n.i18n_validator import load_all_translations

_NEW_KEYS = [
    "audit_tab_nif", "audit_nif_info", "audit_nif_error", "audit_nif_success",
    "xl_audit_nature_nif", "vies_expl_nif_departure", "vies_expl_nif_destination",
    "engine_note_b2b_nif_departure", "engine_note_b2b_nif_destination_oss",
]


def test_nif_i18n_keys_in_all_languages():
    tr = load_all_translations()
    for lang, d in tr.items():
        for k in _NEW_KEYS:
            assert k in d, f"{lang}: clé manquante {k}"


def test_nif_note_differs_from_invalid_vies_note():
    tr = load_all_translations()
    for lang, d in tr.items():
        assert d["engine_note_b2b_nif_departure"] != d.get("engine_note_b2b_no_vies_departure", "")
        assert d["vies_expl_nif_departure"] != d["vies_expl_cross_border_departure"]
        assert d["vies_expl_nif_destination"] != d["vies_expl_cross_border_destination"]
