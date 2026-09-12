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

### 6. Format Amazon 3 — quantité forcée à 1 (biais base AIC)
*   **Description** : `_Format3Parser.qty()` (`tva_intracom/parsers/amazon/parsers.py`) retourne toujours `1` car ce format Amazon n'expose aucune colonne quantité exploitable. Or `_asin_avg_price_and_category` (`ca3_report.py`) calcule le prix moyen HT/unité par ASIN en divisant `amount_ht` par la somme des `quantity` connues : si une ligne Format 3 représente en réalité plusieurs unités groupées, ce prix moyen est artificiellement gonflé, ce qui sur-évalue ensuite la base AIC (ligne 08 CA3) calculée par `_compute_aic_from_fc_transfers`.
*   **Objectif** : Ne pas générer un montant AIC faussé pour les utilisateurs encore sur le Format 3 avec des ventes groupées.
*   **Statut** : Non corrigeable en l'état — le Format 3 ne contient structurellement aucune donnée de quantité, il n'y a rien à déduire sans risque d'invention de données. Décision : documenté ici, laissé tel quel. Piste possible si le besoin se confirme : détecter ce cas et avertir l'utilisateur qu'il devrait migrer vers un export Format 4/5 (qui contiennent une colonne QTY) plutôt que de tenter une estimation supplémentaire côté code.
*   **Lieu concerné** : `tva_intracom/parsers/amazon/parsers.py` (`_Format3Parser.qty`), `tva_intracom/ca3_report.py` (`_asin_avg_price_and_category`)

### 7. Extension du FEC aux achats
*   **Description** : Étendre le module d'export FEC (Fichier des Écritures Comptables) pour inclure les factures d'achats.
*   **Objectif** : Fournir un journal d'achats complet pour la comptabilité.
*   **Statut** : En attente d'une extension future.
*   **Lieu concerné** : `tva_intracom/fec_export.py`

### 8. Titre figé sur `ui/sidebar.py::_render_account_dialog` et `ui/admin.py::render_admin_dialog`
*   **Description** : Ces deux `@st.dialog(title=_("..."))` souffrent du même bug identifié et corrigé le 2026-09-11 sur `vies_ui.py::_render_vies_retry_done_dialog` — l'argument `title` du décorateur n'est évalué qu'UNE SEULE FOIS, à l'import du module (Python ne réexécute jamais le corps d'un module déjà dans `sys.modules`). Sur Streamlit Cloud, plusieurs comptes/langues partagent le même process : le titre de ces deux modales reste donc figé dans la langue active lors du tout premier import du module concerné dans ce process, quelle que soit la langue choisie ensuite par chaque utilisateur qui l'ouvre.
*   **Objectif** : Même correctif que `vies_ui.py` — construire le dialog dynamiquement à l'intérieur de la fonction appelante (titre résolu à l'instant de l'appel, donc dans la langue de la session en cours) au lieu de décorer une fonction module-level.
*   **Statut** : Non corrigé (repéré lors de l'audit VIES du 2026-09-11, hors périmètre de cette session). À traiter dans une session dédiée UI/i18n.

### 9. Incohérence taux de change OSS/IOSS entre détail mensuel et total de période (`excel_report.py`)
*   **Description** : Dans `_write_oss_tab` (et désormais `_write_ioss_tab`, ajouté le 2026-09-11 en miroir de l'existant pour rester cohérent), les colonnes de détail mensuel proviennent de `summary.oss_by_country_month` (`report.py`), qui utilise le taux de change du jour de la vente pour chaque ligne. Les totaux de fin de ligne (Brut/Remboursements/Net) sont recalculés séparément via `aggregate_oss_results`/`aggregate_ioss_results`, qui appliquent le taux de clôture de période (Art. 5 bis Règl. UE 2020/194). Pour un pays facturé dans une devise étrangère à l'EUR (ex : Suède/SEK), la somme des colonnes mensuelles ne correspond donc pas exactement au total affiché en bout de ligne.
*   **Objectif** : Aligner les deux sources sur le même taux (clôture de période) pour supprimer l'écart visuel, ou a minima documenter clairement l'écart à l'utilisateur.
*   **Statut** : Confirmé dans le code (2026-09-11). Correction proprement dite reportée : nécessite de restructurer `_aggregate_by_scenario` (`oss_export.py`) pour ajouter une dimension mensuelle avec conversion au taux de clôture par mois, ce qui dépasse le périmètre d'un correctif ponctuel. Soumis au cabinet comptable avant toute implémentation (l'écart actuel est mineur et documenté, pas un risque de non-conformité immédiat).
*   **Lieu concerné** : `tva_intracom/excel_report.py` (`_write_oss_tab`, `_write_ioss_tab`), `tva_intracom/oss_export.py` (`_aggregate_by_scenario`), `tva_intracom/report.py` (`oss_by_country_month`)
*   **Lieu concerné** : `tva_intracom/ui/sidebar.py` (`_render_account_dialog`), `tva_intracom/ui/admin.py` (`render_admin_dialog`)

### 10. Taux TVA dynamique (TEDB) — catégories non mappables & choix MEDICINES à valider
*   **Description** : Depuis la bascule TVA dynamique du 2026-09-12 (`vat_rates_db.py`, API TEDB de la Commission européenne), 3 des 6 catégories internes n'ont **aucune** catégorie TEDB équivalente et restent donc en repli statique permanent (`rates.py`), sans aucun appel réseau tenté :
    - `BOOKS` : TEDB n'a pas de catégorie générale "livres" (seule `LOAN_LIBRARIES` = prêt en bibliothèque existe, hors sujet ; `NEWSPAPERS`/`PERIODICALS` ne couvrent pas les livres).
    - `CLOTHING` : TEDB n'a pas de catégorie générale "habillement" (seule `CLOTHING_REPAIR` = réparation existe, hors sujet).
    - `SUPER_REDUCED` : notion de palier de taux propre à ce projet, pas une catégorie TEDB (qui catégorise par nature de bien/service).
    Par ailleurs, `MEDICINES` est mappé vers la catégorie TEDB `PHARMACEUTICAL_PRODUCTS` (produits pharmaceutiques vendus) plutôt que `MEDICAL_CARE` (prestations de soins médicaux/dentaires) — choix jugé le plus pertinent pour un catalogue Amazon, mais non encore confirmé.
*   **Objectif** : Cabinet comptable à valider explicitement (1) que le choix `MEDICINES` → `PHARMACEUTICAL_PRODUCTS` est correct, et (2) qu'un repli statique permanent est acceptable pour BOOKS/CLOTHING/SUPER_REDUCED (alternative technique existante mais non implémentée : requête TEDB par code CN/CPA au lieu de catégorie, écartée pour l'instant faute de code CN/CPA fiable par pays sans risque d'erreur fiscale).
*   **Statut** : Implémenté avec repli documenté ; décision de confirmation en attente du cabinet.
*   **Lieu concerné** : `tva_intracom/vat_rates_db.py` (`_CATEGORY_TO_TEDB`)

## Internationalisation (i18n)

*Néant pour le moment — dernier point (entrée #12, onglet "Analyse AIC FBA")
traité et clos le 2026-09-02, voir `README - evolution.md`.*

---
*Note : Les propositions A (Avoid to_dicts), B (MD5 robuste) et C (Streaming CSV) citées dans les versions précédentes du README sont exclues de cette liste pour le moment.*
