# Module `tva_intracom.vat_rates_db`

## Description
Module de gestion des taux de TVA dynamiques via l'API TEDB (Taxes in Europe Database) de la Commission européenne. Fournit des taux de TVA historisés par pays avec repli sur les tables statiques (`rates.py`). Gère le cache PostgreSQL, le prefetch parallèle, et les garde-fous de plausibilité.

## Fonctions publiques

### `vat_rate(country: str, date: date, category: str = "STANDARD") -> Decimal`
**Description courte** : Retourne le taux de TVA pour un pays, une date et une catégorie.

**Description détaillée :**
- Cherche d'abord dans le cache PostgreSQL
- Si non trouvé, appelle l'API TEDB
- Si TEDB échoue ou ne retourne pas de résultat, repli sur `rates.py` (statique)
- Supporte les catégories : STANDARD, REDUCED, SUPER_REDUCED, BOOKS, CLOTHING, MEDICINES
- Applique un garde-fou de plausibilité (écart > 3 points vs statique = rejet)
- Retourne le taux sous forme de Decimal (ex: 0.20 pour 20%)

**Préconditions :**
- country doit être un code pays ISO valide
- date doit être valide
- category doit être valide

**Postconditions :**
- Le taux est stocké dans le cache PostgreSQL
- Si TEDB échoue, le repli statique est utilisé

**Effets de bord :**
- Appelle l'API TEDB (appel réseau)
- Met à jour le cache PostgreSQL

**Paramètres :**
- `country` (str) : Code pays ISO (ex: "FR", "DE")
- `date` (date) : Date du taux souhaité
- `category` (str) : Catégorie de produit (défaut: "STANDARD")

**Retour :** Decimal - Taux de TVA (ex: 0.20 pour 20%)

**Exceptions :**
- `ValueError` : Paramètres invalides
- `TedbRateInvalidError` : Taux TEDB invalide (garde-fou déclenché)
- `NetworkError` : Erreur réseau après retries

---

### `prefetch_vat_rates(countries: list[str], dates: list[date], categories: list[str] = None) -> dict`
**Description courte** : Précharge plusieurs taux de TVA en parallèle.

**Description détaillée :**
- Utilise ThreadPoolExecutor pour charger les taux en parallèle
- Optimise le temps de chargement pour plusieurs pays/dates/catégories
- Stocke tous les résultats dans le cache
- Retourne un dictionnaire {(country, date, category): rate}
- Utile pour précharger les taux avant un calcul

**Préconditions :**
- Les pays doivent être valides
- Les dates doivent être valides
- Les catégories doivent être valides

**Postconditions :**
- Tous les taux sont dans le cache

**Effets de bord :**
- Appelle l'API TEDB plusieurs fois en parallèle

**Paramètres :**
- `countries` (list[str]) : Liste des codes pays
- `dates` (list[date]) : Liste des dates
- `categories` (list[str]) : Liste des catégories (défaut: ["STANDARD"])

**Retour :** dict - Dictionnaire {(country, date, category): rate}

**Exceptions :**
- `ValueError` : Paramètres invalides
- `NetworkError` : Erreur réseau

---

### `clear_vat_cache() -> None`
**Description courte** : Vide le cache PostgreSQL des taux de TVA.

**Description détaillée :**
- Vide la table `vat_rate_cache`
- Utile pour forcer un rechargement depuis TEDB
- Attention : peut ralentir les calculs suivants

**Préconditions :**
- Aucune

**Postconditions :**
- Le cache PostgreSQL est vide

**Effets de bord :**
- Supprime toutes les entrées de la table

**Paramètres :**
- Aucun

**Retour :** None

**Exceptions :**
- `DatabaseError` : Erreur lors de la suppression

---

### `get_tedb_fallback_stats() -> dict`
**Description courte** : Retourne les statistiques de repli TEDB.

**Description détaillée :**
- Compte le nombre de requêtes TEDB réussies
- Compte le nombre de replis sur les tables statiques
- Retourne un dictionnaire avec les statistiques
- Utile pour le monitoring et le debug

**Préconditions :**
- Aucune

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Aucun (lecture des compteurs en mémoire)

**Paramètres :**
- Aucun

**Retour :** dict - Statistiques de repli

**Exceptions :**
- Aucune

---

### `reset_tedb_fallback_stats() -> None`
**Description courte** : Réinitialise les statistiques de repli TEDB.

**Description détaillée :**
- Remet à zéro les compteurs de repli
- Utile pour les tests ou le monitoring par période

**Préconditions :**
- Aucune

**Postconditions :**
- Les compteurs sont à zéro

**Effets de bord :**
- Aucun (mise à jour des compteurs en mémoire)

**Paramètres :**
- Aucun

**Retour :** None

**Exceptions :**
- Aucune

---

## Tables de base de données

### `vat_rate_cache`
- `country` (TEXT, PRIMARY KEY) : Code pays ISO
- `rate_date` (DATE, PRIMARY KEY) : Date du taux
- `category` (TEXT, PRIMARY KEY) : Catégorie de produit
- `rate` (NUMERIC) : Taux de TVA
- `source` (TEXT) : Source ("tedb" ou "static")
- `fetched_at` (TIMESTAMPTZ) : Date de récupération

## Configuration requise

- `SUPABASE_DB_URL` : URL de connexion à la base de données
- `VAT_DYNAMIC_TEDB_ENABLED` : Activation de la TVA dynamique (défaut: False)
- `TEDB_CACHE_TTL` : Durée de vie du cache en secondes (défaut: 86400 = 24h)
- `TEDB_RETRY_MAX_ATTEMPTS` : Nombre maximum de tentatives (défaut: 3)
- `TEDB_RETRY_BACKOFF_BASE` : Base du backoff exponentiel (défaut: 2)
- `TEDB_PLAUSIBILITY_THRESHOLD` : Seuil de plausibilité en points de pourcentage (défaut: 3)

## Mapping catégories → TEDB

Le module mappe les catégories internes vers les catégories TEDB :

- `STANDARD` → `STANDARD_RATE`
- `REDUCED` → `REDUCED_RATE`
- `SUPER_REDUCED` → Repli statique (pas de catégorie TEDB équivalente)
- `BOOKS` → Repli statique (pas de catégorie TEDB équivalente)
- `CLOTHING` → Repli statique (pas de catégorie TEDB équivalente)
- `MEDICINES` → `PHARMACEUTICAL_PRODUCTS`

## Notes

- Le module utilise un garde-fou de plausibilité pour détecter les taux erronés
- Si l'écart entre le taux TEDB et le taux statique est > 3 points, le taux est rejeté
- Certaines catégories (BOOKS, CLOTHING, SUPER_REDUCED) n'ont pas d'équivalent TEDB et utilisent toujours le repli statique
- Le choix MEDICINES → PHARMACEUTICAL_PRODUCTS est à valider par le cabinet comptable
