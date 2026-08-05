"""
Script de debug : réconcilie le compteur `invalid_count` (93) affiché dans
l'app avec la liste de 108 numéros que tu obtiens en dédupliquant le tableau.

Usage :
    SUPABASE_DB_URL="postgresql://..." python3 debug_reconciliation_93_vs_108.py <scope_id>

Ce script n'écrit rien en base, il ne fait que lire et classer.
"""
import os
import sys
import psycopg2

# Colle ici ta liste de 108 numéros (telle que fournie), un par ligne dans la
# liste ci-dessous, ou modifie pour lire depuis un fichier texte si plus simple.
VAT_LIST_108 = """
2669340305 1502180092 1941690677 2350860561 2554190187 2802750188 2862130347
2882650647 3016751202 3035580350 3187810969 3624460139 3645930961 3866460961
3880820927 4072120282 4307000408 4681290161 4718670237 4833531009 4962360238
5586981002 5590310651 5788090875 6304190967 6725791211 9022311006 9360170964
11688421004 11739700968 13118510018 15379921008 17725571008
01833694L 06251465L 40514487X 49220702N 51235746A 52393130G 77734979Q Z1263482M
A63570667 B01409762 B09787425 B09962592 B19380187 B19645936 B21821228 B22669410
B26712950 B26734608 B30870653 B37417896 B42628529 B42751586 B44705242 B44903078
B44988467 B45497765 B46831152 B54349899 B55747810 B56284888 B61280038 B62355169
B62943956 B64284441 B65714438 B65885360 B66080995 B66678749 B67205914 B67437251
B67935171 B70440169 B71547129 B72749492 B75319285 B75419770 B75488122 B75506188
B76259332 B79069100 B79816328 B81095127 B83568899 B83707588 B84541622 B86261534
B87226007 B87646618 B88227137 B88565569 B90212192 B90423666 B90447657 B92693001
B93060655 B96081435 B96340211
BE0542555246 DE157743365 E07701501 E54132683 ESB98700974 ESY6274365W F99091738
G41241373
""".split()

# Motifs Espagnol NIF/NIE : lettre finale + pas de préfixe pays à 2 lettres en tête,
# OU commence par une lettre (NIE) suivie de chiffres et se termine par une lettre,
# et ne commence PAS par "ES". C'est une heuristique -- à valider avec la doc
# officielle NIF/NIE si besoin, mais suffisante pour ce diagnostic.
def looks_like_spanish_nif_without_prefix(v: str) -> bool:
    if v[:2].isalpha() and v[:2].upper() in {"ES", "FR", "DE", "BE", "IT", "PL", "PT", "NL", "AT"}:
        return False  # a déjà un préfixe pays EU standard
    return (v[-1].isalpha() and any(c.isdigit() for c in v))


def normalize_guess(v: str) -> str:
    """Reproduit approximativement normalize_full_vat pour préfixer ES si pertinent."""
    if looks_like_spanish_nif_without_prefix(v):
        return "ES" + v
    return v


def main():
    scope_id = sys.argv[1] if len(sys.argv) > 1 else None
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("SUPABASE_DB_URL non définie -- affichage de la classification heuristique uniquement (sans interroger la base).")
        conn = None
    else:
        conn = psycopg2.connect(db_url)

    categories = {"nif_national": [], "manual_override": [], "inconclusive_cache": [],
                  "invalid_confirmed": [], "unresolved": []}

    for raw in VAT_LIST_108:
        if looks_like_spanish_nif_without_prefix(raw) and raw.upper() not in {"ES" + raw.upper()}:
            # Ne PAS confondre avec un vrai numéro TVA espagnol type "ESB..." déjà taggé ES.
            if not raw.upper().startswith(("ES", "FR", "DE", "BE", "IT", "PL", "PT")):
                categories["nif_national"].append(raw)
                continue

        if conn is None:
            categories["unresolved"].append(raw)
            continue

        full_vat = normalize_guess(raw)
        with conn.cursor() as cur:
            # 1) override manuel ?
            cur.execute(
                "SELECT is_valid, set_at FROM vies_manual_overrides "
                "WHERE scope_id = %s AND full_vat = %s",
                (scope_id, full_vat),
            )
            row = cur.fetchone()
            if row:
                categories["manual_override"].append((raw, full_vat, row))
                continue

            # 2) dernier résultat connu (scope puis global)
            cur.execute(
                "SELECT valid, error, checked_at FROM vies_scope_cache "
                "WHERE scope_id = %s AND vat_id = %s ORDER BY checked_at DESC LIMIT 1",
                (scope_id, full_vat),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "SELECT valid, error, checked_at FROM vies_global_cache "
                    "WHERE vat_id = %s ORDER BY checked_at DESC LIMIT 1",
                    (full_vat,),
                )
                row = cur.fetchone()

            if not row:
                categories["unresolved"].append(raw)
                continue

            valid, error, checked_at = row
            transient_markers = (
                "ms_unavailable", "service_unavailable", "ms_max_concurrent_req",
                "global_max_concurrent_req", "timeout", "erreur de connexion",
                "erreur http 500", "erreur http 502", "erreur http 503",
                "erreur http 504", "non concluante", "remote end closed connection",
                "connection reset by peer", "broken pipe", "connection aborted",
                "server closed the connection", "could not connect", "connection refused",
            )
            is_transient = any(m in (error or "").lower() for m in transient_markers)

            if is_transient:
                categories["inconclusive_cache"].append((raw, full_vat, error, checked_at))
            elif not valid:
                categories["invalid_confirmed"].append((raw, full_vat, checked_at))
            else:
                categories["unresolved"].append(raw)  # valide en cache mais présent dans ta liste -> à investiguer

    print("=" * 70)
    for cat, items in categories.items():
        print(f"\n{cat} : {len(items)}")
        for it in items:
            print("   ", it)
    print("\n" + "=" * 70)
    print(f"Total liste fournie : {len(VAT_LIST_108)}")
    print(f"invalid_confirmed (devrait ~= invalid_count de l'app) : {len(categories['invalid_confirmed'])}")
    print(f"nif_national (jamais passé par VIES) : {len(categories['nif_national'])}")
    print(f"manual_override : {len(categories['manual_override'])}")
    print(f"inconclusive_cache (devrait aller dans inconclusive_count, pas invalid_count) : {len(categories['inconclusive_cache'])}")
    print(f"unresolved (à investiguer manuellement) : {len(categories['unresolved'])}")

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
