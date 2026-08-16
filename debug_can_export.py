"""
Script de diagnostic — à lancer ponctuellement (pas dans l'app) pour
vérifier pourquoi le tableau VIES "N° TVA rejeté" semble non verrouillé
pour un compte gratuit.

Usage :
    python3 debug_can_export.py <user_id> <period_label>

Exemple :
    python3 debug_can_export.py 3f2504e0-... "Août 2026"

Nécessite les mêmes variables d'environnement que l'app en local
(SUPABASE_DB_URL notamment).
"""
import sys

from tva_intracom import billing as tva_billing


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    user_id, period_label = sys.argv[1], sys.argv[2]

    print(f"── Diagnostic can_export pour user_id={user_id} / période={period_label!r} ──\n")

    sub_status = tva_billing.get_subscription_status(user_id)
    print("1. get_subscription_status() :")
    print(f"   active   = {sub_status.active}")
    print(f"   plan     = {sub_status.plan}")
    print(f"   status   = {sub_status.status}")
    print(f"   period_end = {sub_status.current_period_end}")
    print()

    has_credit = tva_billing.has_export_credit(user_id, period_label)
    print(f"2. has_export_credit(user_id, {period_label!r}) = {has_credit}")
    print("   (ce helper retourne True si abonnement actif OU crédit PAYG")
    print("   pour CETTE période exacte — vérifie que period_label matche")
    print("   EXACTEMENT le format utilisé par l'app, espaces/accents compris)")
    print()

    can_export = bool(period_label) and (sub_status.active or has_credit)
    print(f"3. can_export calculé (formule de billing_gate.py) = {can_export}")
    print()

    if can_export:
        print("⚠️  can_export est True — c'est normal SEULEMENT si ce compte a")
        print("    un abonnement actif ou un crédit PAYG pour cette période précise.")
        print("    Si ce n'est PAS censé être le cas, le problème vient d'ici")
        print("    (get_subscription_status / has_export_credit / la table")
        print("    tva_subscriptions ou tva_export_credits elle-même), PAS du")
        print("    tableau VIES lui-même (qui se contente de lire can_export).")
    else:
        print("✅ can_export est False — le tableau VIES DEVRAIT donc être")
        print("    verrouillé (aperçu de 5 lignes max, reste masqué). Si tu vois")
        print("    quand même la donnée complète à l'écran, le souci est côté")
        print("    cache Streamlit (session_state / _cached_db_read) qui retient")
        print("    une ancienne valeur de can_export d'un précédent test avec un")
        print("    compte payant — vide le cache / relance une session propre")
        print("    (navigation privée) et reteste.")


if __name__ == "__main__":
    main()
