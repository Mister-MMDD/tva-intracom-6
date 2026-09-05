# Optimisations en attente de validation

Ce fichier liste les propositions d'améliorations techniques notées pour le système de gestion de fichiers et de calcul, à valider avant implémentation.

## Architecture & Performance

### 1. Monitoring dynamique de la RAM (Proposition D - 2026-08-31)
*   **Description** : Utiliser la bibliothèque `psutil` pour détecter la mémoire vive réellement disponible sur le serveur au moment du démarrage d'un job.
*   **Objectif** : Ajuster `MAX_CONCURRENT_BIG_JOBS` dynamiquement.
*   **Statut** : En attente. Inutile sur le plan gratuit Streamlit (limite 1 Go), mais pertinent pour une future montée en charge sur serveur dédié/Railway.
*   **Lieu concerné** : `tva_intracom/ui/background_calc.py`

### 2. Partage du prix moyen ASIN entre exports
*   **Description** : Partager le résultat du calcul du prix moyen par ASIN déjà effectué pour l'Excel avec la génération du rapport CA3 dans le même run.
*   **Objectif** : Éviter un double calcul coûteux sur les très gros volumes.
*   **Statut** : Différé (nécessite une plomberie via `session_state` keyé sur `calc_key`).
*   **Lieu concerné** : `tva_intracom/ca3_report.py` et `tva_intracom/excel_report.py`

### 3. Utilisation de `cached_property` sur `ReportSummary`
*   **Description** : Utiliser `functools.cached_property` pour les propriétés calculées comme `net_oss_by_country`.
*   **Objectif** : Optimiser les accès multiples lors du rendu des visualisations.
*   **Statut** : Différé (complexité liée à l'usage de `__slots__` dans les dataclasses).
*   **Lieu concerné** : `tva_intracom/report.py`

### 4. Batching VIES & Migration `DictCursor`
*   **Description** : Implémenter le batching VIES par chunks de 50 avec écriture au fil de l'eau, combiné à la migration vers `DictCursor`.
*   **Objectif** : Améliorer la résilience et la lisibilité des interactions BDD VIES.
*   **Statut** : Différé / Reporté.
*   **Lieu concerné** : `tva_intracom/vies_engine.py`

## Fiscalité & Exports

### 5. Export XML IOSS dédié
*   **Description** : Développer un module de génération de fichier XML pour les déclarations IOSS (similaire à l'OSS).
*   **Objectif** : Automatiser le dépôt des déclarations IOSS (actuellement manuel).
*   **Statut** : Travaux en cours / Sur l'horizon.
*   **Lieu concerné** : `tva_intracom/oss_export.py` et `tva_intracom/ui/tabs/telechargements.py`

### 6. Taux TVA AIC par catégorie produit (Généralisation)
*   **Description** : Généraliser l'application des taux réels (Standard/Réduit) via `vat_rate(pays, catégorie)` pour les transferts de stock (AIC).
*   **Objectif** : Précision accrue sur le rapport CA3.
*   **Statut** : Décision en attente de confirmation par le cabinet fiscal.
*   **Lieu concerné** : `tva_intracom/rates.py`

### 7. Extension du FEC aux achats
*   **Description** : Étendre le module d'export FEC (Fichier des Écritures Comptables) pour inclure les factures d'achats.
*   **Objectif** : Fournir un journal d'achats complet pour la comptabilité.
*   **Statut** : En attente d'une extension future.
*   **Lieu concerné** : `tva_intracom/fec_export.py`

## Sécurité

### 8. Durcissement du déchiffrement PII
*   **Description** : Retirer la tolérance "fail-open" (préfixe `gAAAA`) dans `decrypt_data`.
*   **Objectif** : Garantir que toutes les données sensibles en base sont effectivement chiffrées.
*   **Statut** : Reporté en attente de la fin de migration/backfill des colonnes `vat_number`, `ioss_number`, etc.
*   **Lieu concerné** : `tva_intracom/security.py`

## Internationalisation (i18n)

*Néant pour le moment — dernier point (entrée #12, onglet "Analyse AIC FBA")
traité et clos le 2026-09-02, voir `README - evolution.md`.*

---
*Note : Les propositions A (Avoid to_dicts), B (MD5 robuste) et C (Streaming CSV) citées dans les versions précédentes du README sont exclues de cette liste pour le moment.*
