# Module `tva_intracom.ecb_rates`

## Description
Module de gestion des taux de change EUR via l'API de la Banque Centrale Européenne (BCE/ECB). Utilise le service SDW (Statistical Data Warehouse) de la BCE qui fournit les taux de référence quotidiens sans clé API. Gère un cache deux niveaux (mémoire + Postgres), le prefetch parallèle, et la conversion pour les périodes multiples (semestres, années).

## Fonctions publiques

### `get_rate(currency: str, date: date) -> Decimal | None`
**Description courte** : Retourne le taux de change EUR vers la devise pour une date donnée.

**Description détaillée :**
- Cherche d'abord dans le cache mémoire
- Si non trouvé, cherche dans le cache PostgreSQL
- Si non trouvé, appelle l'API BCE
- Stocke le résultat dans le cache mémoire et PostgreSQL
- Retourne None si le taux n'est pas disponible
- Utilise un retry exponentiel en cas d'échec réseau

**Préconditions :**
- La devise doit être un code ISO valide (ex: "USD", "GBP")
- La date doit être valide

**Postconditions :**
- Le taux est stocké dans les caches
- Les anciens taux (> 2 ans) sont purgés du cache PostgreSQL

**Effets de bord :**
- Appelle l'API BCE (appel réseau)
- Met à jour le cache PostgreSQL

**Paramètres :**
- `currency` (str) : Code devise ISO (ex: "USD")
- `date` (date) : Date du taux souhaité

**Retour :** Decimal | None - Taux de change ou None

**Exceptions :**
- `ValueError` : Devise ou date invalide
- `NetworkError` : Erreur réseau après retries

---

### `prefetch_rates(currencies: list[str], dates: list[date]) -> dict`
**Description courte** : Précharge plusieurs taux de change en parallèle.

**Description détaillée :**
- Utilise ThreadPoolExecutor pour charger les taux en parallèle
- Optimise le temps de chargement pour plusieurs devises/dates
- Stocke tous les résultats dans le cache
- Retourne un dictionnaire {(currency, date): rate}
- Utile pour précharger les taux avant un calcul

**Préconditions :**
- Les devises doivent être valides
- Les dates doivent être valides

**Postconditions :**
- Tous les taux sont dans le cache

**Effets de bord :**
- Appelle l'API BCE plusieurs fois en parallèle

**Paramètres :**
- `currencies` (list[str]) : Liste des codes devises
- `dates` (list[date]) : Liste des dates

**Retour :** dict - Dictionnaire {(currency, date): rate}

**Exceptions :**
- `ValueError` : Devise ou date invalide
- `NetworkError` : Erreur réseau

---

### `convert_to_eur_for_oss(amount: Decimal, currency: str, tx_date: date, period_start: date, period_end: date) -> Decimal`
**Description courte** : Convertit un montant en EUR pour l'OSS avec le taux de clôture de période.

**Description détaillée :**
- Pour l'OSS, la conversion utilise le taux de clôture de période
- Règl. UE 2020/194, art. 5 bis
- Si la période est mensuelle, utilise le taux du dernier jour du mois
- Si la période est multiple (semestre, année), calcule le taux moyen
- Retourne le montant en EUR

**Préconditions :**
- amount doit être positif
- currency doit être valide
- tx_date doit être dans la période
- period_start < period_end

**Postconditions :**
- Aucun (fonction pure avec appels de cache)

**Effets de bord :**
- Appelle get_rate pour le taux de clôture

**Paramètres :**
- `amount` (Decimal) : Montant à convertir
- `currency` (str) : Devise source
- `tx_date` (date) : Date de la transaction
- `period_start` (date) : Début de période
- `period_end` (date) : Fin de période

**Retour :** Decimal - Montant en EUR

**Exceptions :**
- `ValueError` : Paramètres invalides
- `RateUnavailableError` : Taux non disponible

---

### `close_idle_connections() -> None`
**Description courte** : Ferme les connexions PostgreSQL inactives.

**Description détaillée :**
- Appelé par app.py au début de chaque run
- Ferme la connexion partagée que le thread avait ouverte lors du run précédent
- Délègue au pool partagé (database.close_idle_connections)
- Idempotent si d'autres modules l'ont déjà appelé

**Préconditions :**
- Aucune

**Postconditions :**
- Les connexions inactives sont fermées

**Effets de bord :**
- Ferme des connexions PostgreSQL

**Paramètres :**
- Aucun

**Retour :** None

**Exceptions :**
- Aucune (silencieuse en cas d'erreur)

---

### `clear_memory_cache() -> None`
**Description courte** : Vide le cache mémoire des taux de change.

**Description détaillée :**
- Vide le cache en mémoire uniquement
- Le cache PostgreSQL reste intact
- Utile pour forcer un rechargement depuis la base de données

**Préconditions :**
- Aucune

**Postconditions :**
- Le cache mémoire est vide

**Effets de bord :**
- Aucun (vide le cache en mémoire)

**Paramètres :**
- Aucun

**Retour :** None

**Exceptions :**
- Aucune

---

## Tables de base de données

### `ecb_rate_cache`
- `currency` (TEXT, PRIMARY KEY) : Code devise ISO
- `rate_date` (DATE, PRIMARY KEY) : Date du taux
- `rate` (NUMERIC) : Taux de change
- `fetched_at` (TIMESTAMPTZ) : Date de récupération

## Configuration requise

- `SUPABASE_DB_URL` : URL de connexion à la base de données (optionnel, fallback sur cache mémoire)
- `ECB_CACHE_TTL` : Durée de vie du cache en secondes (défaut: 86400 = 24h)
- `ECB_RETRY_MAX_ATTEMPTS` : Nombre maximum de tentatives (défaut: 3)
- `ECB_RETRY_BACKOFF_BASE` : Base du backoff exponentiel (défaut: 2)
- `ECB_PREFETCH_MAX_WORKERS` : Nombre maximum de workers pour le prefetch (défaut: 5)

## Notes

- Le module reste utilisable sans base de données configurée (SUPABASE_DB_URL absent)
- Dans ce cas, il retombe silencieusement sur le cache mémoire uniquement
- Les taux BCE sont des données publiques, pas besoin de clé API
