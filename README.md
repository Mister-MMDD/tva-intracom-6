# TVA intracommunautaire — moteur de calcul (ventes marketplace / Amazon)

Outil complet de traitement de la TVA intracommunautaire pour un vendeur établi
en France opérant sur des places de marché (Amazon FBA, formats 1 à 5).

À partir des fichiers bruts de transactions Amazon, le moteur :

- classe chaque vente dans le bon régime fiscal (OSS, CA3, reverse charge B2B,
  deemed supplier Amazon, export, import…),
- calcule la TVA due par pays, par canal de déclaration et par taux,
- valide les numéros TVA acheteurs en temps réel sur le service VIES de l'UE,
- convertit les devises étrangères via l'API BCE,
- génère les exports nécessaires à la déclaration : XML OSS officiel, Excel
  multi-onglets, CSV URSSAF, rapport CA3, aide Intrastat, calendrier fiscal.

---

## Scénarios modélisés

| Scénario | Situation | Règle appliquée | Qui collecte | Canal |
|---|---|---|---|---|
| **DOMESTIC** | Stock et acheteur dans le même pays UE (y compris **Monaco**) **ou** B2B cross-border avec n° TVA acheteur invalide vers un pays couvert par l'art. 194 (ES, IT, PL, CZ, SK, HU, RO, BG, HR, LT, LV) | TVA locale du pays (départ si cross-border) | Vendeur | CA3 (FR) ou immatriculation locale |
| **OSS_B2C** | B2C intra-UE transfrontalier, stock EU, acheteur EU différent **ou** B2B cross-border avec n° TVA acheteur invalide vers un pays non couvert par l'art. 194 (reclassifiée B2C) | TVA du pays de **destination** | Vendeur | Guichet **OSS** (déclaré en France) |
| **DEEMED_SUPPLIER** | Vendeur hors UE, ou import ≤ 150 € marketplace B2C | Amazon collecte et reverse | **Amazon** | EXONERATION (collecté par tiers) |
| **B2B_REVERSE_CHARGE** | B2B intra-UE avec n° TVA VIES valide | Exonération, autoliquidation acheteur | Acheteur | EXONERATION (autoliquidation) |
| **EXPORT** | Acheteur hors UE | Exonéré | — | EXONERATION (export) |
| **IMPORT_STANDARD** | Import > 150 € hors UE, B2C | TVA d'importation (douane) | Importateur | EXONERATION (douane) |
| **IOSS_DIRECT** | Import ≤ 150 €, vendeur ayant explicitement activé son propre numéro IOSS (`ioss_own_number_active`, sinon `DEEMED_SUPPLIER` par défaut — voir audit 08/2026) | Vendeur collecte via IOSS | Vendeur | Guichet **IOSS** (mensuel, déclaration et export **séparés** de l'OSS depuis l'audit 08/2026) |
| **IMPORT_SELLER_AS_IMPORTER** | Import > 150 €, vendeur = importateur officiel (DDP) | Vente domestique dans le pays de destination | Vendeur | CA3 (FR) ou immatriculation locale |

**Cas FBA (stocks hors FR) :** tout pays UE distinct de FR où réside du stock Amazon
déclenche une obligation d'immatriculation TVA locale, signalée dans le rapport et
dans le calendrier fiscal, indépendamment du seuil OSS.

---

## Arborescence du dépôt

Arborescence réelle du dépôt (monorepo — inclut le moteur fiscal `tva_intracom/`,
l'app Streamlit, et la fonction serverless `vercel_webhook/` du webhook Stripe) :

```
tva-intracom/
├── .devcontainer/
├── .github/
│   └── workflows/
│       └── ci.yml                    Pipeline CI (pytest sur push/PR)
├── data/
├── schemas/                          Schémas XSD officiels (DGFIP/UE) pour validation XML OSS
├── tests/
├── tva-site/                         Site vitrine / landing page (HTML/JS/CSS statique)
├── tva_intracom/
│   ├── data/
│   ├── i18n/
│   │   ├── __init__.py
│   │   ├── de.toml                   texte pour l'allemand                    
│   │   ├── en.toml                   texte pour l'anglais
│   │   ├── es.toml                   texte pour l'espagnol                    
│   │   ├── fr.toml                   texte pour l'français
│   │   ├── i18n.py                   choix de la langue
│   │   ├── it.toml                   texte pour l'italien
│   │   ├── pl.toml                   texte pour l'polonais
│   │   ├── pt.toml                   texte pour l'portugais                    
│   ├── parsers/
│   │   ├── amazon/                   Sous-package d'import Amazon (formats 1-5)
│   │   │   ├── __init__.py
│   │   │   ├── aggregate.py          Pré-agrégation multi-juridictions format V5
│   │   │   ├── classify.py           Classification acheteur (B2B/B2C), conversion devise
│   │   │   ├── constants.py          Constantes, SALE_TYPES, REFUND_TYPES, EU_VAT_PREFIXES
│   │   │   ├── detect.py             Détection format (1–5) et séparateur CSV
│   │   │   ├── loader.py             Point d'entrée : load_amazon_report()
│   │   │   └── parsers.py            Parsers par format (Format1–5Parser)
│   │   ├── __init__.py
│   │   ├── aliexpress.py             Parser marketplace AliExpress
│   │   ├── mirakl.py                 Parser marketplace Mirakl
│   │   ├── shopify.py                Parser Shopify
│   │   └── woocommerce.py            Parser WooCommerce
│   ├── __init__.py
│   ├── amazon_adapter.py             Passerelle de compatibilité entre les anciens modèles de données et le nouveau package.
│   ├── amazon_spapi.py               Intégration Amazon Selling Partner API (SP-API) — OAuth 2.0 & Reports.
│   ├── auth.py                       Authentification historique par magic link + jeton de
│   │                                 session (Postgres/Supabase), envoi d'e-mail via l'API
│   │                                 Resend. Gère le chiffrement Fernet des PII (Amazon DPP,
│   │                                 y compris le refresh_token Amazon SP-API). Héberge aussi
│   │                                 le stockage serveur des verifiers PKCE OAuth (voir
│   │                                 auth_supabase.py) dans la table tva_oauth_pkce.
│   ├── auth_supabase.py              Authentification par mot de passe et OAuth (Google,
│   │                                 Microsoft, GitHub, Amazon via Custom OAuth Provider)
│   │                                 déléguée à Supabase Auth (API GoTrue REST). Flux PKCE
│   │                                 uniquement (redirection avec ?code= en query param,
│   │                                 pas de fragment d'URL illisible côté serveur).
│   ├── billing.py                    Facturation Stripe (PAYG + Pro + Cabinet, Customer
│   │                                 Portal, quotas SIREN, grille tarifaire, webhooks,
│   │                                 quotas d'export en base Postgres/Supabase).
│   │                                 Gère aussi le rattachement anti-abus Compte Amazon <-> SIREN.
│   ├── ca3_report.py                 Génération du rapport CA3 (HTML) : compute_ca3_lines_v2,
│   │                                 AIC ligne 08, deductions manuelles, generate_ca3_html_report_v2
│   ├── local_vat_report.py           Équivalent générique du CA3 pour tout pays UE hors France
│   │                                 (canal LOCAL_REGISTRATION/FR_DOMESTIC) : rapport HTML harmonisé
│   │                                 visuellement au CA3 mais PAS un fac-similé du formulaire officiel
│   ├── fec_export.py                 Export comptable FEC (art. A47 A-1 LPF) : journal des ventes
│   │                                 agrégé par période/régime/pays/taux, plan comptable générique
│   │                                 paramétrable (ACCOUNTS), écritures équilibrées débit/crédit
│   ├── cli.py                        Interface en ligne de commande (CLI).
│   ├── config.py                     Utilitaire de gestion des secrets (variables d'environnement, Streamlit secrets).
│   ├── database.py                   Pooling Postgres centralisé (NonPoolingConnectionPool, run_with_retry)
│   ├── ecb_rates.py                  Taux BCE (cache mémoire + disque, convert_to_eur_for_oss)
│   ├── engine.py                     Moteur de classification fiscale (compute_vat, compute_all)
│   ├── excel_report.py               Export Excel multi-onglets
│   ├── historical_rates_widget.py    Composant UI Streamlit pour afficher l'historique des taux de change BCE appliqués
│   ├── mem_utils.py                  Utilitaires d'analyse et d'optimisation de la mémoire (interning, RAM stats)
│   ├── models.py                     Dataclasses : Sale, VatResult, Scenario, BuyerType…
│   ├── oss_export.py                 Agrégation OSS partagée, exports Excel + CSV URSSAF
│   ├── oss_xml.py                    Génération XML OSS officiel (Règl. UE 2021/965)
│   ├── rates.py                      Taux TVA historisés par pays (vat_rate_at_date)
│   ├── report.py                     ReportSummary, build_report, render_report
│   ├── security.py                   Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy)
│   ├── vies_certificate.py           Génération de certificat de validité VIES en PDF (preuve de bonne foi).
│   ├── vies_engine.py                Validation VIES (Backend Postgres multi-niveaux, historique d'audit)
│   ├── ui/                           Découpage modulaire de l'interface Streamlit (app.py appelle ces modules)
│   │   ├── __init__.py
<<<<<<< Updated upstream
│   │   ├── admin.py                  Gestion des rôles et organisation (admin/lecteur).
│   │   ├── auth_flow.py              Authentification complète : mot de passe et OAuth
│   │   │                             (Google/Microsoft/GitHub/Amazon) via Supabase Auth,
│   │   │                             cookie de session, callback OAuth Amazon SP-API.
│   │   ├── background_calc.py        Exécution des calculs longs avec file d'attente FIFO (MAX=1).
│   │   ├── billing_gate.py           Gating crédit PAYG/abonnement/quota SIREN/conformité TVA-IOSS.
│   │   ├── calc_cache.py             Centralisation du state management des calculs (CalcCacheState).
│   │   ├── display_mode.py           Gestion globale du mode d'affichage Simple / Détaillé.
│   │   ├── files.py                  Cache compressé des fichiers uploadés (compression gzip).
│   │   ├── formatting.py             Helpers d'affichage partagés (_fmt, _smart_money_df,
│   │   │                             _gated_preview_table, _fec_period_end_date…)
│   │   ├── onboarding.py             Stepper guidé d'onboarding (Lighthouse CSS).
│   │   ├── rerun_utils.py            Gestion fine des st.rerun() pour préserver l'upload de fichier.
│   │   ├── sidebar.py                Barre latérale complète (SIREN, IOSS, VIES, Facturation Stripe)
=======
│   │   ├── admin.py                  Gestion des rôles admin/lecteur et whitelist organisation.
│   │   ├── auth_flow.py              Authentification complète : mot de passe et OAuth
│   │   │                             (Google/Microsoft/GitHub/Amazon) via Supabase Auth,
│   │   │                             cookie de session, callback OAuth Amazon SP-API.
│   │   ├── background_calc.py        Exécution des calculs longs en thread séparé avec suivi de progression.
│   │   ├── billing_gate.py           Gating crédit PAYG/abonnement/quota SIREN/conformité TVA-IOSS.
│   │   ├── calc_cache.py             Gestion centralisée de l'état du cache de calcul (CalcCacheState).
│   │   ├── display_mode.py           Gestion globale du mode d'affichage Simple / Détaillé.
│   │   ├── files.py                  Cache compressé des fichiers uploadés (signature MD5).
│   │   ├── formatting.py             Helpers d'affichage partagés (_fmt, _smart_money_df,
│   │   │                             _gated_preview_table, _fec_period_end_date…)
│   │   ├── onboarding.py             Stepper guidé d'onboarding avec guidage visuel Lighthouse.
│   │   ├── rerun_utils.py            Gestion fine des st.rerun() pour préserver l'upload de fichier.
│   │   ├── sidebar.py                Barre latérale complète (SIREN, IOSS, VIES, Facturation Stripe).
>>>>>>> Stashed changes
│   │   ├── theme.py                  Configuration de page + CSS de marque adaptatif.
│   │   └── tabs/                     Un module par onglet de l'app, tous consommant un TabContext
│   │       ├── __init__.py
│   │       ├── context.py            TabContext — état partagé construit une fois avant les onglets
│   │       ├── declarations.py       Onglet "💶 Déclarations" (Rendu optimisé)
│   │       ├── detail_ventes.py      Onglet "📋 Détail ventes" (Rendu conditionnel)
│   │       ├── vies_ui.py            Onglet "🛡️ VIES" (Fragments)
│   │       ├── audit.py              Onglet "🔬 Audit Amazon" (Rendu conditionnel)
│   │       ├── telechargements.py    Onglet "📥 Téléchargements" (Caches)
│   │       └── visualisations.py     Onglet "📊 Visualisations" (JSON Plotly arrondi)
│   
├── vercel_webhook/
│   └── api/
│       ├── requirements.txt          Dépendances de la fonction serverless (stripe, psycopg2-binary)
│       └── stripe_webhook.py         Endpoint webhook Stripe, déployé sur Vercel
├── app.py                            Interface Streamlit — orchestrateur (auth, upload, calcul,
│                                     construction du contexte, appel des modules tva_intracom/ui/)
├── Procfile                          Processus de démarrage pour déploiement cloud
├── pyproject.toml
├── railway.toml                      Config spécifique Railway
├── README.md
├── requirements.txt
└── vercel.json                       Config Vercel
```

---

## Architecture du moteur fiscal (`tva_intracom/`)

| Module | Rôle |
|---|---|
| `models.py` | Modèles de données (Pydantic) : Sale, VatResult, Scenario, BuyerType, Channel, Collector |
| `config.py` | Utilitaire de gestion des secrets (lwa, stripe, resend, postgres) avec fallback local |
| `database.py` | Gestion centralisée des connexions Postgres : `NonPoolingConnectionPool` (cache par thread compatible scale-to-zero, ou connexion fraîche par appel selon `cache_connection`) + `run_with_retry()` — consommé par `auth.py`, `billing.py`, `ecb_rates.py` et `vies_engine.py` (voir section « Base de données partagée » ci-dessus) |
| `engine.py` | Moteur de classification fiscale avec documentation légale intégrée (links Bofip/CGI/Dir) |
| `rates.py` | Taux TVA historisés par pays (vat_rate_at_date), is_eu, is_fiscal_eu, seuils |
| `security.py` | Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy) — chiffrement Fernet des PII avec protection **Fail-Safe** contre l'exposition accidentelle en clair. |
| `vies_certificate.py` | Génération d'un "Certificat de Validité VIES" en PDF (preuve de bonne foi opposable) |
| `vies_engine.py` | Validation VIES : cache PostgreSQL à double niveau (privé/global), historique append-only pour piste d'audit, overrides manuels par scope, résoluteur de domaine et retry exponentiel |
| `ecb_rates.py` | Taux BCE : cache deux niveaux (mémoire + Postgres), prefetch parallèle, `convert_to_currency_for_oss` (taux de clôture de période — Règl. UE 2020/194, art. 5 bis), calcul automatique de la date de clôture par transaction pour les périodes multiples (semestres, années), retry exponentiel |
| `oss_export.py` | Agrégation OSS partagée (aggregate_oss_results), exports Excel + CSV URSSAF, détection des soldes négatifs (find_oss_negative_buckets) |
| `oss_xml.py` | Génération XML OSS officiel (Règl. UE 2021/965) avec multi-validation XSD (DGFIP/UE) |
| `ca3_report.py` | Génération du rapport CA3 (HTML uniquement — pas d'export EDI-TVA) : compute_ca3_lines_v2, AIC ligne 08 (transferts FBA), déductions manuelles, calcul du solde net, generate_ca3_html_report_v2 |
| `local_vat_report.py` | Équivalent générique du CA3 pour n'importe quel pays UE hors France (canal `LOCAL_REGISTRATION`, ou `FR_DOMESTIC` quand ce pays est le **pays d'origine** du compte) : `compute_local_vat_lines`, `generate_local_vat_html_report`. Ventilation base/TVA par taux réellement présent dans les données, style visuel harmonisé au CA3, mais **PAS un fac-similé du formulaire officiel** — un avertissement explicite figure dans chaque rapport généré. Codes de case indicatifs pour DE/ES/IT/PL/NL/BE/PT/SE/AT/CZ/RO/HU/IE (`rates.LOCAL_VAT_BOX_CODES`, non vérifiés exhaustivement contre un PDF officiel, contrairement au CA3) |
| `fec_export.py` | Export comptable au format FEC (journal des ventes agrégé par régime/pays/taux, écritures équilibrées débit/crédit) — pré-remplissage pour import dans un logiciel comptable tiers, alternative légère à l'EDI-TVA |
| `excel_report.py` | Export Excel multi-onglets (voir détail onglets ci-dessous) |
| `historical_rates_widget.py` | Composant UI Streamlit pour afficher l'historique des taux de change BCE appliqués |
| `report.py` | ReportSummary, build_report, render_report — ventilation HT exhaustive par canal fiscal (ht_by_bucket) servant de contrôle de cohérence interne, et agrégation mensuelle nette par pays (oss_by_country_month, local_by_country_month) |
| `mem_utils.py` | Utilitaires d'analyse et d'optimisation de la mémoire (interning, RAM stats) |
| `cli.py` | Interface en ligne de commande (CLI) pour exécuter le moteur hors interface web |
| `amazon_adapter.py` | Passerelle de compatibilité entre les anciens modèles de données et le nouveau package de parsers |
| `parsers/amazon/` | Sous-package d'import Amazon (formats 1–5) — voir arborescence ci-dessus |
| `auth.py` | Authentification historique par magic link (Postgres/Supabase, désactivée côté UI), envoi d'e-mail via l'API Resend, chiffrement Fernet du refresh_token Amazon SP-API, stockage serveur des verifiers PKCE OAuth (`tva_oauth_pkce`) |
| `auth_supabase.py` | Authentification par mot de passe et OAuth social (Google, Microsoft, GitHub, Amazon) via l'API Supabase Auth (GoTrue REST), flux PKCE |
| `amazon_spapi.py` | Intégration Amazon Selling Partner API (SP-API) : OAuth 2.0, échange de code, rafraîchissement de token et identification du vendeur — sert à la **liaison de compte** pour la récupération des rapports de vente, distincte de la connexion Amazon de l'écran de login (voir section Authentification) |
| `billing.py` | Facturation Stripe : Checkout PAYG, Pro et Cabinet (mensuel/annuel, paliers dégressifs), Customer Portal, quotas SIREN par compte, grille tarifaire lue en direct sur Stripe, traitement des webhooks, quotas stockés en Postgres/Supabase, et **rattachement anti-abus Compte Amazon <-> SIREN** |
| `app.py` | Orchestrateur Streamlit (racine du dépôt, pas dans `tva_intracom/`) — upload, calcul (avec cache `st.session_state`), construction du contexte, appel des modules `ui/` |

---

## Architecture de l'interface Streamlit (`tva_intracom/ui/`)

Chaque module reprend une partie logique de l'interface, isolé et paramétré par un objet de contexte.

| Module | Rôle |
|---|---|
| `ui/admin.py` | Gestion des rôles admin/lecteur et de la whitelist d'organisation. |
| `ui/auth_flow.py` | Authentification complète via Supabase Auth (mot de passe, OAuth, PKCE, cookie de session). |
| `ui/background_calc.py` | Exécution des calculs longs en thread séparé avec file d'attente FIFO. |
| `ui/billing_gate.py` | Détection de période et gating des téléchargements (PAYG, abonnements, quotas). |
| `ui/calc_cache.py` | Centralisation de l'état du cache de calcul (CalcCacheState). |
| `ui/display_mode.py` | Gestion globale du mode d'affichage Simple / Détaillé. |
| `ui/files.py` | Gestion du cache des fichiers uploadés (compression gzip, signatures MD5). |
| `ui/formatting.py` | Helpers d'affichage partagés et conversion vers la devise d'affichage UI. |
| `ui/onboarding.py` | Stepper guidé d'onboarding avec guidage visuel "Lighthouse". |
| `ui/sidebar.py` | Barre latérale complète (SIREN, IOSS, VIES, abonnements Stripe). |
| `ui/theme.py` | Configuration de page et injection du CSS de marque (Design System). |
| `ui/tabs/` | Un module par onglet de l'application (Declarations, VIES, Audit, etc.). |

---

## Pays d'origine du compte (`home_country`)

Réglage **global au compte** (pas par SIREN — contrairement à l'IOSS ou au mode
DDP), affiché en tout premier dans la barre latérale, persisté en base
(`tva_users.home_country`, défaut `"FR"`).

- **Sélecteur de langue avant connexion** : `language_selector()` est appelé avant l'écran de connexion, pour que l'interface entière s'affiche dans la langue choisie.
- **Impact sur le moteur fiscal** : Définit le régime domestique (`DOMESTIC`). Les ventes sont classées comme domestiques si le stock ou la destination correspondent au pays d'origine choisi.
- **Impact sur l'onglet Téléchargements** : La déclaration du pays d'origine s'affiche en premier (ex: CA3 pour la France, rapport local générique pour les autres pays).
- **Devise de calcul** : Le moteur calcule **toujours en EUR** pour la conformité fiscale (obligatoire pour l'OSS, Règl. UE 2020/194). La conversion vers une devise d'affichage locale intervient uniquement dans la couche présentation.

---

## Authentification & Facturation

- **Authentification & Rôles** : Supabase Auth (flux PKCE). Supporte e-mail/mot de passe et OAuth (Google, Microsoft, GitHub, Amazon).
    - **Partage par Organisation** : L'abonnement, les SIREN et les crédits sont partagés au niveau du domaine e-mail professionnel (`org_id`).
    - **Rôles Admin / Lecteur** : Verrouillage de sécurité dès le premier abonnement payant. Whitelist d'e-mails gérée par les administrateurs.
- **Facturation Stripe** :
    - **Pay-as-you-go** : Déblocage par période fiscale.
    - **Pro** : Abonnement illimité pour 1 SIREN client par compte.
    - **Cabinet** : Abonnement multi-SIREN avec tarif dégressif Stripe (tiered pricing).
- **Quotas & Profils SIREN** : Mémorisation des paramètres par SIREN (IOSS, DDP, seuil OSS, pays d'immatriculation). **Rattachement anti-abus Compte Amazon <-> SIREN** pour limiter l'usage détourné.
- **Contenu gratuit limité** : Aperçu bridé des résultats (10 premières lignes ou 15%, montants et scénarios verrouillés) tant que la période n'est pas débloquée.
- **Base de données partagée** : Postgres (Supabase) centralisé, compatible scale-to-zero, utilisé par Streamlit et les webhooks Vercel.
- **Webhooks Stripe** : Déployés sur Vercel serverless, gèrent l'activation instantanée des abonnements et des crédits.


---

## Formats Amazon supportés

| Format | Description | Clé de détection |
|---|---|---|
| **1** | Ancien format TSV | `departure_country`, `tax_calculation_date` |
| **2** | Format intermédiaire | `activity_period` |
| **3** | TSV/CSV 2024 | `transaction_complete_date` + `tax_collection_model` |
| **4** | CSV 2025+ | `transaction_complete_date` + `tax_collection_responsibility` |
| **5** | Rapport fiscal détaillé V5 | `our_price_tax_exclusive_selling_price` + `transaction_id` + `order_date` |

---

## Fonctionnalités clés

### Moteur fiscal
- **Typage Statique & Validation Pydantic** : Utilisation de `pydantic.dataclasses` pour une validation stricte dès l'import. Précision absolue via **Decimal**.
- **Documentation Fiscale Directe** : Chaque note de résultat (`VatResult.note`) intègre des références légales précises et des liens courts vers le **Bofip**, l'**Art. 262 ter du CGI** ou les **Directives Européennes** (uniquement en langue française).
- **Gestion des seuils** : Seuil OSS 10 000 € avec suivi multi-année (`oss_ht_by_year`).
- **Reverse charge domestique** : Gestion de l'art. 194 pour les pays concernés (ES, IT, PL, CZ, SK, HU, RO, BG, HR, LT, LV).
- **Détection géographique** : Identification des territoires hors UE fiscale (Canaries, DOM-TOM, Åland, Helgoland…) via code postal.
- **Monaco** : Assimilation fiscale à la France (Convention du 18/05/1963).
- **Plan d'action Immatriculations** : Vue consolidée détectant les besoins de mise en conformité (stock Amazon détecté, ventes locales taxables, import DDP) restreinte aux pays UE.

### Validation VIES
- **Architecture résiliente à trois niveaux** : Cache Privé (par scope/domaine) > Cache Global (mutualisé) > API UE (ec.europa.eu).
- **Ré-essai automatique en arrière-plan** des numéros inconclusifs avec notification par modale (`@st.dialog`).
- **Piste d'audit & Pseudonymisation** : Historique des vérifications avec pseudonymisation SHA-256 salée. Table au format *append-only* pour justification lors d'un contrôle fiscal.
- **Certificat PDF** : Preuve de validité instantanée ou **historique complet** (paysage).

### Conversion devises
- **API BCE SDW** : Utilisation des taux officiels avec cache deux niveaux (mémoire + Postgres).
- **Compliance OSS** : Application du taux du dernier jour de la période de déclaration (Règl. UE 2020/194, art. 5 bis).
- **Multi-période** : Calcul automatique de la date de clôture propre à chaque trimestre pour les périodes multiples (semestres, années).

### Import des fichiers Amazon
- **Performance extrême** : Utilisation de **Polars** (moteur Rust) pour le parsing des fichiers volumineux jusqu'à 150 Mo.
- **Détection intelligente** : Encodage (UTF-8/CP1252), format (1–5) et séparateur automatiques.
- **Analyse d'exigibilité** : Distinction entre date de commande et date d'expédition (art. 65 Dir. 2006/112/CE).

### Export XML OSS officiel
- Structure conforme au Règlement UE 2021/965.
- **Multi-validation XSD** (DGFIP/UE) intégrée.
- **Correction assistée** : Rattachement automatique avoirs -> ventes d'origine (même `sale_id`) pour résoudre les soldes négatifs via le bloc `CorrectionsOfVatReturns` (inclut la **normalisation Monaco**).

---

## Export Excel — onglets générés

| # | Onglet | Contenu |
|---|---|---|
| 1 | **Récapitulatif** | Synthèse TVA par canal et **Audit d'intégrité technique** (Signature numérique Hash ID) |
| 2 | **Détail ventes** | Ligne par ligne avec scénario, taux, canal, note légale |
| 3 | **Détail remboursements** | Avoirs détaillés avec structure identique |
| 4 | **OSS par pays** | Agrégation par pays de destination + taux, avec **détail mensuel net** |
| 5 | **TVA locale par pays** | Immatriculations locales (stocks FBA hors pays d'origine) avec **détail mensuel net** |
| 6 | **IOSS par pays** | Ventes IOSS (numéro propre vendeur) avec détail mensuel net |
| 7 | **Audit Écarts Amazon** | Ventes où la TVA calculée diffère de celle collectée par Amazon |
| 8 | **Historique VIES** | Toutes les vérifications VIES horodatées (piste d'audit) |
| 9 | **Analyse AIC FBA** | AIC estimées par flux avec application des taux de TVA réels par ASIN (art. 17 Dir. 2006/112/CE) |
| 10 | **Transferts FBA Détail** | Liste brute des mouvements de stock entre entrepôts |
| 11 | **Intrastat (EMEBI)** | Aide au remplissage : introductions et expéditions par ASIN et **par mois** |
| 12 | **INVOICE & CREDIT_NOTE** | Détail des écritures de service Amazon |
| 13 | **Calendrier Fiscal** | Échéances OSS, CA3, Intrastat, ESL avec compte à rebours |

---

## Export comptable (FEC)

Génération d'un **journal des ventes au format FEC** (art. A47 A-1 LPF) pour import dans Sage, ACD, Quadratus, etc.
- Agrégation par période/régime/pays/taux pour limiter le volume d'écritures.
- Équilibre débit/crédit garanti par construction, avec gestion des inversions de sens pour les soldes négatifs.
- Garde-fou `_assert_balanced()` levant une erreur explicite si une écriture ressort déséquilibrée.

---

## Calendrier fiscal généré automatiquement

Le moteur déduit les échéances directement des transactions :
- **OSS** : Dernier jour du mois suivant le trimestre.
- **CA3** : 24 du mois suivant.
- **Intrastat** : 10e jour ouvré du mois suivant.
- **ESL / Relevé TVA** : 24 du mois suivant.

---

## Intrastat / EMEBI (onglet 11)

Séparation stricte des deux obligations depuis 2022 :
- **EMEBI** : Enquête statistique basée sur les seuils annuels (460 000 €). Fournit les flux UE->pays d'origine et pays d'origine->UE agrégés par ASIN.
- **État récapitulatif TVA (ESL)** : Obligation fiscale dès le 1er euro pour les livraisons B2B.

---

## Conformité Amazon DPP & Sécurité

- **Chiffrement au Repos** : Algorithme Fernet (AES-128 CBC + HMAC-SHA256) pour les données sensibles (PII). Protection **Fail-Safe** interdisant tout traitement si la clé est absente.
- **Pseudonymisation réversible** : Hachage SHA-256 salé (`PSEUDONYMIZATION_SALT`) des identifiants pour l'historique VIES.
- **Protection contre l'injection de formules** : Helper `_safe()` systématisé sur les exports Excel/CSV pour bloquer les attaques par injection de formules (`=`, `+`, `-`, `@`).
- **Protection XSS & Injections** : Échappement systématique des données utilisateur (HTML/Markdown) et durcissement des clés de widgets Streamlit via hash.
- **Isolation Multi-tenant & Multi-SIREN** : Clés de cache isolées par utilisateur (`current_user.id`) et par SIREN (`_vies_scope_id`).
- **Contrôle de concurrence** : Utilisation de **verrous avisés Postgres** (`pg_advisory_xact_lock`) pour garantir l'intégrité des quotas SIREN et du verrouillage d'organisation en environnement multi-comptes.
- **Validation des Redirects** : Contrôle du header `Host` via une allowlist pour prévenir les attaques de redirection ouverte (Open Redirect).
- **Rétention limitée** : Suppression automatique des PII après 365 jours.

---

## Installation & Usage

### Installation
Python ≥ 3.10 requis.
```bash
pip install -e ".[dev]"
```

### Interface Streamlit
```bash
streamlit run app.py
```

### Utilisation en bibliothèque
```python
from tva_intracom.parsers.amazon import load_amazon_report
from tva_intracom.engine import compute_all

result = load_amazon_report("rapport.csv", seller_country="FR")
status = compute_all(result.sales)
```

### Génération du XML OSS
```python
from tva_intracom.oss_xml import generate_oss_xml

xml_bytes = generate_oss_xml(results=res, seller_vat="FR...", period="2026-Q1")
```

---

## Optimisations de performance & UX (Mises à jour récentes)

### Performance & Réactivité
- **Optimisations CPU (Audit 09/2026)** :
  - Remplacement de `st.tabs` par des sélecteurs radio conditionnels pour n'exécuter que l'onglet actif (gain massif sur le vCPU partagé).
  - Mémoïsation des boucles O(n) du thread principal via `calc_key` (KPIs, alertes, devises).
  - Utilisation de `lru_cache` sur la normalisation VIES et interning des chaînes répétitives (`sys.intern`).
  - Arrondi des données Plotly pour une sérialisation JSON plus légère vers le navigateur.
- **File d'attente (Gros uploads)** : Slot unique tenu du début du parsing jusqu'à la fin du calcul pour les fichiers volumineux (> 10 Mo). Inclut un timeout de réservation sécurisé (45s) et une réincrémentation défensive du compteur de slots.
- **Respiration CPU** : Points de respiration (`time.sleep(0)`) dans `_process_rows` et `_run_oss_loop` pour garantir la fluidité de l'interface sur vCPU partagé.
- **Optimisation de la RAM** : String Interning (ASIN, TVA, pays) et gestion fine des caches (suppression des `cache_clear()` globaux impactant les autres sessions).
- **Fragments Streamlit** : Utilisation intensive de `@st.fragment` pour isoler le rendu et éviter les reruns complets lors d'interactions locales.
- **Cache intelligent** : Mise en cache des parsers, du catalogue et des exports via signatures MD5 (128 Ko start/end).
- **Réactivité post-paiement** : Rafraîchissement instantané du statut d'abonnement lors du retour de Checkout Stripe (via `export_ok=1`) pour supprimer la latence du cache.

### UX & Fiabilité
- **Onboarding Lighthouse** : Guidage visuel par pulsations CSS vers les sections requises pour la configuration (Entreprise, TVA, Upload).
- **Réactivité post-paiement** : Rafraîchissement instantané du statut d'abonnement lors du retour de Checkout Stripe (via `export_ok=1`) pour supprimer le délai de latence du cache (60s).
- **Résilience de la barre latérale** : Isolation des pannes transitoires (lecture de l'historique de crédits) pour garantir l'accès permanent aux options d'abonnement et de paiement.
- **Mode Simple / Détaillé** : Bascule d'affichage globale et persistante par compte pour simplifier l'interface ou accéder aux détails d'audit.
- **Barre de statut persistante** : Affichage constant du nombre de fichiers chargés, de la période détectée et de l'état du calcul.
- **Boucle de ré-essai VIES automatique** : Revérification en arrière-plan des numéros inconclusifs (erreurs serveur transitoires) après chaque calcul.
- **Gestion de la confidentialité** : Sortie des paramètres sensibles vers une modale dédiée (`st.dialog`).
- **Persistance multilingue** : Interface et exports localisés en 7 langues (FR, EN, DE, ES, IT, PL, PT).

---

## Audit de conformité & Technique (08/2026 - 09/2026)

Audit réglementaire et structurel exhaustif (moteur fiscal, sécurité, exports, UI) :
- **Seuil OSS 10 000 €** : Appreciation sur l'année en cours et l'année précédente (N-1). Les avoirs suivent désormais le régime (OSS ou DOMESTIC) de la vente d'origine même s'ils font repasser le cumul sous le seuil.
- **IOSS_DIRECT vs DEEMED_SUPPLIER** : Le comportement par défaut devient `DEEMED_SUPPLIER` (Amazon redevable), `IOSS_DIRECT` est désormais un choix explicite via toggle.
- **Monaco** : Traitement des ventes et stocks à Monaco comme des ventes domestiques françaises (Convention fiscale franco-monégasque du 18/05/1963).
- **Ligne 18 CA3 (Monaco)** : Remplissage de la ligne mémo dédiée (case 0038) sur le Cerfa 3310-CA3-SD.
- **Gestion d'Organisation** : Partage de l'abonnement et des SIREN par domaine e-mail professionnel (`org_id`) avec rôles Admin/Lecteur.
- **Concurrence & Verrous** : Utilisation de verrous avisés Postgres (`pg_advisory_xact_lock`) pour sécuriser les quotas SIREN et le verrouillage d'organisation.
- **Optimisations CPU** : Migration vers un rendu conditionnel des onglets et mémoïsation intensive des scans O(n) pour fluidifier l'interface sur vCPU partagé.
- **Audit exhaustif du code** : Revue intégrale ligne à ligne des modules critiques pour garantir la robustesse structurelle et la cohérence des flux de données.
- **Traçabilité & Monitoring** : Instrumentation renforcée des échecs d'API tiers (Stripe) et des flux de calculs longs pour un diagnostic rapide en production.

---

## Incidents de production résolus

- **2026-09-03 — Backfill `org_id` manquant (`billing.py`)** : le premier déploiement `dev → main` post-migration `user_id → org_id` a fait planter la prod (`psycopg2.errors.UndefinedColumn: column "org_id" does not exist`). Cause : `_migrate_billing_to_org_id()` tentait un `SET NOT NULL` sans backfill préalable, provoquant un rollback complet de l'`ALTER TABLE ADD COLUMN`. Corrigé par l'ajout du backfill `UPDATE ... SET org_id = user_id WHERE org_id IS NULL` avant la contrainte `NOT NULL`, sur les 4 tables de facturation. Script de diagnostic dédié conservé (`scripts/diag_org_id_state.py`).
- **2026-09-03 — Nettoyage de l'instrumentation de debug** : retrait des logs `[QUEUE_DEBUG]` temporaires (file d'attente de calcul), des affichages `st.caption` de diagnostic serveur, et du script `backfill_encrypt_pii.py` (déjà exécuté en production avec `--apply`, migration close). Scripts de diagnostic utiles conservés (`debug_can_export.py`, `scripts/diag_org_migration.py`, `scripts/diag_org_id_state.py`).

---

## Tests

```bash
pytest -q
```
La suite couvre la classification fiscale, le cache VIES, le seuil OSS multi-année, les formats Amazon 1–5, la conversion BCE, la thread-safety du pool DB et l'équilibrage FEC.

---

## Conformité légale — références

| Sujet | Texte de référence |
|---|---|
| Régime OSS | Dir. 2006/112/CE art. 369 bis à septdecies ; Règl. UE 2021/965 |
| Taux de change OSS | Règl. UE 2020/194, art. 5 bis |
| Exonération B2B | Dir. 2006/112/CE art. 138 ; Règl. UE 2018/1912 |
| Reverse charge domestique | Dir. 2006/112/CE art. 194 |
| Acquisitions AIC | Dir. 2006/112/CE art. 17 & 83 |
| Intrastat | Règl. UE 2019/2152 |
| IOSS (import ≤ 150 €) | Dir. 2006/112/CE art. 369 ter |

---

## Roadmap

- **EDI-TVA** : Export pour télétransmission directe des CA3 (actuellement HTML pour saisie manuelle).
- **XML IOSS** : Export XML officiel pour le guichet unique IOSS (Import Scheme).

---
> Ce projet est un outil d'aide au calcul et à la préparation des déclarations.
> Il ne remplace pas un conseil fiscal professionnel.
