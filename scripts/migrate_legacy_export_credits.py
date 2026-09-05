"""
Script de migration manuelle — à lancer ponctuellement (pas dans l'app) pour
resserrer les crédits PAYG achetés AVANT le rattachement d'un crédit à un
SIREN précis (voir BUGFIX 2026-09-04 : colonne `siren` sur
tva_export_credits, has_export_credit()).

Ces crédits "legacy" (siren='') restent valables pour n'importe quel SIREN
de leur organisation — ils ne sont donc PAS bloquants en l'état, juste moins
stricts que les nouveaux crédits (scellés à un SIREN dès l'achat). Ce script
ne "corrige" rien d'urgent : il permet, pour les organisations où le SIREN
concerné peut être établi SANS AMBIGUÏTÉ, de resserrer rétroactivement ces
crédits — par exemple avant un audit, ou si vous voulez que le comportement
soit strictement identique pour tous les crédits, anciens et nouveaux.

RÈGLE DE DÉCISION (volontairement conservatrice — pas de devinette) :
  - l'organisation n'a QU'UN SEUL SIREN enregistré au moment du script
    -> ce SIREN est forcément celui concerné par le crédit (il n'y en a pas
       d'autre) : migration automatique possible.
  - l'organisation a 0 ou >= 2 SIREN enregistrés
    -> impossible de déterminer avec certitude lequel des SIREN était visé
       par ce crédit passé (l'info n'existait pas à l'achat) : SIGNALÉ pour
       décision manuelle, jamais deviné automatiquement.

Usage :
    python3 scripts/migrate_legacy_export_credits.py            # dry-run (par défaut)
    python3 scripts/migrate_legacy_export_credits.py --apply    # applique réellement

Nécessite les mêmes variables d'environnement que l'app en local
(SUPABASE_DB_URL notamment).
"""
import sys
from pathlib import Path

# Le script vit dans scripts/, un niveau sous la racine du projet (là où se
# trouve le package tva_intracom/) — contrairement à debug_can_export.py qui
# est à la racine et bénéficie automatiquement de l'ajout de son propre
# dossier à sys.path par Python. On ajoute donc explicitement la racine du
# projet (dossier parent de scripts/) à sys.path, AVANT l'import, sinon :
# "ModuleNotFoundError: No module named 'tva_intracom'".
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tva_intracom import billing as tva_billing


def main():
    apply_changes = "--apply" in sys.argv[1:]

    legacy_credits = tva_billing.list_legacy_export_credits()
    if not legacy_credits:
        print("Aucun crédit legacy (siren='') trouvé — rien à migrer.")
        return

    print(f"── {len(legacy_credits)} crédit(s) legacy trouvé(s) ──\n")

    # Cache : évite un aller-retour Postgres par ligne pour les org
    # apparaissant plusieurs fois (plusieurs périodes achetées).
    _sirens_by_org: dict[str, list[dict]] = {}

    n_auto, n_manual = 0, 0
    for credit in legacy_credits:
        org_id = credit["org_id"]
        period_label = credit["period_label"]

        if org_id not in _sirens_by_org:
            try:
                _sirens_by_org[org_id] = tva_billing.list_registered_sirens(org_id)
            except Exception as _err:
                print(f"  ⚠ org_id={org_id} : impossible de lire les SIREN enregistrés ({_err}) — signalé pour décision manuelle.")
                _sirens_by_org[org_id] = []

        _sirens = _sirens_by_org[org_id]

        if len(_sirens) == 1:
            _siren = _sirens[0]["siren"]
            n_auto += 1
            print(f"  ✓ org_id={org_id!r} période={period_label!r} -> SIREN unique {_siren!r} (non ambigu)")
            if apply_changes:
                _n = tva_billing.assign_siren_to_legacy_credit(org_id, period_label, _siren)
                if _n:
                    print(f"      appliqué ({_n} ligne mise à jour)")
                else:
                    print("      ⚠ aucune ligne mise à jour (déjà migré entre-temps ?)")
        else:
            n_manual += 1
            _label = "aucun SIREN enregistré" if not _sirens else f"{len(_sirens)} SIREN enregistrés"
            print(f"  ✗ org_id={org_id!r} période={period_label!r} -> AMBIGU ({_label}) — décision manuelle requise")
            for _s in _sirens:
                print(f"      candidat possible : {_s['siren']} ({_s.get('company_name') or 'sans nom'})")

    print(f"\n{n_auto} crédit(s) migré(s) sans ambiguïté" + ("" if apply_changes else " (dry-run — relancez avec --apply pour écrire)"))
    print(f"{n_manual} crédit(s) restent ambigus — laissés en 'siren=\"\"' (valables pour tout SIREN de l'org, sans danger), à trancher au cas par cas si besoin.")


if __name__ == "__main__":
    main()
