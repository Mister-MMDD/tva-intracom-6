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

### 7bis. Format Amazon 3 — quantité forcée à 1 (biais base AIC)
*   **Description** : `_Format3Parser.qty()` (`tva_intracom/parsers/amazon/parsers.py`) retourne toujours `1` car ce format Amazon n'expose aucune colonne quantité exploitable. Or `_asin_avg_price_and_category` (`ca3_report.py`) calcule le prix moyen HT/unité par ASIN en divisant `amount_ht` par la somme des `quantity` connues : si une ligne Format 3 représente en réalité plusieurs unités groupées, ce prix moyen est artificiellement gonflé, ce qui sur-évalue ensuite la base AIC (ligne 08 CA3) calculée par `_compute_aic_from_fc_transfers`.
*   **Objectif** : Ne pas générer un montant AIC faussé pour les utilisateurs encore sur le Format 3 avec des ventes groupées.
*   **Statut** : Non corrigeable en l'état — le Format 3 ne contient structurellement aucune donnée de quantité, il n'y a rien à déduire sans risque d'invention de données. Décision : documenté ici, laissé tel quel. Piste possible si le besoin se confirme : détecter ce cas et avertir l'utilisateur qu'il devrait migrer vers un export Format 4/5 (qui contiennent une colonne QTY) plutôt que de tenter une estimation supplémentaire côté code.
*   **Lieu concerné** : `tva_intracom/parsers/amazon/parsers.py` (`_Format3Parser.qty`), `tva_intracom/ca3_report.py` (`_asin_avg_price_and_category`)

### 7. Extension du FEC aux achats
*   **Description** : Étendre le module d'export FEC (Fichier des Écritures Comptables) pour inclure les factures d'achats.
*   **Objectif** : Fournir un journal d'achats complet pour la comptabilité.
*   **Statut** : En attente d'une extension future.
*   **Lieu concerné** : `tva_intracom/fec_export.py`

## Internationalisation (i18n)

*Néant pour le moment — dernier point (entrée #12, onglet "Analyse AIC FBA")
traité et clos le 2026-09-02, voir `README - evolution.md`.*

---
*Note : Les propositions A (Avoid to_dicts), B (MD5 robuste) et C (Streaming CSV) citées dans les versions précédentes du README sont exclues de cette liste pour le moment.*
