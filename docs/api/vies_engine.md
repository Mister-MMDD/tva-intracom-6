# Module `tva_intracom.vies_engine`

## Description
Module de validation VIES (VAT Information Exchange System) de l'UE. Gère la validation des numéros TVA intracommunautaires via le service VIES, avec un cache PostgreSQL à double niveau (privé/global), un historique append-only pour piste d'audit, des overrides manuels par scope, un résolveur de domaine et un retry exponentiel.

## Fonctions publiques

### `validate_vat(vat_number: str, scope_id: str | None = None) -> dict`
**Description courte** : Valide un numéro TVA via le service VIES et retourne les informations de validation.

**Description détaillée :**
- Normalise le numéro TVA (format canonique)
- Vérifie d'abord le cache privé (scope_id) puis le cache global
- Si non trouvé, appelle le service VIES de la Commission européenne
- Stocke le résultat dans le cache global et le cache privé
- Retourne les informations : valide, nom, adresse, date de validation
- Utilise un retry exponentiel en cas d'échec réseau
- Supporte les overrides manuels par scope

**Préconditions :**
- Le numéro TVA doit être au format ISO (ex: "FR12345678901")
- scope_id est optionnel pour le cache privé

**Postconditions :**
- Le résultat est stocké dans le cache
- L'historique d'audit est mis à jour

**Effets de bord :**
- Appelle le service VIES (appel réseau)
- Insère dans le cache PostgreSQL
- Ajoute à l'historique d'audit

**Paramètres :**
- `vat_number` (str) : Numéro TVA à valider
- `scope_id` (str | None) : ID du scope pour le cache privé

**Retour :** dict - Informations de validation (valid, name, address, request_date, etc.)

**Exceptions :**
- `ValueError` : Numéro TVA invalide
- `ViesServiceUnavailable` : Service VIES indisponible
- `NetworkError` : Erreur réseau après retries

---

### `normalize_full_vat(vat_number: str) -> str`
**Description courte** : Normalise un numéro TVA au format canonique ISO.

**Description détaillée :**
- Extrait le code pays (2 lettres)
- Extrait le numéro (sans espaces, points, tirets)
- Reconstruit le numéro au format ISO (ex: "FR12345678901")
- Gère les cas particuliers (Monaco = MC, Grèce = EL)

**Préconditions :**
- Le numéro TVA doit contenir un code pays valide

**Postconditions :**
- Le numéro est au format canonique

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `vat_number` (str) : Numéro TVA à normaliser

**Retour :** str - Numéro TVA normalisé

**Exceptions :**
- `ValueError` : Code pays invalide

---

### `get_vies_cache(vat_number: str, scope_id: str | None = None) -> dict | None`
**Description courte** : Retourne le résultat de validation depuis le cache si disponible.

**Description détaillée :**
- Cherche d'abord dans le cache privé (scope_id)
- Si non trouvé, cherche dans le cache global
- Retourne None si non trouvé dans les deux caches
- Vérifie que le cache n'est pas expiré (24h par défaut)

**Préconditions :**
- scope_id est optionnel

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `vat_number` (str) : Numéro TVA à chercher
- `scope_id` (str | None) : ID du scope pour le cache privé

**Retour :** dict | None - Résultat de validation ou None

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

### `set_vies_override(scope_id: str, vat_number: str, valid: bool, name: str = "", address: str = "") -> bool`
**Description courte** : Définit un override manuel pour un numéro TVA dans un scope.

**Description détaillée :**
- Permet de forcer le résultat de validation pour un scope
- Utile pour les cas où le service VIES est en maintenance
- L'override est prioritaire sur le cache et le service VIES
- Retourne True si l'override est créé

**Préconditions :**
- scope_id doit être fourni
- vat_number doit être normalisé

**Postconditions :**
- L'override est stocké dans la base de données
- Les futures validations utiliseront l'override

**Effets de bord :**
- Insère dans la table des overrides

**Paramètres :**
- `scope_id` (str) : ID du scope
- `vat_number` (str) : Numéro TVA
- `valid` (bool) : Validité forcée
- `name` (str) : Nom forcé (optionnel)
- `address` (str) : Adresse forcée (optionnelle)

**Retour :** bool - True si l'override est créé

**Exceptions :**
- `DatabaseError` : Erreur lors de l'insertion

---

### `clear_vies_cache(scope_id: str | None = None) -> int`
**Description courte** : Vide le cache VIES (privé ou global).

**Description détaillée :**
- Si scope_id est fourni, vide le cache privé
- Si scope_id est None, vide le cache global
- Retourne le nombre d'entrées supprimées
- Utile pour forcer une revalidation

**Préconditions :**
- Aucune

**Postconditions :**
- Le cache spécifié est vidé

**Effets de bord :**
- Supprime des entrées de la base de données

**Paramètres :**
- `scope_id` (str | None) : ID du scope (None = global)

**Retour :** int - Nombre d'entrées supprimées

**Exceptions :**
- `DatabaseError` : Erreur lors de la suppression

---

### `get_vies_audit_history(vat_number: str, limit: int = 100) -> list[dict]`
**Description courte** : Retourne l'historique d'audit pour un numéro TVA.

**Description détaillée :**
- Lit l'historique append-only des validations
- Retourne les N dernières validations
- Inclut : date, validité, source (cache/VIES/override), scope
- Utile pour la traçabilité et le debug

**Préconditions :**
- Aucune

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `vat_number` (str) : Numéro TVA
- `limit` (int) : Nombre maximum d'entrées

**Retour :** list[dict] - Historique des validations

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

## Tables de base de données

### `vies_global_cache`
- `vat_number` (TEXT, PRIMARY KEY) : Numéro TVA normalisé
- `valid` (BOOLEAN) : Validité
- `name` (TEXT) : Nom de l'entreprise
- `address` (TEXT) : Adresse
- `request_date` (DATE) : Date de la validation
- `fetched_at` (TIMESTAMPTZ) : Date de récupération

### `vies_scope_cache`
- `vat_number` (TEXT, PRIMARY KEY) : Numéro TVA normalisé
- `scope_id` (TEXT, PRIMARY KEY) : ID du scope
- `valid` (BOOLEAN) : Validité
- `name` (TEXT) : Nom de l'entreprise
- `address` (TEXT) : Adresse
- `request_date` (DATE) : Date de la validation
- `fetched_at` (TIMESTAMPTZ) : Date de récupération

### `vies_overrides`
- `vat_number` (TEXT, PRIMARY KEY) : Numéro TVA normalisé
- `scope_id` (TEXT, PRIMARY KEY) : ID du scope
- `valid` (BOOLEAN) : Validité forcée
- `name` (TEXT) : Nom forcé
- `address` (TEXT) : Adresse forcée
- `created_at` (TIMESTAMPTZ) : Date de création

### `vies_audit_log`
- `id` (TEXT, PRIMARY KEY) : ID de l'entrée
- `vat_number` (TEXT) : Numéro TVA normalisé
- `scope_id` (TEXT) : ID du scope
- `valid` (BOOLEAN) : Validité
- `source` (TEXT) : Source ("cache", "vies", "override")
- `name` (TEXT) : Nom
- `address` (TEXT) : Adresse
- `request_date` (DATE) : Date de validation
- `created_at` (TIMESTAMPTZ) : Date de création

## Configuration requise

- `SUPABASE_DB_URL` : URL de connexion à la base de données
- `VIES_CACHE_TTL` : Durée de vie du cache en secondes (défaut: 86400 = 24h)
- `VIES_RETRY_MAX_ATTEMPTS` : Nombre maximum de tentatives (défaut: 3)
- `VIES_RETRY_BACKOFF_BASE` : Base du backoff exponentiel (défaut: 2)
