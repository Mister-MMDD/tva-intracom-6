# Module `tva_intracom.billing`

## Description
Module de facturation Stripe et gestion des quotas SIREN. Gère 3 forfaits (PAYG, Pro, Cabinet), le quota de SIREN par compte, et l'extraction des détails d'abonnement Stripe depuis les objets renvoyés par l'API. Gère aussi le rattachement anti-abus Compte Amazon <-> SIREN.

## Fonctions publiques

### `create_payg_checkout_session(org_id: str, user_id: str, email: str, period_label: str, success_url: str, cancel_url: str, siren: str) -> str`
**Description courte** : Crée une session de paiement Stripe pour un achat PAYG (pay-as-you-go).

**Description détaillée :**
- Crée une session Checkout Stripe pour un achat unique
- Le prix est déterminé par `STRIPE_PRICE_PAYG_EXPORT`
- Métadonnées : org_id, user_id, period_label, siren
- L'URL de succès contient un paramètre pour confirmer le paiement
- L'URL d'annulation redirige vers l'app

**Préconditions :**
- L'organisation doit exister
- L'utilisateur doit exister
- `STRIPE_PRICE_PAYG_EXPORT` doit être configuré
- `STRIPE_SECRET_KEY` doit être configuré

**Postconditions :**
- Une session Checkout Stripe est créée
- Les métadonnées sont attachées pour le webhook

**Effets de bord :**
- Appelle l'API Stripe (appel réseau)

**Paramètres :**
- `org_id` (str) : ID de l'organisation
- `user_id` (str) : ID de l'utilisateur
- `email` (str) : Email de l'utilisateur
- `period_label` (str) : Période fiscale (ex: "2026-01")
- `success_url` (str) : URL de redirection après succès
- `cancel_url` (str) : URL de redirection après annulation
- `siren` (str) : SIREN à rattacher

**Retour :** str - URL de la session Checkout Stripe

**Exceptions :**
- `ValueError` : Paramètres invalides
- `StripeError` : Erreur lors de la création de la session

---

### `create_subscription_checkout_session(org_id: str, user_id: str, email: str, plan: str, interval: str, success_url: str, cancel_url: str, siren_quantity: int) -> str`
**Description courte** : Crée une session de paiement Stripe pour un abonnement (Pro ou Cabinet).

**Description détaillée :**
- Crée une session Checkout Stripe pour un abonnement
- Le plan détermine le prix (business ou cabinet)
- L'intervalle détermine la périodicité (mensuel ou annuel)
- Pour le plan cabinet, `siren_quantity` détermine le nombre de SIREN
- Métadonnées : org_id, user_id, plan, interval

**Préconditions :**
- L'organisation doit exister
- L'utilisateur doit exister
- Le plan doit être valide ("business" ou "cabinet")
- L'intervalle doit être valide ("month" ou "year")
- Les price_id Stripe doivent être configurés

**Postconditions :**
- Une session Checkout Stripe est créée
- Les métadonnées sont attachées pour le webhook

**Effets de bord :**
- Appelle l'API Stripe (appel réseau)

**Paramètres :**
- `org_id` (str) : ID de l'organisation
- `user_id` (str) : ID de l'utilisateur
- `email` (str) : Email de l'utilisateur
- `plan` (str) : Plan ("business" ou "cabinet")
- `interval` (str) : Intervalle ("month" ou "year")
- `success_url` (str) : URL de redirection après succès
- `cancel_url` (str) : URL de redirection après annulation
- `siren_quantity` (int) : Nombre de SIREN (plan cabinet uniquement)

**Retour :** str - URL de la session Checkout Stripe

**Exceptions :**
- `ValueError` : Paramètres invalides
- `StripeError` : Erreur lors de la création de la session

---

### `get_subscription_status(org_id: str) -> dict | None`
**Description courte** : Retourne le statut d'abonnement d'une organisation.

**Description détaillée :**
- Recherche l'abonnement actif dans la base de données
- Retourne les détails : plan, intervalle, quantité, date d'échéance
- Utilise le cache Streamlit pour éviter les requêtes répétées
- Retourne None si aucun abonnement actif

**Préconditions :**
- L'organisation doit exister
- La connexion à la base de données doit être disponible

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation

**Retour :** dict | None - Détails de l'abonnement ou None

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

### `has_export_credit(org_id: str, siren: str, period_label: str) -> bool`
**Description courte** : Vérifie si une organisation a un crédit d'export pour un SIREN et une période.

**Description détaillée :**
- Recherche les crédits PAYG dans la base de données
- Vérifie si un crédit existe pour le SIREN et la période
- Vérifie si le crédit n'a pas été utilisé
- Retourne True si un crédit valide existe

**Préconditions :**
- L'organisation doit exister
- La connexion à la base de données doit être disponible

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation
- `siren` (str) : SIREN à vérifier
- `period_label` (str) : Période fiscale (ex: "2026-01")

**Retour :** bool - True si un crédit valide existe

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

### `consume_export_credit(org_id: str, siren: str, period_label: str) -> bool`
**Description courte** : Consomme un crédit d'export pour un SIREN et une période.

**Description détaillée :**
- Marque le crédit comme utilisé dans la base de données
- Met à jour le timestamp d'utilisation
- Retourne True si le crédit a été consommé avec succès
- Retourne False si aucun crédit n'était disponible

**Préconditions :**
- L'organisation doit exister
- Un crédit doit être disponible
- La connexion à la base de données doit être disponible

**Postconditions :**
- Le crédit est marqué comme utilisé
- Il ne peut plus être réutilisé

**Effets de bord :**
- Met à jour la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation
- `siren` (str) : SIREN concerné
- `period_label` (str) : Période fiscale (ex: "2026-01")

**Retour :** bool - True si le crédit a été consommé

**Exceptions :**
- `DatabaseError` : Erreur lors de la mise à jour de la base de données

---

### `get_siren_quota(org_id: str) -> int`
**Description courte** : Retourne le quota de SIREN pour une organisation.

**Description détaillée :**
- Pour le plan business : quota fixe de 1 SIREN
- Pour le plan cabinet : quota dynamique basé sur la quantité Stripe
- Sans abonnement : quota par défaut (mode don)
- Le quota détermine combien de SIREN peuvent être enregistrés

**Préconditions :**
- L'organisation doit exister

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation

**Retour :** int - Quota de SIREN

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

### `register_siren(org_id: str, siren: str) -> bool`
**Description courte** : Enregistre un SIREN pour une organisation.

**Description détaillée :**
- Vérifie que le quota n'est pas dépassé
- Insère le SIREN dans la base de données
- Retourne True si l'enregistrement réussit
- Retourne False si le quota est dépassé

**Préconditions :**
- L'organisation doit exister
- Le SIREN doit être valide (9 chiffres)
- Le quota ne doit pas être dépassé

**Postconditions :**
- Le SIREN est enregistré pour l'organisation
- Le quota restant est réduit

**Effets de bord :**
- Insère une ligne dans la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation
- `siren` (str) : SIREN à enregistrer

**Retour :** bool - True si l'enregistrement réussit

**Exceptions :**
- `ValueError` : SIREN invalide
- `QuotaExceededError` : Quota dépassé
- `DatabaseError` : Erreur lors de l'insertion

---

### `over_quota_by(org_id: str) -> int`
**Description courte** : Retourne le nombre de SIREN au-dessus du quota.

**Description détaillée :**
- Compte le nombre de SIREN enregistrés
- Soustrait le quota
- Retourne le nombre de SIREN en excès
- Retourne 0 si le quota n'est pas dépassé

**Préconditions :**
- L'organisation doit exister

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Appelle la base de données

**Paramètres :**
- `org_id` (str) : ID de l'organisation

**Retour :** int - Nombre de SIREN en excès (0 si OK)

**Exceptions :**
- `DatabaseError` : Erreur lors de l'accès à la base de données

---

## Tables de base de données

### `tva_subscriptions`
- `id` (TEXT, PRIMARY KEY) : ID de l'abonnement Stripe
- `org_id` (TEXT) : ID de l'organisation
- `plan` (TEXT) : Plan ("payg", "business", "cabinet")
- `interval` (TEXT) : Intervalle ("month", "year")
- `siren_quantity` (INTEGER) : Nombre de SIREN (plan cabinet)
- `status` (TEXT) : Statut ("active", "canceled", "past_due")
- `current_period_end` (TIMESTAMPTZ) : Fin de période courante
- `created_at` (TIMESTAMPTZ) : Date de création

### `tva_export_credits`
- `id` (TEXT, PRIMARY KEY) : ID du crédit
- `org_id` (TEXT) : ID de l'organisation
- `siren` (TEXT) : SIREN concerné
- `period_label` (TEXT) : Période fiscale
- `used` (BOOLEAN) : Utilisé ou non
- `used_at` (TIMESTAMPTZ) : Date d'utilisation
- `created_at` (TIMESTAMPTZ) : Date de création

### `tva_sirens`
- `id` (TEXT, PRIMARY KEY) : ID de l'enregistrement
- `org_id` (TEXT) : ID de l'organisation
- `siren` (TEXT) : SIREN
- `amazon_account_id` (TEXT) : ID du compte Amazon (rattachement)
- `created_at` (TIMESTAMPTZ) : Date de création

## Configuration requise

- `STRIPE_SECRET_KEY` : Clé secrète Stripe
- `STRIPE_PRICE_PAYG_EXPORT` : Price ID Stripe pour PAYG
- `STRIPE_PRICE_BUSINESS_MONTHLY` : Price ID Stripe pour Pro mensuel
- `STRIPE_PRICE_BUSINESS_YEARLY` : Price ID Stripe pour Pro annuel
- `STRIPE_PRICE_CABINET_MONTHLY` : Price ID Stripe pour Cabinet mensuel
- `STRIPE_PRICE_CABINET_YEARLY` : Price ID Stripe pour Cabinet annuel
- `SUPABASE_DB_URL` : URL de connexion à la base de données
