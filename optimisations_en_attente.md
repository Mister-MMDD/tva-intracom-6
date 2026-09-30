# Optimisations en attente de validation

Ce fichier liste les propositions d'améliorations techniques et fiscales notées pour le système, à valider ou à conserver en file d'attente.

> **Note de mise à jour (2026-09-28)** : Nettoyage du fichier selon le protocole du projet. Les points résolus ou rejetés lors des revues de performance (`cached_property`, partage `asin_avg`, batching VIES & `DictCursor`) ont été retirés. Le point `psutil` est conservé pour une éventuelle montée en charge future.

---

## Architecture & Performance

### 1. Monitoring dynamique de la RAM (Proposition D — `psutil`)
*   **Description** : Utiliser la bibliothèque `psutil` pour détecter la mémoire vive réellement disponible sur le serveur au moment du démarrage d'un job afin d'ajuster `MAX_CONCURRENT_BIG_JOBS` dynamiquement.
*   **Objectif** : Empêcher les dépassements de mémoire (OOM) en régulant les gros calculs concurrents selon la RAM disponible.
*   **Statut** : En attente. Non nécessaire sur l'hébergement gratuit Streamlit Cloud actuel (quota fixe 1 Go), mais conservé pour une future montée en charge ou migration vers un serveur dédié / Railway.
*   **Lieu concerné** : `tva_intracom/ui/background_calc.py`

### 2. Monitoring structuré & Métriques in-process (optionnel)
*   **Description** : Suivi centralisé des métriques de performance (hits/misses de cache BCE/VIES/TEDB, temps de parsing, consommation mémoire in-process).
*   **Statut** : Différé. L'architecture hébergée sur Streamlit Cloud (scale-to-zero, quota mémoire 1 Go fixe, pas de thread d'arrière-plan persistant) exclut tout agent externe ou thread de collecte. Toute implémentation devra se limiter à des compteurs in-process flushés en fin de job via les logs structurés.

---

## Fiscalité & Exports

### 3. Export XML IOSS dédié
*   **Description** : Développer un module de génération de fichier XML homologué pour les déclarations IOSS (régime d'importation ≤ 150 €), similaire à ce qui existe pour l'OSS Union Scheme (`oss_xml.py`).
*   **Contexte actuel** : L'IOSS dispose déjà d'agrégats et d'exports Excel/CSV dédiés (`build_ioss_excel`, `build_ioss_csv` dans `oss_export.py`). Le format XML varie selon les guichets uniques nationaux UE et n'est pas encore harmonisé pour l'IOSS.
*   **Statut** : En attente d'une normalisation ou d'un besoin explicite par guichet national.
*   **Lieu concerné** : `tva_intracom/oss_export.py` et `tva_intracom/ui/tabs/telechargements.py`

### 4. Format Amazon 3 — quantité forcée à 1 (limitation structurelle)
*   **Description** : `_Format3Parser.qty()` (`tva_intracom/parsers/amazon/parsers.py`) retourne toujours `1` car ce format Amazon obsolète n'expose aucune colonne de quantité. Lors du calcul du prix moyen HT/unité par ASIN (`_asin_avg_price_and_category` dans `ca3_report.py`), si une ligne Format 3 regroupe plusieurs unités, le prix moyen est artificiellement surévalué, gonflant la base AIC (ligne 08 CA3).
*   **Statut** : Non corrigeable côté code sans invention de données. Conservé comme limitation documentée. Recommandation utilisateur : migrer vers les exports Amazon Format 4/5 (qui comportent la colonne quantité).
*   **Lieu concerné** : `tva_intracom/parsers/amazon/parsers.py` (`_Format3Parser.qty`), `tva_intracom/ca3_report.py`

### 5. Extension du FEC aux achats
*   **Description** : Étendre le module d'export FEC (Fichier des Écritures Comptables) pour inclure le journal des factures d'achats, en plus du journal des ventes.
*   **Statut** : En attente d'une évolution fonctionnelle future.
*   **Lieu concerné** : `tva_intracom/fec_export.py`

### 6. Taux TVA dynamique (TEDB) — validation cabinet comptable
*   **Description** : L'intégration de l'API TEDB de la Commission européenne (`vat_rates_db.py`) est pleinement opérationnelle : préchargement en lot, garde-fou de plausibilité, cache mensuel auto-réparateur et activation par défaut (`VAT_DYNAMIC_TEDB_ENABLED=true` depuis le 15/09/2026).
    Cependant, 3 catégories internes n'ont pas d'équivalent TEDB direct et utilisent un repli statique permanent (`rates.py`) :
    - `BOOKS` (TEDB ne couvre que le prêt en bibliothèque `LOAN_LIBRARIES`).
    - `CLOTHING` (TEDB ne couvre que la réparation `CLOTHING_REPAIR`).
    - `SUPER_REDUCED` (notion de palier propre au projet, non présente telle quelle dans TEDB).
    Par ailleurs, `MEDICINES` est mappé vers `PHARMACEUTICAL_PRODUCTS`.
*   **Statut** : Technique livrée et active par défaut. Décision finale en attente de confirmation par le cabinet comptable quant au maintien du repli statique pour `BOOKS`/`CLOTHING`/`SUPER_REDUCED` et à la validation du mapping `MEDICINES`.
*   **Lieu concerné** : `tva_intracom/vat_rates_db.py` (`_CATEGORY_TO_TEDB`)
