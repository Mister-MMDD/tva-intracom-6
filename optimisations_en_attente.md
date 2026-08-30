# Optimisations en attente de validation

Ce fichier liste les propositions d'améliorations techniques notées pour le système de gestion de fichiers et de calcul, à valider avant implémentation.

## Architecture & Performance

### 1. Monitoring dynamique de la RAM (Proposition D - 2026-08-31)
*   **Description** : Utiliser la bibliothèque `psutil` pour détecter la mémoire vive réellement disponible sur le serveur au moment du démarrage d'un job.
*   **Objectif** : Ajuster `MAX_CONCURRENT_BIG_JOBS` dynamiquement.
*   **Statut** : En attente. Inutile sur le plan gratuit Streamlit (limite 1 Go), mais pertinent pour une future montée en charge sur serveur dédié/Railway.
*   **Lieu concerné** : `tva_intracom/ui/background_calc.py`

### 2. Vectorisation Polars (Boucle OSS B2C)
*   **Description** : Vectoriser en Polars les calculs OSS pour les cas B2C simples.
*   **Objectif** : Amélioration sensible des performances sur les fichiers de plus de 100k lignes.
*   **Statut** : Reporté (ROI incertain dû à la logique stateful du seuil 10k€).
*   **Lieu concerné** : `tva_intracom/engine.py::_run_oss_loop`

### 3. Optimisation RAM via type `category`
*   **Description** : Convertir les colonnes texte répétitives (`Canal`, `Scenario`, `Collector`, `Pays`) en type `category` de Pandas/Polars.
*   **Objectif** : Réduire l'empreinte mémoire du cache des lignes formatées.
*   **Statut** : Reporté (bon candidat pour un prochain patch ciblé).
*   **Lieu concerné** : `tva_intracom/ui/formatting.py::_build_rows_df`

### 4. Isolation multi-tenant du cache Streamlit
*   **Description** : Revoir l'usage de `@st.cache_data` qui est partagé entre tous les utilisateurs (process-wide).
*   **Objectif** : Garantir une isolation parfaite des données en cache entre différents tenants.
*   **Statut** : Sujet d'architecture de fond en attente.
*   **Lieu concerné** : Global / `app.py`

### 5. Partage du prix moyen ASIN entre exports
*   **Description** : Partager le résultat du calcul du prix moyen par ASIN déjà effectué pour l'Excel avec la génération du rapport CA3 dans le même run.
*   **Objectif** : Éviter un double calcul coûteux sur les très gros volumes.
*   **Statut** : Différé (nécessite une plomberie via `session_state` keyé sur `calc_key`).
*   **Lieu concerné** : `tva_intracom/ca3_report.py` et `tva_intracom/excel_report.py`

### 6. Utilisation de `cached_property` sur `ReportSummary`
*   **Description** : Utiliser `functools.cached_property` pour les propriétés calculées comme `net_oss_by_country`.
*   **Objectif** : Optimiser les accès multiples lors du rendu des visualisations.
*   **Statut** : Différé (complexité liée à l'usage de `__slots__` dans les dataclasses).
*   **Lieu concerné** : `tva_intracom/report.py`

### 7. Batching VIES & Migration `DictCursor`
*   **Description** : Implémenter le batching VIES par chunks de 50 avec écriture au fil de l'eau, combiné à la migration vers `DictCursor`.
*   **Objectif** : Améliorer la résilience et la lisibilité des interactions BDD VIES.
*   **Statut** : Différé / Reporté.
*   **Lieu concerné** : `tva_intracom/vies_engine.py`

## Fiscalité & Exports

### 8. Export XML IOSS dédié
*   **Description** : Développer un module de génération de fichier XML pour les déclarations IOSS (similaire à l'OSS).
*   **Objectif** : Automatiser le dépôt des déclarations IOSS (actuellement manuel).
*   **Statut** : Travaux en cours / Sur l'horizon.
*   **Lieu concerné** : `tva_intracom/oss_export.py` et `tva_intracom/ui/tabs/telechargements.py`

### 9. Taux TVA AIC par catégorie produit (Généralisation)
*   **Description** : Généraliser l'application des taux réels (Standard/Réduit) via `vat_rate(pays, catégorie)` pour les transferts de stock (AIC).
*   **Objectif** : Précision accrue sur le rapport CA3.
*   **Statut** : Décision en attente de confirmation par le cabinet fiscal.
*   **Lieu concerné** : `tva_intracom/rates.py`

### 10. Extension du FEC aux achats
*   **Description** : Étendre le module d'export FEC (Fichier des Écritures Comptables) pour inclure les factures d'achats.
*   **Objectif** : Fournir un journal d'achats complet pour la comptabilité.
*   **Statut** : En attente d'une extension future.
*   **Lieu concerné** : `tva_intracom/fec_export.py`

## Sécurité

### 11. Durcissement du déchiffrement PII
*   **Description** : Retirer la tolérance "fail-open" (préfixe `gAAAA`) dans `decrypt_data`.
*   **Objectif** : Garantir que toutes les données sensibles en base sont effectivement chiffrées.
*   **Statut** : Reporté en attente de la fin de migration/backfill des colonnes `vat_number`, `ioss_number`, etc.
*   **Lieu concerné** : `tva_intracom/security.py`

---
*Note : Les propositions A (Avoid to_dicts), B (MD5 robuste) et C (Streaming CSV) citées dans les versions précédentes du README sont exclues de cette liste pour le moment.*
