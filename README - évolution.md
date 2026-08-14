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
| **DOMESTIC** | Stock et acheteur dans le même pays UE **ou** B2B cross-border avec n° TVA acheteur invalide vers un pays couvert par l'art. 194 (ES, IT, PL, CZ, SK, HU, RO, BG, HR, LT, LV) | TVA locale du pays (départ si cross-border) | Vendeur | CA3 (FR) ou immatriculation locale |
| **OSS_B2C** | B2C intra-UE transfrontalier, stock EU, acheteur EU différent **ou** B2B cross-border avec n° TVA acheteur invalide vers un pays non couvert par l'art. 194 (reclassifiée B2C) | TVA du pays de **destination** | Vendeur | Guichet **OSS** (déclaré en France) |
| **DEEMED_SUPPLIER** | Vendeur hors UE, ou import ≤ 150 € marketplace B2C | Amazon collecte et reverse | **Amazon** | EXONERATION (collecté par tiers) |
| **B2B_REVERSE_CHARGE** | B2B intra-UE avec n° TVA VIES valide | Exonération, autoliquidation acheteur | Acheteur | EXONERATION (autoliquidation) |
| **EXPORT** | Acheteur hors UE | Exonéré | — | EXONERATION (export) |
| **IMPORT_STANDARD** | Import > 150 € hors UE, B2C | TVA d'importation (douane) | Importateur | EXONERATION (douane) |
| **IOSS_DIRECT** | Import ≤ 150 €, vendeur ayant explicitement activé son propre numéro IOSS (`ioss_own_number_active`, sinon `DEEMED_SUPPLIER` par défaut — voir audit 08/2026 ci-dessous) | Vendeur collecte via IOSS | Vendeur | Guichet **IOSS** (mensuel, déclaration et export **séparés** de l'OSS depuis l'audit 08/2026) |
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
│   │   ├── fr.toml                   texte pour le français
│   │   ├── i18n.py                   choix de la langue
│   │   ├── it.toml                   texte pour l'italien
│   │   ├── pl.toml                   texte pour le polonais
│   │   ├── pt.toml                   texte pour le portugais                    
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
│   │   ├── theme.py                  Configuration de page + CSS de marque (apply_theme())
│   │   ├── formatting.py             Helpers d'affichage partagés (_fmt, _smart_money_df,
│   │   │                             _gated_preview_table, _fec_period_end_date…)
│   │   ├── auth_flow.py              Authentification complète : mot de passe et OAuth
│   │   │                             (Google/Microsoft/GitHub/Amazon) via Supabase Auth,
│   │   │                             cookie de session, callback OAuth Amazon SP-API
│   │   │                             (liaison de compte, distincte du login Amazon), écran
│   │   │                             de connexion/déconnexion. Lien magique conservé dans le
│   │   │                             code mais désactivé côté UI ("en préparation").
│   │   ├── onboarding.py             Visite guidée de première connexion (st.dialog).
│   │   ├── rerun_utils.py            Gestion fine des st.rerun() pour préserver l'upload de fichier.
│   │   ├── sidebar.py                Barre latérale complète (SIREN, IOSS, VIES, catalogue produits,
│   │   │                             abonnements & forfaits Stripe)
│   │   ├── billing_gate.py           Détection de période, gating crédit PAYG/abonnement/quota
│   │   │                             SIREN/conformité TVA-IOSS, téléchargements gatés
│   │   ├── background_calc.py        Exécution des calculs longs en thread séparé avec suivi de progression (st.fragment).
│   │   └── tabs/                     Un module par onglet de l'app, tous consommant un TabContext
│   │       ├── __init__.py
│   │       ├── context.py            TabContext — état partagé construit une fois avant les onglets
│   │       ├── declarations.py       Onglet "💶 Déclarations"
│   │       ├── detail_ventes.py      Onglet "📋 Détail ventes"
│   │       ├── vies_ui.py            Onglet "🛡️ VIES"
│   │       ├── audit.py              Onglet "🔬 Audit Amazon"
│   │       ├── telechargements.py    Onglet "📥 Téléchargements"
│   │       └── visualisations.py     Onglet "📊 Visualisations"
│   
├── vercel_webhook/
│   └── api/
│       ├── requirements.txt          Dépendances de la fonction serverless (stripe, psycopg2-binary)
│       └── stripe_webhook.py         Endpoint webhook Stripe, déployé sur Vercel — charge
│                                     tva_intracom/billing.py par chemin de fichier (monorepo)
├── .gitignore
├── app.py                            Interface Streamlit — orchestrateur (auth, upload, calcul,
│                                     construction du contexte, appel des modules tva_intracom/ui/)
├── conftest.py
├── generate_dataset.py               Générateur de données de test au format Amazon.
├── generer_donnees_10k.py
├── generer_donnees_multian.py
├── mise.toml                         Config gestionnaire d'outils mise
├── nixpacks.toml                     Config build Nixpacks (Railway)
├── Procfile                          Processus de démarrage pour déploiement cloud
├── pyproject.toml
├── railway.toml                      Config spécifique Railway
├── README.md
├── requirements.txt
├── runtime.txt                       Version Python pour déploiement
└── vercel.json                       Config Vercel (includeFiles vers tva_intracom/billing.py)
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
| `ca3_report.py` | Génération du rapport CA3 (HTML uniquement — pas d'export EDI-TVA, voir Roadmap) : compute_ca3_lines_v2, AIC ligne 08 (transferts FBA), déductions manuelles, calcul du solde net, generate_ca3_html_report_v2 |
| `local_vat_report.py` | Équivalent générique du CA3 pour n'importe quel pays UE hors France (canal `LOCAL_REGISTRATION`, ou `FR_DOMESTIC` quand ce pays est le **pays d'origine** du compte) : `compute_local_vat_lines`, `generate_local_vat_html_report`. Ventilation base/TVA par taux réellement présent dans les données, style visuel harmonisé au CA3, mais **PAS un fac-similé du formulaire officiel** — un avertissement explicite figure dans chaque rapport généré. Codes de case indicatifs pour DE/ES/IT/PL/NL/BE/PT/SE/AT/CZ/RO/HU/IE (`rates.LOCAL_VAT_BOX_CODES`, non vérifiés exhaustivement contre un PDF officiel, contrairement au CA3) |
| `fec_export.py` | Export comptable au format FEC (journal des ventes agrégé par régime/pays/taux, écritures équilibrées débit/crédit) — pré-remplissage pour import dans un logiciel comptable tiers, alternative légère à l'EDI-TVA (voir Roadmap) |
| `excel_report.py` | Export Excel multi-onglets (voir détail onglets ci-dessous) |
| `historical_rates_widget.py` | Composant UI Streamlit pour afficher l'historique des taux de change BCE appliqués |
| `report.py` | ReportSummary, build_report, render_report — ventilation HT exhaustive par canal fiscal (ht_by_bucket) servant de contrôle de cohérence interne, et agrégation mensuelle nette par pays (oss_by_country_month, local_by_country_month) |
| `mem_utils.py` | Utilitaires d'analyse et d'optimisation de la mémoire (interning, RAM stats) |
| `cli.py` | Interface en ligne de commande (CLI) pour exécuter le moteur hors interface web |
| `amazon_adapter.py` | Passerelle de compatibilité entre les anciens modèles de données et le nouveau package de parsers |
| `parsers/amazon/` | Sous-package d'import Amazon (formats 1–5) — voir arborescence ci-dessus |
| `auth.py` | Authentification historique par magic link (Postgres/Supabase, désactivée côté UI, voir plus bas), envoi d'e-mail via l'API Resend, chiffrement Fernet du refresh_token Amazon SP-API, stockage serveur des verifiers PKCE OAuth (`tva_oauth_pkce`) |
| `auth_supabase.py` | Authentification par mot de passe et OAuth social (Google, Microsoft, GitHub, Amazon) via l'API Supabase Auth (GoTrue REST), flux PKCE |
| `amazon_spapi.py` | Intégration Amazon Selling Partner API (SP-API) : OAuth 2.0, échange de code, rafraîchissement de token et identification du vendeur — sert à la **liaison de compte** pour la récupération des rapports de vente, distincte de la connexion Amazon de l'écran de login (voir section Authentification) |
| `billing.py` | Facturation Stripe : Checkout PAYG, Pro et Cabinet (mensuel/annuel, paliers dégressifs), Customer Portal, quotas SIREN par compte, grille tarifaire lue en direct sur Stripe, traitement des webhooks, quotas stockés en Postgres/Supabase, et **rattachement anti-abus Compte Amazon <-> SIREN** |
| `app.py` | Orchestrateur Streamlit (racine du dépôt, pas dans `tva_intracom/`) — upload, calcul (avec cache `st.session_state`), construction du contexte, appel des modules `ui/` |

---

## Architecture de l'interface Streamlit (`tva_intracom/ui/`)

`app.py` a été réduit à un rôle d'orchestrateur (~650 lignes) : upload des
fichiers, calcul TVA mis en cache, puis délégation de tout le rendu à
`tva_intracom/ui/`. Chaque module reprend le code d'origine **à
l'identique** (aucune logique métier modifiée), simplement isolé et
paramétré par un objet de contexte plutôt que par des variables globales
du script.

| Module | Rôle |
|---|---|
| `ui/theme.py` | `apply_theme()` — configuration de page Streamlit (titre, icône, layout) et injection du CSS de marque |
| `ui/formatting.py` | Helpers d'affichage partagés : `_fmt`, `country_label`, `_money_col`, `_pct_col`, `_smart_money_df` (formatage vectorisé haute performance), `_gated_preview_table` (optimisé RAM, affichage du décompte total des lignes masquées, protection "tva"), `_fec_period_end_date`, tri numérique robuste, `_render_filter_bar` (scan optimisé) |
| `ui/auth_flow.py` | `AuthContext` + `ensure_cookie_manager()` / `run_auth_flow()` — bypass dev local, restauration de session par cookie, consommation du lien magique, migration `?session_token=`, callback OAuth Amazon SP-API, écran de connexion (bloquant via `st.stop()`), bandeau connecté/déconnexion |
| `ui/onboarding.py` | `maybe_show_sidebar_tour` / `maybe_show_tabs_tour` — Visite guidée de première connexion utilisant `st.dialog` et `st.fragment` |
| `ui/rerun_utils.py` | `preserve_upload_rerun()` — Gestion fine des reruns pour éviter de perdre le fichier uploadé lors d'interactions sidebar |
| `ui/sidebar.py` | `SidebarResult` + `render_sidebar()` — tous les accordéons de la barre latérale : **Pays d'origine** (`home_country`, tout premier réglage, voir section dédiée ci-dessous), connexion SP-API, Validation & Devises, Cache VIES, Paramètres du fichier, Catalogue Produits, Entreprise & Paramètres avec gestion des SIREN, Abonnements & forfaits Stripe, et génération de **Certificat VIES (PDF)** basé sur un snapshot |
| `ui/billing_gate.py` | `BillingGate` + `build_billing_gate()` — détection de période, gating crédit PAYG/abonnement actif, gating quota SIREN, gating conformité (TVA locales/IOSS manquants), **rattachement anti-abus Compte Amazon <-> SIREN**, méthode `gated_download()` utilisée par tous les exports de tous les onglets |
| `ui/background_calc.py` | `start_background_job` / `render_job_progress` — Exécution des calculs longs (VIES/moteur) en thread séparé pour ne pas bloquer l'UI Streamlit |
| `ui/tabs/context.py` | `TabContext` — dataclass regroupant tout l'état nécessaire aux onglets (résultats moteur, statut billing, paramètres entreprise, données brutes d'import), construite une fois avant l'affichage des onglets |
| `ui/tabs/declarations.py` | Onglet **💶 Déclarations** — récapitulatif "Ce que vous devez reverser" (CA3, OSS par pays, IOSS, DDP, Fisc local), barre de seuil OSS, Contrôle de Cohérence Comptable |
| `ui/tabs/detail_ventes.py` | Onglet **📋 Détail ventes** — 4 sous-onglets : Ce que vous devez / Géré par des tiers / Ligne par ligne / Remboursements |
| `ui/tabs/vies_ui.py` | Onglet **🛡️ VIES** — KPIs de validation, classification manuelle des numéros non vérifiés (`st.fragment`), overrides persistés, reclassifications B2B→B2C |
| `ui/tabs/audit.py` | Onglet **🔬 Audit Amazon** — écarts TVA Amazon par catégorie (taux, VIES, UK, autoliquidation art.194, TVA manquante), mouvements de stock FBA |
| `ui/tabs/telechargements.py` | Onglet **📥 Téléchargements** — génération de tous les exports (Excel complet avec détail mensuel, XML/Excel/CSV OSS, déclaration du **pays d'origine** en premier — CA3 HTML si FR, rapport HTML générique sinon —, déclarations locales HTML/CSV pour tous les autres pays (dont la France si elle n'est pas le pays d'origine), B2B, FEC) |
| `ui/tabs/visualisations.py` | Onglet **📊 Visualisations** — TVA due par pays, répartition Vous/Amazon/Douane, carte Europe, évolution mensuelle, répartition par scénario |

**Dépendance intentionnelle entre onglets** : `ui/tabs/declarations.py`
calcule `_oss_tva_net_total` et le stocke sur `ctx.oss_tva_net_total` ;
`ui/tabs/telechargements.py` le relit pour l'export CSV de la déclaration
locale française. Ce couplage existait déjà dans l'ancien script monolithique
(variables partagées dans le même scope) — il est resté volontairement
explicite plutôt que dupliqué, voir la docstring de `context.py`.

---

## Pays d'origine du compte (`home_country`)

Réglage **global au compte** (pas par SIREN — contrairement à l'IOSS ou au mode
DDP), affiché en tout premier dans la barre latérale, persisté en base
(`tva_users.home_country`, défaut `"FR"`).

- **Sélecteur de langue avant connexion** : `language_selector()` est
  désormais appelé avant l'écran de connexion, pour que l'interface entière
  — y compris l'écran de connexion lui-même (mot de passe, OAuth) — s'affiche
  dans la langue choisie, sans attendre l'authentification.
- **Impact sur le moteur fiscal** : `sale.seller_country` (déjà présent sur
  chaque `Sale`, transmis via le paramètre `seller_country` de
  `load_amazon_report()` et des autres parsers marketplace) reflète ce choix.
  `engine.py` compare désormais le pays de stock/destination à
  `sale.seller_country` plutôt qu'à un littéral `"FR"` figé, pour classer une
  vente en régime domestique (`Channel.FR_DOMESTIC` — nom conservé pour
  compatibilité, signifie désormais « domestique dans le pays d'origine du
  compte », pas littéralement la France) ou en immatriculation locale
  (`Channel.LOCAL_REGISTRATION`).
  - Cas non concernés par cette généralisation (volontairement laissés en
    l'état) : le cas Monaco (convention fiscale franco-monégasque du 18 mai
    1963, spécifique à la France par nature) et le seuil OSS sous 10 000 €
    opt-in (`apply_fr_under_threshold`, mécanisme OSS — aucun impact sur
    l'OSS n'était souhaité).
- **Impact sur l'onglet Téléchargements** : la déclaration du pays d'origine
  s'affiche en premier — le CA3 (Cerfa, fac-similé vérifié) si le pays
  d'origine est la France, sinon le rapport HTML générique de
  `local_vat_report.py` pour ce pays. La section « Déclarations Locales »
  regroupe ensuite tous les **autres** pays où une immatriculation locale est
  détectée, France comprise si elle n'est pas le pays d'origine.
- **Aucun impact sur l'OSS** : le guichet unique OSS reste toujours déclaré et
  agrégé de la même façon, indépendamment du pays d'origine choisi.
- **Devise d'affichage locale** : la devise de **calcul interne** du moteur
  fiscal reste **toujours l'EUR**, quel que soit le pays d'origine choisi —
  `home_country`/`seller_country` ne sert qu'au classement des ventes
  (domestique / OSS / immatriculation locale), jamais à la devise de calcul.
  Pour l'affichage (page de synthèse Excel, KPIs et tableaux Streamlit,
  graphiques de l'onglet Visualisations), les montants EUR sont convertis à la
  volée vers la **devise d'affichage choisie** (réglable dans la sidebar, par
  défaut celle du pays d'origine via `rates.COUNTRY_CURRENCIES`), au taux BCE du
  jour de génération (`ecb_rates.convert_to_currency`,
  `ui/formatting.py::_get_conversion_rate`, mis en cache en session pour éviter
  un appel BCE par cellule affichée). En cas d'indisponibilité du taux BCE,
  repli silencieux sur le montant EUR plutôt que de faire échouer l'affichage.
  Les déclarations légales elles-mêmes (CA3, XML OSS officiel, exports
  CSV/HTML des immatriculations locales) restent en EUR, comme l'exige la
  réglementation — seule la couche de présentation convertit.
  - ⚠️ Historique : cette séparation calcul/affichage n'a pas toujours été
    respectée — voir Roadmap pour le détail du bug corrigé (le moteur
    convertissait autrefois les montants dans la devise du pays d'origine
    dès l'import, contaminant tous les calculs fiscaux en aval).

---



- **Auth** : depuis juillet 2026, authentification déléguée à **Supabase Auth**
  (API GoTrue REST, module `auth_supabase.py`) :
  - **Mot de passe** : signup/signin classiques (`/auth/v1/signup`,
    `/auth/v1/token?grant_type=password`).
  - **OAuth social** : **Google**, **Microsoft** (provider Supabase `azure`),
    **GitHub**, et **Amazon** (Login with Amazon, configuré comme *Custom OAuth
    Provider* Supabase — endpoints manuels ou auto-discovery via
    `https://www.amazon.com`, distinct de la connexion SP-API de la barre
    latérale qui sert à la récupération des rapports de vente). Flux **PKCE**
    exclusivement (Supabase renvoie un `?code=` en paramètre de requête classique,
    lisible côté serveur — le mode implicite renverrait les jetons dans un
    fragment d'URL `#access_token=...`, invisible pour Streamlit).
  - **Stockage du verifier PKCE** : table Postgres dédiée `tva_oauth_pkce`
    (nonce → verifier, purge automatique après 15 min), et **non** un cookie
    navigateur — un cookie posé depuis l'iframe du composant
    `extra_streamlit_components` ne s'est pas montré fiable pour survivre à la
    redirection externe vers le fournisseur OAuth. Le nonce transite dans le
    paramètre `redirect_to` et revient dans l'URL de callback
    (`?sb_provider=...&sb_nonce=...&code=...`). Un cache `session_state` évite
    de regénérer un nonce à chaque rerun Streamlit tant que le login n'a pas
    abouti.
  - Un utilisateur authentifié par Supabase Auth (mot de passe ou OAuth) est
    mappé sur un `tva_users` local par e-mail (`tva_auth.get_or_create_user()`)
    — `tva_users` reste la source de vérité pour `home_country`, langue,
    devise d'affichage, SIREN, etc. ; Supabase Auth ne sert qu'à vérifier
    l'identité.
  - **Lien magique** : le code historique (`tva_auth.create_magic_link` /
    `send_magic_link_email`, API Resend) reste dans le dépôt mais son bouton
    est **désactivé côté écran de connexion** ("en préparation") le temps
    d'une éventuelle bascule vers le magic link natif de Supabase Auth.
  - Jeton de session applicatif distinct (30 jours, réutilisable, porté par
    cookie `tva_session_token`) permettant de rester connecté après une
    redirection externe (OAuth, paiement Stripe) ou un rafraîchissement de
    page, quelle que soit la méthode de connexion utilisée. La détection de
    l'URL de l'application est dynamique (headers HTTP) ou forcée via le secret
    `APP_BASE_URL`. En développement local uniquement, le secret
    `LOCAL_DEV_BYPASS_AUTH` (jamais défini en production, à réserver au
    `.streamlit/secrets.toml` local non commité) permet de se connecter avec
    n'importe quelle adresse e-mail.
  - **Secrets requis** : `SUPABASE_URL`, `SUPABASE_ANON_KEY` (clé **anon**,
    jamais `service_role`) en plus de `SUPABASE_DB_URL` (connexion Postgres
    directe, utilisée par `auth.py`/`billing.py`/`vies_engine.py`, à ne pas
    confondre avec l'API Auth). Configuration côté tableau de bord Supabase :
    Authentication > Providers (activer Email, Google, Azure, GitHub, et le
    Custom Provider Amazon), Authentication > URL Configuration (ajouter
    `APP_BASE_URL` aux Redirect URLs).
- **Facturation** : Stripe Checkout, 3 forfaits —
  - **Pay-as-you-go** : un crédit d'export correspond à une période fiscale
    (`period_label`, ex. `2026-Q2`) débloquée pour un utilisateur donné. Le
    déblocage est indépendant du nom de fichier ou du contenu exact du CSV
    importé : seule la période détectée dans les transactions compte. Un même
    fichier renommé, ou un fichier légèrement corrigé pour la même période,
    reste débloqué sans nouveau paiement.
  - **Pro** : abonnement récurrent (mensuel ou annuel), accès illimité,
    limité à 1 SIREN client par compte.
  - **Cabinet** : abonnement récurrent (mensuel ou annuel), accès illimité,
    quantité de SIREN choisie au Checkout (3 minimum), avec tarif dégressif
    Stripe (tiered pricing) selon la quantité. La modification de quantité ou
    de forfait sur un abonnement déjà actif se fait via le Portail client
    Stripe (bouton "Gérer mon abonnement"), jamais via un nouveau Checkout.
  - **Crédits PAYG** : historique des périodes débloquées visible directement
    dans la section "Abonnements & forfaits".
- **Quotas & Profils SIREN** : chaque compte enregistre les SIREN de ses clients
  (nom d'entreprise, SIREN, n° de TVA FR) ainsi que leurs **paramètres
  persistants** (numéro IOSS, mode DDP, seuil OSS, pays d'immatriculation). Ces
  paramètres sont sauvegardés en base de données par SIREN et restaurés
  automatiquement lors de la sélection du client. Le retrait d'un SIREN est
  différé (lazy deletion) à la date anniversaire de l'abonnement.
- **Rattachement anti-abus (Account Linking)** : pour éviter qu'un même compte
  SaaS ne serve à générer des rapports pour une infinité d'entreprises distinctes,
  le moteur détecte les identifiants de compte Amazon présents dans les fichiers.
  Chaque identifiant doit être lié à un SIREN spécifique. Si un identifiant est
  déjà rattaché à un autre SIREN (concurrence), le téléchargement est bloqué.
- **Grille tarifaire** : les montants affichés dans l'app (achat unique, Pro,
  paliers Cabinet) sont récupérés en direct depuis l'API Stripe
  (`billing.get_pricing_grid()`), jamais recopiés en dur, pour ne jamais
  diverger du tarif réellement configuré dans le Dashboard Stripe.
- **Contenu gratuit limité** : tant qu'une période n'est pas débloquée, l'outil
  propose un aperçu bridé pour protéger sa valeur ajoutée fiscale :
  - **Tableaux de résultats** : affichage de l'intégralité du volume (pour
    constater le traitement complet), mais avec une **double limitation** : seules
    les 10 premières lignes (ou 15 %) sont affichées en clair. Pour toutes les
    autres, les données sensibles (identifiants, montants, taux, scénarios) sont
    remplacées par un badge de verrouillage. Les colonnes Date, Pays et ID
    restent visibles partout pour permettre le rapprochement visuel.
  - **Déclarations** : les lignes de synthèse (totaux par canal) affichent le CA
    HT net pour validation, mais les montants de TVA sont verrouillés. Le détail
    par pays (sous-lignes) est intégralement masqué.
  - **Visualisations** : le graphique de répartition de la TVA par pays et la
    carte interactive de l'Europe sont verrouillés.
- **Webhook Stripe** : fonction serverless Vercel (`vercel_webhook/api/stripe_webhook.py`)
  qui reçoit les événements Stripe et met à jour Supabase via `tva_intracom/billing.py`,
  chargé directement par chemin de fichier (`importlib`) pour éviter de dupliquer le
  code entre les deux environnements de déploiement (Streamlit Cloud + Vercel).
  L'abonnement est enregistré dès l'événement `checkout.session.completed`
  (récupération de l'abonnement complet via `stripe.Subscription.retrieve`)
  plutôt que de dépendre uniquement des événements `customer.subscription.*`
  séparés, qui peuvent ne pas être cochés sur l'endpoint selon la config
  Stripe. Les erreurs de traitement sont loggées côté serveur (logs Vercel),
  jamais renvoyées dans la réponse HTTP.
*   **Base de données partagée** : Postgres (Supabase), accessible à la fois depuis
    Streamlit Cloud (lecture des crédits/abonnements) et depuis la fonction serverless
    Vercel (écriture après paiement confirmé) — un SQLite local ne conviendrait pas
    puisque les deux environnements ne partagent aucun disque. La gestion des
    connexions est centralisée dans `database.py`
    (`NonPoolingConnectionPool` + `run_with_retry()`), consommée par `auth.py`,
    `billing.py` et `ecb_rates.py` en mode `cache_connection=True` : une seule
    connexion est ouverte et réutilisée par exécution (run) Streamlit (cache par
    thread via `threading.local()`), puis fermée au début du run suivant sur le
    même thread. C'est optimal pour Streamlit (1 thread = 1 session) tout en
    restant compatible avec PgBouncer — et volontairement **pas** un vrai pool
    persistant (`psycopg2.pool.ThreadedConnectionPool`), qui garderait une
    connexion TCP ouverte en permanence et empêcherait Railway de détecter
    l'inactivité et de mettre l'app en veille (scale-to-zero). `vies_engine.py`
    utilise le même module en mode `cache_connection=False` (connexion fraîche
    par appel) pour supporter son exécution parallèle (`ThreadPoolExecutor` à 25
    workers).

---

## Formats Amazon supportés

| Format | Description | Clé de détection |
|---|---|---|
| **1** | Ancien format TSV | `departure_country`, `tax_calculation_date` |
| **2** | Format intermédiaire | `activity_period` |
| **3** | TSV/CSV 2024 | `transaction_complete_date` + `tax_collection_model` |
| **4** | CSV 2025+ | `transaction_complete_date` + `tax_collection_responsibility` (sans `tax_collection_model`) |
| **5** | Rapport fiscal détaillé V5 | `our_price_tax_exclusive_selling_price` + `transaction_id` + `order_date` |

La détection est automatique sur le header. Le format 5 fait l'objet d'une
pré-agrégation multi-juridictions (une ligne par juridiction → une ligne par
transaction) avant traitement.

---

## Fonctionnalités clés

### Moteur fiscal

- **Typage Statique & Validation Pydantic** : Utilisation de `pydantic.dataclasses` pour une validation stricte dès l'import (codes pays ISO 2, montants décimaux nettoyés des symboles €/$). Précision absolue via `Decimal`.
- **Documentation Fiscale Directe** : Chaque note de résultat (`VatResult.note`) intègre des références légales précises et des liens courts vers le **Bofip**, l'**Art. 262 ter du CGI** ou les **Directives Européennes** pour justifier le traitement (ex: Monaco, IOSS, Art. 194). Ce texte complet n'est produit que lorsque la langue de l'interface est le français (`engine.py::_note()`) : les articles de loi français n'ayant pas de traduction pertinente dans une autre langue, les 6 autres langues affichent une note générique minimale (scénario, pays, taux — sans référence légale), via des clés i18n dédiées (`engine_note_*` dans `i18n/*.toml`). Le comportement hors Streamlit (usage bibliothèque, voir plus bas) reste inchangé : note française complète par défaut.
- Taux TVA historisés par pays avec gestion des changements de taux dans le temps
  (`vat_rate_at_date`).
- Taux réduits par catégorie produit (`product_category` : STANDARD, REDUCED,
  SUPER_REDUCED, ZERO, EXEMPT).
- Reverse charge domestique art. 194 (national uniquement, jamais en
  cross-border) pour ES, IT, PL, CZ, SK, HU, RO, BG, HR, LT, LV. Pour une
  vente B2B **cross-border** dont le n° TVA acheteur est invalide vers l'un
  de ces pays, voir la section « Roadmap » : la TVA reste due au pays de
  départ, pas d'exonération.
- Détection des territoires hors UE fiscale (Canaries, DOM-TOM, Åland, Helgoland…)
  via code postal (`is_non_fiscal_eu`).
- Seuil OSS 10 000 € opt-in, suivi multi-année avec `oss_ht_by_year`.
- **Plan d'action Immatriculations** : vue consolidée détectant les besoins de
  mise en conformité (stock Amazon détecté, ventes locales taxables, import DDP),
  restreinte aux pays **UE** (`rates.is_eu`) — un stock hors UE (Royaume-Uni,
  États-Unis, Suisse, Chine…) ne crée jamais d'obligation d'immatriculation TVA
  intracommunautaire. Alerte critique pour l'Allemagne (DE) et le pays d'origine
  du compte (`home_country`) avec rappel des risques de blocage de compte Amazon.
- **Gestion fine des périodes** : support complet des mois isolés (`2026-06`)
  pour les achats uniques PAYG, avec conversion automatique au format
  trimestriel pour le XML OSS officiel.
- Refunds intégrés chronologiquement dans la boucle OSS via `id()` Python (pas
  de collision sur les `sale_id` répétés).
- Clé composite `(sale_id, amount_ht)` pour le suivi stable des ventes affectées par VIES (remplace `id()` Python).
- Composite key `(sale_id, buyer_vat_number)` pour `sale_vat_index`.

### Validation VIES

Le module s'appuie sur une architecture résiliente à trois niveaux pour interroger le service officiel de la Commission Européenne (VIES), optimiser les temps de réponse et garantir la continuité de service même en cas de panne du serveur de l'UE.

*   **Backend Postgres (Supabase)** : Remplace définitivement l'ancien cache SQLite local (qui n'était pas persistant entre deux redéploiements sur Streamlit Cloud). Il utilise le pool de connexions `psycopg2-binary` et partage la variable d'environnement `SUPABASE_DB_URL` avec les modules d'authentification et de facturation.
*   **Architecture à trois niveaux (Cascade de cache)** :
1.  **vies_scope_cache** : Cache PRIVÉ par "scope" (compte isolé ou domaine d'entreprise). Consulté en premier pour garantir une isolation stricte des données de tes clients ou cabinets.
2.  **vies_global_cache** : Cache PARTAGÉ entre tous les comptes du SaaS, alimenté uniquement par les vérifications automatiques réussies auprès de l'UE. Sert de filet de sécurité mutualisé ultra-rapide.
3.  **API VIES (ec.europa.eu)** : Interrogée en dernier recours si le numéro est inconnu ou expiré dans les deux caches précédents.
*   **Résolution intelligente de la portée (Scope ID)** :
*   *Messageries grand public* (`@gmail.com`, `@outlook.fr`, etc.) : Le cache est strictement isolé par utilisateur (`user:<email>`).
*   *Domaines professionnels* (`@cabinet-comptable.fr`) : Le cache est partagé entre tous les collaborateurs d'une même structure (`domain:<domaine>`).
*   **Piste d'audit (vies_check_history)** : Table au format *append-only* (jamais écrasée). Chaque scope conserve sa propre preuve horodatée de la date à laquelle il a validé un statut VIES (y compris s'il l'a récupéré via le cache global), indispensable pour justifier une exonération B2B lors d'un contrôle fiscal.
*   **Classifications manuelles (vies_manual_overrides)** : Permet à l'utilisateur de forcer le statut d'un numéro indisponible ou inconclusif. Ces overrides sont strictement privés, ont une durée de vie indexée sur le TTL **propre au scope courant** (voir ci-dessous), et **ne remontent jamais** dans le cache global.
*   **TTL de cache configurable, isolé par scope** : Le TTL (durée avant revalidation, slider 1-365 jours dans la sidebar) est stocké par `scope_id`, jamais dans une variable partagée par tout le process — un cabinet qui ajuste son TTL n'affecte ni les autres cabinets, ni le cache global mutualisé (`vies_global_cache`), qui conserve toujours sa valeur par défaut (30 jours) non modifiable depuis l'UI.
*   **Blocage de conformité** : Téléchargements bloqués si des numéros TVA B2B
    demeurent non classifiés (erreur serveur UE) pour garantir l'exactitude
    fiscale des rapports.
*   **Performances et résilience** :
*   Validation en lot via 25 workers `ThreadPoolExecutor` en parallèle avec barre de progression.
*   Système de retry avec *backoff exponentiel* (1s ➔ 2s ➔ 4s) sur erreurs transitoires.
*   *Batch degradation detection* : Si le serveur de l'UE renvoie trop de réponses vides sous forte charge, le moteur bascule sur le dernier état valide en cache (mode dégradé) au lieu d'invalider à tort les clients B2B.
*   **Normalisation native** : La fonction `normalize_full_vat()` évite les faux rejets et gère les structures complexes (ex: Espagne NIF/CIF, alias EL/GR, ou ventes transfrontalières avec numéro d'un tiers pays).

### Conversion devises

- API BCE SDW (`data-api.ecb.europa.eu`) sans clé, fenêtre ±7 jours pour les
  weekends/jours fériés.
- Cache deux niveaux : mémoire (`dict`) + base de données Postgres (Supabase)
  partagée et persistante.
- **Warm-up du cache (Batch)** : Scanne les dates du fichier au démarrage et effectue
  une requête groupée vers l'API BCE pour toutes les devises concernées. Cette stratégie
  optimise radicalement le chargement pour les fichiers couvrant plusieurs années.
- **Compliance OSS (Règl. UE 2020/194, art. 5 bis)** : Utilisation du taux BCE du
  **dernier jour de la période de déclaration** pour les ventes OSS.
  - **Intelligence multi-période** : Si le fichier couvre plusieurs trimestres
    (ex: `2026-S1`), le moteur calcule automatiquement la date de clôture propre au
    trimestre de *chaque* transaction.
  - **Gestion des taux futurs** : Si la période n'est pas encore terminée (ex: analyse
    le 15 mars pour le Q1), l'outil bascule automatiquement sur le taux du jour
    de la vente à titre d'estimation, avec un avertissement explicite dans l'UI.
- **Affichage transparent** : Expandeur dédié listant chaque paire (Devise, Date de clôture)
  réellement utilisée dans le calcul, garantissant une piste d'audit claire.
- HRK (kuna croate) : taux fixe irrévocable 1 EUR = 7,53450 HRK depuis le 01/01/2023
  (Règl. UE 2022/1540).

### Import des fichiers Amazon

- **Performance extrême** : utilisation de **Polars** (moteur Rust ultra-rapide) pour le parsing des fichiers CSV volumineux, avec repli automatique sur Pandas et `csv.DictReader`.
- **Détection intelligente d'encodage** : bascule automatique entre **UTF-8** et **Windows-1252** (cp1252) pour garantir la lecture correcte des exports Excel/CSV sans corruption des caractères spéciaux.
- Détection automatique du format et du séparateur (tab / `;` / `,`).
- Support des fichiers jusqu'à **150 Mo**.
- Filtrage des placeholders Amazon (`FRINV…`, `ITINV…`) et des NIF fiscaux nationaux
  (codice fiscale IT, NIF ES, NIP PL…) — ces derniers ne sont pas interrogeables VIES.
- Détection des territoires d'exception TVA via code postal de destination
  (`arrival_post_code`).
- `order_date` conservée distinctement de `transaction_date` (date d'exigibilité =
  date d'expédition, art. 65 Dir. 2006/112/CE) — permet de détecter les commandes à
  cheval sur deux périodes de déclaration (`period_mismatches`).
- Avertissements surfacés dans l'UI Streamlit pour les commandes à cheval.
- **Cache de l'analyse des fichiers** (`app.py`) : Streamlit ré-exécute tout le
  script à chaque interaction (rerun), ce qui relançait auparavant toute la
  boucle de parsing sans le vouloir — invisible sur un petit fichier, mais
  doublant le temps de chargement sur un gros fichier. L'analyse n'est
  désormais relancée que si les fichiers ou les options d'import (pays
  d'origine, encodage, conversion devise, format, catalogue ASIN) ont
  réellement changé (clé de cache en session, indépendante du cache de calcul
  TVA `_calc_key`).

### Export XML OSS officiel

- Structure conforme Règlement UE 2021/965 :
  `SupplyFromMemberState` → `SuppliesPerMemberStateOfConsumption` → `GoodsSupplies`.
- Qualification `STANDARD` / `REDUCED` basée sur `STANDARD_VAT_RATES[arrival_country]`
  (et non un seuil fixe).
- Validation de la période avant génération (formats : `YYYY-QN`, `YYYY-TN`, `YYYY-SN`,
  `YYYY`, `YYYY-QN_QM`, `YYYY-YYYY`).
- **Multi-validation XSD** : Validation automatique du flux XML par rapport aux schémas de l'administration (`oss_dgfip_complete.xsd`, `oss_dgfip_minimal.xsd`, `oss_vat_return.xsd`). Le système est valide si conforme à au moins l'un des schémas présents.
- **Garde-fou soldes négatifs** : détecte si un couple (pays/taux) ressort en
  négatif (avoirs supérieurs aux ventes), ce qui est interdit dans le corps
  principal d'une déclaration OSS. L'outil propose alors un diagnostic de
  rattachement (voir Correction assistée ci-dessous). Pour faciliter les tests,
  la génération du XML reste possible malgré un solde négatif (via un message
  d'avertissement), bien que le fichier soit susceptible d'être rejeté par le
  portail fiscal.

- **Correction assistée (rattachement automatique avoir → vente d'origine)** :
  `oss_export.suggest_negative_bucket_corrections()` recherche, pour chaque avoir
  responsable d'un solde négatif, une vente antérieure de MÊME `sale_id` (même
  commande Amazon) présente dans le fichier importé. Ce rattachement n'est
  utilisé que s'il repose sur un identifiant de commande identique — jamais sur
  une simple déduction à partir d'`order_date`, jugé insuffisamment fiable pour
  générer automatiquement une correction fiscale (voir `models.py`).
  - Si **tous** les avoirs d'un couple pays/taux négatif sont ainsi rattachés,
    l'UI propose une case de confirmation ; une fois cochée,
    `generate_oss_xml(confirm_corrections=True)` exclut ces avoirs du corps
    principal et génère automatiquement le bloc `CorrectionsOfVatReturns`
    référençant la période d'origine détectée.
  - Si une partie seulement (ou aucun avoir) ne peut être rattachée — typiquement
    quand le fichier importé ne couvre pas la période d'origine de la vente
    créditée — le blocage manuel reste actif pour la part non rattachée, avec le
    détail affiché (montant HT/TVA non résolu).
  - ⚠️ La structure XML du bloc `CorrectionsOfVatReturns` généré est une
    approximation, non vérifiée contre le schéma XSD officiel DGFIP/UE — à
    valider avant tout dépôt réel utilisant cette fonctionnalité.

  **Exemple concret** : la période `2026-Q2` contient un avoir DE (19%) de 300 €
  alors que les ventes DE (19%) de la période ne totalisent que 120 € → solde de
  -180 € détecté sur le couple (DE, 19%).
  - *Si* cet avoir partage le même `sale_id` qu'une vente DE (19%) de `2026-Q1`
    présente dans le fichier importé : rattachement automatique proposé, le XML
    `2026-Q2` (une fois confirmé) inclut le corps principal assaini **et** un
    bloc `CorrectionsOfVatReturns` référençant `2026-Q1`.
  - *Sinon* : blocage inchangé — marche à suivre manuelle sur le portail OSS
    (guichet-unique.impots.gouv.fr ou portail de l'État membre d'identification),
    rubrique corrections de périodes antérieures, en y référençant explicitement
    la période d'origine identifiée par l'utilisateur.

### Interface Streamlit — contrôles & ergonomie

- **UI Modernisée** : Identité visuelle "Pro" avec couleur de marque (`#1f4e79`), cartes de métriques animées et support du **mode Sombre** (Theme selection restaurée).
- **Réactivité via st.fragment** : La classification manuelle VIES est isolée dans un fragment Streamlit, permettant de corriger des statuts sans recharger toute l'application ni recalculer les graphiques.
- **Profils Clients persistants** : sélection et configuration rapide des SIREN avec
  mémorisation des paramètres d'import et numéros de TVA locaux.
- **Exports personnalisés** : tous les noms de fichiers incluent désormais le nom de
  l'entreprise et la période (ex: `Export OSS URSSAF - MonEntreprise - 2026-Q1.csv`).
- **Barre de progression** sur le parsing des rapports Amazon volumineux, via le
  paramètre `progress_callback` de `load_amazon_report()`.
- **Découpage modulaire** : l'ancien `app.py` monolithique (~3000 lignes) a été
  scindé en un package `tva_intracom/ui/` (thème, formatage, auth, sidebar,
  gating billing, un module par onglet) — voir la section dédiée ci-dessus.
  `app.py` ne fait plus que 650 lignes et se limite à l'orchestration.

---

## Export Excel — onglets générés

| # | Onglet | Contenu |
|---|---|---|
| 1 | **Récapitulatif** | Synthèse TVA par canal et **Audit d'intégrité technique** (Nombre de lignes, CA HT Net, Signature numérique Hash ID) |
| 2 | **Détail ventes** | Ligne par ligne avec scénario, taux, canal, note |
| 3 | **Détail remboursements** | Avoirs avec même structure |
| 4 | **OSS par pays** | Agrégation par pays de destination + taux, avec **détail mensuel net** |
| 5 | **TVA locale par pays** | Immatriculations locales (stocks FBA hors FR) avec **détail mensuel net** |
| 6 | **Audit Écarts Amazon** | Ventes où la TVA calculée diffère de celle collectée par Amazon |
| 7 | **Historique VIES** | Toutes les vérifications VIES horodatées (piste d'audit) |
| 8 | **Analyse AIC FBA** | AIC estimées par flux (art. 17 Dir. 2006/112/CE), TVA AIC à autodéclarer |
| 9 | **Transferts FBA Détail** | Liste brute des mouvements de stock FC |
| 10 | **Intrastat (EMEBI)** | Aide au remplissage : introductions et expéditions par mois/ASIN/flux, seuil annualisé, renvoi vers l'ESL (obligation fiscale distincte, voir onglet Calendrier Fiscal) |
| 11 | **INVOICE & CREDIT_NOTE** | Détail des écritures de service Amazon (hors ventes/remboursements clients) |
| 12 | **Calendrier Fiscal** | Prochaines échéances OSS, CA3, Intrastat, ESL avec jours restants |

---

### Export comptable (FEC)

En complément du rapport CA3 (HTML, saisie manuelle) et en attendant un
éventuel export EDI-TVA homologué (voir Roadmap), l'outil génère un **journal
des ventes au format FEC** (art. A47 A-1 du LPF), prêt à être importé dans un
logiciel comptable tiers (Sage, Ciel, Quadratus, ACD…) par le cabinet
comptable.

- **Agrégation** : une écriture par (période, régime fiscal, pays de TVA,
  taux) — pas une écriture par vente. Un fichier de plusieurs milliers de
  transactions tient donc en quelques dizaines de lignes FEC.
- **Gestion des codes journaux & numérotation** : attribution automatique des
  codes journaux (ex: `VEN` pour les ventes) et génération de numéros de pièces
  séquentiels robustes basés sur la chronologie des opérations.
- **Équilibre débit/crédit garanti** par construction, y compris :
  - quand un régime ne génère aucune TVA collectée par le vendeur
    (`DEEMED_SUPPLIER`, `B2B_REVERSE_CHARGE`, `EXPORT` — Amazon collecte ou
    exonération à justifier) : le compte client n'est débité que du HT ;
  - quand le solde net d'un groupe est négatif (avoirs de la période
    dépassant les ventes du même régime/pays/taux) : le sens débit/crédit
    est inversé plutôt que d'écrire un montant négatif (invalide en FEC).
- **Plan comptable flexible** : configuration fine des comptes (ex: comptes de
  racines `707` ventilés par pays) via le dictionnaire `ACCOUNTS` centralisé.
- **⚠️ Pré-remplissage, pas une télédéclaration** : ce n'est ni un logiciel de
  comptabilité, ni un export validé automatiquement. Le traitement de
  `DEEMED_SUPPLIER` en particulier suppose un rapprochement avec les relevés
  de règlement Amazon réels (le module ne connaît que le HT calculé par le
  moteur, pas le flux de règlement net effectivement perçu). Faites relire le
  premier export par votre expert-comptable avant tout usage récurrent.

---

## Calendrier fiscal généré automatiquement


Le moteur déduit les échéances déclaratives directement des données traitées :

| Canal | Délai légal | Source légale |
|---|---|---|
| **OSS** | Dernier jour du mois suivant la fin du trimestre | Art. 369 sexdecies & septdecies Dir. 2006/112/CE |
| **CA3 / TVA FR** | 24 du mois suivant (régime normal mensuel) | Art. 287 CGI |
| **Intrastat** | 10e jour ouvré du mois suivant | Art. 7 Règl. UE 2019/2152 |
| **Relevé TVA intracom (ESL/DES)** | 24 du mois suivant (même délai que CA3) | Art. 289 B CGI |

---

## Intrastat / EMEBI (onglet 10)

Depuis 2022, la douane française a scindé l'ancienne « DEB » en deux obligations
**distinctes et indépendantes**, que le moteur traite séparément :

| Obligation | Nature | Seuil | Où dans l'outil |
|---|---|---|---|
| **EMEBI** (Enquête statistique) | Statistique | Seuil annuel (voir ci-dessous), par sens de flux | Onglet **Intrastat (EMEBI)** |
| **État récapitulatif TVA (ESL/DES)** | Fiscale | Dès le 1er euro, pour les livraisons B2B intra-UE exonérées (art. 289 B CGI) | Onglet **Calendrier Fiscal**, généré indépendamment du seuil EMEBI |

L'onglet 10 rappelle explicitement ce renvoi : un flux sous le seuil EMEBI peut
malgré tout déclencher une obligation ESL, les deux étant indépendantes.

L'onglet Intrastat/EMEBI est pré-rempli à partir des mouvements de stock FC détectés :

- **Introductions** (flux UE → FR) et **Expéditions** (flux FR → UE) séparées.
- Agrégation par mois, pays et ASIN.
- Nature de transaction : `11 — Transfert stock (art. 17 Dir. 2006/112/CE)`.
- Valeur statistique estimée = prix de vente HT moyen × quantité (Amazon ne fournit
  pas la valeur d'achat — approximation par excès, art. 83 Dir. 2006/112/CE).
- **Code NC (CN8) et masse nette** : colonnes `À COMPLÉTER` manuellement (non
  disponibles dans les fichiers Amazon).
- **Seuil EMEBI** : géré dynamiquement par année via
  `rates.INTRASTAT_EMEBI_THRESHOLDS_FR` (dict année → seuil) et la fonction
  `rates.intrastat_emebi_threshold_for_year(year)`, qui renvoie aussi un
  indicateur `seuil_confirmé`. Valeur actuellement répertoriée : 460 000 €/an
  (stable depuis 2022, mais non garantie par la loi d'une année sur l'autre).
  Si l'année traitée n'est pas explicitement dans la table, le seuil de la
  dernière année connue est repris par extrapolation et un avertissement
  explicite est affiché à l'utilisateur dans l'onglet Excel — **ce seuil doit
  être revérifié chaque année sur pro.douane.gouv.fr**, la table de ce dépôt
  n'étant mise à jour qu'au fil des évolutions constatées.
- Dépôt : [pro.douane.gouv.fr](https://pro.douane.gouv.fr).

### Conformité Amazon DPP (Data Protection Policy)

Le moteur est conçu pour respecter les exigences strictes d'Amazon concernant la sécurité des données personnelles (PII) :

*   **Sécurité du Transport** : Toutes les connexions à la base de données (Supabase) sont chiffrées de bout en bout via TLS/SSL forcé (`sslmode=require`).
*   **Chiffrement au Repos (At-Rest)** : Les données sensibles (noms et adresses des acheteurs, noms d'entreprises) sont chiffrées au niveau applicatif avant insertion en base via l'algorithme Fernet (**AES-128 en mode CBC avec signature HMAC-SHA256**).
*   **Protection Fail-Safe** : Le système de sécurité interdit tout traitement de données si la clé de chiffrement est absente ou invalide, empêchant toute manipulation de PII en clair par accident.
*   **Protection des Cookies** : Authentification sans jeton dans l'URL. Les sessions sont gérées via des cookies sécurisés pour éviter les fuites de tokens dans l'historique du navigateur ou les en-têtes *Referer*.
*   **Protection Brute-Force** : Limitation automatique du débit (Rate Limiting) sur les tentatives de connexion basées sur l'empreinte IP.
*   **Piste d'Audit & Rétention** : Piste d'audit horodatée pour chaque vérification VIES. Suppression automatique des données personnelles de l'historique après 365 jours (délai de conservation minimal justifié par la fiscalité).
*   **Anonymisation des Logs** : Masquage partiel automatique des numéros de TVA et suppression totale des PII dans les journaux serveurs.

---

## Installation

Python ≥ 3.10 requis.

```bash
pip install -e ".[dev]"
```

Dépendances principales : `streamlit`, `openpyxl`, `pandas`, `polars` (parsing haute performance), `plotly`, `psycopg2-binary`
(base Postgres/Supabase pour l'auth et la facturation), `stripe` (paiements),
`requests` (appels à l'API Resend), `reportlab` (génération PDF).

### Interface Streamlit

```bash
streamlit run app.py
```

### Utilisation en bibliothèque

```python
from decimal import Decimal
from tva_intracom.models import Sale, BuyerType
from tva_intracom.engine import compute_all
from tva_intracom.report import build_report, render_report

ventes = [
  Sale("V1", Decimal("100"), BuyerType.B2C, stock_country="FR", buyer_country="DE"),
  Sale("V2", Decimal("200"), BuyerType.B2B, stock_country="FR",
       buyer_country="DE", buyer_vat_valid=True),
]
resultats = compute_all(ventes)
print(render_report(build_report(resultats)))
```

### Import d'un fichier Amazon

```python
from tva_intracom.parsers.amazon import load_amazon_report

result = load_amazon_report(
  "rapport_amazon.csv",
  seller_country="FR",
  convert_currencies=True,   # conversion BCE automatique
  # progress_callback=lambda done, total: print(f"{done}/{total}"),  # optionnel
)
print(f"Format détecté : {result.detected_format}")
print(f"Ventes : {len(result.sales)}, Remboursements : {len(result.refunds)}")
print(f"Écarts de période : {len(result.period_mismatches)}")
```

### Génération du XML OSS

```python
from tva_intracom.oss_xml import generate_oss_xml

xml_bytes = generate_oss_xml(
  results=resultats_oss,
  seller_vat="FR12345678901",
  period="2026-Q1",
)
with open("oss_declaration_2026-Q1.xml", "wb") as f:
  f.write(xml_bytes)
```

---

## Tests

```bash
pytest -q
```

La suite couvre actuellement : classification des scénarios moteur, taux par
catégorie produit, cache VIES, seuil OSS multi-année, parsing des formats Amazon 1–5,
conversion BCE, isolation thread-safe du pool de connexions DB (`cache_connection=True`,
voir `tests/test_connection_pool_threading.py`), pré-chargement batch des taux BCE
pour l'export OSS (`tests/test_oss_rate_prefetch.py`), parité numérique de la
conversion devise dans l'export Excel (`tests/test_excel_currency_conversion.py`).

---

## Conformité légale — références

| Sujet | Texte de référence |
|---|---|
| Régime OSS (guichet unique) | Dir. 2006/112/CE art. 369 bis à septdecies ; Règl. UE 2021/965 |
| Taux de change OSS | Règl. UE 2020/194, art. 5 bis |
| Exonération B2B intra-UE | Dir. 2006/112/CE art. 138 ; Règl. UE 2018/1912 (Quick Fixes) |
| Reverse charge domestique | Dir. 2006/112/CE art. 194 |
| Acquisitions intracommunautaires assimilées (AIC FBA) | Dir. 2006/112/CE art. 17 |
| Base imposable AIC | Dir. 2006/112/CE art. 83 |
| Intrastat | Règl. UE 2019/2152 |
| Territoires hors UE fiscale | Dir. 2006/112/CE art. 6 |
| HRK → EUR taux fixe | Règl. UE 2022/1540, art. 1 |
| IOSS (import ≤ 150 €) | Dir. 2006/112/CE art. 369 ter et suivants |
| Fait générateur livraison biens | Dir. 2006/112/CE art. 65 |
| Relevé TVA intracom (ESL) | Art. 289 B CGI |

---

## Optimisations de performance & UX (Mises à jour récentes)

### Performance & Réactivité
- **Gestion de l'inactivité & Optimisation RAM (Auto-Sleep)** : Implémentation d'un détecteur d'inactivité côté client (JavaScript). Après 30 minutes sans mouvement de souris ou touche clavier, l'application bascule automatiquement en mode **"Veille"** (`?sleep=1`).
  - **Purge Proactive** : En mode veille, toutes les données lourdes (fichiers uploadés, résultats de calcul) sont supprimées du `st.session_state` et l'exécution est interrompue (`st.stop()`). Cela permet de libérer instantanément la mémoire vive et d'autoriser l'hébergeur à mettre le conteneur en sommeil profond (Idle) si aucun autre utilisateur n'est actif.
  - **Réactivation Transparente** : Un bouton "Réactiver" permet de relancer l'app. Grâce au système de cookies persistant, l'utilisateur retrouve sa session sans avoir à se reconnecter.
- **Optimisation Streamlit (`@st.fragment`)** : Utilisation intensive de fragments dans les onglets "Détail ventes", "Téléchargements" et dans les formulaires SIREN de la **sidebar** pour isoler le rendu et éviter les reruns complets du script lors d'interactions locales.
- **Mise en cache intelligente (TTL & Keys)** :
  - **Sidebar** : Cache TTL (20s) sur les appels coûteux (Amazon credentials, listes SIREN, quotas, abonnements Stripe, grille tarifaire) avec invalidation explicite immédiate après chaque mutation (ajout/suppression SIREN).
  - **Sidebar — détection de période fiscale** : Le tri des dates de transaction et le calcul des bornes (mois/trimestre/semestre/année détecté) sont mis en cache dans `session_state` (`_period_detect_cache`), invalidés uniquement quand le jeu de résultats change réellement (nouvel objet et nouvelle taille). Auparavant recalculés à chaque rerun de la sidebar, y compris pour un simple changement de widget sans rapport — coûteux sur les gros volumes (100k+ lignes).
  - **Sidebar — catalogue produits (ASIN)** : Le parsing du CSV/TSV de catalogue est extrait dans une fonction dédiée décorée `@st.cache_data`, indexée sur le contenu binaire du fichier : un même catalogue n'est plus re-parsé à chaque interaction. Une garde de taille (`_MAX_CATALOG_MB`, 20 Mo) rejette les fichiers surdimensionnés avant lecture, pour éviter un épuisement mémoire (DoS) sur le process Streamlit partagé entre sessions.
  - **Excel — Ajustement colonnes (`_auto_width`)** : Optimisation par échantillonnage (150 premières lignes) pour estimer la largeur des colonnes, au lieu de scanner l'intégralité du fichier — gain de temps radical sur les exports de 10k+ lignes.
  - **Billing** : Réutilisation du cache SIREN/Abonnement déjà peuplé par la sidebar, éliminant les requêtes SQL dupliquées lors de la construction du tunnel de paiement.
  - **Téléchargements** : Mise en cache des 5 exports indépendants (Excel principal, OSS Excel, CA3/HTML local, B2B Excel, FEC) via une clé de téléchargement dédiée (`_dl_cache_key`).
- **Optimisation de la RAM (SaaS High-Load)** :
  - **Filtrage sélectif des colonnes** : Réduction drastique de la consommation RAM en ne conservant que les colonnes strictement nécessaires avant la conversion en dictionnaires Python (`to_dicts()`) — gain de performance majeur.
  - **Internage des chaînes (String Interning)** : Les objets `Sale` et `VatResult` utilisent `sys.intern()` pour les codes pays, devises et catégories fiscales. Cela réduit radicalement l'empreinte mémoire sur les imports de 100k+ lignes en ne stockant qu'une seule instance de chaque chaîne répétitive.
  - **Lazy Concaténation** : Évitement des copies de listes lors de la fusion ventes/remboursements pour les exports.
  - **Nettoyage automatique du cache binaire** : Suppression explicite des anciens fichiers générés du `session_state` dès que les données de calcul changent.
  - **Génération différée (Lazy Artifacts)** : Les fichiers ne sont plus générés systématiquement en RAM, mais uniquement au clic de l'utilisateur sur le bouton de génération.
- **Stabilisation du calcul** : Introduction de `calc_key` dans le `TabContext` (transmis depuis `app.py`) pour garantir la cohérence des résultats entre onglets et éviter les recalculs intempestifs.
- **Efficacité du moteur fiscal** : Optimisation de `engine.py` (résolution de la langue une seule fois par lot dans `_run_oss_loop` au lieu d'une résolution par vente dans `_note()`).
- **Stabilité des identifiants** : Passage à une clé composite `(sale_id, amount_ht)` pour identifier les transactions de façon stable à travers les différents modules d'audit et de reporting, éliminant la fragilité des `id()` Python lors des copies d'objets.
- **Stripe** : La session du portail de facturation (Billing Portal) est désormais créée uniquement au clic, au lieu d'être pré-générée à chaque rerun Streamlit.
- **Filtrage UI (`_render_filter_bar`)** : Optimisation par scan concaténé unique avec gestion robuste des valeurs nulles (évite les lignes vides en recherche).

### Historique des optimisations perf (2026-08)
Une instrumentation temporaire (`tva_intracom/perf_log.py`, décorateur
`@timeit()` / context manager `timed()`) a été ajoutée début août 2026 pour
identifier les points lents via analyse de logs de production réels
(avant/après redéploiement, comparaison statistique), puis retirée une fois
l'investigation terminée — les correctifs de code ci-dessous, eux,
restent :
- **VIES (`vies_engine.py`)** : connexion DB mise en cache par thread
  (`cache_connection=True`, comme auth/billing/ecb_rates) au lieu d'une
  connexion neuve par requête batch — aucun appel DB n'a lieu depuis les
  workers du `ThreadPoolExecutor` (uniquement des requêtes HTTP VIES), donc
  sûr. Gain mesuré en prod : `compute_all_with_vies` divisé par ~4
  (4.4s → 1.1s en moyenne).
- **SIREN (`billing.py`)** : `list_registered_sirens` et
  `get_siren_links_for_identifiers` mis en cache (`@st.cache_data`,
  TTL 60s), invalidés explicitement après toute mutation
  (`register_siren`, `request_siren_removal`, `cancel_siren_removal`,
  `link_account_identifier`) pour ne jamais servir un état SIREN périmé.
- **Taux BCE pour l'export OSS (`oss_export.py`)** : pré-chargement batch
  (`prefetch_rates`) des paires (devise, date de clôture trimestrielle)
  nécessaires à `aggregate_oss_results`, calculées en amont via la fonction
  pure `get_oss_rate_date` — élimine les requêtes DB individuelles
  séquentielles (une par devise rencontrée pour la première fois).
- **Jobs "gros fichier" (`ui/background_calc.py`)** : fermeture explicite
  des connexions DB mises en cache par le thread du job dans son bloc
  `finally`, plutôt que de compter sur le `__del__` implicite de psycopg2
  (comportement fiable en pratique, vérifié empiriquement, mais rendu
  déterministe).
- **Diagnostic ciblé (`ecb_rates.get_rate`)** : lorsqu'un live-fetch ECB est
  déclenché (cache DB manqué), l'appelant exact (fichier:ligne, fonction)
  est loggé — a permis d'identifier `ui/formatting.py:_get_conversion_rate`
  comme seule origine des lookups BCE en direct (un par devise d'affichage
  jamais choisie ce jour-là ; comportement normal et déjà mis en cache par
  session, coût ponctuel non récurrent).
- **Export Excel (`excel_report._write_details_tab`)** : décomposition de
  `export_xlsx` par onglet (`@timeit()` sur chaque `_write_*_tab`) a révélé
  que `_write_details_tab` (~1.7s pour 7172 lignes) et
  `_write_vies_history_tab` (~2.1s, requête déjà indexée et batchée —
  proportionnel au volume réel d'historique, pas une inefficacité) étaient
  les deux principaux postes. Pour `_write_details_tab` : le taux de
  conversion vers `display_currency` était recalculé intégralement
  (`convert_to_currency`->`convert_to_eur`+`get_rate`, avec une string
  `_info` formatée puis jetée) à CHAQUE cellule (3 conversions/ligne), alors
  que la devise et la date de conversion sont fixes pour tout l'onglet — le
  taux est maintenant calculé une seule fois avant la boucle (parité
  numérique vérifiée dans `tests/test_excel_currency_conversion.py`).
- **Connu et accepté (non corrigé)** : changer la devise d'affichage
  (`target_currency`) redéclenche un calcul complet
  (`compute_all_with_vies`, ~2.4s) car `_cache_key` (app.py) inclut cette
  valeur — nécessaire car le texte des notes OSS (seuil cumulé affiché en
  devise locale) est actuellement calculé au moment du calcul et non de
  l'affichage. Découpler proprement demanderait de toucher `engine.py`
  (moteur fiscal) ; jugé trop risqué pour un gain marginal (coût ponctuel
  par changement de devise, pas par rerun) — laissé tel quel.

### Optimisations perf — audit calcul avoirs & réactivité UI (2026-08, suite)
Audit externe ciblé sur trois pistes (latence CPU du calcul, RAM, réactivité
UI) — deux corrigées avec gain réel mesuré, une écartée après vérification
(le diagnostic initial la surestimait) :

- **Double calcul des avoirs (`engine.py` / `app.py` / `cli.py`)** :
  `compute_all_with_vies()` était appelé deux fois par calcul — une fois
  pour les ventes (avec les avoirs passés en paramètre pour le seuil OSS
  net), une seconde fois pour les avoirs seuls, refaisant intégralement le
  tri chronologique, la normalisation TVA et le lookup VIES déjà effectués
  en interne par le premier appel (`_run_oss_loop` calculait déjà le
  `VatResult` de chaque avoir puis le jetait). `_run_oss_loop` et
  `compute_all_with_vies()` retournent désormais `(results,
  refund_results, vies_summary, oss_summary)` en un seul passage — le
  second appel dédié aux avoirs est supprimé côté `app.py` et `cli.py`.
  Deux cumuls OSS distincts sont maintenus en interne pour préserver
  exactement le comportement fiscal existant : le cumul net partagé
  ventes+avoirs (seuil affiché, notes des ventes) et un cumul recalculé
  sur les avoirs seuls (bascule domestique d'un avoir sous le seuil, comme
  avant). Non-régression vérifiée par comparaison bit-à-bit
  ancien/nouveau chemin sur un cas déclenchant la bascule, + suite de
  tests complète (165/169, les 4 échecs restants préexistants et sans
  rapport — VIES DB indisponible en environnement de test, casse
  `"amazon"`/`"Amazon"` dans un parser).
- **Recompression gzip de l'upload à chaque rerun (`app.py`)** : les octets
  uploadés (mis en cache compressés, voir plus haut) étaient recompressés
  (`gzip.compress`) à *chaque* rerun Streamlit tant que les fichiers
  restaient dans le widget — pas seulement au premier upload. Une
  signature `{nom: taille}` est maintenant comparée à celle déjà en cache
  avant de relancer la compression ; skip si identique (même convention de
  déduplication `(name, size)` qu'ailleurs dans ce bloc).
- **Interning des notes (`engine.py::_note`)** : le texte de
  `VatResult.note` (référence légale, taux, pays — souvent identique
  pour des milliers de lignes partageant le même scénario fiscal) est
  construit par f-string, jamais interné automatiquement par CPython
  (contrairement aux littéraux). Un cache `_NOTE_INTERN_CACHE`
  (`dict.setdefault`, sûr sous GIL y compris depuis le thread de calcul
  d'arrière-plan) fait partager le même objet `str` à toutes les
  occurrences d'un texte identique. Vérifié : 5000 résultats identiques →
  1 seul objet `str` en mémoire pour `.note` (contre 5000 avant).
  Distinct de l'internage `sys.intern()` déjà en place sur les codes
  pays/devises/catégories (champs courts) — ici le texte est long et
  variable, d'où un cache dédié plutôt que `sys.intern()`.
- **Triple stockage en session (écarté)** : un audit externe pointait
  `all_sales`/`results`/`refund_results` référencés à la fois dans
  `st.session_state` et dans `TabContext` comme une duplication mémoire
  de "centaines de Mo" sur 100k lignes. Vérifié : ce sont des références
  Python vers les mêmes objets (pas de copie), le coût réel est de
  l'ordre de quelques Mo (conteneurs de pointeurs), et `TabContext.all_sales`
  est un besoin fonctionnel réel (lu par l'onglet Audit, `@st.fragment`
  séparé, sans accès direct à la variable locale de `app.py`). Rien
  changé — le retirer aurait cassé l'onglet Audit pour un gain nul.


- **Persistance de l'upload** : Correction d'un bug où le changement de langue supprimait les fichiers chargés (stabilisation de l'identité du widget `st.file_uploader` via une clé explicite `main_file_uploader` indépendante du label traduit).
- **Certificat VIES** : Ajout d'une option de génération de certificat PDF global directement dans la sidebar, basée sur un snapshot complet du scope.
- **Rendu des onglets** : Correction d'un blocage d'affichage lors du changement de pays d'origine (suppression d'un `st.rerun()` forcé qui interrompait le script avant le rendu).
- **Lisibilité des données** :
  - Colonnes "Note" et "Référence légale" élargies par défaut (`width="large"`) pour éviter la troncature des explications fiscales.
  - **Visualisations** : Amélioration de la légende des cartes (marge droite `r=90`, fond semi-opaque et bordure fixe) pour garantir la lisibilité sur petits écrans et en mode sombre.

---

## Audit conformité TVA (08/2026)

Audit réglementaire ciblé (sources : EUR-Lex, Legifrance, BOFiP, douane.gouv.fr,
puis n-lex.europa.eu + portails nationaux ES/PL/CZ pour le point sur
l'autoliquidation domestique), portant sur `engine.py`, `rates.py`,
`oss_export.py`, `oss_xml.py`, `ca3_report.py`, `local_vat_report.py`,
`fec_export.py`. 5 points trouvés, 4 corrigés, 1 documenté sans correction
(décision explicite) :

- ~~Seuil OSS 10 000 € sans rattachement à l'année N-1~~ **Corrigé** :
  `_run_oss_loop()` réinitialisait le cumul OSS à zéro à chaque nouvelle
  année civile, sans savoir si le seuil avait déjà été dépassé l'année
  précédente — or l'art. 59 quater dir. 2006/112/CE (CGI art. 258 B)
  apprécie le seuil sur l'année en cours **et** l'année précédente : un
  dépassement en N interdit le régime "sous seuil" dès le 1er janvier de
  N+1, quel que soit le nouveau cumul. Nouveau réglage compte
  `oss_threshold_exceeded_prev_year` (sidebar), déclaratif utilisateur
  (l'outil ne voit pas forcément tout le CA multi-canal de N-1) : si coché,
  force `apply_fr_under_threshold=False` pour l'année de traitement en
  cours, indépendamment du cumul recalculé sur les seules données importées.

- ~~Numéro IOSS activant automatiquement IOSS_DIRECT~~ **Corrigé** : dès
  qu'un n° IOSS était renseigné sur le compte, **toute** vente B2C import
  ≤ 150 € basculait en `IOSS_DIRECT`, court-circuitant `DEEMED_SUPPLIER` —
  alors que l'art. 14 bis dir. 2006/112/CE fait de la marketplace (Amazon)
  le redevable présumé pour toute vente facilitée par une interface
  électronique, indépendamment d'un n° IOSS propre au vendeur. Nouveau
  toggle sidebar `ioss_own_number_active` (visible seulement si un n° IOSS
  est saisi, défaut `False`) : le comportement par défaut devient
  `DEEMED_SUPPLIER` (sécurisé), `IOSS_DIRECT` désormais un choix explicite.
  ⚠️ Changement de comportement pour les comptes ayant déjà un n° IOSS
  enregistré — à signaler individuellement après déploiement.

- **`DOMESTIC_REVERSE_CHARGE_COUNTRIES` (ES, IT, PL, CZ, SK, HU, RO, BG, HR,
  LT, LV) — réserve documentée, non corrigée (décision explicite)** : ce
  `Set[str]` applique une autoliquidation domestique généralisée par pays,
  alors que les mécanismes nationaux vérifiés (ES art. 84 Ley IVA, CZ
  §92a–92f zVAT) sont en réalité sectoriels/plafonnés (BTP, déchets, or,
  quotas CO2, électronique ≥ seuil) — jamais généraux à tout B2B domestique,
  cohérent avec le cadre UE (art. 199/199 bis/199 ter dir. 2006/112/CE, qui
  n'autorisent que des dérogations sectorielles encadrées). **PL en
  particulier : le mécanisme cité (art. 17 uVAT, biens) est abrogé depuis le
  01/11/2019**, remplacé par le split payment (qui n'est PAS une
  autoliquidation — le vendeur collecte et déclare la TVA normalement).
  Décision de Matthieu (2026-08-09) : le cabinet comptable valide le
  comportement actuel pour le produit testé ("serrures" vendues en ES, TVA
  ES domestique correcte en pratique) — fondement juridique précis non
  identifié dans les catégories génériques de l'art. 84 relevées ici (à
  confirmer par le cabinet, possible rattachement art. 84.Uno.2°f/ejecución
  de obra, ou catégorisation produit différente). Aucune correction
  appliquée ; refonte en `dict[str, set[str]]` (pays, catégorie) documentée
  en commentaire dans `rates.py`, à reconsidérer si un nouveau produit/pays
  pose problème.

- ~~Ligne 18 CA3 (Monaco) jamais renseignée~~ **Corrigé** : le Cerfa
  3310-CA3-SD officiel comporte une ligne mémo dédiée « Dont TVA sur
  opérations à destination de Monaco » (case 0038), sur le même principe que
  la ligne 17 pour l'AIC — les ventes Monaco étaient bien incluses dans le
  montant principal (A1/Ligne 08, Monaco assimilé FR par la convention
  fiscale franco-monégasque du 18/05/1963) mais ce mémo informatif restait
  vide. Calculé et affiché dans `ca3_report.py`.

- ~~OSS et IOSS mélangés dans le même export/XML~~ **Corrigé (bug le plus
  impactant de cet audit)** : `aggregate_oss_results()` traitait
  `Scenario.OSS_B2C` et `Scenario.IOSS_DIRECT` comme un seul flux — or ce
  sont deux régimes distincts avec des périodicités différentes
  (trimestrielle pour l'OSS, mensuelle pour l'IOSS, art. 369a-k vs.
  art. 369l-x dir. 2006/112/CE) et des numéros d'identification distincts.
  Conséquence concrète : dans l'Excel/CSV URSSAF, les montants IOSS étaient
  mélangés à tort au total trimestriel OSS (`OSS_Résumé` les incluait,
  `OSS_Détail` les excluait — incohérence interne visible entre les deux
  onglets) ; dans le XML officiel (`oss_xml.py`), le filtre `is_eu` sur le
  pays de départ (systématiquement hors UE pour l'IOSS, importé d'un pays
  tiers) les faisait **disparaître silencieusement** — la TVA IOSS collectée
  n'était donc déclarée nulle part via l'export automatisé. `fec_export.py`
  distinguait déjà correctement les deux régimes (comptes 4457180 vs
  4457190) — preuve que la bonne pratique était connue ailleurs dans le
           code, non répliquée ici avant ce correctif.
           Correctif : `aggregate_oss_results()` ne traite plus que `OSS_B2C` ;
           nouvelle `aggregate_ioss_results()` dédiée à `IOSS_DIRECT` (factorisée
           avec l'OSS via `_aggregate_by_scenario()` commune) ; nouvel export IOSS
           séparé (`build_ioss_excel()`/`build_ioss_csv()`, onglets IOSS_Résumé/
           IOSS_Détail, section dédiée dans l'onglet Téléchargements, mensuel).
           ⚠️ **Limite assumée** : cet export IOSS est indicatif (Excel/CSV), **pas
           un XML officiel homologué** — le format XML IOSS (Import Scheme, distinct
           du XML OSS Union Scheme déjà généré par `oss_xml.py`) n'est pas implémenté
           faute de spécification technique disponible au moment de ce correctif.
           Progrès réel (plus de disparition silencieuse de TVA collectée) mais
           implémentation XML IOSS dédiée à prévoir en roadmap si le volume le
           justifie.

Point identifié mais non corrigé, hors périmètre de ce patch (signalé pour
suite) :

- **`b2b_lines` (état récapitulatif B2B / DES intracommunautaire biens) sans
  découpage mensuel** : `build_b2b_excel()`/`build_oss_csv()` agrège tout ce
  qu'on lui donne sans notion de mois, alors que cet état est une
  déclaration **mensuelle** (art. 289 B CGI) — contrairement à l'OSS
  (trimestriel) sur lequel s'aligne le `period_label` utilisé ici. Si le
  workflow traite les données par trimestre (cas courant OSS), l'export
  produit un total agrégé sur 3 mois sans correspondre à aucune déclaration
  réelle telle quelle. Chaque ligne garde `transaction_date` (splittable
  manuellement) mais rien n'assiste ce découpage. Non corrigé dans ce patch.

## Roadmap

- ~~Vente B2B cross-border avec n° TVA invalide mal orientée~~ **Corrigé (bug
  critique)** : pour une vente B2B intra-UE dont le n° de TVA acheteur
  s'avère invalide/introuvable sur VIES (exonération Art. 138 refusée),
  `engine.py::compute_vat()` distingue désormais deux traitements — l'ancien
  moteur les confondait dans les deux sens, à deux reprises :
  1. **D'abord** un exemple corrigé (`correction_engine.xlsx`) : une vente
     B2B expédiée vers un pays ayant adopté l'art. 194 dir. 2006/112/CE
     (autoliquidation domestique — ES, IT, PL, CZ, SK, HU, RO, BG, HR, LT,
     LV) était exonérée à 0 % à tort, l'ancien code appliquant l'art. 194
     au transfrontalier alors qu'il ne s'applique **qu'au national**.
  2. **Ensuite**, le correctif du point 1 a été généralisé par erreur à
     *toutes* les ventes B2B à n° TVA invalide, y compris celles à
     destination d'un pays **non** couvert par l'art. 194 (DE, FR, AT, BE,
     NL, DK…) — qui basculaient alors, elles aussi, en taxation au pays de
     **départ**, cassant des ventes déjà correctement taxées à la
     destination via OSS.

  Le comportement final retenu :
  - Pays de destination **couvert par l'art. 194** : l'exonération à tort
    est corrigée → TVA due au pays de **départ** (Art. 31 Directive
    2006/112/CE), collectée par le vendeur (`Scenario.DOMESTIC`).
  - Pays de destination **non couvert** : aucune exonération à corriger ici
    — la vente est simplement reclassifiée B2C (n° TVA invalide = pas de
    preuve de statut assujetti) et suit le régime normal des ventes à
    distance (Art. 33), taxée au pays de **destination** via **OSS**
    (`Scenario.OSS_B2C`) — l'art. 194 n'a plus sa place dans ce second cas,
    qui ne l'a jamais concerné.

  Par ailleurs, les ventes dont le n° fourni est un identifiant fiscal
  national sans préfixe pays (codice fiscale IT, NIF ES, NIP PL…) —
  jamais interrogées sur VIES car rejetées au format dès le parsing
  (`parsers/amazon/classify.py`) — étaient invisibles dans l'onglet
  **🛡️ VIES** et son export : elles apparaissent désormais dans les
  reclassifications (`Sale.national_tax_id` conserve l'identifiant brut à
  des fins de traçabilité, sans jamais être transmis à VIES). La colonne
  « Explication » de l'onglet VIES distingue maintenant explicitement
  taxation au départ vs à destination (`ViesReclassification.taxed_at_departure`).

- ~~Authentification mono-canal (lien magique uniquement)~~ **Migré** :
  authentification déléguée à Supabase Auth — mot de passe, et OAuth Google/
  Microsoft/GitHub/Amazon (Custom Provider). Voir section « Auth » ci-dessus
  pour le détail (flux PKCE, stockage serveur du verifier dans
  `tva_oauth_pkce`). Lien magique conservé dans le code mais désactivé côté
  écran de connexion. Au passage, le refresh_token Amazon SP-API — stocké en
  clair jusque-là malgré le chiffrement Fernet déjà en place pour d'autres PII
  — est désormais chiffré au repos comme le reste (`auth.py`).
  ⚠️ Le Custom OAuth Provider Amazon suppose une app **Login with Amazon**
  (LWA) distincte de l'app SP-API utilisée par ailleurs pour la récupération
  des rapports de vente (barre latérale) — deux usages différents, deux
  enregistrements différents côté Amazon.

- ~~Vendeur toujours supposé établi en France~~ **Corrigé** : `engine.py`
  comparait plusieurs classifications (domestique vs immatriculation locale)
  à un littéral `"FR"` figé au lieu de `sale.seller_country`. Un nouveau
  réglage de compte **Pays d'origine** (`home_country`, global, persisté en
  base) permet désormais à un cabinet gérant un client établi hors de France
  d'obtenir une classification fiscale correcte et un ordre d'affichage des
  déclarations adapté (déclaration du pays d'origine en premier). Voir la
  section dédiée ci-dessus. Volontairement non généralisés : le cas Monaco
  (convention franco-monégasque, spécifique à la France) et le seuil OSS
  sous 10 000 € (`apply_fr_under_threshold`, hors périmètre OSS demandé).

- ~~Devise de calcul contaminée par le pays d'origine~~ **Corrigé (bug
  critique)** : lors de l'introduction du réglage **Pays d'origine**, la
  devise de **calcul interne** du moteur avait été confondue avec la devise
  d'**affichage**. `parsers/amazon/loader.py` (et à l'identique `mirakl.py`,
  `shopify.py`, `woocommerce.py`, `aliexpress.py`) convertissaient les
  montants dans la devise du pays d'origine (`COUNTRY_CURRENCIES.get(seller_country)`)
  dès l'import, au lieu de toujours calculer en EUR — contaminant `amount_ht`/
  `vat_amount` pour tous les calculs en aval (seuil OSS 10 000 €, cases CA3,
  écart Amazon/moteur). Plus grave : `oss_export.py::aggregate_oss_results()`
  (utilisée aussi bien par l'export Excel/CSV que par **le XML OSS officiel**
  télétransmis à l'administration) reproduisait le même bug, ce qui aurait pu
  faire télétransmettre une déclaration OSS dans une devise autre que l'EUR
  (obligatoire, Règl. UE 2020/194). Les 6 fichiers forcent désormais
  `target_currency = "EUR"` sans exception ; `home_country`/`seller_country`
  ne sert plus qu'au classement des ventes. La conversion vers une devise
  d'affichage locale est cantonnée à la couche présentation — voir section
  « Devise d'affichage locale » ci-dessus.

- ~~Immatriculation locale réclamée pour du stock hors UE~~ **Corrigé** :
  `app.py` (bandeau « Plan d'action Immatriculations ») et
  `ui/billing_gate.py` (verrou de téléchargement) utilisaient
  `all_stock_countries` sans filtre UE, réclamant à tort un numéro de TVA
  local — et bloquant le téléchargement — pour un stock situé hors UE
  (Royaume-Uni, États-Unis, Suisse, Chine…). Restreint à `rates.is_eu()`, et
  l'exclusion du pays « domestique » (auparavant figée sur `"FR"`) généralisée
  à `home_country`.

- ~~Notes légales du moteur uniquement en français~~ **Corrigé (simplifié)** :
  `VatResult.note` était toujours produite en français en dur, y compris
  quand l'interface était affichée dans une autre langue. `engine.py::_note()`
  bascule désormais sur une note générique minimale (scénario, pays, taux —
  sans référence légale) dans les 6 langues non-françaises, les articles de
  loi français (CGI, Bofip…) n'ayant pas d'équivalent pertinent à traduire. Le
  français conserve le texte complet avec ses références légales, inchangé.

- ~~Affichage non dynamique (devise, libellés, seuils)~~ **Corrigé** : plusieurs
  endroits affichaient encore des valeurs figées en EUR/français quel que soit
  le pays d'origine ou la langue choisis — libellés de colonnes `HT (EUR)` /
  `TVA (EUR)` (`ui/tabs/detail_ventes.py`, `ui/tabs/audit.py`), seuil OSS
  « 10 000 € » non converti (`ui/formatting.py::render_oss_threshold_bar` —
  la comparaison elle-même comparait un total non converti à une limite
  convertie), graphiques de l'onglet Visualisations (montants relabellisés
  sans être réellement convertis), montant de TVA locale toujours en EUR dans
  l'onglet Téléchargements, et placeholder `{platform}` jamais substitué dans
  les KPI « Config {platform} conforme ». Tous corrigés en s'appuyant sur
  `ui/formatting.py::_fmt()`/`_get_conversion_rate()`, seul point de
  conversion EUR → devise d'affichage (voir « Devise d'affichage locale »
  ci-dessus). Au passage, l'onglet Excel Intrastat (EMEBI) contenait un bloc
  entièrement dupliqué (avec un `for...else` toujours exécuté par erreur,
  ajoutant une ligne parasite) — supprimé.


produit qu'un rapport HTML (`generate_ca3_html_report_v2`) destiné à une
saisie manuelle sur le portail impots.gouv.fr (mode EFI) ou par un cabinet
comptable. Un export au format **EDI-TVA** (norme utilisée par les
partenaires EDI homologués DGFIP pour la télétransmission directe des CA3)
permettrait une automatisation complète pour les cabinets comptables gérant
de multiples dossiers. Cela suppose : l'obtention du cahier des charges
EDI-TVA auprès de la DGFIP ou d'un partenaire EDI, un partenariat ou une
homologation (la télétransmission directe n'est pas ouverte à un éditeur
non homologué), et la gestion de la signature/authentification du canal
EDI. **Alternative plus légère déjà implémentée** : `fec_export.py` génère
un journal des ventes au format FEC consommable par les logiciels
comptables existants (voir section « Export comptable (FEC) » ci-dessus) —
sans viser la télétransmission directe. L'EDI-TVA proprement dit (dépôt
automatique sur le portail DGFIP) reste non implémenté à ce jour.

- ~~Territoire Monaco (MC) non géré~~ **Corrigé** : une vente expédiée depuis
  un stock français vers Monaco tombait à tort en `EXPORT` (exonérée) faute
  de reconnaissance du code pays "MC" par `is_eu()`/`is_fiscal_eu()`. Un cas
  spécial dans `engine.py` (`compute_vat`) traite désormais ces ventes comme
  des ventes domestiques françaises (convention fiscale franco-monégasque du
  18 mai 1963), avec TVA FR collectée et déclarée en CA3. `ca3_report.py` a
  été mis à jour en conséquence pour inclure ces ventes dans l'agrégation
  (leur `buyer_country` reste "MC", pas "FR").
  - **Stock français → Monaco** : vente domestique française (`Scenario.DOMESTIC`,
    `Channel.FR_DOMESTIC` si le pays d'origine du compte est la France, sinon
    `Channel.LOCAL_REGISTRATION`).
  - **Stock dans un autre État membre → Monaco** (ex. ES → MC) : traitée comme
    une vente **OSS vers la France** (`Scenario.OSS_B2C`, `Channel.OSS`, TVA FR),
    Monaco étant assimilée au territoire français pour la TVA.
  - ⚠️ **Point à trancher avec un fiscaliste** : la convention franco-monégasque
    du 18 mai 1963 est bilatérale (France ↔ Monaco). Son application à une
    vente expédiée depuis un stock situé dans un *autre* État membre (ni la
    France, ni Monaco) n'a pas de fondement juridique établi dans ce document
    — le comportement actuel du moteur est volontairement large (toute vente
    vers MC, quel que soit le pays de stock, est traitée en France) mais n'a
    pas été validé contre un texte ou une doctrine fiscale couvrant ce cas de
    figure précis. À confirmer/ajuster avant de s'appuyer dessus pour une
    déclaration réelle sur ce cas particulier.

- ~~Références de lignes Cerfa CA3 incorrectes~~ **Corrigé** : les libellés de
  ligne utilisés dans `ca3_report.py` (`compute_ca3_lines_v2`,
  `generate_ca3_html_report_v2`) ne correspondaient pas à la numérotation
  réelle du Cerfa 3310-CA3-SD officiel — corrigés après vérification contre
  le formulaire PDF officiel (cadres A et B) :
  - Ventes domestiques FR : Case **A1** (0979), pas "Ligne 01"
  - Livraisons intracom B2B exonérées : Case **F2** (0034), pas "Ligne 02"
  - Exportations hors UE : Case **E1** (0032), pas "Ligne 14"
  - AIC — base : Case **B2** (0031) — absente auparavant, ajoutée
  - AIC — mémo TVA : **Ligne 17** (0035) — absent auparavant, ajouté
  - Taux normal 20 % : **Ligne 08** (0207), pas "Ligne 20"
  - Taux réduit 5,5 % : **Ligne 09** (0105), pas "Ligne 22"
  - Taux intermédiaire 10 % : **Ligne 9B** (0151), pas "Ligne 25"
  - Taux particulier 2,1 % métropole : **Ligne T6** (1010), pas "Ligne 24"
  - Déduction immobilisations : **Ligne 19** (0703), pas "Ligne 20"
  - Déduction autres biens/services : **Ligne 20** (0702), pas "Ligne 21"
  - Crédit période précédente : **Ligne 22** (8001), pas "Ligne 27" (qui est
    en réalité la sortie "crédit à reporter" vers la période suivante, pas
    l'entrée du crédit précédent)
    ⚠️ Le module suppose un vendeur établi en France MÉTROPOLITAINE — le cas
    DOM (taux 8,5 %/2,1 %, lignes 10/11) n'est pas géré.

- ~~Robustesse des connexions VIES~~ **Corrigé** : `vies_engine.py` utilise un pool à connexion fraîche par appel (nécessaire pour le `ThreadPoolExecutor` parallèle) avec résolution intelligente de la portée et retry exponentiel.

- ~~Correction automatique des soldes OSS négatifs dans le XML~~ **Implémenté
  (version assistée)** : `oss_export.suggest_negative_bucket_corrections()`
  tente de rattacher chaque avoir responsable d'un solde négatif à sa vente
  d'origine, mais UNIQUEMENT via un `sale_id` identique (même commande)
  retrouvé dans le fichier importé — jamais par déduction sur `order_date`,
  jugé non fiable pour une correction fiscale automatisée (voir
  `models.py`). Si TOUS les avoirs d'un couple pays/taux négatif sont ainsi
  rattachés, `generate_oss_xml(confirm_corrections=True)` génère
  automatiquement le bloc `CorrectionsOfVatReturns` référençant la période
  d'origine et exclut ces avoirs du corps principal ; sinon, le blocage
  manuel historique reste actif pour la part non rattachée. L'UI
  (`app.py`) affiche le détail (rattaché / non rattaché) et ne propose la
  case de confirmation que si le rattachement est total. ⚠️ La structure
  XML du bloc `CorrectionsOfVatReturns` généré est une approximation non
  vérifiée contre le schéma XSD officiel — à valider avant tout dépôt réel.

- ~~Fuites de ressources temporaires sur Windows~~ **Corrigé** : Correction des handlers de fichiers temporaires (`tempfile`) qui n'étaient pas correctement supprimés du disque sur les serveurs Windows, risquant de saturer le stockage à long terme.

- **Gestion des connexions Postgres centralisée** : la classe de pooling
  compatible scale-to-zero (`_NonPoolingConnectionPool`, une connexion mise en
  cache par thread via `threading.local`, jamais un vrai pool persistant — un
  pool classique garderait une connexion TCP ouverte en permanence et
  empêcherait Railway de détecter l'inactivité et de mettre l'app en veille)
  était dupliquée à l'identique dans `auth.py`, `billing.py` et `ecb_rates.py`,
  avec une variante sans cache dans `vies_engine.py` (connexion neuve par
  appel, nécessaire pour son `ThreadPoolExecutor` à 25 workers). Centralisée
  dans un nouveau module `database.py`
  (`NonPoolingConnectionPool(cache_connection=True/False)` + `run_with_retry()`
  commun), consommé par les quatre fichiers. Au passage, un bug latent a été
  corrigé : en cas de connexion perdue (`InterfaceError`/`OperationalError`),
  `pool.putconn(conn, close=True)` ignorait silencieusement le paramètre
  `close` (l'ancienne classe ne fermait jamais réellement la connexion cassée,
  seul l'objet pool global était jeté) — la connexion cassée est désormais
  fermée explicitement avant retry.

- ~~Deadlock au chargement de la barre latérale (billing.py)~~ **Corrigé (bug
  critique introduit par la centralisation ci-dessus, puis corrigé dans la
  foulée)** : lors du refactor de la connexion DB, l'ordre d'initialisation de
  `billing.py::_get_pool()` a été involontairement inversé — `_pool` était
  assigné **après** l'appel à `_init_schema()` au lieu d'avant. Or
  `_init_schema()` appelle en interne `_run()`, qui rappelle `_get_pool()` :
  avec `_pool` encore `None` à ce stade, cet appel récursif retentait
  d'acquérir `_pool_lock` (nouvellement ajouté, `threading.Lock` non
  réentrant) déjà tenu par le même thread → blocage définitif de l'app juste
  après l'écran de connexion (rendu du `st.file_uploader()` jamais atteint),
  sur un thread Streamlit non principal (d'où l'inefficacité de `Ctrl+C`,
  qui n'interrompt que le thread principal — un `terminate` du process était
  nécessaire). `auth.py`/`ecb_rates.py`/`vies_engine.py` n'étaient pas
  affectés : leur `_init_schema(pool)` reçoit le pool directement en
  argument et n'appelle jamais `_get_pool()`. Reproduit et confirmé corrigé
  par un test de régression dédié (mock `psycopg2` + timeout dur).

- ~~Décalage entre le récapitulatif Excel et l'onglet OSS détaillé~~
  **Corrigé** : pour les ventes OSS facturées dans une devise autre que
  l'EUR (Suède/SEK, Pologne/PLN…), `excel_report.py::_write_recap` (onglet
  Récapitulatif) sommait `summary.oss_by_country`/`summary.oss_ht_by_country`
  — figés au **taux BCE du jour de la vente** — tandis que
  `_write_oss_tab` (onglet OSS détaillé) et le tableau de bord recalculaient
  au **taux BCE de clôture de période** via `oss_export.aggregate_oss_results()`
  (Règl. UE 2020/194, art. 5 bis), seule méthode légalement correcte pour une
  déclaration OSS. Les deux onglets pouvaient donc afficher des totaux TVA
  OSS différents (écart constaté : 1,29 € sur un fichier de test contenant
  des ventes SEK), alors que le total TVA global restait identique (les
  autres postes de la déclaration n'étant pas concernés). Nouvelle fonction
  partagée `_oss_period_totals()`, utilisée par les deux onglets — le
  récapitulatif reprend désormais le total déjà correct de l'onglet OSS
  détaillé. Un repli vers l'ancien comportement (taux du jour de vente)
  subsiste pour les appels ne fournissant pas `results`/`refund_results`
  (usage bibliothèque hors `app.py`).

---

## Audit systématique « bugs silencieux » (08/2026)

Suite à la découverte répétée d'écarts non signalés, revue fonction par
fonction des modules cœur (`engine.py`, `rates.py`, `ca3_report.py`,
`oss_export.py`, `excel_report.py`, `vies_engine.py`, parsers marketplace)
à la recherche spécifique de tout `except`/fallback qui masque un montant
faux sans log ni trace utilisateur. 12 points trouvés, 10 corrigés :

- ~~Statut VIES reconstitué "à la date de vente" retombant sur "maintenant"
  si date illisible~~ **Corrigé** : `vies_engine.py::_parse_flexible_date()`
  retournait `_now_utc()` en cas d'échec de parsing. Utilisée par
  `get_vies_status_as_of()` pour reconstituer le statut VIES connu à la
  date d'une vente (justificatif d'exonération B2B en cas de contrôle
  fiscal, Art. 262 ter I CGI), une date de vente malformée faisait
  silencieusement remonter le statut *actuel* au lieu de celui *à la
  date des faits* — pouvant justifier à tort une exonération. Retourne
  désormais `None` (+ log warning), propagé proprement par l'appelant.

- ~~Base AIC (transferts FC) dégradée sans trace~~ **Corrigé** :
  `ca3_report.py::_compute_aic_from_fc_transfers()` forçait silencieusement
  `qty=1` si la colonne QTY d'un transfert FC était illisible, et
  `prix_moyen=0` si l'ASIN transféré n'apparaissait dans aucune vente
  connue — dans les deux cas sans log ni compteur, base AIC (ligne 08
  CA3, art. 83 CGI) potentiellement fausse de façon invisible. Compteurs
  + logs détaillés ajoutés, avec warning de synthèse en fin de calcul.
    Même correctif appliqué à `excel_report.py::_parse_fc_transfer()`
    (onglet Intrastat/EMEBI, qui dupliquait le même bug QTY).

- ~~Montants marketplace illisibles silencieusement à 0~~ **Corrigé** :
  `_safe_decimal()`/`safe_decimal()` (Mirakl, AliExpress, WooCommerce,
  Shopify, Amazon) retournait `Decimal("0")` sans log pour tout montant
  HT non parsable (symbole monétaire non nettoyé, séparateur inattendu…).
  Ces lignes étaient déjà exclues en aval (`skipped_rows`) donc pas de
  vente fantôme à 0 €, mais le compteur mélangeait sans distinction un
  montant *légitimement* nul (avoir, échantillon) et un montant *non
  parsable* (fichier source à corriger). Log ajouté sur le cas non
  parsable dans les 5 fichiers pour distinguer les deux causes.

- ~~Avoirs calculés sans le contexte de la vente d'origine~~ **Corrigé
  (bug structurel)** : dans `app.py`, le second appel à
  `compute_all_with_vies()` dédié aux avoirs omettait `asin_to_category`
  et `apply_fr_under_threshold`, pourtant transmis au premier appel
  (ventes). Un vendeur sous le seuil OSS (10 000 €, régime domestique
  FR) ou avec des produits à taux réduit voyait ses avoirs recalculés à
  un taux et/ou un pays de TVA différents de la vente qu'ils étaient
  censés annuler — écart silencieux entre CA3 et OSS, visible seulement
  en réconciliant les deux déclarations. `cli.py` n'était pas concerné
  (n'expose pas `apply_fr_under_threshold`, ne passe pas
  `asin_to_category` aux deux appels par cohérence).

- ~~Indexation par `sale_id` seul dans le calcul de TVA évitée~~ **Corrigé** :
  `engine.py::compute_all_with_vies()` reconstruisait
  `result_by_sale_id` par simple `sale_id`, alors que le reste de la
  fonction utilise explicitement des clés composites (`_sale_key()`,
  `sale_vat_index`) car un `sale_id` seul n'est pas unique (commande
  multi-articles, avoir partageant l'identifiant de sa vente). Une
  collision écrasait silencieusement un résultat, pouvant attribuer un
  montant de TVA évitée erroné dans l'onglet reclassifications VIES.
  Remplacé par une indexation `_sale_key()` (sale_id + montant HT
  signé), cohérente avec le reste de la fonction.

- ~~Distinction VIES/NIF perdue dans les reclassifications~~ **Corrigé
  (régression détectée en test utilisateur)** : la reconstruction de
  `ViesReclassification` dans `engine.py` (post-traitement du montant de
  TVA évitée, voir point précédent) omettait le champ
  `is_national_tax_id` — présent dans le code d'origine avant cet audit,
  mais rendu plus visible par la correction de l'indexation ci-dessus
  (davantage de lignes correctement matchées = davantage de pertes du
  flag). Un identifiant fiscal national (NIF/codice fiscale, jamais
  interrogé sur VIES par construction) retombait donc silencieusement en
  "N° TVA rejeté par VIES" dans le tableau — deux catégories bien
  distinctes fiscalement, mélangées à l'affichage. Champ restauré dans
  la reconstruction.

- ~~Onglet Audit Ecarts Amazon sans les avoirs~~ **Corrigé (évolution
  demandée suite à l'audit)** : `excel_report.py::_write_audit_tab()`
  ne recevait que `results` (ventes), jamais `refund_results` — un écart
  Amazon/moteur portant sur un avoir n'apparaissait jamais dans la
  réconciliation, contrairement à l'onglet Historique VIES qui fusionne
  bien les deux. `refund_results` désormais transmis et fusionné ; une
  colonne **Type** (Vente/Remboursement) a été ajoutée aux deux sections
  (agrégée et détail) pour ne pas mélanger les deux dans l'affichage,
  y compris dans la clé d'agrégation des sous-totaux.

- ~~Overrides manuels VIES perdus en silence si la base est indisponible~~
  **Corrigé (double avalage d'exception)** : `vies_engine.py::get_manual_overrides()`
  retournait `{}` sur toute exception DB (`except Exception: return {}`),
  sans log. Or `engine.py` (appelant unique) a un `try/except` englobant
  spécifiquement écrit pour logger un warning explicite dans ce cas
  précis — qui ne se déclenchait donc **jamais**, l'exception ne
  remontant jamais jusqu'à lui. Une panne DB passagère faisait ainsi
  disparaître silencieusement toutes les classifications manuelles VIES
  du compte pour le calcul en cours, sans trace nulle part. La fonction
  logue désormais explicitement avant de relancer l'exception.

- ~~Taux de change contaminé par une date de transaction illisible~~
  **Corrigé (bug dupliqué 7×)** : `parsers/amazon/classify.py::convert_currency()`
  retombait silencieusement sur la date du jour de génération du rapport
  pour choisir le taux BCE, si la date de transaction ne parsait pas —
  écart de change potentiellement significatif sur des ventes en devise
  étrangère, invisible. Même pattern strictement dupliqué dans
  `mirakl.py` (×2, CSV et XLSX), `aliexpress.py`, `woocommerce.py`,
  `shopify.py`. Log/warning ajouté aux 7 occurrences (`result.warnings`
  pour les 4 parsers marketplace génériques, `logger.warning` pour
  Amazon). `parsers/amazon/constants.py::safe_decimal()` aligné par la
  même occasion avec le log déjà ajouté aux 4 autres parsers (montant
  non parsable).

Points de vigilance identifiés mais **non corrigés** (données insuffisantes
pour trancher sans risquer un mauvais calcul de substitution) :

- **Remboursements partiels WooCommerce/AliExpress** : `_CANCELLED_STATUSES`
  traite tout statut "refunded"/"remboursé" comme une exclusion totale de
  la ligne (ni vente ni avoir comptabilisé) — correct pour un
  remboursement intégral, silencieusement faux pour un remboursement
  partiel (TVA due sur la part non remboursée disparaîtrait du calcul).
  Aucune colonne de montant remboursé reconnue dans `_AMOUNT_COLS` pour
  ces deux formats. Commentaire de vigilance laissé dans le code aux deux
  endroits ; à trancher si des exports avec remboursements partiels sont
  un jour rencontrés.
- **Mirakl sans détection de remboursement** : `mirakl.py` ne lit aucune
  colonne de statut (contrairement à WooCommerce/AliExpress/Shopify). Si
  un export Mirakl représente un avoir via un statut plutôt que via un
  montant déjà négatif, la ligne serait actuellement traitée comme une
  vente normale (double comptage de TVA). Non traité faute de cas réel
  disponible au moment de l'audit — commentaire de vigilance laissé dans
  le code.

---

## Audit performance (08/2026) — pool DB, allocations, imports, clé de comparaison

Revue de 6 pistes d'optimisation proposées en audit externe sur le moteur et
les modules DB/export. Chaque point vérifié sur le code réel avant tout
correctif, avec suite de tests complète (`pytest`) après chaque patch :
baseline stable à 165 passed / 4 failed (échecs préexistants, liés à
`SUPABASE_DB_URL` absente en environnement de test — sans rapport avec ces
patchs). 4 points corrigés, 2 écartés après vérification :

- **Pool DB dupliqué 4× (corrigé)** : `auth.py`, `billing.py`,
  `vies_engine.py` et `ecb_rates.py` maintenaient chacun leur propre
  instance de pool (même DSN partout) — un run touchant les 4 modules
  ouvrait donc 4 connexions Postgres/Supabase distinctes par thread au lieu
  d'une seule réutilisée. `database.py` expose désormais
  `get_shared_pool()` / `close_idle_connections()` / `reset_shared_pool()` /
  `has_shared_pool()` : un seul pool partagé entre les 4 modules, mode
  `cache_connection=True` et fermeture en fin de run inchangés (aucun impact
  sur le scale-to-zero Railway). Chaque module garde son propre flag
  `_schema_ready` pour que ses tables restent initialisées indépendamment.
  Le pattern anti-deadlock de `billing.py` (assigner l'état avant d'appeler
  `_init_schema()`, à cause du rappel récursif `_run → _get_pool`) a été
  reproduit à l'identique sur le nouveau flag. `tests/test_connection_pool_threading.py`
  (3 tests) toujours au vert.

- **Cache LRU sur le déchiffrement VIES (écarté après vérification)** :
  l'idée — mettre en cache `decrypt_data()` pour l'export en masse de
  l'historique VIES (noms/adresses souvent répétés pour un même n° de TVA)
  — repose sur une prémisse fausse en production. `Fernet` (comme toute
  construction AEAD sérieuse) inclut un IV aléatoire à chaque chiffrement :
  vérifié empiriquement, `Fernet.encrypt(b"X") != Fernet.encrypt(b"X")` sur
  deux appels successifs. Chaque ligne d'historique étant chiffrée
  indépendamment à l'insertion, deux lignes portant le même nom en clair
  ont des ciphertexts différents en base — un cache keyé sur le ciphertext
  ne peut donc jamais matcher entre deux lignes distinctes, même
  identiques en clair. Confirmé par micro-benchmark : 2000 déchiffrements
  de 50 valeurs répétées → 0 hit, 100 % de cache-miss. Aucun patch livré ;
  le coût CPU réel des déchiffrements en masse n'est pas réductible sans
  changer d'architecture de chiffrement (déterministe type AES-SIV),
  option écartée pour ne pas affaiblir les garanties de sécurité sur du
  PII sous DPA Amazon.

- **`itertools.chain` au lieu de `list(...) + list(...)` (corrigé)** :
  - `engine.py` (tri chronologique ventes+avoirs) : `sorted()` construit de
    toute façon sa propre liste de sortie ; les deux `list()` + la
    concaténation intermédiaires étaient inutiles — remplacés par
    `sorted(chain(sales, refunds or []), key=...)`.
  - `excel_report.py::_write_vies_history_tab` : un seul passage sur les
    données, `chain(...)` s'y substitue directement à `results + (refund_results or [])`.
  - `excel_report.py` (hash totals de contrôle d'intégrité, section audit) :
    ce site repassait sur la liste concaténée 5 fois (`len()` + 3×`sum()` +
    1 boucle `for`) — un `chain` s'épuise après un seul passage, donc
    insuffisant tel quel. Refactoré en **une seule boucle** calculant
    `count`/`abs_ht`/`vat`/`net_ht_check`/`id_hash` en un passage unique sur
    `chain(results, refund_results or [])` : plus économe que la
    proposition initiale (1 itération au lieu de 5, aucune liste
    intermédiaire).

- **Imports inline dans les boucles chaudes (corrigé, gain marginal
  confirmé)** : `models.py::__post_init__` (`Sale`, `VatResult`) faisait
  `import sys` à chaque instanciation — déplacé en top-level (stdlib pure,
  aucun risque). `engine.py::_note()` faisait `from .i18n import _ as _i18n`
  à chaque appel en langue non-fr — **pas d'import top-level** ici
  volontairement : `i18n.py` importe `streamlit` en top-level, et
  `engine.py` doit rester chargeable sans dépendance dure à `streamlit`
  (règle déjà en place, voir isolation documentée dans
  `vercel_webhook/api/stripe_webhook.py` et le pattern déjà utilisé dans
  `engine._resolve_lang()`). Remplacé par un cache paresseux
  (`_get_i18n_translate()`) : import réel une seule fois au premier appel
  non-fr, puis simple lookup de variable ensuite.

- **Clé de comparaison `_sale_key()` en Decimal natif (corrigé, 3 sites
  cachés trouvés et synchronisés)** : `(sale.sale_id, str(sale.amount_ht))`
  remplacé par `(sale.sale_id, sale.amount_ht)` — `Decimal` est hashable et
  son égalité/hash sont stables pour deux valeurs numériquement égales
  écrites différemment (`Decimal("10.00") == Decimal("10.0")`, même hash),
  ce qui est même plus robuste qu'une comparaison de string en plus d'être
  plus rapide. Avant de patcher, recherche explicite de tout site
  reconstruisant cette clé à la main plutôt que d'appeler `_sale_key()` —
  **3 trouvés** (`excel_report.py::_write_audit_tab`,
  `ui/tabs/audit.py` — onglet audit UI —, `app.py` — calcul des KPI
  d'écarts), tous comparaient encore `str(amount_ht)` contre les clés
  produites par `_sale_key()`. Sans cette vérification préalable, la
  détection "vente affectée par une reclassification VIES" (nature
  d'écart dans l'audit, export Excel, KPI de l'app) se serait cassée
  silencieusement — plus aucun match, sans erreur ni warning. Les 3 sites
  synchronisés ; annotation `vies_affected_sale_ids: set[int]`
  (déjà incorrecte avant ce patch — le vrai type est un set de tuples)
  corrigée en `set[tuple[str, Decimal]]` au passage.

- **`Sale` en `dataclass` standard au lieu de Pydantic (écarté après
  benchmark)** : gain mesuré réel mais nettement inférieur à l'estimation
  initiale (×5-10 annoncés) — **×1.8** sur 100 000 instanciations
  (1420 ms → 785 ms), avec le pattern d'appel exact du parser Amazon
  (valeurs déjà typées en amont, aucune coercion réelle en jeu). Sur un
  fichier réel de quelques milliers de lignes, l'écart tombe à quelques
  dizaines de ms — négligeable face au reste du pipeline (VIES, I/O,
  génération Excel). Risque identifié en creusant : `buyer_type` n'a
  **aucune validation manuelle** dans `__post_init__` (contrairement à
  `amount_ht`/`original_amount`/etc., qui repassent par `_to_decimal()`) —
  c'est Pydantic seul qui garantit que ce champ reste un `BuyerType` valide.
  Vérifié : `SalePydantic(buyer_type="TYPO", ...)` lève `ValidationError`,
  l'équivalent en `dataclass` standard crée l'objet silencieusement avec
  une simple string. `buyer_type` pilotant directement la branche B2B/B2C
  du moteur de classification fiscale, une valeur corrompue passerait sans
  aucune exception nulle part dans le pipeline. Rapport gain/risque
  jugé défavorable — non corrigé.

---

## Audit externe — reclassifications VIES, avoirs, agrégation V5 (2026-08-11)

Revue de 6 pistes d'amélioration proposées en audit externe sur `engine.py`
et `parsers/amazon/aggregate.py`. 3 points corrigés (2 bugs, 1 optimisation),
2 écartés après vérification (faux positif / gain marginal), 1 gardé en
roadmap (refactor plus large). Suite de tests complète après chaque patch :
baseline stable à 165 passed / 4 failed (échecs préexistants, liés à
`SUPABASE_DB_URL` absente en environnement de test) → **166 passed / 4
failed** après patch (nouveau test ajouté, aucune régression) :

- ~~`vat_avoided` toujours à 0.00 dans l'onglet VIES (régression)~~
  **Corrigé** : `engine.py::compute_all_with_vies()`, post-traitement des
  reclassifications (ligne ~1130), utilisait `result_by_key.get((reclass.sale_id,
  str(reclass.amount_ht)))` — or `result_by_key` est indexé par `_sale_key()`,
  qui retourne `(sale_id, Decimal)` et non `(sale_id, str)` (voir le
  correctif de l'audit performance précédent, section ci-dessus, qui avait
  justement introduit `_sale_key()` en Decimal natif à cet endroit). Le
  `str()` réintroduit ici faisait échouer le `.get()` à tous les coups
  (`(id, Decimal("10.00")) != (id, "10.00")`), donc `vat_avoided` restait
  systématiquement à `Decimal("0.00")` pour toutes les reclassifications
  affichées dans l'onglet VIES — régression silencieuse sur un correctif
  déjà livré. Le `str()` est retiré, commentaire d'avertissement ajouté à
  l'appel pour éviter une réintroduction future.

- ~~Avoirs ignorant leur propre résultat VIES~~ **Corrigé (bug structurel)** :
  `engine.py::_effective_sale_with_vies()` retournait l'avoir tel quel dès
  le début (`if _sale_key(sale) in refund_keys: return sale`), sans jamais
  consulter le résultat VIES — alors que le numéro de TVA de l'avoir **est**
  bien vérifié en amont (la boucle de collecte itère sur
  `chain(sales, refunds)`). Un avoir dont le n° de TVA se révélait invalide
  restait donc taxé en `B2B_REVERSE_CHARGE` au lieu d'être reclassé
  `OSS_B2C` comme la vente qu'il annule, créant un décalage silencieux entre
  la déclaration OSS et le CA3. Les avoirs passent désormais par le même
  arbitrage VIES que les ventes, **sans** dupliquer d'entrée dans
  `vies_summary.reclassifications` / `vies_affected_sale_ids` (déjà
  renseignées via la vente d'origine), pour ne pas fausser les compteurs
  affichés dans l'onglet VIES. Nouveau test dédié :
  `tests/test_vies.py::test_compute_all_with_vies_refund_reclassified_like_sale`
  (verrouille le comportement avoir + n° invalide → `OSS_B2C`, une seule
  entrée dans le tableau, `vat_avoided` correctement calculé).

- ~~Double itération + 9 sommes séparées en agrégation V5~~ **Corrigé
  (optimisation, sans changement de comportement)** :
  `parsers/amazon/aggregate.py::preaggregate_v5()` parcourait deux fois
  `raw_rows` (pré-calcul des commandes multi-ASIN, puis regroupement par
  clé) — fusionné en un seul passage, les deux calculs ne dépendant que de
  la ligne courante. `_aggregate_group()` faisait 9 `sum()` séparés (un par
  colonne de montant, donc 9 itérations complètes du même groupe) —
  remplacé par une seule boucle accumulant les 9 totaux simultanément. Gain
  sensible attendu sur les gros fichiers V5 (une ligne par juridiction
  fiscale, donc très verbeux). Validé par test différentiel (ancienne vs
  nouvelle version, 500 lignes synthétiques multi-juridictions/multi-ASIN) :
  sortie strictement identique ligne à ligne.

- **`asin_to_category` par défaut `None` mais typé `dict` (écarté, faux
  positif)** : la fonction fait déjà `if asin_to_category is None:
  asin_to_category = {}` en tout début de corps — aucun risque réel, le
  typage sert uniquement de documentation pour l'appelant.

- **`Sale` en `dataclass` Pydantic — cache LRU / micro-optimisations
  périphériques proposées (écarté, gain marginal)** : cf. la remarque
  équivalente déjà tranchée dans l'audit performance précédent (section
  ci-dessus) ; pas de nouvelle piste creusée ici qui change ce constat.

Point identifié mais **non corrigé dans ce patch** (refactor plus large,
gardé en roadmap) :

- **Reconstruction complète de `result_by_key` en post-traitement** :
  `engine.py` reconstruit un dictionnaire de 100k tuples après le calcul
  principal, uniquement pour faire correspondre les ventes à leurs
  reclassifications VIES (quelques millisecondes de boucle). Passer la
  référence de l'objet `ViesReclassification` directement dans
  `compute_vat()`/`_run_oss_loop()` dès le premier passage éviterait cette
  reconstruction, mais touche davantage de code partagé — reporté à une
  session dédiée plutôt que combiné à ce patch ciblé.

---

## Internationalisation des noms de pays (2026-08-11)

Mise en place d'une localisation complète pour les noms de pays, éliminant les
derniers résidus de texte français hardcodé dans le moteur et les exports.

- **Centralisation i18n** : Ajout de la fonction `country_label(code)` dans
  `tva_intracom/i18n/i18n.py` (exportée via le package). Elle remplace l'ancien
  dictionnaire `COUNTRY_NAMES` (hardcodé en français dans `rates.py`) et les
  multiples fonctions `_country_label` locales qui étaient dupliquées dans les
  modules UI.
- **Localisation complète des exports** :
  - `excel_report.py` : Tous les onglets (Récapitulatif, Détail, Audit, AIC,
    Intrastat) utilisent désormais les noms de pays traduits selon la langue
    choisie par l'utilisateur.
  - `oss_xml.py` : Les messages d'erreur de solde négatif incluent désormais
    les noms localisés des pays de départ et de destination.
  - `report.py` : Le rendu texte (utilisé pour les logs et la CLI) affiche
    désormais les libellés traduits.
  - `fec_export.py` : Les libellés d'écritures incluent le nom complet
    localisé au lieu du code ISO brut, facilitant le travail des cabinets
    comptables étrangers.
- **Interface Utilisateur** :
  - Mise à jour de `ui/formatting.py` pour déléguer systématiquement la
    traduction à la fonction centrale.
  - Synchronisation de tous les sélecteurs (sidebar, téléchargements) et de
    la génération de certificats PDF VIES pour un affichage multilingue
    cohérent.
- **Données de traduction** : Les 7 fichiers TOML (`fr`, `en`, `de`, `es`,
  `it`, `pl`, `pt`) ont été complétés pour couvrir l'intégralité des États
  membres de l'UE et les pays tiers fréquents (63 clés `country_XX` par
  fichier).

---

## Audit externe — fuite connexion DB, collision upload, XML OSS invalide silencieux (2026-08-13)

Revue de 4 pistes d'amélioration proposées en audit externe (performance,
RAM, bugs, IOSS) sur `engine.py`, `formatting.py`, `mem_utils.py`,
`database.py`, `app.py`, `telechargements.py`. 3 bugs confirmés et corrigés,
1 déjà résolu par des correctifs antérieurs (faux positif), 2 pistes de
performance/architecture identifiées mais reportées (gain incertain ou
risque de régression défavorable). Suite de tests complète après chaque
patch : baseline stable **166 passed / 4 failed** (échecs préexistants liés
à `SUPABASE_DB_URL` absente en environnement de test), aucune régression.

- ~~Fuite de connexion DB dans `run_with_retry`~~ **Corrigé (bug critique)** :
  `database.py::run_with_retry()` ne rendait la connexion au pool
  (`pool.putconn(conn)`) que sur le chemin de succès ou sur
  `InterfaceError`/`OperationalError`. Toute autre exception levée par `fn`
  (ex : erreur métier dans un appelant avec `cache_connection=False`, cas de
  `vies_engine.py`) sortait de la fonction sans jamais restituer la
  connexion — fuite pouvant saturer le quota de connexions Supabase lors de
  pics d'erreurs. La restitution est déplacée dans un bloc `finally`,
  garantissant `putconn()` dans tous les cas (succès, retry, ou exception
  quelconque), avec `close=True` réservé aux seules erreurs de connectivité.

- ~~Collision de fichiers homonymes dans le cache d'upload~~ **Corrigé
  (bug)** : `app.py`, le cache `_last_uploaded_files_bytes` (octets
  compressés gzip conservés pour survivre à un rerun interne, voir
  `rerun_utils.py`) était indexé uniquement par `f.name`. Deux fichiers
  différents partageant le même nom (ex : deux exports `rapport.csv` de
  tailles différentes uploadés ensemble, `accept_multiple_files=True`)
  s'écrasaient silencieusement dans ce cache — l'un des deux disparaissait
  ou était remplacé par le contenu de l'autre au premier rerun interne
  (changement de langue notamment). Clé de cache changée pour `(name,
  size)`, cohérente avec la déduplication déjà appliquée plus loin dans le
  même fichier.

- ~~Masquage silencieux d'un solde OSS négatif non résolu dans le XML~~
  **Corrigé (risque fiscal)** : `telechargements.py::_build_oss_xml()`
  capturait la `ValueError` levée par `generate_oss_xml()` en cas de solde
  négatif bloquant (avoir non rattaché à sa vente d'origine) et relançait
  immédiatement l'appel avec `ignore_negatives=True`. Ce paramètre ne
  filtre **pas** les montants négatifs restants — il désactive uniquement le
  blocage — si bien que le XML généré contenait des `TaxableAmount`/
  `VatAmountIssued` négatifs dans le corps principal de la déclaration,
  techniquement produit mais fiscalement invalide et rejeté par le portail
  OSS, sans que l'utilisateur soit informé qu'une correction avait été
  ignorée. Nouveau comportement : `ignore_negatives` n'est plus jamais
  invoqué depuis l'UI. En cas de solde bloquant après tentative de
  rattachement automatique, **aucun XML n'est généré**, le détail de
  l'erreur (pays/taux/montants concernés) s'affiche via `st.error()` et
  reste visible tant que le point n'est pas résolu, y compris après un
  rerun du fragment (persistance en `session_state`, purgée automatiquement
  au changement de contexte de calcul comme les autres artefacts). Nouvelle
  clé i18n `dl_oss_xml_blocked_error` ajoutée symétriquement dans les 7
  fichiers TOML (1131 → 1132 clés partout, vérifié par parsing).

- **Lookups `_get_conversion_rate()` répétés à chaque cellule dans
  `detail_ventes.py` (écarté, déjà résolu)** : le point O(n) réel
  (`_build_rows_df`, jusqu'à 100k lignes) stocke des `float` bruts sans
  jamais appeler `_fmt()` — la conversion/formatage n'intervient qu'après,
  sur le DataFrame déjà filtré/paginé (quelques dizaines de lignes). Aucune
  modification nécessaire.

Pistes identifiées mais **non corrigées dans ce patch** :

- **Boucle `_run_oss_loop`/`compute_all_with_vies` non vectorisée** :
  vectoriser en Polars les cas B2C simples apporterait un gain réel sur les
  gros fichiers, mais la logique OSS a un cumul stateful (seuil 10 000 €,
  reset annuel, distinction ventes/avoirs) qui ne se vectorise pas
  trivialement — gros chantier à ROI incertain, reporté.
- **Colonnes texte répétitives (`Canal`, `Scenario`, `Collector`, `Pays`)
  non typées `category` dans le cache `_build_rows_df`** : gain mémoire
  réel et risque faible, bonne candidate pour un prochain patch ciblé.
- **Cache `@st.cache_data` process-wide partagé entre tenants** : le
  compromis (un seul process Streamlit pour tous les utilisateurs) est déjà
  assumé et partiellement mitigé (`release_memory()` cible désormais le
  registre `_HEAVY_CACHE_REGISTRY`, pas un `clear()` global) — reste un
  sujet d'architecture de fond, pas un bug isolé à corriger.
- **Export IOSS sans XML dédié** : confirmé, seule la saisie manuelle est
  possible actuellement (voir « Sur l'horizon » / travaux en cours sur
  l'export IOSS séparé).

## Audit performance & Fiabilité — thread-safety, MD5 upload, dates de secours (2026-08-13, suite)

Dernière salve de correctifs suite au checkup de performance approfondi, visant à
stabiliser l'application en environnement multi-tenant (Streamlit Cloud/Railway)
et à améliorer la robustesse du moteur.

- **Thread-safety du cache de notes (`engine.py`)** : Ajout d'un verrou
  `_NOTE_INTERN_LOCK` (`threading.Lock`) autour des opérations sur le cache
  d'interning des notes (`_NOTE_INTERN_CACHE`). Bien que protégé par le GIL,
  l'enchaînement des méthodes `OrderedDict` (lookup, `move_to_end`, `popitem`)
  n'était pas atomique et pouvait théoriquement lever une `KeyError` sous forte
  charge multi-utilisateur concurrent.

- **Isolation des caches entre sessions (`mem_utils.py`)** : `release_memory()`
  ne vide plus brutalement les caches `@heavy_cache_data` via `.clear()`
  global au process. On laisse désormais l'éviction naturelle (TTL/max_entries)
  opérer, afin d'éviter qu'une action d'un utilisateur (déconnexion, retrait
  de fichier) ne pénalise la réactivité des autres sessions actives partageant
  le même conteneur.

- **Optimisation de l'agrégation OSS pour Excel (`excel_report.py`)** :
  `aggregate_oss_results()` n'est plus appelée deux fois (une fois pour le
  récapitulatif, une fois pour l'onglet détaillé). L'agrégat est calculé une
  seule fois au début de `export_xlsx()` et transmis aux fonctions de rendu via
  le paramètre `oss_agg`, divisant par deux le coût CPU de l'agrégation sur les
  gros volumes (100k+ lignes).

- **Signature d'upload robuste (`app.py`)** : Introduction de `_upload_sig()`,
  une clé composite incluant le nom, la taille et un **hash MD5 des 128
  premiers Ko** du fichier. Cette signature remplace le couple `(name, size)`
  dans tous les caches (compression, dédoublonnement, parsing, calcul),
  éliminant le risque de collision où un fichier modifié mais gardant la
  même taille ne serait pas re-parsé.

- **Fiabilité des taux de change historiques (`loader.py` / `classify.py`)** :
  `convert_currency()` utilise désormais `last_valid_date` (dernière date de
  transaction valide rencontrée chronologiquement dans le fichier) comme
  repli avant `date.today()` en cas de date malformée. Ce suivi incrémental
  garantit une meilleure précision fiscale pour les lignes corrompues au
  sein d'un export ancien, sans impacter la consommation RAM (pas de
  pré-passe sur le fichier).

## Audit performance & Fiabilité — VIES, seuil OSS, formatage, nettoyage DB (2026-08-14)

Suite du checkup performance : validation de chaque piste contre le code réel
avant patch (certaines pistes de l'audit initial se sont révélées mal ciblées
et n'ont pas été reprises telles quelles — voir plus bas).

- **Sémaphore global VIES (`vies_engine.py`)** : `_check_one()` (appelée par
  `validate_vat_numbers_parallel`) acquiert désormais un
  `threading.BoundedSemaphore(25)` process-wide (`_vies_global_semaphore`)
  autour de l'appel réseau `check_vat_with_retry`. Corrige un risque réel de
  thread exhaustion : `MAX_CONCURRENT_BIG_JOBS` (background_calc.py) ne
  s'applique qu'aux fichiers > 20k lignes, un fichier "moyen" (ex. 15k
  lignes) le contourne. Sans ce sémaphore, 4 utilisateurs simultanés sur des
  fichiers moyens pouvaient ouvrir jusqu'à 100 threads réseau, avec risque
  de saturation RAM et de bannissement temporaire par l'API VIES (limite de
  requêtes concurrentes par IP). N'a volontairement pas changé la taille des
  `ThreadPoolExecutor` existants ni leur cycle de vie.

- **Parsing de date dédupliqué (`engine.py`)** : `compute_vat()` et
  `_build_oss_note()` acceptent désormais un paramètre `tx_date` optionnel.
  La date de transaction est parsée **une seule fois** dans la boucle
  appelante (`_run_oss_loop`) et réutilisée par les deux fonctions, au lieu
  d'être reparsée une seconde fois dans `_build_oss_note` pour chaque vente
  sous le seuil OSS. Suppression au passage d'un import local
  `from datetime import date as _d` redondant (le module importe déjà
  `date as _date` en top-level).

- **Colonnes `category` dans le détail des ventes (`detail_ventes.py`)** :
  `_build_rows_df()` type désormais `canal`, `scenario`, `vat_country` et
  `collector` en dtype pandas `category`. Ces colonnes sont très répétitives
  sur un fichier de plusieurs dizaines de milliers de lignes (quelques
  dizaines de valeurs distinctes) — gain mémoire réel, comparaisons/tri
  inchangés côté appelant.

- **Formatage `Decimal`-safe (`ui/formatting.py`)** : `_fmt()` formate
  désormais directement depuis un `Decimal` (via `quantize`) quand `symbol`
  est fourni explicitement (pas de conversion FX nécessaire), au lieu de
  systématiquement passer par `float`. Évite un écart d'arrondi possible de
  0,01 € entre cet affichage et un total calculé ailleurs en `Decimal`. Le
  passage par `float` reste nécessaire quand une conversion FX a lieu (taux
  BCE lui-même en `float`).

- **Nettoyage des connexions DB idle sans garde global (`app.py`)** : le
  garde `if not any_job_running()` autour de l'appel aux 4
  `close_idle_connections()` (auth, ecb_rates, billing, vies_engine) a été
  retiré. Vérifié dans `database.py` : `close_idle_connections()` ne ferme
  que la connexion mise en cache par `threading.local()` sur le **thread
  appelant** (le thread principal du run Streamlit courant) — jamais celle
  d'un job de calcul en cours, qui tourne dans son propre thread
  (`background_calc.py`, thread `bgjob-*`). Le garde ne protégeait donc
  rien : il retardait seulement, sans raison, le nettoyage des connexions
  d'utilisateurs par ailleurs inactifs pendant qu'un job tournait n'importe
  où sur le process. Import `any_job_running` retiré (devenu mort).

- **`is_non_fiscal_eu()` optimisé (`rates.py`)** : ajout de
  `_NON_FISCAL_EU_COUNTRY_CODES`, un `frozenset` aplati précalculé une seule
  fois au chargement du module, pour le test d'appartenance direct (cas GL,
  FO, CW, AW, SX, BQ, CY-NORTH, GB-SBA). Auparavant, la fonction reparcourait
  les 9 règles de `NON_FISCAL_EU_POSTCODES` à chaque appel — pour un
  dictionnaire pourtant statique. Comportement strictement identique validé
  sur 48 521 cas de test avant bascule (0 écart). Gain mesuré sur benchmark
  synthétique 50k lignes : ~75% sur la fonction isolée, ~10% sur le temps
  de parsing total, ~8% sur le pipeline complet (parsing + `compute_vat`).

**Pistes de l'audit initial examinées et non retenues :**

- **Vectoriser en Polars les cas simples (EXPORT/DEEMED_SUPPLIER) avant
  `to_dicts()`** (`loader.py`) : rejetée après profiling. `_process_rows`
  construit un objet `Sale` pour 100% des lignes sans exception — il est
  utilisé en aval par l'UI, les exports et `compute_vat`, impossible de le
  sauter pour les cas "simples" sans casser ces usages. Le profiling a
  montré que `to_dicts()` ne représente que ~9% du temps de parsing (le vrai
  coût est la construction `Sale`/Pydantic, ~24%, et les lookups territoire
  — d'où le fix `is_non_fiscal_eu` ci-dessus, qui cible le bon goulot).
- **Batching VIES par chunks de 50** (écriture au fil de l'eau) : reporté,
  à regrouper avec la migration DictCursor déjà différée sur les mêmes
  zones de `vies_engine.py` plutôt que de toucher le fichier deux fois.
- **Cache VIES local `st.session_state`** : rejeté. `validate_vat_numbers_parallel`
  tourne dans le thread d'arrière-plan (`background_calc.py`) — y accéder à
  `st.session_state` violerait le principe de thread-safety déjà en place
  pour `lang` (transmis explicitement pour cette même raison).
- **Signature MD5 catalogue ASIN au lieu de `frozenset(...)`** (`app.py`,
  `_asin_catalog_sig`) : reporté. Le fichier catalogue uploadé n'est
  disponible que dans `sidebar.py` (scope local) ; utiliser son hash
  nécessiterait d'étendre l'interface `SidebarResult` pour le faire
  remonter — pas un simple patch local. Sans lien avec `_upload_sig()`
  (audit précédent, ci-dessus), qui couvre le fichier de transactions
  principal, pas le catalogue ASIN.

**2026-08-14 (suite) — Audit perf/RAM/cohérence, points traités :**

- **`app.py` — garde `any_job_running()` réellement retiré** : le point ci-dessus
  documentait déjà la décision, mais le code du run précédent contenait
  encore le garde `if not any_job_running():` et son import. Corrigé pour
  être en phase avec cette entrée.
- **`ca3_report.py::_asin_avg_price_from_results`** : implémentation en liste
  de `Decimal` par ASIN remplacée par un accumulateur `(somme, compteur)`,
  alignée sur `excel_report.py::_build_asin_avg_price` qui avait déjà ce
  pattern. Évite de conserver un objet `Decimal` par vente en RAM juste pour
  calculer une moyenne. Équivalence numérique vérifiée sur données
  aléatoires (5000 lignes) avant patch.
- **`excel_report.py::export_xlsx`** : `_build_asin_avg_price(results)` était
  appelée deux fois séparément (onglet Analyse AIC FBA et onglet Intrastat),
  chacune reparcourant l'intégralité de `results`. Calculée une seule fois
  dans `export_xlsx` (même pattern que `_oss_agg`, déjà en place) et passée
  en paramètre optionnel `asin_avg` aux deux fonctions, qui gardent un
  fallback de calcul interne pour compat ascendante (appels directs hors
  `export_xlsx`, tests).
- **`excel_report.py::_write_vies_history_tab`** : `normalize_full_vat()`
  était appelée pour chaque ligne de `results`, pas seulement une fois par
  numéro de TVA distinct (le dédoublonnage via `seen_vats` n'intervenait
  qu'après). Ajout d'un cache local `(vat_brut, pays_acheteur) -> full_vat`
  pour éviter de renormaliser le même numéro à chaque vente d'un acheteur
  récurrent. Gain CPU modeste (normalisation = manipulation de string), mais
  cohérent avec les autres optimisations O(n) déjà faites sur ce fichier.

**Point identifié mais non traité (reporté) :**

- **`_ColumnWidthTracker` local redondant dans ~13 fonctions `_write_*_tab`** :
  vérifié empiriquement (test openpyxl isolé) qu'en mode `write_only=True`,
  toute largeur de colonne posée après le tout premier `ws.append()` réel
  est silencieusement ignorée. Or `_SequentialSheetWriter` (le wrapper passé
  à toutes ces fonctions) calcule déjà ses propres largeurs en interne et
  les applique *avant* d'émettre réellement les lignes vers la feuille —
  c'est tout l'objet de ce wrapper. L'appel explicite `_width_tracker.apply(ws)`
  fait par chaque fonction `_write_*_tab` à sa toute fin intervient donc
  *après* l'émission réelle : c'est un no-op silencieux, sans impact sur le
  fichier produit. Chaque tracker local (instanciation + tous les
  `observe_row()` associés) fait un travail CPU pour un résultat jeté —
  vestige du code d'avant l'introduction de `_SequentialSheetWriter`.
  **Reporté** : correctif non risqué en soi (suppression de code mort) mais
  qui toucherait ~13 fonctions différentes pour un gain CPU seul (pas de
  bug visible, le rendu final est correct) — ratio risque/gain défavorable
  pour un refactor en une passe. À traiter au fil de l'eau si ces fonctions
  sont retouchées pour d'autres raisons, plutôt qu'en bloc.

---
> Il ne remplace pas un conseil fiscal professionnel.
> Les taux de TVA et seuils doivent être vérifiés et tenus à jour annuellement.
