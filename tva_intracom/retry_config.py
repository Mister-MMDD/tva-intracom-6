"""Constantes de résilience réseau partagées.

Centralise les paramètres de retry/backoff et de mise en cache des échecs
utilisés par les clients réseau du moteur (BCE dans ``ecb_rates.py``, TEDB
SOAP dans ``vat_rates_db.py``). Ces deux modules avaient chacun leur propre
copie de ces constantes, avec le risque qu'elles divergent silencieusement
au fil des correctifs. `vies_engine.py` n'est PAS concerné : il ne fait pas
de boucle retry/backoff (validation parallélisée via ``ThreadPoolExecutor``,
avec un simple ``DEFAULT_TIMEOUT`` par requête), donc rien à y centraliser.

Purement des constantes en mémoire — aucun impact sur la compatibilité
scale-to-zero (pas de thread, pas de connexion persistante).
"""

from __future__ import annotations

# Backoff exponentiel sur erreurs réseau/HTTP transitoires (dont HTTP 429).
# Ne couvre PAS les réponses malformées (JSON/XML invalide, structure
# inattendue) : une réponse mal formée n'est pas transitoire, la retenter
# ne change rien. Ne couvre pas non plus les erreurs SSL de vérification de
# certificat (permanentes pour la durée du process, court-circuitées en
# amont dans chaque module appelant).
FETCH_MAX_ATTEMPTS = 3
FETCH_BACKOFF_BASE_SECONDS = 1.0  # 1s, puis 2s, puis 4s

# Mémorise, par process, les paires ayant déjà échoué côté réseau — évite
# de re-tenter (avec FETCH_MAX_ATTEMPTS essais + backoff) pour CHAQUE ligne
# d'un fichier contenant de nombreuses ventes sur le même pays/devise/jour
# quand le service distant est injoignable. TTL volontairement court : au
# cas où la panne serait transitoire.
FAILED_PAIR_TTL_SECONDS = 300  # 5 minutes
