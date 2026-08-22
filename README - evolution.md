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

**2026-08-14 (suite 2) — Audit externe perf/RAM/cohérence #2, 6 points traités :**

- **`vies_engine.py::check_vat_with_retry` / `_check_one`** : le sémaphore
  global `_vies_global_semaphore` (limite 25 requêtes VIES concurrentes)
  englobait tout le cycle retry, `time.sleep()` de backoff exponentiel
  inclus (1s → 2s → 4s). Un pays en panne/lent pouvait donc faire dormir
  jusqu'à 25 threads tout en gardant leur slot réservé, bloquant les
  vérifications d'autres pays dont le service VIES fonctionne normalement.
  Le sémaphore est désormais acquis uniquement autour de l'appel réseau
  (`check_vat`) individuel, à l'intérieur de la boucle de retry — les sleep
  de backoff n'immobilisent plus de slot. Comportement de retry/backoff par
  ailleurs inchangé.
- **`ui/formatting.py::_smart_money_df`** : la fonction mute `df` en place
  (conversion de devise sur les colonnes monétaires) — vérifié que c'est un
  contrat volontaire dont dépendent les 9 points d'appel actuels (le même
  objet `df` est réaffiché juste après via `_gated_preview_table`/
  `st.dataframe` ; la rendre pure aurait cassé silencieusement l'affichage
  devise partout dans l'app). Pas de bug actif constaté (tous les appelants
  passent un df fraîchement `.copy()`/construit). Ajout d'un garde-fou
  idempotent (`df.attrs["_tva_currency_converted"]`) : si la fonction est
  rappelée une seconde fois sur le même objet `df` (réutilisation
  accidentelle, référence partagée via un cache), le taux de change n'est
  plus appliqué une deuxième fois.
- **`excel_report.py` — 10 appels `_width_tracker.apply(ws)` morts supprimés** :
  sur les ~13 fonctions `_write_*_tab` documentées ci-dessus comme code
  mort, seuls les appels situés *après la boucle d'écriture complète*
  (donc réellement no-op, cf. entrée précédente) ont été retirés — 10
  occurrences, dans les fonctions ayant des données à écrire. Les 2 appels
  situés sur un chemin de retour anticipé (feuille vide, avant toute
  émission réelle vers la feuille sous-jacente) sont conservés inchangés
  car leur effet, bien que redondant avec `_SequentialSheetWriter.finalize()`,
  n'est pas garanti no-op de la même façon et n'entrait pas dans le
  périmètre "aucun risque" de ce correctif. Les boucles `observe_row()`
  restent en place (retrait complet du tracker local hors périmètre —
  refactor plus large, cf. entrée reportée ci-dessus).
- **`ui/sidebar.py::_parse_catalog_bytes`** : passé de `@heavy_cache_data`
  (= `st.cache_data`, une copie du dict retournée à chaque appel) à
  `@st.cache_resource` (même instance mémoire partagée entre toutes les
  sessions). Le catalogue ASIN n'est jamais muté après parsing (uniquement
  des `.get()` en aval) : safe. Pour un catalogue de 20k+ entrées et
  plusieurs sessions utilisateur simultanées, évite une copie complète du
  dict par session. Effet de bord positif : ce cache n'étant plus dans
  `_HEAVY_CACHE_REGISTRY`, il n'est plus vidé par `release_memory()` au
  logout d'un utilisateur quelconque (correct, puisque c'est désormais une
  ressource partagée entre sessions — le vider sur l'évènement d'un seul
  utilisateur forçait un re-parsing inutile pour les autres sessions
  actives). Import `heavy_cache_data` retiré de `sidebar.py` (plus utilisé).
  Commentaire dans `app.py` (autour de `_asin_catalog_sig`) mis à jour :
  ne prétend plus que l'`id()` change à chaque rerun (ce n'est plus vrai
  avec `cache_resource`) ; le hash de contenu est conservé malgré tout pour
  ne pas faire reposer la clé de cache sur un détail d'implémentation de
  `st.cache_resource`.
- **`excel_report.py` — cache `id_hash` par `sale_id`** : dans le calcul des
  hash totals (contrôle d'intégrité technique), `re.sub(r"\D", "", str(sale_id))`
  était recalculé pour chaque ligne. Un `sale_id` (TRANSACTION_EVENT_ID)
  revient très souvent sur plusieurs lignes consécutives (une commande
  Amazon = plusieurs articles partageant le même ID de commande). Ajout
  d'un cache local `dict[sale_id, int]` (vidé à chaque appel, pas de fuite
  mémoire inter-appels) : le nettoyage regex + parsing int n'est fait
  qu'une fois par `sale_id` distinct au lieu d'une fois par ligne.
- **`engine.py::_run_oss_loop`** : la date de transaction était déjà parsée
  une seule fois par vente (`date.fromisoformat`, réutilisée pour
  `compute_vat` et `_build_oss_note`). Ajout d'un cache "dernière date vue"
  (`sorted_items` est trié chronologiquement en amont) : si la valeur brute
  ISO (10 premiers caractères) est identique à la ligne précédente, la
  date déjà parsée est réutilisée sans nouvel appel à `date.fromisoformat()`.
  Gain concentré sur les gros fichiers où de nombreuses ventes partagent la
  même date de transaction consécutivement.

Validation : `py_compile` sur les 6 fichiers modifiés + suite `pytest`
complète — 166 passed / 4 failed, échecs identiques au baseline documenté
(liés à l'absence de `SUPABASE_DB_URL` en sandbox, sans rapport avec ces
patchs).

---

## Audit externe perf/RAM #3 — interning ASIN/TVA, nettoyage tracker largeur (2026-08-15)

Nouvel audit externe, 8 points soumis. 3 points redondants avec des sujets
déjà traités/tranchés lors d'audits précédents (rejetés ou déjà décidés, non
retraités ici), 2 points déjà corrigés dans le code actuel (audit basé sur
une version antérieure), 1 point sans gain réel identifié, 2 points corrigés :

**Corrigés :**

- **`models.py::Sale.__post_init__`** — `asin` et `buyer_vat_number`
  n'étaient pas internés (`sys.intern`), contrairement aux champs pays/devise/
  catégorie déjà traités. Ces deux champs ont une cardinalité répétitive
  élevée (même ASIN ou même client sur des milliers de lignes) : ajout de
  l'interning, casse d'origine conservée (pas d'`.upper()`, contrairement aux
  codes pays) pour ne pas altérer une valeur déjà normalisée ailleurs (VIES,
  affichage).

- **`excel_report.py` — nettoyage complet du `_ColumnWidthTracker` local
  résiduel dans les ~13 fonctions `_write_*_tab`** : l'audit du 2026-08-14
  avait déjà supprimé les 10 appels `.apply(ws)` réellement no-op, en
  conservant volontairement par prudence les boucles `observe_row()` ainsi
  que les 2 `.apply(ws)` situés sur un chemin de retour anticipé (feuille
  vide), leur effet n'étant pas jugé "garanti no-op" à l'époque. Analyse
  approfondie du wrapper `_SequentialSheetWriter` : celui-ci maintient son
  propre tracker interne et observe déjà chaque ligne ajoutée via son propre
  `append()` (y compris les lignes d'en-tête et de repli "aucune donnée") ;
  `finalize()`, toujours appelé après écriture de chaque feuille, applique
  systématiquement les largeurs via ce tracker interne — y compris sur les
  chemins de retour anticipé, où l'`apply()` externe est donc écrasé/rendu
  redondant par l'`apply()` interne déclenché par `finalize()`. Les ~28
  boucles `observe_row()` restantes et les 2 derniers `.apply(ws)` (chemins
  de retour anticipé) ont donc été retirés — code entièrement mort ou
  redondant confirmé. La classe `_ColumnWidthTracker` elle-même est
  conservée : toujours utilisée en interne par `_SequentialSheetWriter`.

**Déjà corrigés (audit basé sur une version antérieure du code) :**

- Pré-calcul du taux de change hors boucle dans `_write_details_tab`
  (`excel_report.py`) — déjà fait le 2026-08-04.
- Suppression du tracker de largeur mort — voir ci-dessus (déjà partiellement
  fait le 2026-08-14, complété aujourd'hui).

**Rejetés (analyse détaillée) :**

- *Filtrage Polars vectorisé des types de transaction (`loader.py`)* : la
  boucle réelle ne fait pas qu'un test `tx_type in {...}` (déjà O(1),
  négligeable) — elle construit aussi les objets `Sale`, convertit les
  devises, valide les données ligne par ligne via des fonctions `parser.*`
  opérant sur des dicts. Vectoriser réellement demanderait de réécrire tous
  les `parser.*` pour opérer sur DataFrame : refactor lourd et risqué pour un
  gain quasi nul, le coût réel de la boucle n'étant pas le test de type.
- *`DOMESTIC_REVERSE_CHARGE_COUNTRIES` en `dict[pays, categories]`
  (`rates.py`)* : déjà tranché le 2026-08-09, validé par le cabinet
  comptable pour le périmètre actuel — reste en attente d'un nouveau
  cas produit/pays pour être rouvert.
- *Découpage mensuel B2B DES (`oss_export.py::build_b2b_excel`)* : déjà
  identifié et suivi comme item de roadmap (obligation légale mensuelle,
  art. 289 B CGI, export actuel non subdivisé par mois) — pas de nouvelle
  information apportée par cet audit, statut inchangé (en attente).

**Test "déjà trié" avant `sorted()` (`engine.py`, tri chronologique)** :
gain jugé marginal et non prioritaire — `sorted()` avec `key=` calcule la
clé une fois par élément quel que soit l'état de tri initial (transformée de
Schwartz), donc vérifier si la liste est déjà triée nécessiterait de toute
façon un passage complet sur les éléments pour un gain net faible. Non
retenu pour l'instant.

Validation : `py_compile` sur `models.py` + `excel_report.py`, AST complet
validé sur `excel_report.py`, suite `pytest` complète — 166 passed /
4 failed, échecs identiques au baseline (liés à l'absence de
`SUPABASE_DB_URL` en sandbox, sans rapport avec ces patchs).

---

## Découpage mensuel de l'état récapitulatif B2B — DES (2026-08-15, suite)

Correction de l'item roadmap identifié lors de l'audit précédent : la
déclaration DES (état récapitulatif des clients, autoliquidation B2B) est
une obligation **mensuelle** (art. 289 B CGI), alors que `build_b2b_excel` /
`build_oss_csv` agrégeaient toutes les ventes B2B de la période choisie
(souvent un trimestre) en une seule liste avec un unique total — obligeant
l'utilisateur à redécouper manuellement par mois.

Aucune contrainte technique bloquante : chaque `B2bLine` porte déjà sa
`transaction_date` individuelle, normalisée en amont au format `YYYY-MM-DD`
(voir `parsers/amazon/detect.py::parse_date`). Correctif limité à la couche
d'export (`oss_export.py`), sans toucher au moteur de classification fiscale
(`engine.py`) ni à `rates.py`.

- **`oss_export.py::_build_b2b_recap` (Excel, onglet `B2B_Recap`)** : les
  lignes sont désormais triées chronologiquement et regroupées par mois
  (clé `YYYY-MM`, nouvelle fonction `_b2b_month_key`). Chaque groupe reçoit
  un bandeau ("Période : YYYY-MM") et une ligne de sous-total ; le total
  général reste affiché en bas comme avant. Les lignes sans date exploitable
  (transaction_date vide) sont regroupées à part sous "Date inconnue", en
  dernier. Choix : un seul onglet avec sous-totaux (option retenue par
  Matthieu) plutôt qu'un onglet par mois — plus simple, moins de risque de
  régression, montants mensuels exacts conservés.
- **Total général** : passé d'une formule Excel `=SUM(F4:F{n})` à une valeur
  précalculée (`data.total_b2b_ht`) — la plage de somme n'est plus
  contiguë une fois les bandeaux/sous-totaux insérés (elle aurait
  recompté les sous-totaux mensuels en plus des lignes, doublant le total).
- **`oss_export.py::build_oss_csv`** — même regroupement mensuel appliqué
  au CSV `b2b_recap.csv` (bandeau de mois en première colonne + ligne de
  sous-total), pour rester cohérent avec l'Excel.
- **i18n** : 3 nouvelles clés (`b2b_month_group_header`,
  `b2b_month_subtotal`, `b2b_unknown_date`) ajoutées dans les 7 fichiers
  TOML, symétrie vérifiée (`toml.load` + diff de clés sur les 7 langues).
  Format machine `YYYY-MM` conservé pour l'étiquette de mois (pas de nom de
  mois localisé) : évite d'avoir à maintenir des noms de mois traduits dans
  7 langues, et reste sans ambiguïté pour un usage comptable.
- **Test existant mis à jour** : `tests/test_oss_export.py::
  test_b2b_excel_sheet_and_rows` référençait les numéros de ligne fixes de
  l'ancienne mise en page (plus de bandeau de mois) — adapté aux nouveaux
  indices de ligne et à la valeur de total précalculée.

Validation : `py_compile` sur `oss_export.py`, test manuel de génération
CSV + Excel (contrôle visuel du regroupement et des sous-totaux, pas de
double comptage), suite `pytest` complète — 166 passed / 4 failed,
échecs identiques au baseline (`SUPABASE_DB_URL` absent en sandbox, sans
rapport avec ce patch).

---

## Retrait de l'Auto-Sleep applicatif (2026-08-15, suite)

Suppression du mécanisme de veille côté application (détection d'inactivité JS
+ purge proactive du `session_state`).

- **Raison technique** : Toute activité périodique (même minimale) sur le
  canal WebSocket Streamlit empêchait l'infrastructure (Railway Serverless)
  de détecter l'inactivité réelle du service. Cela bloquait la mise en
  veille profonde (scale-to-zero) du conteneur, entraînant une consommation
  de crédits inutile.
- **Nouvelle stratégie** : La gestion de la mémoire et de la mise en veille
  est désormais déléguée intégralement à l'infrastructure. Railway coupe le
  conteneur après une période d'inactivité réelle (RAM rendue à 0). Le
  système de cookies persistant (`tva_session_token`) garantit que
  l'utilisateur retrouve sa session au redémarrage sans friction.
- **Nettoyage** : Commentaire d'explication ajouté dans `app.py` et mise à
  jour du `README.md` principal.

---

## Audit de performance/fiabilité — 4 points corrigés (2026-08-15, suite)

Suite à un audit externe (5 points soumis), vérification systématique du code
réel avant patch : 1 point rejeté (référence à un champ `shipment_date`
inexistant dans `models.py`), 1 point confirmé mais laissé en l'état
(interning des notes OSS dans `engine.py` — déjà mitigé par un cache LRU
plafonné existant, refonte jugée risque/gain défavorable sur du code moteur
légalement sensible), 4 corrections appliquées :

- **`app.py::_upload_sig`** — décompression Gzip répétée corrigée. Les
  fichiers restaurés depuis le cache interne (`_CachedUploadedFile`, survit
  aux reruns internes type changement de langue) forçaient une décompression
  complète du blob (jusqu'à 100 Mo) à chaque appel de `_upload_sig`, appelé
  plusieurs fois par rerun Streamlit (dédup + clé de cache de parsing), donc
  à chaque clic/filtre. Le hash MD5 partiel est désormais porté directement
  par `_CachedUploadedFile` (`_content_hash`, calculé une seule fois à la
  compression initiale) et réutilisé tel quel — plus aucune décompression
  hors re-parsing réel.
- **`tva_intracom/ui/tabs/audit.py::render_audit`** — construction de
  `row_d` déplacée après le test d'écart (`abs(ecart)<=0.05`) au lieu
  d'avant. Sur un fichier sans écart significatif, la boucle n'alloue plus
  un dict à 11 clés par ligne pour le jeter immédiatement.
- **`tva_intracom/models.py::Sale.__post_init__`** — `transaction_date` et
  `order_date` ajoutés à l'interning (`sys.intern`), au même titre que
  `asin`/`buyer_vat_number` : forte cardinalité répétitive sur un rapport
  couvrant une période donnée (ex. ~30 valeurs distinctes pour 100k lignes
  sur un mois). `sale_id`/`display_id` volontairement laissés hors périmètre
  (cardinalité réelle non mesurée, à date potentiellement quasi unique par
  vente selon le connecteur Amazon).
- **`tva_intracom/engine.py`** (bug fonctionnel) — les compteurs
  `manual_valid_count`/`manual_invalid_count` (`ViesValidationSummary`,
  `models.py`) n'étaient jamais incrémentés, alors que `manual_override_count`
  l'était. Conséquence : `total_manual_override` (= somme des deux) renvoyait
  toujours 0, faussant `total_checked_or_covered` et le taux de fiabilité
  affiché à l'utilisateur. Correction : ventilation sur le champ `valid` déjà
  porté par l'objet `SimpleNamespace` construit lors de l'application d'un
  override manuel — `manual_override_count` garde son sens de total.

Validation : `py_compile` sur les 4 fichiers modifiés, suite `pytest`
complète — 165 passed / 5 failed, échecs strictement identiques au baseline
sans les patches (comparaison directe faite sur le dépôt `dev` non modifié :
mêmes 5 tests en échec, tous liés à `SUPABASE_DB_URL` absente en sandbox ou
préexistants, sans rapport avec ce patch). Aucune régression introduite.

## 2026-08-16 — Dépassement quota SIREN : affichage déjà aligné sur compte gratuit + message clarifié

Suite à question de l'utilisateur : "si j'ai un abonnement avec 5 SIREN et 5
enregistrés que je passe à 3 SIREN, faut-il bloquer l'affichage et le
téléchargement jusqu'à redescendre à un nombre de SIREN ≤ abonnement ?"

**Vérification du code réel (pas de patch structurel nécessaire)** :
- `build_billing_gate()` positionne déjà `can_export=False` dès que
  `SirenQuotaStatus.blocked` est vrai (SIREN enregistrés > quota de
  l'abonnement).
- Tous les onglets (`declarations.py`, `detail_ventes.py`, `audit.py`,
  `vies_ui.py`, `telechargements.py`, `visualisations.py`) consomment déjà
  `ctx.can_export` de façon uniforme via `_gated_preview_table()`
  (`formatting.py`) — qui verrouille l'aperçu des tableaux **à l'identique**,
  qu'il s'agisse d'un compte jamais abonné ou d'un compte en dépassement de
  quota. Aucune distinction de comportement d'affichage entre les deux cas
  n'existait déjà côté code — la demande "même affichage que les comptes
  gratuits" était donc déjà satisfaite structurellement.
- Les téléchargements sont également déjà bloqués (`gated_download()`), avec
  un message dédié `gate_quota_blocked_err` / `gate_quota_global_blocked_err`
  (distinct du paywall générique) plutôt qu'un blocage silencieux.

**Seul ajustement apporté** — `tva_intracom/i18n/*.toml` (7 langues) :
les 2 clés `gate_quota_blocked_err` et `gate_quota_global_blocked_err`
n'indiquaient que "retirez des SIREN" comme solution. Ajout de l'alternative
"ou augmentez votre abonnement" dans les 7 langues, à la demande explicite de
l'utilisateur. Clés inchangées (1134 dans chaque fichier, symétrie
`toml.load()` revérifiée) — seul le texte des 2 valeurs a changé.

**Validation** : suite `pytest` complète — 174 passed / 4 failed, échecs
strictement identiques au baseline (SUPABASE_DB_URL absente en sandbox, sans
rapport). Vérifié qu'aucun test n'asserte le texte exact de ces 2 clés avant
modification (`grep` sur `tests/`).

## 2026-08-16 — Downgrade différé Stripe (Subscription Schedules)

Passage de la logique de downgrade en cours de période (application immédiate,
avec avoir) à un downgrade différé (effectif à la fin de la période en cours,
via Subscription Schedules Stripe côté configuration Stripe). Ajout du support
webhook des 4 nouveaux types d'événements associés, à la demande de
l'utilisateur.

- **Prise en charge du changement de plan EFFECTIF** — aucune modification
  nécessaire. `handle_stripe_webhook_event` (branche
  `customer.subscription.updated`) dérivait déjà le plan actif depuis le
  `price_id` réellement actif sur l'abonnement (bugfix du 2026-08-16 précédent
  dans la même session, pour un autre incident) et non depuis les metadata
  figées au Checkout initial. Quand la phase planifiée s'active réellement,
  Stripe modifie le `price_id` du `SubscriptionItem` et déclenche cet event
  standard — la base reste donc automatiquement cohérente sans changement de
  code sur ce point.

- **`tva_intracom/billing.py::handle_stripe_webhook_event`** (nouveau) — ajout
  de 2 branches pour les 4 événements demandés :
  - `subscription_schedule.created` / `subscription_schedule.updated` :
    extrait la phase suivante (`_extract_scheduled_change`, cherche dans
    `phases` celle dont `start_date` correspond exactement à
    `current_phase.end_date`) et enregistre plan/intervalle/date en base pour
    affichage utilisateur uniquement — ne touche jamais au plan actif.
  - `subscription_schedule.released` / `subscription_schedule.completed` /
    `subscription_schedule.canceled` : efface l'info "changement à venir"
    (devenue effective ou annulée).

- **Nouvelles colonnes `tva_subscriptions`** — `scheduled_plan`,
  `scheduled_billing_interval`, `scheduled_change_at` (toutes nullable,
  `ADD COLUMN IF NOT EXISTS`, idempotent). Purement informatives : n'affectent
  ni `can_export`/`get_subscription_status().active`, ni aucune logique de
  facturation existante.

- **`SubscriptionStatus`** — 3 nouveaux champs optionnels
  (`scheduled_plan`, `scheduled_billing_interval`, `scheduled_change_at`),
  défauts `None` — rétrocompatible avec tous les appels positionnels/mots-clés
  existants (vérifié : tests existants passent sans modification).

- **`tva_intracom/ui/sidebar.py`** — affichage d'un `st.info` dans la zone
  "Abonnements & forfaits" (sous le message d'abonnement actif) si un
  changement est programmé : "🔄 Passage prévu au forfait **{plan}** le
  {date}". Nouvelle clé i18n `sub_scheduled_change_msg` ajoutée aux 7 fichiers
  TOML (symétrie vérifiée via `toml.load()` — 1134 clés dans chacun).

- **Scale-to-zero** — aucun impact : mêmes patterns `_run`/`_get_pool` que le
  reste de `billing.py`, aucune connexion/thread persistant ajouté.

- **Tests** — 8 nouveaux tests (`tests/test_billing_payment_quotas.py`) :
  5 sur `_extract_scheduled_change` (phase suivante trouvée/absente, price_id
  string vs objet étendu, price_id inconnu → None) et 3 sur le webhook
  (`subscription_schedule.created` écrit bien plan/intervalle/date,
  `subscription_schedule.released` efface bien les 3 colonnes, event sans
  `user_id` résolu → no-op, pas d'écriture DB).

- **Point d'attention pour la mise en prod** — à faire côté **Stripe
  Dashboard** (hors code) : ajouter les 4 event types à la liste des
  événements écoutés par l'endpoint webhook, sinon ils ne seront jamais
  envoyés. Recommandé également : déclencher un événement de test réel
  (`subscription_schedule.created`) en mode test Stripe avant bascule en
  production, pour confirmer que `customer.subscription.updated` accompagne
  bien la création du schedule sans changer le `price_id` actif (comportement
  attendu d'après la documentation Stripe, non re-vérifiable depuis ce
  sandbox sans clé API réelle).

**Validation** : `py_compile` sur tous les fichiers modifiés + suite `pytest`
complète — 174 passed / 4 failed, échecs strictement identiques au baseline
(166/4, tous liés à `SUPABASE_DB_URL` absente en sandbox, sans rapport avec ce
patch). Aucune régression introduite.

## 2026-08-15 — Audit externe (7 points) : seuil OSS, RAM, cache, i18n, threads

Traitement d'un audit externe transmis par l'utilisateur (7 points). Chaque
point vérifié sur le code réel (dépôt `dev`) avant tout patch — plusieurs
affirmations de l'audit étaient inexactes ou trop générales par rapport au
code effectif.

- **`tva_intracom/engine.py::_oss_eligible`** (bug fiscal, confirmé) — les
  ventes B2B cross-border requalifiées `Scenario.OSS_B2C` par `compute_vat`
  (numéro de TVA acheteur invalide VIES, pays de destination HORS
  `DOMESTIC_REVERSE_CHARGE_COUNTRIES`) étaient déclarées en OSS mais absentes
  du cumul du seuil 10 000 € (`_oss_eligible` ne testait que
  `buyer_type == B2C`). Corrigé : `_oss_eligible` inclut désormais cette
  branche précise. Ne couvre PAS l'autre branche B2B invalide (pays DANS
  `DOMESTIC_REVERSE_CHARGE_COUNTRIES`, restée `Scenario.DOMESTIC`,
  correctement hors OSS) — contrairement à ce que suggérait l'audit
  (`buyer_vat_valid is False` générique, qui aurait cassé ce second cas).

- **`tva_intracom/parsers/amazon/aggregate.py::preaggregate_v5`** (RAM,
  confirmé) — `groups.pop(key)` remplace `groups[key]` dans la construction
  de `rows_to_process` : chaque liste de lignes brutes est libérée dès son
  agrégation au lieu de rester en mémoire jusqu'à la fin de la compréhension
  de liste. Évite de quasiment doubler la RAM au pic sur les gros fichiers V5
  (100k+ lignes).

- **`tva_intracom/vies_engine.py::_is_empty_response`** (retry VIES) —
  **REJETÉ**, documenté en commentaire directement sur la fonction plutôt que
  patché. L'audit proposait de sortir les réponses vides des conditions de
  retry (réponse standard pour un n° réellement invalide). Rejet motivé :
  ce comportement existe précisément pour absorber l'incident de production
  du 31/07/2026 (panne du service national allemand renvoyant
  `valid=False, error=""`, indiscernable d'un numéro réellement invalide,
  ayant fait basculer en masse des n° allemands valides en "invalides").
  Patcher ce point réouvrirait une faille déjà colmatée sur un incident vécu.
  Coût accepté : latence de traitement sur les vrais numéros invalides,
  pas d'impact fiscal.

- **`tva_intracom/ui/sidebar.py::vies_cache_stats`** (charge Supabase,
  confirmé) — `get_cache_stats` (3 `SELECT COUNT(*)`, dont un sur le cache
  global) tournait à chaque rerun Streamlit, y compris `st.expander` replié.
  Wrapper `@st.cache_data(ttl=60)` ajouté côté `sidebar.py` (pas dans
  `vies_engine.py`, qui reste sans dépendance Streamlit dure) ; cache
  invalidé explicitement quand l'utilisateur change le TTL, pour ne pas
  afficher une valeur périmée jusqu'à 60s après son propre changement.

- **`tva_intracom/models.py::Sale`** (cleanup, impact nul confirmé sur
  Amazon) — le nettoyage de `_to_decimal` (symboles €/$/£, virgule
  décimale) dans `__post_init__` était mort : Pydantic valide déjà
  `amount_ht`/`original_amount`/`exchange_rate`/`amazon_vat_amount` en
  `Decimal` AVANT `__post_init__`, donc soit la valeur brute est déjà
  rejetée par Pydantic avant d'atteindre le nettoyage, soit elle est déjà
  convertie. Remplacé par `CleanDecimal` (`Annotated[Decimal,
  BeforeValidator(_clean_decimal)]`), qui nettoie AVANT la validation
  Pydantic — testé ("10,50 €" → `Decimal('10.50')`). Le parser Amazon
  (seul maintenu) fournissait déjà des `Decimal` propres en amont : aucun
  changement de comportement observable sur le pipeline actuel, cleanup de
  robustesse pour les autres parsers/champs.

- **`tva_intracom/ui/background_calc.py::start_background_job`** (thread
  fantôme, confirmé partiellement) — quand un réglage change pendant qu'un
  calcul (gros fichier) tourne en tâche de fond, un nouveau `job_id`
  démarre un nouveau thread ; l'ancien continuait de tourner jusqu'à son
  terme (accepté, pas d'annulation coopérative sûre sans complexifier
  engine.py/vies_engine.py) MAIS son entrée `_JobState` — potentiellement
  volumineuse une fois le résultat complet stocké — n'était jamais libérée
  de `st.session_state`, s'accumulant indéfiniment à chaque changement de
  réglage sur un gros fichier. Corrigé : l'entrée session_state du job
  précédent est libérée dès qu'un nouveau job démarre pour un `job_id`
  différent.

- **i18n / `onboarding`** (dette confirmée, mais 24 clés et non 150+) —
  `onboarding.py` avait déjà été supprimé du code, mais les 24 clés
  `onboarding_*` (bloc de section complet, symétrique) restaient dans les 7
  `i18n/*.toml` (1157 → 1133 clés partout, vérifié `toml.load()`) ainsi que
  2 mentions obsolètes de `ui/onboarding.py` dans `README.md` et ce fichier
  — toutes retirées.

- **`tva_intracom/ui/formatting.py::_gated_preview_table`** (détection
  colonnes identifiant) — ajout de `"nº"` (variante ordinal ≠ "n°" degré)
  aux marqueurs identifiant, couvrant des libellés ES/PT réels
  ("Nº IVA rechazado"...). Piste explorée et **abandonnée** : ajouter
  `"vat"`/`"iva"`/`"mwst"` comme marqueurs identifiant pour couvrir les n°
  TVA traduits (proposition de l'audit) — rejetée après vérification des
  `i18n/*.toml` : ces mots apparaissent aussi dans de VRAIS libellés de
  colonnes MONTANT (`col_vat_eur` = "VAT (EUR)" / "MwSt (EUR)" / "IVA
  (EUR)"...) ; les marquer comme identifiant aurait cassé le formatage
  monétaire dans 6 langues sur 7. Aucun bug actif constaté sur les libellés
  de colonnes VAT-ID réellement utilisés dans l'app aujourd'hui.

Validation : `py_compile` sur les 7 fichiers Python modifiés
(`engine.py`, `parsers/amazon/aggregate.py`, `ui/sidebar.py`, `models.py`,
`ui/background_calc.py`, `ui/formatting.py`, `vies_engine.py`) + `toml.load()`
sur les 7 fichiers i18n (clés symétriques, 1133 partout) + suite `pytest`
complète — 165 passed / 5 failed, échecs strictement identiques au baseline
avant patch (mêmes 5 tests, tous liés à `SUPABASE_DB_URL` absente en
sandbox ou à des limitations réseau du sandbox — aucun rapport avec ces
patches). Aucune régression introduite.


## 2026-08-15 — Audit externe (10 points) : seuil OSS avoirs, perf BCE/VIES/RAM, précision AIC

Audit externe reçu sous forme de liste de 10 points (bug fiscal, performance,
fiabilité, RAM, redondance, précision). Chaque point vérifié contre le code
réel avant tout jugement (voir règle #1) — tous confirmés sauf nuance sur le
point 2 (impact réel seulement en devise étrangère non listée dans les
contre-valeurs fixes) et le point 10 (déjà résolu). 9 points corrigés, 1
laissé en l'état.

- **BUG FISCAL — seuil OSS des avoirs (`tva_intracom/engine.py::_run_oss_loop`,
  confirmé)** — les avoirs étaient classés selon un cumul `refund_cumulative_oss_ht`
  reconstruit uniquement à partir des avoirs (toujours ≤ 0), donc `_build_oss_note`
  reclassait systématiquement tout avoir en régime DOMESTIC (FR) dès que
  l'option seuil était active, même quand l'entreprise avait dépassé le seuil
  OSS via ses ventes. Un avoir annulant une vente OSS (taxée à destination)
  était alors indûment déduit de la TVA française sur la CA3. Corrigé : les
  avoirs utilisent désormais le même cumul net partagé `cumulative_oss_ht`
  que les ventes pour déterminer leur régime — un avoir suit la classification
  de la vente qu'il annule. Le cumul dédié aux avoirs (devenu inutile) a été
  supprimé.

- **PERF — lookups BCE dans la boucle OSS (`engine.py::_oss_threshold_display`,
  confirmé, impact réel nuancé)** — `_oss_threshold_display` retourne
  immédiatement sans appel BCE quand la devise est EUR (cas majoritaire), donc
  l'impact "massif" décrit ne concerne que les ventes facturées dans une devise
  étrangère non listée dans `OSS_THRESHOLD_FIXED_EQUIVALENTS`. Dans ce cas,
  `get_rate`/`get_oss_rate_date` (verrou `_cache_lock` compris) étaient bien
  rappelés à chaque ligne éligible OSS. Corrigé : cache local
  `(devise, période, date) -> limite locale` créé une fois par appel de
  `_run_oss_loop` et propagé via `_build_oss_note`, mémoïsant le calcul pour
  tout le batch (devise et date de taux quasi constantes sur un batch donné).

- **BUG — clé de cache incomplète (`app.py::_cache_key`, confirmé)** — la
  clé ne contenait ni `language`, ni `oss_period`, ni `on_invalid_behavior`,
  alors que les trois influencent le résultat affiché (langue des notes,
  taux BCE du seuil OSS selon la période, comportement sur numéro TVA
  invalide). Un changement de langue ou de période OSS réutilisait donc
  silencieusement l'ancien résultat en cache. Corrigé : les trois variables
  ajoutées à `_cache_key`.

- **PERF BDD — purge de session VIES (`vies_engine.py::purge_malformed_entries`,
  confirmé)** — `SELECT DISTINCT vat_id` sur toute la table suivi d'un
  `DELETE` ligne par ligne en boucle Python, déclenché une fois par SESSION
  Streamlit (donc à chaque nouvel onglet/utilisateur, pas une fois pour
  toutes). Corrigé : un seul `DELETE ... WHERE upper(left(vat_id,2)) = ANY(%s)
  AND ...` par table (comparaison d'ensemble côté SQL, plus de boucle Python),
  et la purge réelle n'est désormais tentée qu'une fois par jour au maximum —
  horodatage persisté dans une nouvelle table `vies_maintenance` (survit au
  scale-to-zero et aux redéploiements, contrairement à un garde en mémoire).

- **FIABILITÉ — signature MD5 tronquée (`app.py::_upload_sig`, confirmé)** —
  seuls les 128 premiers Ko du fichier étaient hashés ; une modification en
  fin de fichier (ex. correction d'un montant sur la dernière ligne d'un CSV
  de 100 Mo, taille inchangée) n'était pas détectée, risquant de réutiliser
  d'anciens résultats de parsing/calcul. Corrigé : le hash porte désormais
  sur les 128 premiers Ko **et** les 128 derniers Ko (coût toujours borné à
  256 Ko max, pas le fichier entier).

- **RAM — copies de listes répétées (`ui/tabs/telechargements.py::_get_results_net`,
  confirmé, ampleur nuancée)** — `results + refund_results` (nouvelle liste
  de ~100k références) était recréé à chaque appel ; plusieurs sections
  (aperçu OSS, correctifs négatifs) l'appellent sans clic de bouton. Le "8
  fois" de l'audit est surestimé (plusieurs appels vivent dans des callbacks
  de boutons, jamais tous exécutés le même run), mais au moins 2 appels
  inconditionnels par rendu de l'onglet restaient réels. Corrigé : mémoïsée
  via une closure locale au rendu de l'onglet (pas de `session_state`,
  ne vit que le temps du rendu).

- **CPU — initialisation Fernet répétée (`security.py::_get_fernet`,
  confirmé)** — l'objet `Fernet` (parsing/validation de la clé inclus) était
  recréé à chaque appel de `encrypt_data`/`decrypt_data`, coûteux sur un
  batch VIES de plusieurs centaines/milliers de numéros. Corrigé : instance
  mise en cache en singleton module-level.

- **REDONDANCE — double calcul du prix ASIN (`ca3_report.py`, confirmé)** —
  `_asin_avg_price_from_results` dupliquait à l'identique
  `excel_report.py::_build_asin_avg_price`. Corrigé : suppression de la copie,
  `ca3_report.py` délègue désormais à l'implémentation unique d'`excel_report.py`
  (source unique, plus de risque de drift entre les deux). Piste explorée et
  **différée** : partager le résultat déjà calculé par l'export Excel avec
  la génération CA3 dans le même run (via `session_state` keyé sur `calc_key`,
  comme le fait déjà l'aperçu OSS de `telechargements.py`) — gain réel mais
  modeste (les deux rapports sont généralement générés sur des clics
  séparés), pour une plomberie plus invasive à travers `app.py`/`telechargements.py`
  sans enjeu fiscal ; laissé de côté (reject > defer > patch).

- **PRÉCISION — taux de TVA sur AIC toujours STANDARD (`ca3_report.py::_compute_aic_from_fc_transfers`,
  confirmé)** — le calcul de la TVA sur les transferts de stock (AIC)
  appliquait systématiquement le taux standard du pays vendeur, sur-évaluant
  base et TVA AIC affichées sur le rapport pour les ASIN à taux réduit
  (livres, alimentaire...) — neutre au global (autoliquidation immédiate en
  Ligne 20) mais trompeur ligne à ligne sur le mémo B2/L17. Corrigé : nouvelle
  `_asin_category_map` déduit la catégorie produit connue par ASIN à partir
  des ventes de la période, utilisée pour appliquer le taux réel via
  `vat_rate(pays, catégorie)` ; repli sur STANDARD conservé si l'ASIN
  n'apparaît dans aucune vente connue (comportement précédent inchangé dans
  ce cas précis).

- **NETTOYAGE — fermetures de connexions redondantes (`app.py`, point
  vérifié et écarté)** — l'audit proposait de ne garder qu'un seul appel à
  `close_idle_connections` sur les 4 modules. Vérification faite : les 4
  modules (`auth.py`, `billing.py`, `ecb_rates.py`, `vies_engine.py`)
  délèguent déjà tous à un unique `database.close_idle_connections()` opérant
  sur un `_shared_pool` commun (voir fix `cache_connection=True` du
  2026-08-02) — les 3 appels "en trop" sont des no-ops volontaires et bon
  marché (un simple test `if _p is not None`), déjà documentés comme tels
  dans le code. Aucune modification nécessaire.

Validation : `py_compile` sur les 6 fichiers modifiés (`engine.py`, `app.py`,
`vies_engine.py`, `security.py`, `ca3_report.py`,
`ui/tabs/telechargements.py`) + suite `pytest` complète — 166 passed / 4
failed, échecs strictement identiques au baseline avant patch (3 liés à
`SUPABASE_DB_URL` absente en sandbox, 1 test parser insensible à la casse
déjà cassé avant ces patches) — aucune régression introduite.

## Audit externe — seuil OSS B2B, ASIN, purge VIES, overrides check_vat_raw (2026-08-15)

Revue de 8 points soumis par un audit externe. Chaque point vérifié sur le
code réel (`dev`) avant tout patch, conformément à la règle « jamais deviner
le code par déduction ».

- **BUG FISCAL — seuil OSS incomplet pour le B2B reclassé (`engine.py`,
  point écarté, faux positif)** — l'audit affirmait que `_oss_eligible` ignore
  les ventes B2B transfrontalières requalifiées OSS_B2C (n° TVA invalide vers
  un pays hors art. 194). Vérification faite : `_oss_eligible` (L584-616)
  gère déjà explicitement ce cas, avec commentaire dédié expliquant la
  logique. Aucune modification nécessaire.

- **FIABILITÉ — purge des « malformés » trop restrictive (`vies_engine.py::purge_malformed_entries`,
  confirmé)** — la clause `upper(left(vat_id,2)) <> upper(substring(vat_id
  from 3 for 2))` excluait les doublons de préfixe identique (ex.
  `"FRFR12345..."`), issus du même bug historique que la procédure est censée
  nettoyer. Corrigé : suppression de la clause `<>` sur les deux tables
  (`vies_global_cache`, `vies_scope_cache`) — tout `vat_id` dont les 4
  premiers caractères forment deux codes pays UE valides consécutifs est
  désormais purgé, identiques ou non.

- **OPTIMISATION RAM — casse ASIN (`models.py`, point vérifié et écarté)** —
  l'audit signalait un double lookup (`asin_to_category.get(asin)` puis
  `.get(asin.upper())`) en L863-864 d'`engine.py`, le catalogue (`sidebar.py`)
  étant normalisé en majuscules. Confirmé, mais sans impact mémoire réel :
  `sys.intern` empêche déjà toute duplication RAM sur l'ASIN lui-même : au
  pire un second lookup dict en cas de miss (coût négligeable). Normaliser la
  casse dans `models.py::Sale.__post_init__` risquerait de désynchroniser
  d'autres usages en aval (commentaire explicite du code à ce sujet). Laissé
  de côté (reject > defer > patch).

- **PERFORMANCE — propriétés calculées répétées (`report.py::ReportSummary`,
  point différé)** — l'audit proposait `functools.cached_property` sur
  `net_oss_by_country`/`net_local_by_country`. `ReportSummary` est un
  `@dataclass(slots=True)` (choix mémoire assumé, cohérent avec les 4 autres
  dataclasses du même fichier) : `cached_property` ne fonctionne pas nativement
  avec `__slots__` sans ajouter à la main les slots de cache correspondants,
  ce qui complexifie le dataclass pour un gain non mesuré. Différé — à
  instrumenter (profilage réel sur l'onglet Visualisations) avant de patcher.

- **DETTE TECHNIQUE — prefetch BCE incomplet sur les autres parsers,
  point ignoré (hors périmètre)** — seul le parser Amazon est maintenu
  actuellement (Shopify/WooCommerce/AliExpress/Mirakl non utilisés,
  conformément à la règle de suivi de ce projet).

- **LOGIQUE VIES — cache-miss sur les overrides dans `check_vat_raw`,
  point écarté après vérification des appelants (faux positif corrigé)** —
  l'audit affirmait que le bouton « Revérifier » de l'UI VIES pouvait afficher
  un statut erroné car `check_vat_raw` ignore les overrides manuels. Trace des
  appelants réels : `check_vat_raw` n'est appelée **nulle part dans l'app**
  (`app.py`, `ui/`) — seulement dans les tests. Le bouton « Revérifier »
  (`vies_ui.py`) déclenche un `st.rerun()` qui refait tourner tout le pipeline
  `compute_all_with_vies`, lequel applique correctement les overrides
  (`engine.py` L1100-1147). Aucun impact utilisateur actuel ; `check_vat_raw`
  reste une fonction publique dont le comportement diverge du pipeline
  principal, mais sans conséquence en production. Non patché (reject).

- **PERFORMANCE — multiples passes O(n) sur les résultats
  (`ca3_report.py::_compute_aic_from_fc_transfers`, point écarté)** — l'audit
  proposait de fusionner `_asin_avg_price_from_results` et
  `_asin_category_map` en une seule passe. `_asin_avg_price_from_results`
  délègue déjà à `excel_report.py::_build_asin_avg_price` (source unique
  partagée entre les deux rapports, voir audit précédent du 2026-08). Une
  fusion casserait ce partage intentionnel pour un gain marginal (une passe
  de dict-building économisée, pas sur l'ensemble de `results`). Laissé de
  côté.

- **PERFORMANCE — tri chronologique déjà partiellement trié
  (`engine.py::compute_all_with_vies`, point écarté)** — gain jugé
  négligeable : Timsort (tri de Python) est déjà efficace sur des séquences
  partiellement ordonnées, et la clé de tri est une simple chaîne. Aucune
  modification.

Validation : `py_compile` sur `vies_engine.py` (seul fichier modifié) +
suite `pytest` complète — 166 passed / 4 failed, échecs identiques au
baseline (liés à `SUPABASE_DB_URL` absente en sandbox) — aucune régression
introduite. Aucun risque scale-to-zero (patch SQL pur, pas de connexion ni
thread persistant ajouté).

---

## Audit externe — 8 points (2026-08-15, revue complémentaire)

Nouvelle liste de 8 points soumis par un audit externe. Chaque point vérifié
sur le code réel (`dev`) avant tout patch. 5 points patchés, 3 écartés.

**Points écartés (déjà corrigés ou faux positifs) :**

- **Seuil OSS « fantôme » (`engine.py::_run_oss_loop`, faux positif)** —
  l'audit décrivait un cumul scalaire réinitialisé par changement d'année,
  fragile sur données multi-années entrelacées. Code déjà en l'état
  recommandé : `oss_ht_by_year: dict[str, Decimal]` sauvegarde le cumul de
  l'année sortante AVANT bascule (L869-870) et restaure la bonne valeur pour
  l'année entrante (L872) — robuste même en cas d'années entrelacées. Aucune
  modification.

- **Seuil OSS « limite locale » figée (`engine.py::_oss_threshold_display`,
  faux positif)** — l'audit demandait d'utiliser une table de contre-valeurs
  fixes par devise plutôt qu'un recalcul au taux BCE du jour.
  `OSS_THRESHOLD_FIXED_EQUIVALENTS` (`rates.py` L135) existe déjà et est
  déjà utilisé en priorité par `_oss_threshold_display` (L660), la
  conversion BCE n'intervenant que pour les devises hors de cette table.
  Aucune modification.

- **Libellés « MwSt »/« IVA » non reconnus (`ui/formatting.py::_smart_money_df`,
  faux positif)** — la détection par sous-chaîne française n'est qu'un filet
  de sécurité pour des colonnes non explicitement listées. Tous les
  appelants réels (`detail_ventes.py`, `vies_ui.py`, `declarations.py`,
  `audit.py`) passent déjà `money_cols=[...]` avec les libellés traduits via
  `_()` — aucune colonne monétaire affichée à l'utilisateur ne dépend de la
  détection par sous-chaîne. Aucun impact en DE/IT. Aucune modification.

**Points patchés :**

- **PERF — TTL VIES recalculé par ligne (`vies_engine.py::_db_get_scope_batch`
  / `_db_get_global_batch`)** — `_get_ttl_days` était déjà mise en cache par
  scope (pas de requête SQL répétée comme le supposait l'audit), mais restait
  appelée une fois par ligne du lot (lookup dict). `_is_expired` accepte
  désormais un paramètre optionnel `ttl_days` ; les deux fonctions de batch
  le calculent une seule fois avant la boucle et le passent à chaque appel.
  Comportement par défaut inchangé pour tout appelant qui ne fournit pas ce
  paramètre.

- **PERF CPU — regex non compilée (`vies_engine.py::_clean_vat_number`)** —
  `re.sub(r"[\s.\-]", "", ...)` appelée à chaque numéro de TVA nettoyé.
  Python met déjà en cache les patterns compilés (jusqu'à 512), donc gain
  réel proche de zéro, mais patché par hygiène : `_VAT_CLEAN_RE` compilée
  une fois au niveau module.

- **RAM — duplication ASIN catalogue (`ui/sidebar.py::_parse_catalog_bytes`)**
  — les clés ASIN du dictionnaire catalogue n'étaient pas internées
  (contrairement à `Sale.asin`, interné dans `models.py`), créant une
  seconde copie mémoire de chaque ASIN. `sys.intern()` appliqué aux clés en
  plus des valeurs (catégorie), qui l'étaient déjà.

- **UX/PERF — seuil « gros fichier » mal calibré (`app.py`)** —
  `_is_big_file` comparait `total_rows_sum` (lignes CSV brutes, transferts
  FBA et lignes ignorées comprises) à 20 000, déclenchant le chemin thread
  d'arrière-plan bien trop souvent pour des fichiers dont le volume de
  calcul fiscal réel est modeste. Basé désormais sur
  `len(sales) + len(refunds)`, le volume qui alimente réellement
  `_run_oss_loop`/VIES.

- **PERF — double passe ASIN (`ca3_report.py::_compute_aic_from_fc_transfers`)**
  — un audit précédent (voir entrée du 2026-08-15 ci-dessus) avait écarté ce
  même point pour ne pas casser le partage intentionnel de
  `_asin_avg_price_from_results` avec `excel_report.py`. Cette fois,
  approche différente : `_asin_avg_price_from_results` et
  `_asin_category_map` restent intactes et partagées ailleurs ; une nouvelle
  fonction dédiée `_asin_avg_price_and_category` calcule les deux en une
  seule passe, utilisée uniquement dans `_compute_aic_from_fc_transfers` (le
  seul appelant ayant besoin des deux simultanément). Aucun autre appelant
  affecté.

Validation : `py_compile` sur chacun des 4 fichiers modifiés
(`vies_engine.py`, `ui/sidebar.py`, `app.py`, `ca3_report.py`) + suite
`pytest` complète — 166 passed / 4 failed, échecs identiques au baseline
(liés à `SUPABASE_DB_URL` absente en sandbox) — aucune régression
introduite. Aucun risque scale-to-zero (aucune connexion, thread ou polling
persistant ajouté ; tous les changements sont des optimisations locales de
calcul/mémoire).

---
> Il ne remplace pas un conseil fiscal professionnel.
> Les taux de TVA et seuils doivent être vérifiés et tenus à jour annuellement.


## Audit externe sécurité — 7 points (2026-08-16)

Audit de sécurité (isolation multi-tenant, chiffrement, verrouillage fiscal,
pseudonymisation). Chaque point vérifié sur le code réel (`dev`) avant tout
patch. 5 points patchés, 1 écarté (faux positif), 1 reporté (dépendance
avec un des correctifs de cette même session).

**Point écarté (faux positif) :**

- **Permissions fichiers temporaires (`app.py`, `telechargements.py`,
  `tempfile.NamedTemporaryFile(delete=False)`)** — l'audit craignait des
  fichiers temporaires lisibles par d'autres utilisateurs/process du même
  serveur selon l'umask du conteneur. Vérifié empiriquement : CPython crée
  ces fichiers via `mkstemp()` en interne, qui ouvre toujours avec le mode
  explicite `0o600` (confirmé par un test direct sur cet environnement).
  L'umask ne peut que *retirer* des permissions, jamais en ajouter — le
  risque décrit ne peut pas se matérialiser ici. Aucune modification.

**Point reporté (Defer) :**

- **`decrypt_data` fail-open sur préfixe `gAAAA` (`security.py`)** — pattern
  "fail-open" réel en théorie, mais actuellement un mécanisme de migration
  ACTIF : `refresh_token` (`auth.py`) et, depuis ce patch,
  `tva_number`/`ioss_number`/`vat_numbers_json` (`billing.py`) s'appuient
  explicitement sur cette tolérance pour déchiffrer sans échec les lignes
  déjà en base non encore migrées (pas de script de backfill prévu — la
  ré-écriture se fait au fil de l'eau). Retirer l'heuristique maintenant
  casserait la lecture des lignes existantes. Documenté en commentaire dans
  `security.py`, à reconsidérer une fois toutes les colonnes concernées
  effectivement migrées.

**Points patchés :**

- **Cache multi-tenant (`app.py::_cache_key`)** — `st.cache_data` est un
  cache global au process (pas par session). Deux comptes uploadant un
  fichier strictement identique avec les mêmes réglages fiscaux partageaient
  la même entrée de cache (`_build_rows_df`, `_aggregate_viz_raw` et les
  fonctions `_build_fig_*` de `visualisations.py`, toutes clées sur
  `ctx.calc_key`). `current_user.id` ajouté en tête de `_cache_key`,
  propagé automatiquement à `calc_key` et donc à toutes les fonctions
  `heavy_cache_data` qui en dépendent — isolation cryptographique par
  utilisateur, sans toucher aux fonctions elles-mêmes.

- **SIREN/IOSS/n° TVA locaux en clair en base (`billing.py::register_siren`
  / `list_registered_sirens`)** — seul `company_name` passait par
  `encrypt_data`. `tva_number`, `ioss_number` et `vat_numbers_json`
  chiffrent désormais de la même façon à l'écriture et sont déchiffrés à la
  lecture. Repose sur la tolérance `decrypt_data` (voir point reporté
  ci-dessus) pour les lignes existantes déjà en base — aucune migration à
  froid nécessaire, ré-écriture naturelle au prochain `register_siren`.
  Tous les appelants (`sidebar.py`, `billing_gate.py`) passent exclusivement
  par ces deux fonctions — aucun autre point de lecture directe en base
  vérifié.

- **Verrouillage fiscal UI-only (`billing.py::register_siren`,
  `ON CONFLICT ... DO UPDATE`)** — le formulaire (`sidebar.py`) masque déjà
  les champs `tva_number`/`ioss_number` une fois renseignés, mais la requête
  SQL écrasait sans condition via `EXCLUDED.*`. Ajout d'un `CASE` conservant
  la valeur déjà enregistrée si non vide, quel que soit ce qui est passé en
  paramètre — verrouillage désormais garanti même en cas d'appel direct hors
  UI (bug de script, appel API). `vat_numbers_json` volontairement **non**
  verrouillé ainsi : son usage légitime est d'ajouter un nouveau pays au fil
  du temps (le verrouillage par pays déjà rempli reste géré côté UI).

- **Pseudonymisation réversible (`vies_engine.py::anonymize_and_retain_scope_history`)**
  — hash SHA-256 non salé de `scope_id` (contient l'e-mail en clair),
  recalculable par dictionnaire d'e-mails. Sel secret dédié
  `PSEUDONYMIZATION_SALT` (variable d'environnement, distincte de
  `ENCRYPTION_KEY`) ajouté avant hachage. Repli sur un sel constant non
  secret si la variable n'est pas configurée (avec avertissement loggé) :
  la pseudonymisation reste toujours appliquée plutôt que de lever une
  exception qui bloquerait la suppression de compte.

- **Isolation VIES par domaine (`vies_engine.py::resolve_scope_id`)** —
  point partiellement invalidé : l'exemple cité par l'audit (`orange.fr`)
  est déjà dans `PERSONAL_EMAIL_DOMAINS` (isolé par compte, pas mutualisé).
  Le risque théorique général (grande organisation non listée partageant
  son historique VIES par domaine) reste réel mais correspond à un choix de
  design intentionnel (mutualisation cabinet comptable) — traiter le cas
  général relève d'une décision produit (opt-out, liste blanche), hors
  périmètre d'un correctif de sécurité ponctuel. Documenté en docstring,
  aucune modification de comportement.

**Validation :** `py_compile` sur les 4 fichiers modifiés (`billing.py`,
`vies_engine.py`, `security.py`, `app.py`) + suite complète `pytest` :
166 passed / 4 failed, baseline strictement inchangée (échecs pré-existants
liés à l'absence de `SUPABASE_DB_URL` en sandbox).

**Variable d'environnement à ajouter en production (Railway) :**
`PSEUDONYMIZATION_SALT` (chaîne aléatoire secrète, distincte de
`ENCRYPTION_KEY`) — fonctionne sans, mais avec un sel de repli non secret.

## Clôture point 2 — retrait du fail-open de `decrypt_data` (2026-08-16)

Suite au backfill (`backfill_encrypt_pii.py`, exécuté en production par
Matthieu, `--apply`) confirmant qu'il ne restait plus aucune ligne en clair
sur `tva_number` / `ioss_number` / `vat_numbers_json`
(`tva_siren_registrations`) ni `refresh_token`
(`tva_amazon_credentials`) : le fail-open de `decrypt_data` (`security.py`)
est retiré. Toute valeur ne commençant pas par `gAAAA` lève désormais une
`ValueError` explicite au lieu d'être retournée telle quelle.

Commentaires mis à jour en conséquence dans `auth.py` (docstring
`get_amazon_credentials`) et `billing.py` (docstring `register_siren`), qui
faisaient référence à l'ancienne tolérance.

**Impact opérationnel** : si une ligne en clair réapparaissait (nouvelle
colonne future non passée par `encrypt_data` avant écriture, restauration
d'un backup pré-chiffrement, etc.), la lecture échoue bruyamment
(`ValueError`) au lieu d'être silencieusement acceptée en clair — c'est le
comportement recherché.

**Validation** : `py_compile` sur `security.py`, `auth.py`, `billing.py` +
suite complète `pytest` : 166 passed / 4 failed, baseline strictement
inchangée (échecs pré-existants liés à l'absence de `SUPABASE_DB_URL` en
sandbox).

---

**Audit externe sécurité — 7 points : clôture.** Les 7 points sont
désormais tous traités : 5 patchés, 1 écarté (faux positif confirmé), 1
initialement reporté puis patché après backfill (ce point 2). Aucun point
restant en attente sur cet audit.

## Audit externe sécurité — 6 points, injections & fuites (2026-08-16, suite)

Second audit de sécurité le même jour (XSS, injection de formule Excel/CSV,
open redirect, fuite de logs, durcissement cookie). Chaque point vérifié sur
le code réel (`dev`) avant tout patch. 5 points patchés, 1 documenté sans
modification de code (limitation structurelle d'une dépendance tierce).

**Points patchés :**

- **Injection de formule Excel/CSV (`excel_report.py`, `oss_export.py`)** —
  les valeurs texte issues du fichier Amazon importé (`display_id`/`sale_id`,
  ASIN, désignation produit, numéro de TVA acheteur, nom retourné par VIES)
  étaient écrites brutes dans les cellules `WriteOnlyCell`. Un champ
  commençant par `=`, `+`, `-`, `@`, tabulation ou retour chariot peut être
  interprété comme une formule par Excel/LibreOffice/Google Sheets à
  l'ouverture par l'expert-comptable (CSV/Formula Injection, OWASP). Ajout
  d'un helper `_safe()` (dupliqué dans les deux fichiers — pas de dépendance
  croisée voulue entre eux) qui préfixe d'une apostrophe toute chaîne
  commençant par un de ces caractères, appliqué à chaque point d'écriture de
  donnée importée identifié : onglets Détails, Audit, Historique VIES,
  Intrastat, FBA/AIC (`excel_report.py`) ; `B2bLine` (source commune de
  l'onglet B2B Excel et du CSV B2B) et détails OSS/IOSS (`oss_export.py`).
  Les formules internes construites par l'appli (ex. `=E5+F5` dans les
  totaux) ne passent jamais par `_safe()` — vérifié explicitement, aucun
  appel accidentel introduit.

- **XSS via données CSV (`ui/tabs/vies_ui.py`)** — le tableau des
  reclassifications VIES manuelles affiche les `display_id`/`sale_id` du
  fichier importé via `st.markdown(..., unsafe_allow_html=True)` sans
  échappement. `html.escape()` appliqué à `_ov_vat2`, aux `display_ids`
  listés (`_ov_sales2`) et à `_ov_date_str2` (défense en profondeur) avant
  insertion dans le bloc HTML.

- **Injection CSS/XSS via nom d'entreprise (`ui/billing_gate.py`)** — la clé
  de widget du bouton de paiement (`_btn_key`) intègre `file_name`, qui
  contient `nom_entreprise` (saisie libre), injectée ensuite brute dans un
  bloc `<style>` via `unsafe_allow_html=True`. `_btn_key` est désormais dérivé
  d'un hash SHA-256 tronqué (`period_label` + `file_name`) plutôt que de la
  chaîne utilisateur directement — la clé n'a besoin que d'être stable et
  unique, pas lisible.

- **Open Redirect via header Host (`ui/auth_flow.py::_resolve_app_base_url`)**
  — en l'absence du secret `APP_BASE_URL`, le header `Host` (falsifiable
  selon la configuration du reverse proxy) était utilisé sans vérification
  pour construire les URLs de redirection OAuth/Stripe. Ajout d'une fonction
  `_is_trusted_host()` avec allowlist (`localhost`/`127.0.0.1`, suffixes
  `.streamlit.app`, `.up.railway.app`, `.railway.app`) : un Host hors de
  cette liste est ignoré et l'appli retombe sur le fallback historique
  plutôt que de faire confiance à une valeur arbitraire. Rendre
  `APP_BASE_URL` strictement obligatoire (erreur si absent) a été écarté :
  cela casserait le comportement actuel qui permet de déployer sur un
  nouvel environnement sans configuration manuelle immédiate — l'allowlist
  couvre le risque réel (redirection vers un domaine tiers) sans ce
  compromis.

- **Fuite d'information dans les logs du webhook Stripe
  (`vercel_webhook/api/stripe_webhook.py`)** — le traceback complet était
  systématiquement envoyé sur stderr en cas d'erreur ; `str(exception)` peut
  dans certains cas (ex. erreur de parsing JSON citant le contenu fautif)
  inclure un fragment du payload (donc potentiellement des données client).
  Par défaut, seul un message générique + type d'exception + `event_id`
  Stripe (extrait en best-effort du payload, non fiable puisque non vérifié
  à ce stade — utilisé uniquement pour corrélation avec le Dashboard Stripe,
  jamais pour une décision) est désormais loggé. Le traceback complet reste
  disponible via la variable d'environnement `STRIPE_WEBHOOK_DEBUG_LOGS=1`, à
  réserver à un environnement de test/staging Vercel.

**Point documenté sans patch (limitation structurelle) :**

- **Durcissement des cookies de session (`ui/auth_flow.py`, cookie
  `tva_session_token`)** — le composant tiers `stx.CookieManager`
  (streamlit-cookies-manager) pose le cookie côté JavaScript, ce qui exclut
  par nature l'attribut `HttpOnly`. Non patchable en code applicatif sans
  changer de composant (refactor hors périmètre d'un correctif ponctuel).
  Mesures compensatoires recommandées au niveau infrastructure (headers
  `Strict-Transport-Security` et `Content-Security-Policy` sur Railway,
  durée de vie de cookie réduite si le contexte le permet) plutôt qu'un
  changement de code.

**Validation :** `py_compile` sur les 7 fichiers modifiés (`excel_report.py`,
`oss_export.py`, `ui/tabs/vies_ui.py`, `ui/billing_gate.py`,
`ui/auth_flow.py`, `billing.py`, `vercel_webhook/api/stripe_webhook.py`) +
suite complète `pytest` : 166 passed / 4 failed, baseline strictement
inchangée (échecs pré-existants liés à l'absence de `SUPABASE_DB_URL` en
sandbox). Sanitizer `_safe()` validé par test manuel sur des payloads
`=`, `+`, `-`, `@` ainsi que sur des valeurs `None`/numériques (aucune
altération).

## Bugfix — plan non mis à jour lors d'un changement d'offre via le Portail client Stripe (2026-08-16, suite)

**Symptôme rapporté** : passage de l'offre Pro (annuel) à Cabinet (annuel),
quantité 3 SIREN, via le Portail client Stripe. Paiement, event Stripe et
log Vercel tous corrects (webhook reçu, `200 OK`). Mais l'app continuait
d'afficher l'abonnement Pro, et Supabase confirmait : ligne `tva_subscriptions`
avec `plan='business'` (Pro) et `siren_quantity=3` — une quantité de 3
n'a pourtant aucun sens pour le plan Pro (limité à 1 SIREN), incohérence qui
a permis de repérer le bug.

**Cause** : le handler `customer.subscription.updated`
(`billing.py::handle_stripe_webhook_event`) lisait le plan depuis
`event["data"]["object"]["metadata"]["plan"]` — la metadata de l'objet
Subscription Stripe, posée une seule fois au moment du Checkout initial.
Un changement de plan effectué depuis le Portail client Stripe modifie bien
le `price_id` du `SubscriptionItem` (d'où la quantité correctement mise à
jour, extraite dynamiquement de l'item par `_extract_subscription_item_details`)
mais **ne touche jamais** à cette metadata figée côté Subscription — Stripe
ne la resynchronise pas automatiquement lors d'un switch de prix via le
portail. Un mécanisme d'inférence du plan depuis le `price_id` existait déjà
dans `_fulfill_checkout_session` (pour le flux Pricing Table externe, sans
metadata), mais n'était jamais appelé dans le handler
`customer.subscription.updated`.

**Correctif** : deux fonctions extraites (`_plan_from_price_id`,
`_first_item_price_id`) pour partager la logique de résolution entre
`_fulfill_checkout_session` et le handler `customer.subscription.updated`.
Ce dernier dérive désormais le plan **en priorité depuis le `price_id`
réellement actif** sur l'abonnement (source de vérité, reflète tout
changement fait via le portail), avec repli sur la metadata uniquement si ce
`price_id` ne correspond à aucun des 4 price_id configurés (ex. prix legacy
retiré de la configuration) — pour ne pas régresser un cas imprévu où
l'inférence échouerait.

**Portée du bug** : tout changement de plan (Pro↔Cabinet) effectué par un
client via le Portail client Stripe était affecté, silencieusement — le
paiement et la quantité étaient corrects, seul le plan restait figé sur
l'ancienne valeur, ce qui pouvait bloquer l'accès aux fonctionnalités du
nouveau plan ou laisser un plan Pro accepter plusieurs SIREN dans les
données sans que l'UI ne le permette (incohérence constatée, pas
d'exploitation du plan Cabinet malgré paiement).

**Action recommandée pour les comptes déjà impactés** : identifier en base
les lignes `tva_subscriptions` où `siren_quantity > 1` et `plan='business'`
(incohérence structurelle, le plan Pro étant limité à 1 par construction
UI) — ce sont des comptes ayant changé de plan via le portail avant ce
correctif, à corriger manuellement (`plan='cabinet'`) ou en renvoyant
l'event `customer.subscription.updated` correspondant depuis le Dashboard
Stripe (Developers > Events > Resend) une fois le correctif déployé.

**Validation** : `py_compile` sur `billing.py` + suite complète `pytest` :
166 passed / 4 failed, baseline strictement inchangée. Test manuel de
`_plan_from_price_id`/`_first_item_price_id` sur un event simulé
(metadata="business" périmée, price_id pointant vers Cabinet annuel) :
plan résolu = "cabinet" (attendu), et vérification du repli sur la metadata
quand le price_id est inconnu.

## Audit typage IDE — 11 fichiers, faux positifs rejetés + 7 corrections (2026-08-16, suite)

Revue systématique des avertissements de typage remontés par l'IDE sur 11
fichiers (`classify.py`, `constants.py`, `loader.py`, `billing_gate.py`,
`formatting.py`, `sidebar.py`, `declarations.py`, `detail_ventes.py`,
`telechargements.py`, `vies_ui.py`, `visualisations.py`), avec vérification
de chaque point contre le code réel avant toute action.

**Corrections appliquées (vrais soucis de typage, aucun bug runtime actif) :**

1. **`loader.py`** — `_read_and_prepare_rows()` retournait `object` au lieu
   de `_RowParser` pour le parser détecté, ce qui masquait toute vérification
   statique sur `parser.tx_type/departure/arrival/...` dans `_process_rows()`
   et `load_amazon_report()`. Retypé en `tuple[list, int, _RowParser]`.
   Signature `progress_callback` de `_process_rows` harmonisée avec celle de
   `load_amazon_report`/`_bce_cb` (3ᵉ paramètre `label` optionnel) — les deux
   déclaraient des `Callable` incompatibles pour le même objet transmis tel
   quel ; aucun bug actif tant que seul le callback 2-arg y transitait, mais
   fragile en cas de futur branchement du callback 3-arg sur ce chemin.
2. **`classify.py`** — `BuyerClassification.buyer_type` et le paramètre
   `BuyerType` de `classify_buyer()` étaient typés `object` pour éviter
   l'import circulaire réel avec `models.py`. Typés proprement via
   `TYPE_CHECKING` (import réservé au vérificateur de types, jamais exécuté
   au runtime) — l'anti-circularité est préservée à l'identique.
3. **`constants.py`** — `_PATTERNS` (dans `is_national_tax_id`) était typé
   `dict[str, object]` alors que ses valeurs sont des listes de callables.
   Retypé `dict[str, list[Callable[[str], bool]]]`.
4. **`billing_gate.py`** — `pay_eu`, `all_stock_countries` et
   `all_account_identifiers` (paramètres de `build_billing_gate`) n'étaient
   pas typés, ce qui cassait l'inférence de `sorted()` sur ces ensembles et
   provoquait des faux mismatches lors des lookups `dict[str, ...]` et de la
   construction de `list[tuple[str, str]]` en aval. Typés `set[str] |
   Iterable[str]`, `set[str]` et `Optional[set[str]]`.
5. **`formatting.py`** — 7 paramètres annotés avec un type non-Optional mais
   un défaut `None` (`_gated_preview_table`, `_smart_money_df`) : passés en
   `Optional[...] = None`. Les annotations de retour `-> st.column_config.
   NumberColumn` de `_money_col`/`_pct_col` étaient invalides
   (`NumberColumn` est une fonction factory Streamlit, pas une classe) :
   annotations retirées.
6. **`declarations.py`** — `.astype(object)` (type Python) faisait échouer
   la résolution des overloads pandas et retombait sur `Never`, rendant
   `_recap_preview.columns` invisible pour l'IDE. Remplacé par
   `.astype("object")` (chaîne) — comportement runtime strictement
   identique.
7. **`vies_ui.py`** — dans le bloc `try/except` d'import optionnel des
   overrides manuels VIES, les noms `_smo_edit`/`_dmo_edit`/
   `_vies_is_expired_b` n'étaient définis que si l'import réussissait ; la
   garantie qu'ils ne soient jamais appelés en cas d'échec reposait
   implicitement sur l'atomicité de l'import + un garde-fou plus bas
   (`if _existing_overrides_b:`). Toujours correct au runtime, mais fragile
   pour un futur ajout de code hors de ce garde-fou. Fallback explicite
   ajouté dans le bloc `except`.

**Faux positifs identifiés et volontairement laissés en l'état** (limites
des stubs pandas/plotly/Streamlit ou du narrowing de l'IDE sur les
fermetures `nonlocal`), avec justification consignée dans ce changelog
plutôt que silencieusement ignorés :
- `classify.py` L196 : narrowing de `tx_date: _date | None` non suivi par
  l'IDE après le bloc `if tx_date is None: tx_date = ...` (garantie
  correcte au runtime).
- `loader.py` : `to_dict("records")` (ambiguïté de stub pandas sur l'union
  de retour selon l'`orient`), et l'écriture volontaire de `None` dans
  `rows_to_process[idx]` pour libération RAM (documentée sur place).
- `billing_gate.py` : `stripe_success_url`/`stripe_cancel_url` typés `Any`
  mais defaultés à `field(default=None)` — quirk connu de l'inspecteur
  PyCharm sur `dataclasses.field()`, qui infère parfois depuis le défaut
  plutôt que depuis l'annotation explicite.
- `sidebar.py` L567 : `format_func` de `st.selectbox` typé `Any | None` par
  le stub Streamlit, mismatche avec le `dict[str, str]` correctement typé
  `_siren_label_by_value` — sans risque au runtime (`v` est toujours une
  chaîne SIREN de la liste d'options).
- `declarations.py`/`detail_ventes.py` : `sorted()` sur des dicts non
  paramétrés (`_aggregate_declarations_raw() -> dict`) et `.rename()`/
  `.sort_values()` sur un `DataFrame` sélectionné via une liste de colonnes
  construite par `.append()`/`+=` — ambiguïtés de stubs pandas sur
  l'overload `__getitem__`/`to_dict`, sans impact runtime.
- `telechargements.py` : `_results_net_cache: list | None` réassigné dans
  une fermeture `nonlocal` — l'IDE ne suit pas la garantie de non-`None` à
  travers la fermeture (même limite que le point `classify.py` ci-dessus).
- `visualisations.py` (L153, 256, 261, 286) et `vies_ui.py` (L394) :
  `go.Bar(marker_color="#hex"/dict.get(...))` — les stubs auto-générés de
  Plotly ne reconnaissent pas toujours `marker_color` comme acceptant un
  `str` scalaire (limite connue de ces stubs).
- `vies_ui.py` L146 : aucune correspondance dict/str trouvée dans le code
  réel à cet endroit après vérification — signalé pour clarification si le
  point persiste dans l'IDE.

**Point hors périmètre constaté (non corrigé ici)** : `pytest` sur `dev`
remonte désormais 174 passed / 4 failed (contre 166/4 au dernier baseline
enregistré) — le test supplémentaire en échec,
`test_parsers.py::TestAmazonParserWrapper::test_parse_via_parsers`
(`'Amazon' == 'amazon'`, casse du champ `platform`), est confirmé
pré-existant sur `dev` **avant** les corrections de cette session
(reproduit sur un clone vierge du repo) — régression indépendante à traiter
séparément, aucune modification apportée ici.

**Validation** : `py_compile` sur les 7 fichiers modifiés + suite complète
`pytest` : 174 passed / 4 failed, identique au repo vierge (aucune
régression introduite par ces corrections).

## 2026-08-17 — Revue d'erreurs IDE fichier par fichier (auth/billing/cli/database/engine/excel_report/oss_export/oss_xml/security/vies_engine/app.py)

Revue systématique de la liste d'erreurs signalées par l'IDE sur 11 fichiers,
chaque point vérifié sur le code réel (fetch GitHub `dev`), classé en bug
réel / cosmétique-type / faux positif, avant tout patch.

**Bugs réels corrigés (2) :**

- `engine.py` (L1164) : `vies_summary.manual_override_count += 1` levait
  `AttributeError` au premier override manuel VIES rencontré —
  `ViesValidationSummary` (`slots=True`, models.py) n'a pas ce champ,
  seulement `manual_valid_count`/`manual_invalid_count` et la property
  `total_manual_override` qui en fait déjà la somme. Ligne supprimée
  (remplacée par un commentaire explicatif) ; `manual_valid_count`/
  `manual_invalid_count` juste en dessous continuent d'être incrémentés
  normalement, `total_manual_override` reste donc correct.
- `cli.py` (L218) : `export_xlsx(results, args.xlsx, summary=summary)`
  omettait le paramètre obligatoire `scope_id`, provoquant un `TypeError`
  systématique dès `--xlsx` utilisé en CLI. Corrigé en passant
  `scope_id=_CLI_VIES_SCOPE_ID` (déjà utilisé plus haut dans le fichier pour
  le même besoin).

**Cosmétique / annotations de type corrigées (aucun impact runtime), 5 points :**

- `auth.py` : `import psycopg2` inutile retiré ; annotation
  `_init_schema(pool: psycopg2.pool.AbstractConnectionPool)` remplacée par
  `NonPoolingConnectionPool` (classe maison réellement utilisée) — l'import
  `psycopg2.pool` devenu inutile a aussi été retiré.
- `billing.py` : `import psycopg2` et `import psycopg2.pool` inutiles retirés
  (seule une docstring en texte les mentionnait).
- `engine.py` : `refund_keys: set[tuple[str, str]]` corrigé en
  `set[tuple[str, Decimal]]` (incohérent avec `_sale_key()` et la ligne
  1009 utilisant déjà la bonne annotation) ; `asin_to_category: dict[str,
  str] = None` corrigé en `dict[str, str] | None = None` (le `None` est
  bien géré au runtime, seule l'annotation mentait).
- `oss_export.py` : `_build_oss_resume(data: OssExportData, ...)` élargi en
  `data: "OssExportData | IossExportData"` — réutilisation volontaire et
  documentée par `_build_ioss_resume()` (le paramètre `lines=` est toujours
  fourni sur ce chemin, `data.oss_by_country` n'est jamais lu), le typage
  reflète maintenant l'usage réel.
- `app.py` : callback de progression VIES (petit fichier) renvoyait le
  `DeltaGenerator` de `st.progress()` au lieu de `None` attendu par la
  signature du callback — valeur de toute façon ignorée par l'appelant,
  corrigé pour forcer un retour `None` explicite.

**Faux positifs identifiés, non modifiés (limites IDE/stubs, aucun risque
runtime confirmé par lecture du code réel) :**

- `excel_report.py` / `oss_export.py` : `'Cell' has no attribute
  font/fill/alignment/number_format/border` — vient du helper
  `WriteOnlyCell` déjà en place (conforme aux règles write-only : largeurs
  avant `append()`, pas de `merge_cells()` classique) ; aucun `ws.cell()`
  post-append trouvé qui casserait réellement le mode write-only.
- `oss_xml.py` (L369) : `toprettyxml(..., encoding="utf-8")` retourne bien
  des `bytes` au runtime (cohérent avec la signature `-> bytes` déclarée) ;
  stub typeshed de `xml.dom.minidom` imprécis (ignore l'effet du paramètre
  `encoding` sur le type de retour).
- `security.py` : narrowing de `_fernet_singleton: Fernet | None` perdu par
  l'inspecteur sur une variable module-level réassignée via `global` — la
  logique garantit un `Fernet` non-`None` à chaque `return` de
  `_get_fernet()`.
- `vies_engine.py` (L1610-1630) : `dict[str, tuple[ViesResult, bool]]` bien
  déclaré et respecté par `_db_get_scope_batch`/`_db_get_global_batch` ;
  élargissement en union par l'inspecteur sur l'indexation de tuple
  hétérogène (`tuple[X, Y].__getitem__(int)`), limite connue de certains
  checkers.
- `app.py` : les 4 `results`/`refund_results`/`summary`/`oss_summary`
  "can be undefined" viennent toutes de code situé après un `st.stop()`
  — Streamlit ne type pas `st.stop()` en `NoReturn`, l'inspecteur ne sait
  donc pas que le flux s'arrête réellement à cet endroit.
- `app.py` : `getvalue`/`name`/`size` vs `bytes | Any` sur les fichiers
  uploadés — stubs Streamlit imprécis sur `file_uploader()` (retourne
  toujours des `UploadedFile` en usage normal ici, jamais de `bytes` bruts).

**Point hors périmètre, non traité ici** : `database.py` (`Optional[...]`
sur le pool partagé) suit le même schéma que `auth.py`/`billing.py`
ci-dessus — pas d'annotation à corriger côté `database.py` lui-même, le
type déclaré (`Optional["NonPoolingConnectionPool"]`) est correct pour un
singleton lazy-initialisé.

**Validation** : `py_compile` OK sur les 6 fichiers modifiés
(`auth.py`, `billing.py`, `cli.py`, `engine.py`, `oss_export.py`,
`app.py`) ; suite complète `pytest` : 174 passed / 4 failed — identique au
repo vierge (3 échecs confirmés pré-existants indépendamment de cette
session : `SUPABASE_DB_URL` absente en sandbox ×2 côté VIES + casse
`platform` côté parseur Amazon ; 1 échec supplémentaire lié à
`SUPABASE_DB_URL` sur `test_check_vat_raw_valid`). Aucune régression
introduite.

## 2026-08-17 — Audit sécurité complet + migration DictCursor (`vies_engine.py`/`billing.py`)

**Audit sécurité complet** (connexion, chiffrement, données, exports) — aucune
faille exploitable trouvée. Points vérifiés et confirmés déjà couverts :

- **Injection SQL** : 100% des requêtes paramétrées (`%s`), aucune
  concaténation/f-string dans un `execute()` sur l'ensemble du dépôt.
- **Isolation multi-tenant** : toutes les requêtes `billing.py` (customers,
  subscriptions, credits, SIREN) filtrent systématiquement par `user_id`.
  Aucun IDOR détecté.
- **Chiffrement PII** (`security.py`) : Fernet fail-closed (fail-open retiré
  le 2026-08-16), clé jamais en dur.
- **Formula injection (Excel/CSV)** : `_safe()` neutralise systématiquement
  les champs non fiables (display_id, ASIN, désignation, nom/erreur VIES)
  dans `excel_report.py`/`oss_export.py`. `piece_ref` du FEC provient d'un
  libellé i18n contrôlé par l'app, pas d'un nom de fichier utilisateur —
  pas de risque.
- **XSS** : seul point à risque (display_id/sale_id dans `vies_ui.py`) déjà
  protégé par `html.escape()` avant `unsafe_allow_html`. Les autres usages
  d'`unsafe_allow_html` portent uniquement sur du contenu généré
  côté serveur (CSS, grille tarifaire Stripe).
- **CSRF / Open Redirect** : `_is_trusted_host()` (`ui/auth_flow.py`) valide
  déjà le header `Host` contre une liste blanche avant toute construction
  d'URL de redirection OAuth/Stripe.
- **Webhook Stripe** : signature vérifiée (`stripe.Webhook.construct_event`),
  détails d'erreur jamais renvoyés au client, logs verbeux uniquement si
  `STRIPE_WEBHOOK_DEBUG_LOGS=1`.
- **PKCE OAuth** : verifiers stockés côté serveur (jamais en cookie),
  fenêtre de grâce idempotente correcte, purge périodique.
- **Brute-force login** : rate-limit par hash d'IP (5 tentatives/5 min).
- **Clés Supabase** : seule la clé `anon` utilisée côté client, jamais
  `service_role`.
- **Réseau sortant** : tous les appels `requests` ont un `timeout` explicite,
  aucun `verify=False`.
- **Pas d'`eval`/`exec`/`pickle`/`os.system`/`subprocess`** sur tout le dépôt.

**Point secondaire relevé** : `amazon_spapi.py` (OAuth SP-API avec paramètre
`state`) — code mort, aucun appelant trouvé nulle part dans le dépôt.
Confirmé non utilisé par Matthieu → laissé tel quel, aucune action (pas de
suppression demandée).

**Migration DictCursor — `vies_engine.py` + `billing.py`** (dette technique
notée précédemment comme différée, traitée aujourd'hui) :

- `billing.py` et `vies_engine.py` partagent le même pool Postgres
  (`database.get_shared_pool`) — `vies_engine._ConnCtx` pointe vers ce même
  pool. Un seul changement dans `database.py`
  (`NonPoolingConnectionPool.getconn()`, les deux branches
  `cache_connection=True`/`False`) active `psycopg2.extras.DictCursor` par
  défaut sur toute connexion ouverte par le pool partagé — couvre donc
  `auth.py`, `billing.py`, `ecb_rates.py` et `vies_engine.py` d'un coup.
- Changement non cassant : `DictRow` hérite de `list`, donc `row[0]`,
  `row[1:8]` et le unpacking de tuple (`a, b, c = row`) continuent de
  fonctionner à l'identique. Confirmé sur `billing.py` (`dict(zip(cur
  .description, row))` en particulier, toujours valide).
- Conversion en accès par nom de colonne des points les plus fragiles dans
  `vies_engine.py` (index non contigu ou éloigné du `SELECT`) :
  `_db_get_scope_batch`, `_db_get_global_batch` (`row[0]`/`row[7]` →
  `row["vat_id"]`/`row["checked_at"]`), `get_vies_history`,
  `get_vies_history_bulk`, `get_vies_status_as_of` (mapping 7-8 colonnes).
- `billing.py` : accès `row[0]` existants tous sur des `SELECT`
  mono-colonne — faible fragilité, non convertis (churn sans gain réel).
- **Rejeté volontairement** : conversion des `SELECT *` / mono-colonne
  restants — gain marginal, risque de régression pour un bénéfice nul.

**Validation** : `py_compile` OK sur `database.py`, `vies_engine.py`,
`billing.py`, `auth.py` ; suite complète `pytest` : 174 passed / 4 failed —
identique à la baseline (échecs pré-existants liés à `SUPABASE_DB_URL`
absente en sandbox + casse `platform` côté parseur Amazon). Aucune
régression introduite.

## 2026-08-18 — Optimisations app.py : imports, extraction UI, st.status, CSS, state management

Suite à un audit externe proposant 5 points d'optimisation sur `app.py` et
modules associés. Chaque point vérifié sur le code réel (récupéré depuis
`codeload.github.com/Mister-MMDD/tva-intracom-6`, branche `dev`) avant
patch, conformément à la règle « ne jamais deviner le code par déduction ».

**1. Centralisation des imports (`app.py`)**

Tous les `import`/`from ... import` remontés en tête de fichier. Les
instructions exécutables intercalées avec les anciens imports
(`init_i18n()`, `logging.basicConfig()`, fermeture des connexions DB idle
avant `run_auth_flow()`, `apply_theme()`, `language_selector()`) sont
restées strictement à leur emplacement d'origine et dans le même ordre
relatif — seule la position des `import` a changé, jamais celle d'un appel
de fonction. Vérifié qu'aucun module importé n'a d'effet de bord au niveau
module dépendant de `init_i18n()`/`logging.basicConfig()` déjà exécutés
(loggers/constantes uniquement).

**2. Extraction fichiers uploadés → `tva_intracom/ui/files.py`**

`_CachedUploadedFile` et `_upload_sig` (cache compressé gzip + signature de
contenu des fichiers uploadés, pour survivre à un rerun Streamlit
"interne" sans re-décompresser/re-hasher) déplacés tels quels dans un
nouveau module. Usage confirmé exclusivement local à `app.py` avant
extraction (aucun autre fichier du dépôt n'y faisait référence).

**3. `st.progress` → `st.status` (parsing/calcul)**

Deux usages identifiés, traités différemment selon le risque :
- `app.py` (barre VIES, chemin synchrone/petit fichier) : remplacé par
  `st.status` avec mise à jour du label à chaque callback de progression.
- `tva_intracom/ui/background_calc.py` (`render_job_progress`, chemin gros
  fichier) : **volontairement non modifié**. Ce `st.progress` vit dans un
  `@st.fragment(run_every=0.4)` couplé à un thread de calcul en arrière-
  plan — architecture déjà identifiée comme zone sensible (voir points
  précédents de ce changelog). Gain UX jugé insuffisant pour justifier de
  toucher à ce mécanisme.

**4. CSS des KPI externalisé vers `theme.py`**

Le bloc `<style>` injecté à chaque rendu de la section KPI (`.kpi-card`,
`.kpi-label`, `.kpi-value`, `.badge-alert`) déplacé dans la constante
`_CSS` de `tva_intracom/ui/theme.py`, qui centralisait déjà tout le CSS de
l'application et n'est injectée qu'une seule fois par `apply_theme()` en
tête de script (au lieu d'être réinjectée à chaque interaction). Suit la
convention déjà en place plutôt que d'introduire un fichier `.css` externe
chargé depuis le disque.

**5. State management — dataclass `CalcCacheState`**
(`tva_intracom/ui/calc_cache.py`)

Cartographie complète effectuée avant toute modification (règle : bug
complexe / risque d'aller-retours → analyse d'abord). Couplages
inter-modules identifiés : `vies_ui.py` invalidait directement `_calc_key`
en 3 endroits (reclassification manuelle VIES), `sidebar.py` lisait
`_results` directement pour la détection de période. `TabContext.calc_key`
(dataclass déjà existante) laissé inchangé — bon pattern déjà en place,
réutilisé tel quel plutôt que dupliqué.

Nouvelle dataclass avec façade explicite (`load()`, `save_parse()`,
`save_calc()`, `save_period_sync_key()`, `save_vies_retry_nonce()`,
`invalidate_calc()`, `get_results()`) remplaçant les accès dispersés à
`_parse_cache_key`, `_parse_cache_data`, `_calc_key`, `_period_sync_key`,
`_results`, `_refund_results`, `_summary`, `_vies_summary`, `_oss_summary`,
`_vies_retry_nonce`.

Deux points de comportement intentionnellement préservés à l'identique
(documentés dans le module) :
- Parsing et calcul restent deux caches indépendants (clés de cache
  différentes) — pas de fusion qui forcerait un recalcul croisé inutile.
- `invalidate_calc()` n'efface QUE `calc_key`, jamais
  `results`/`summary`/`vies_summary`/`oss_summary` — les anciens résultats
  restent affichés jusqu'au recalcul suivant (évite un flash d'interface
  vide pendant une reclassification manuelle VIES). Comportement UX
  volontaire de l'ancien code, pas un oubli à "corriger".

**Rejeté / différé** : migration `pandas` → `polars` sur l'ensemble du
dépôt. Le hot path CSV (jusqu'à 100 Mo, `parsers/amazon/loader.py`) est
déjà en Polars, avec une chaîne de fallback intentionnelle et documentée à
3 niveaux (Polars → pandas → `csv.DictReader` stdlib) pour absorber des CSV
Amazon malformés — supprimer le fallback pandas ferait perdre ce filet de
robustesse pour un gain RAM quasi nul (le chemin nominal n'y passe jamais).
Les 9 autres usages de `pandas` (10 fichiers, 36 appels `pd.` au total)
sont côté affichage (`formatting.py`, tabs, `sidebar.py`,
`visualisations.py`) — une migration complète change la syntaxe partout
(`.groupby()`, indexation, `pd.notna()`...) pour un gain mémoire marginal
vu que les gros volumes sont déjà en Polars. Abandonné : gain incertain vs
effort et risque de régression sur des tabs de calcul fiscal.

**Résultat** : `app.py` réduit de 1101 à 986 lignes.

**Validation** : `python3 -m py_compile` OK sur tous les fichiers modifiés
(`app.py`, `tva_intracom/ui/files.py`, `tva_intracom/ui/calc_cache.py`,
`tva_intracom/ui/theme.py`, `tva_intracom/ui/sidebar.py`,
`tva_intracom/ui/tabs/vies_ui.py`) après chaque étape ; suite complète
`pytest` relancée après chacun des 5 points : 174 passed / 4 failed à
chaque fois — identique à la baseline (échecs pré-existants liés à
`SUPABASE_DB_URL` absente en sandbox). Aucune régression introduite sur
l'ensemble de la session.

## 2026-08-18 (2) — Audit libre : except silencieux et log incohérent

Nouvel audit sans point de départ externe, sur les fichiers pas encore
passés en revue lors des sessions précédentes (`auth.py`, `billing.py`,
`database.py`, `engine.py`, `vies_engine.py`, `parsers/amazon/loader.py`).
Code récupéré depuis `codeload.github.com/Mister-MMDD/tva-intracom-6`,
branche `dev`, avant tout diagnostic — aucune modification devinée.

**1. `billing.py` — webhook Stripe, fallback `customer.retrieve` (~L.1577)**

`except Exception: pass` avalait silencieusement tout échec du fallback
liant `customer_id` Stripe → `user_id` (via email), y compris pour des
causes autres qu'un simple aléa réseau (client Stripe supprimé, clé API
invalide/expirée). Un abonnement ne se retrouvant lié à aucun `user_id`
disparaissait sans trace exploitable en prod. Ajout d'un
`logger.warning(..., exc_info=True)` avant le `pass` — comportement de
fallback inchangé (toujours pas de levée d'exception), juste tracé.

**2. `billing.py` — `print()` isolé dans le webhook (~L.1564)**

Un seul `print()` au milieu d'un fichier qui utilise `logger.*` partout
ailleurs (paiement différé échoué/expiré). Pas un bug — ce webhook tourne
sur Vercel serverless (pas Railway, donc aucun risque scale-to-zero) — mais
incohérence de style/niveau de log. Remplacé par `logger.info(...)`.

**3. `auth.py` — PKCE, deux `except Exception: return None`**
(`consume_pkce_verifier`, `consume_latest_pkce_verifier_by_provider`)

Toute erreur DB pendant la consommation d'un verifier PKCE (callback OAuth
et flux "mot de passe oublié") était traduite silencieusement en "verifier
introuvable", indiscernable d'un cas normal (nonce expiré/absent). Une
vraie panne de connexion pendant un callback OAuth devenait donc un échec
de login silencieux sans aucune trace. Ajout de `logger.warning(...,
exc_info=True)` dans les deux fonctions avant le `return None` —
comportement de retour strictement inchangé (ne casse pas le flux OAuth
existant), juste tracé pour diagnostic a posteriori.

**4. `parsers/amazon/loader.py` — chaîne de fallback Polars → pandas →
csv.DictReader : vérifiée, pas de changement**

Hypothèse de départ (logs insuffisants sur bascule de fallback) invalidée
à la lecture du code réel : la chaîne loggue déjà correctement à chaque
niveau — `logger.debug` sur l'échec polars (bascule vers pandas, fréquente
et attendue sur des CSV Amazon malformés), `logger.warning` sur l'échec
pandas (bascule vers le dernier recours `csv.DictReader`, là où ça mérite
vraiment d'être investigué). Niveaux déjà correctement calibrés — point
refermé sans modification.

**Validation** : `python3 -m py_compile` OK sur `billing.py` et `auth.py`.
Suite complète `pytest` : 174 passed / 4 failed — identique à la baseline
(échecs pré-existants liés à `SUPABASE_DB_URL` absente en sandbox), aucune
régression.

## 2026-08-18 (3) — Nettoyage code mort (analyse statique pyflakes)

Suite de l'audit libre. Passage de `pyflakes` sur l'ensemble du dépôt pour
repérer variables/imports morts. Chaque signalement vérifié individuellement
sur le code réel avant toute suppression (plusieurs faux positifs écartés :
walrus operator dans `classify.py`, shadowing volontaire et documenté de
`i18n_` dans `excel_report.py`, `unused_results` en `fec_export.py`
explicitement commenté "Utilisation future", `net` en `excel_report.py`
déjà marqué `# noqa: F841` intentionnel).

**Résidus morts confirmés et supprimés :**
- `ca3_report.py` : `buyer_in_seller`, résidu d'un ancien filtre remplacé
  par le test `channel == Channel.FR_DOMESTIC` (voir bugfix DDP documenté
  juste au-dessus).
- `ui/tabs/declarations.py` : `oss_summary = ctx.oss_summary`, résidu de
  la migration vers `aggregate_oss_results()` recalculé directement dans
  cet onglet (voir commentaire en place expliquant la divergence avec
  `summary.oss_by_country`).
- `excel_report.py` : plusieurs listes `_vals*`/variables `_xxx_f`
  construites juste avant un `ws.append(...)` mais jamais relues (le
  `ws.append` réutilise directement `_conv(...)`/`float(...)`) — 4 sites
  distincts. `BLUE_FILL := _BLUE_HEADER_FILL` (walrus dont le nom n'était
  jamais lu) simplifié en passage direct. `col_net`/`letter_net`/`_vals`
  morts sur 2 sites (le total "net" est en réalité calculé par une formule
  Excel `=Brut+Remb` directement, jamais via ces variables). Nettoyage fait
  en deux passes : la suppression des `_vals*` a révélé des variables
  `_xxx_f` devenues mortes en cascade (seules `_pct_f` et les usages
  directs de `_conv()` servaient réellement) — re-scan pyflakes après
  chaque étape pour capturer cet effet domino avant validation finale.
- `oss_export.py` : `n = len(data.b2b_lines)`, jamais relu.
- `mem_utils.py` : `all_arenas = ctypes.c_uint(0xFFFFFFFF)`, jamais utilisé
  — le `mallctl` en dessous appelle déjà directement la chaîne en dur
  `b"arena.4294967295.purge"` (qui encode la même valeur), comportement de
  purge jemalloc inchangé.

Aucun changement de comportement fonctionnel dans tous les cas : uniquement
suppression de code strictement mort (valeurs jamais lues), jamais de
logique de calcul touchée.

**Validation** : `python3 -m py_compile` OK sur les 5 fichiers modifiés.
Re-scan `pyflakes` après nettoyage : plus aucun signalement hors les 3 faux
positifs volontairement conservés. Suite complète `pytest` : 174 passed / 4
failed — identique à la baseline (échecs pré-existants `SUPABASE_DB_URL`),
aucune régression. Sous-suite ciblée `excel`/`oss_export`/`fec` (12 tests)
également passée après le nettoyage, vu le volume de fonctions Excel
touchées.

## 2026-08-19 — Audit poussé des fonctions cœur (engine/rates/ca3/oss_export/vies_engine/excel_report) : 6 points corrigés

Audit ciblé sur les grosses fonctions de calcul métier (pas les fonctions
d'export Excel/mise en forme) : `engine.py` (`compute_vat`, `_run_oss_loop`,
`compute_all_with_vies`), `rates.py` (`vat_rate_at_date`), `ca3_report.py`
(`compute_ca3_lines_v2`, `_compute_aic_from_fc_transfers`), `oss_export.py`
(`aggregate_oss_results`, `suggest_negative_bucket_corrections`),
`vies_engine.py` (`validate_vat_numbers_parallel`), `excel_report.py`
(`_deadline_oss`). Aucun bug fiscal actif trouvé sur le chemin réellement
emprunté par l'application — tous les points ci-dessous sont des correctifs
de robustesse/lisibilité à faible risque, sans changement de comportement
observable aujourd'hui (sauf point 3, qui change un montant affiché dans de
rares cas).

**1. `rates.py::_HISTORY_INDEX` — tri par `date_from` réellement appliqué.**
Le commentaire affirmait l'index "trié par date_from" mais aucun tri
n'était effectivement exécuté — les périodes n'étaient conservées que dans
leur ordre d'apparition dans `VAT_RATE_HISTORY`. Sans impact aujourd'hui
(périodes déjà saisies chronologiquement et disjointes pour chaque couple
pays/catégorie), mais fragile : une future période insérée hors-ordre
(ex. correctif rétroactif ajouté en fin de liste) aurait pu faire retourner
un taux incorrect sans aucune erreur levée. Ajout d'un tri explicite
(`_periods.sort(key=lambda p: p.date_from)`) à la construction de l'index.

**2. `rates.py::vat_rate_at_date` — suppression d'une branche `elif` morte.**
`elif product_category.upper() in country_rates:` ne pouvait jamais être
atteinte : `cat` est déjà normalisé en tête de fonction vers la même forme
canonique testée par le `if` précédent, et `country_rates` contient de
toute façon les deux formes (FR et EN) comme clés directes. Branche
retirée, remplacée par un commentaire expliquant pourquoi elle était morte.

**3. `ca3_report.py::_compute_aic_from_fc_transfers` — taux AIC historisé.**
Le taux de TVA appliqué à l'AIC (transferts de stock FBA entrants) utilisait
`vat_rate()` sans `tx_date`, donc toujours le taux COURANT — contrairement à
`compute_vat()` qui applique le taux historique en vigueur à la date réelle
de la transaction (ex. EE 22%→24% au 01/07/2025, RO 19%→21% au 01/08/2025).
Un transfert FBA daté d'avant un changement de taux se voyait donc à tort
appliquer le taux d'après. Extraction best-effort d'une date sur le
transfert (`tax_calculation_date` / `transaction_complete_date` /
`shipment_date`, mêmes clés candidates que le reste du parsing Amazon) pour
appliquer le taux historique correct ; repli sur le taux courant (comportement
inchangé) si aucune date exploitable n'est trouvée sur la ligne.

**4. `oss_export.py::suggest_negative_bucket_corrections` — origine
déterministe pour les avoirs multi-lignes.** Quand plusieurs ventes
positives partagent le même `(sale_id, stock_country, vat_country,
vat_rate)` (commande multi-articles), `candidates[0]` prenait la première
rencontrée dans l'ordre d'itération de `results` — pas nécessairement la
plus ancienne. Remplacé par `min(candidates, key=...transaction_date)`
pour que la période d'origine déduite soit déterministe et reflète le début
réel de la commande.

**5. `vies_engine.py::validate_vat_numbers_parallel` — robustesse aux
doublons de `vat_ids`.** `to_fetch` était `dict[norm -> UN SEUL vat_id
original]` : si deux chaînes brutes distinctes en entrée se normalisaient
vers le même numéro (ex. espacement/casse différents), seul le dernier
original rencontré pour ce `norm` recevait une entrée dans `results` — les
autres auraient été silencieusement absents du dict retourné. Sans impact
aujourd'hui (le seul appelant, `compute_all_with_vies`, déduplique déjà sur
le VAT normalisé avant d'appeler cette fonction), mais fragile pour un futur
appelant qui ne dédupliquerait pas. `to_fetch` devient `dict[norm ->
list[vat_id original]]` ; tous les originaux partageant un `norm` reçoivent
désormais le même résultat, et `_tick()` de progression est appelé une fois
par original (pas une fois par requête réseau dédupliquée) pour rester
cohérent avec `total = len(vat_ids)`.

**6. `excel_report.py::_deadline_oss` — retrait de deux branches mortes.**
`q_end_month` ne peut valoir que 3, 6, 9 ou 12 par construction
(`((mois-1)//3*3)+3`) : le garde-fou `if q_end_month > 12` et la branche
`elif q_end_month + 1 == 12` (qui supposerait `q_end_month == 11`) étaient
tous deux inatteignables. Résultat déjà correct dans tous les cas (le
`else` calculait la bonne date via `calendar.monthrange`) — nettoyage
purement cosmétique, aucun changement de comportement.

**Validation** : `python3 -m py_compile` OK sur les 4 fichiers modifiés
(`rates.py`, `ca3_report.py`, `oss_export.py`, `vies_engine.py`,
`excel_report.py`). Suite complète `pytest` : 174 passed / 4 failed —
identique à la baseline (échecs pré-existants `SUPABASE_DB_URL` absente en
sandbox, confirmé par comparaison directe avec une copie vierge du dépôt),
aucune régression.

## 2026-08-19 (2) — Audit perf/RAM/CPU : 1 optimisation, 1 code mort retiré, 2 clarifications

Suite de l'audit du même jour, cette fois côté performance plutôt que
correction de bug fiscal. Fichiers passés en revue : `loader.py::_process_rows`,
`ecb_rates.py` (`prefetch_rates`, `_fetch_ecb_batch`, `_db_get_rates_batch`,
`_db_upsert_batch`, `convert_to_eur`, `convert_to_currency`,
`convert_to_currency_for_oss`, `get_rates_for_dates`, `clear_cache`,
`cache_info`), `classify.py`, `constants.py` (Amazon).

**Verdict global** : le pipeline de parsing Amazon et la couche de conversion
BCE étaient déjà très matures niveau perf (batching déjà en place partout où
ça compte : taux BCE pré-chargés en un seul aller-retour HTTP + une seule
requête DB batch avant la boucle principale, écriture DB en une seule
transaction `execute_values`, regex précompilées au niveau module,
nettoyage RAM incrémental de `rows_to_process`). Une seule vraie
optimisation trouvée, un code mort, deux clarifications de commentaires/API.

**1. `constants.py::is_national_tax_id` — dict de patterns hissé au niveau
module (gain mesuré ~2,3x sur la fonction).** Le dict `_PATTERNS` (12 clés,
~20 fermetures lambda, dont deux regex ES compilées implicitement à chaque
appel via `re.match(pattern, s)`) était reconstruit intégralement À CHAQUE
APPEL de la fonction — appelée pour chaque ligne ayant un `buyer_vat` non
vide, dans la boucle la plus chaude de tout le pipeline
(`loader._process_rows` → `classify.classify_buyer` →
`is_national_tax_id`). Sur un rapport Amazon volumineux très majoritairement
B2B, ça revenait à ré-allouer ce dict et ses fermetures des centaines de
milliers de fois pour un résultat strictement identique à chaque fois.
Renommé `_NATIONAL_TAX_ID_PATTERNS`, hissé au niveau module (construit une
seule fois au chargement), et les deux patterns ES précompilés en
`_ES_NIF_PHYSIQUE`/`_ES_CIF_ENTITE`. Micro-benchmark avant/après (200 000
appels, cas IT) : 0,323 s → 0,139 s, soit ~2,3x. Comportement strictement
identique (mêmes règles, même ordre d'évaluation par pays).

**2. `ecb_rates.py::get_rates_for_dates` — code mort retiré.** Aucun
appelant nulle part dans le dépôt (vérifié par recherche exhaustive,
y compris les tests). En plus d'être morte, elle n'utilisait pas le pattern
batch employé partout ailleurs dans ce fichier (`prefetch_rates`,
`_db_get_rates_batch`) — elle appelait `get_rate()` une fois par date en
boucle, un piège de perf (retour au pattern N+1) si jamais réutilisée telle
quelle sans y penser. Supprimée.

**3. `ecb_rates.py::convert_to_currency_for_oss` — docstring clarifiée.**
La docstring affirmait que la fonction "retombe sur le taux du jour de la
transaction" si le taux BCE de clôture de trimestre n'est pas encore publié
(cas d'un trimestre encore en cours). En réalité la fonction elle-même ne
fait rien de tel : elle lève `ValueError` (ou utilise `fallback_rate` si
fourni). Le repli documenté existe bel et bien côté produit, mais il est
implémenté UN NIVEAU AU-DESSUS, chez le seul appelant réel
(`oss_export.convert_ht_tva_for_oss_period`), qui (a) passe systématiquement
`fallback_rate=res.sale.exchange_rate` (le taux du jour de la transaction,
retenu à l'import) et (b) capture `ValueError` en conservant le montant déjà
calculé à ce même taux. Comportement produit inchangé et correct — seule la
docstring induisait en erreur sur QUI implémente le repli. Note croisée
ajoutée des deux côtés (dans `ecb_rates.py` et dans `oss_export.py`) pour
qu'un futur appelant de cette fonction sache qu'il doit reproduire ce même
filet (`fallback_rate` + `except ValueError`) sous peine de voir l'exception
se propager pour une période en cours.

**4. `loader.py::load_amazon_report` — paramètre `target_currency`
clarifié.** Signalé lors de l'audit précédent : ce paramètre, transmis
explicitement par `app.py`, est toujours écrasé à `"EUR"` avant l'appel à
`_process_rows` (le moteur fiscal doit calculer en EUR quel que soit le pays
vendeur — voir le "BUGFIX CRITIQUE" déjà en place). Comportement inchangé
(toujours correct), mais désormais explicité en tête de docstring + log de
diagnostic (`logger.debug`) si une valeur autre qu'EUR est demandée, pour
qu'un futur mainteneur ne découvre pas ce silence par surprise. Paramètre
conservé dans la signature (pas renommé) pour ne pas casser l'appel par
mot-clé existant dans `app.py`.

**Validation** : `python3 -m py_compile` OK sur les 3 fichiers modifiés
(`constants.py`, `ecb_rates.py`, `oss_export.py`, `loader.py`). Suite
complète `pytest` : 174 passed / 4 failed — identique à la baseline
(échecs pré-existants `SUPABASE_DB_URL` absente en sandbox), aucune
régression.

## 2026-08-20 — Mode d'affichage Simple / Détaillé (UX)

Ajout d'un bouton bascule ("Simple" / "Détaillé") dans le tableau de bord,
pour proposer une expérience allégée aux utilisateurs qui n'ont besoin que
des résultats par pays, du statut VIES et des téléchargements. Uniquement
de l'affichage — **aucun changement de calcul**, aucune nouvelle clé dans
`_parse_cache_key` ni `_cache_key` (vérifié explicitement avant
implémentation) : basculer de mode ne redéclenche donc aucun recalcul, la
bascule se fait par simple rerun Streamlit lisant les résultats déjà en
cache (`CalcCacheState`).

**Masqué en mode Simple** (visible en mode Détaillé) :
- Résumé fichiers analysés (`import_summary_single/multi`) + expander
  "Détail par fichier"
- `import_warnings_header`, `different_sources_warning`,
  `period_mismatch_title`
- Encart "📅 Taux TVA historiques détectés"
- Encart "💱 Taux de change BCE utilisés"
- Sous-onglet "Mouvements stock FBA" de l'onglet Audit (transferts FBA
  toujours disponibles via l'export Excel/CSV, plus lisible pour cet usage
  qu'un tableau brut dans l'app)
- Onglet **Audit {platform} entier**, sauf si un écart TVA Amazon
  significatif existe (`abs(total_ecarts_autres) > 0.05`, KPI "Config
  Amazon conforme") **ou** qu'un pays de stock FBA local est à risque
  d'immatriculation manquante — ce second critère réutilise
  `_aggregate_fba_local_sales` (déjà mise en cache via `heavy_cache_data`),
  aucun nouveau calcul introduit, uniquement une lecture pour décider de
  l'affichage de l'onglet.

Les alertes critiques (numéro de TVA d'origine manquant, blocage
`critical_blocking`) restent affichées dans les deux modes, ainsi que les 4
KPI cards et le plan d'action immatriculations.

**Mode Détaillé également allégé** à cette occasion :
- Bandeau explicatif de l'encart taux historiques (`historical_rates_widget.py`) :
  `st.warning`/`st.info` colorés remplacés par `st.caption` discret — reste
  informatif, n'est pas une alerte actionnable.
- 3 des 4 textes d'intro des sous-onglets "Écarts TVA" (`audit_uk_info`,
  `audit_art194_info`) passés en `st.caption` (non actionnables).
  `audit_vies_info` et `audit_manquante_info` conservés en `st.info` :
  le premier constitue tout le contenu du sous-onglet tant que VIES est
  désactivé, le second pointe une action concrète ("vérifier le
  paramétrage Amazon").
  Correction en cours de session : une factorisation des 4 textes en un
  seul intro commun avait été envisagée puis abandonnée après vérification
  du contenu réel — ce ne sont pas des textes répétitifs mais 4
  explications distinctes propres à chaque sous-onglet ; les factoriser
  aurait fait perdre du contexte utile sans gain réel.

**i18n** : 3 nouvelles clés (`display_mode_label`, `display_mode_simple`,
`display_mode_detailed`) ajoutées symétriquement aux 7 fichiers TOML, juste
avant le bloc "Onglets"/"Tabs". Comptage vérifié via `toml.load()` : 1137
clés par langue (baseline 1134 + 3), identique dans les 7 fichiers.

**Fichiers modifiés** : `app.py`, `tva_intracom/historical_rates_widget.py`,
`tva_intracom/ui/tabs/audit.py`, `tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : le toggle ne crée ni thread, ni connexion, ni
polling — stockage `session_state` uniquement, aucun impact sur la
détection d'inactivité.

**Validation** : `py_compile` OK sur les 3 fichiers Python modifiés. Suite
complète `pytest` : 174 passed / 4 failed — identique à la baseline
(échecs pré-existants `SUPABASE_DB_URL` absente en sandbox), aucune
régression.

---

## 2026-08-21 — Restructuration UX : barre de statut persistante, mode avancé global, sidebar et téléchargements allégés

Suite du chantier UX du 2026-08-20. Quatre points traités :

**1. Mode avancé global et unique (`tva_intracom/ui/display_mode.py`, nouveau).**
Le mode "Simple/Détaillé" du 2026-08-20 vivait uniquement dans `app.py`
(`_is_detailed`, lu localement dans ce fichier). Il est désormais le point
de vérité unique consommé par `sidebar.py` et `telechargements.py` en plus
d'`app.py`, via trois fonctions : `ensure_display_mode()` (init, à appeler
tôt), `is_detailed()` (lecture pure, sans dépendance circulaire), et
`render_mode_toggle()` (le widget lui-même, comportement de rerun
strictement identique à l'ancien : un seul `st.rerun()` si la valeur
change réellement). Invariant explicitement documenté dans le module :
`display_mode` ne doit **jamais** entrer dans `_parse_cache_key` ni
`calc_key` — vérifié, aucune des deux clés ne le contient.

**2. Barre de statut persistante (`app.py`).**
Bandeau visuel toujours affiché (fichier chargé ou non), positionné juste
après le rendu de la sidebar, contenant : nom du/des fichier(s) importé(s)
ou message d'attente, période détectée, statut du calcul (point vert "à
jour" / orange "en attente"), et le sélecteur Simple/Détaillé (déplacé
depuis son ancien emplacement au milieu du bloc KPI, `app.py` ex-L874-886,
qui n'était visible qu'après un calcul complet). CSS ajouté dans
`theme.py` (`.status-bar`, `.status-bar-dot`), même famille de variables
(`--brand-blue`, `--secondary-background-color`) que les KPI cards
existantes — pas de nouveau système visuel introduit.
L'ancienne initialisation `if "display_mode" not in st.session_state:`
(ex-L463) est supprimée, remplacée par l'appel unique à
`ensure_display_mode()`.

**3. Sidebar allégée (`tva_intracom/ui/sidebar.py`).**
- Expander "Catalogue Produits" (taux réduits par ASIN) et expander
  "Cache VIES" (TTL, stats, purge, certificat) : masqués entièrement en
  mode Simple, visibles en mode Détaillé. `asin_to_category` reste
  initialisé à `{}` et `encoding` à `"utf-8"` dans tous les cas — aucune
  variable non définie plus loin dans le flux de parsing, quel que soit
  le mode.
- Bloc "Compte & Confidentialité" (changement de mot de passe, export
  RGPD, suppression de compte) : sorti du corps de la sidebar (accord
  explicite du 2026-08-21) vers une modale (`@st.dialog`, fonction
  `_render_account_dialog`), ouverte par un bouton. Contenu strictement
  inchangé, seul l'emplacement change — ce bloc n'a pas sa place dans un
  panneau consulté à chaque session de calcul.
- Compatibilité vérifiée : `st.dialog(title: str, ...)` sur Streamlit
  1.58.0 (version épinglée du projet) n'accepte pas `None` pour `title`,
  d'où `title=""` plutôt que `title=None`.

**4. Téléchargements allégés (`tva_intracom/ui/tabs/telechargements.py`).**
Sections IOSS, B2B et Déclarations Locales (hors pays d'origine) :
masquées entièrement en mode Simple **uniquement si aucune donnée réelle
correspondante n'existe** (`_has_ioss_sales`, `_has_b2b_sales`,
`_local_countries`). Dès qu'une des trois catégories contient des données
réelles, la section reste affichée dans les deux modes — on ne masque
jamais un export dont l'utilisateur a effectivement besoin. Rapport
principal, export OSS et déclaration pays d'origine (CA3/local) restent
toujours visibles, dans les deux modes : ce sont les exports de base.

**i18n** : 7 nouvelles clés ajoutées symétriquement aux 7 fichiers TOML
(`status_bar_file_label`, `status_bar_no_file`, `status_bar_period_label`,
`status_bar_period_pending`, `status_bar_status_label`,
`status_bar_status_ready`, `status_bar_status_none`), juste après le bloc
"Mode d'affichage". Comptage vérifié via `toml.load()` : 1144 clés par
langue (baseline 1137 + 7), identique dans les 7 fichiers.

**Fichiers modifiés** : `app.py`, `tva_intracom/ui/display_mode.py`
(nouveau), `tva_intracom/ui/theme.py`, `tva_intracom/ui/sidebar.py`,
`tva_intracom/ui/tabs/telechargements.py`,
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucune connexion, thread ou polling ajouté —
uniquement `session_state`, widgets et rendu conditionnel. Aucun impact
sur la détection d'inactivité.

**Validation** : `py_compile` OK sur les 5 fichiers Python modifiés.
`pyflakes` : aucun nouveau warning introduit (le seul avertissement
existant, `sidebar.py` "import '_dt' shadowed by loop variable", est
pré-existant et déjà documenté comme faux positif, confirmé identique
sur le dépôt non modifié). Suite complète `pytest` : 174 passed / 4
failed — identique à la baseline (échecs pré-existants
`SUPABASE_DB_URL` absente en sandbox), confirmé également en relançant
la suite sur le dépôt d'origine non modifié pour comparaison directe.
Symétrie TOML vérifiée programmatiquement.

**Correctif post-livraison (2026-08-21, même jour)** : `@st.dialog(title="")`
levait `StreamlitAPIException` chez Matthieu (Streamlit exige un `title`
non vide — passait par erreur sur l'environnement de test initial, pas sur
le sien). Remplacé par `@st.dialog(title=_("account_privacy_header"))` ;
suppression du `st.markdown(f"### ...")` en double devenu redondant dans
le corps de la modale, le titre s'affichant désormais nativement en
en-tête du dialog. `py_compile` + suite `pytest` complète relancés :
174 passed / 4 failed, baseline inchangé.

---

## 2026-08-21 (2) — Correctifs mode Simple/Détaillé : recalcul intempestif, persistance par compte, barre de statut

Trois signalements après mise en prod des changements du 2026-08-21 (1).

**1. Bug racine : la bascule de mode déclenchait un recalcul complet (et
donnait l'impression d'un plantage à l'affichage).**
`asin_to_category` (catalogue ASIN → taux réduit) et `encoding` (encodage
fichier) alimentent respectivement `calc_key` et `parse_key` (voir
`app.py`). En masquant leurs expanders en mode Simple, ces deux variables
retombaient à leur valeur par défaut (`{}` / `"utf-8"`) à chaque run où
l'expander n'était pas rendu — ce qui changeait `calc_key`/`parse_key`
dès qu'on quittait le mode Détaillé après avoir chargé un catalogue ou
changé l'encodage, et déclenchait donc un **recalcul, voire un
re-parsing complet des fichiers**, à chaque bascule de mode. Sur un jeu
de données volumineux (plusieurs fichiers ~1 Mo, cas réel signalé), ce
recalcul soudain donnait l'impression d'un plantage de l'interface.
Corrigé : les deux valeurs sont désormais mises en cache dans
`session_state` (`_asin_catalog_data`, `_file_encoding_choice`) et relues
quel que soit le mode d'affichage — le contenu réel ne varie plus jamais
en fonction du mode, seul l'affichage du réglage (visible/masqué) change.
Le `file_uploader` du catalogue reçoit en plus une `key` explicite
(`catalog_file_uploader`), absente avant, pour que Streamlit conserve son
état correctement quand l'expander n'est pas rendu à chaque run.

**2. Dernier mode choisi non sauvegardé par utilisateur.**
Suit exactement le schéma déjà en place pour la langue préférée
(`tva_users.language` / `set_language()`) : nouvelle colonne
`tva_users.display_mode` (défaut `'simple'`, migration rétro-compatible
via `ALTER TABLE ADD COLUMN IF NOT EXISTS`), nouvelle fonction
`tva_intracom.auth.set_display_mode()`, `User.display_mode` ajouté à la
dataclass et à `_row_to_user()`. Dans `app.py`, le bloc de synchro
langue↔compte est étendu pour traiter aussi le mode d'affichage, sur un
**flag `_prefs_synced_user` unique et partagé** (calculé une seule fois
avant de traiter langue et mode, plutôt que deux blocs séquentiels
indépendants — la première version aurait fait positionner le flag par
le traitement de la langue avant que le mode n'ait pu le lire, empêchant
sa restauration à la première connexion). À la première vue d'un compte
dans la session : mode sauvegardé restauré s'il diffère. Ensuite : tout
changement via le toggle est persisté sur le compte.

**3. Barre de statut : noms de fichiers remplacés par un simple
compteur.** `📁 Fichier :` affiche désormais "N fichier(s) importé(s)"
au lieu de la liste des noms — sans intérêt pour l'utilisateur et prenait
trop de place dans un bandeau censé rester compact. Nouvelle clé i18n
`status_bar_files_count` (avec placeholder `{count}`), ajoutée
symétriquement aux 7 langues. `_status_bar_filenames` (ancienne clé
session_state) remplacée par `_status_bar_file_count` (entier) —
volontairement **absente** de la whitelist de préservation d'état lors
d'un vrai retrait de fichier (`app.py`, bloc "Vrai retrait de fichier") :
le compteur doit repasser à 0 dans ce cas, contrairement à `display_mode`
et aux deux caches ci-dessus qui doivent survivre à un retrait de fichier
puisqu'ils ne dépendent pas du fichier chargé.

**i18n** : 1 nouvelle clé (`status_bar_files_count`) ajoutée
symétriquement aux 7 fichiers TOML. Comptage vérifié via `toml.load()` :
1145 clés par langue (1144 + 1), identique dans les 7 fichiers.

**Fichiers modifiés** : `app.py`, `tva_intracom/auth.py`,
`tva_intracom/ui/sidebar.py`,
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun changement — toujours uniquement
`session_state` et une colonne SQL supplémentaire sur une table déjà
lue/écrite par ailleurs (`tva_users`), aucune connexion, thread ou
polling ajouté.

**Validation** : `py_compile` OK sur les 4 fichiers Python modifiés.
`pyflakes` : aucun nouveau warning (seul le faux positif pré-existant
`sidebar.py` "import '_dt' shadowed by loop variable" subsiste, déjà
documenté). Suite complète `pytest` : 174 passed / 4 failed — identique
à la baseline (échecs pré-existants `SUPABASE_DB_URL` absente en
sandbox), aucune régression. Symétrie TOML vérifiée programmatiquement.

---

## 2026-08-21 (3) — Correctif : avertissement d'interface à la bascule de mode

`render_mode_toggle()` passait à la fois `default=` et `key=` à
`st.segmented_control` alors que la clé du widget existait déjà en
session_state dès le 2e rendu — combinaison que Streamlit désapprouve
explicitement une fois le widget créé, ce qui produit un avertissement
d'interface (pas une exception Python, donc invisible côté logs serveur)
à chaque bascule de mode. C'est ce qui restait sous le nom de "bug
d'affichage" après correction du recalcul intempestif (voir entrée
2026-08-21 (2)) : les deux symptômes avaient des causes distinctes.

Corrigé : la clé du widget (`_display_mode_widget`) n'est désormais
initialisée qu'une seule fois par session, juste avant sa toute première
instanciation — `default=` n'est plus jamais repassé au widget une fois
créé. Le widget reste seul maître de sa valeur entre deux clics,
conformément au pattern documenté par Streamlit pour un widget à clé
stable. Le cas de synchro "mode restauré depuis le compte à la première
connexion" (voir entrée précédente) reste correctement géré : cette
synchro n'intervient qu'avant tout premier rendu du widget dans la
session, exactement le moment où sa clé n'existe pas encore.

Le log `asyncio ConnectionResetError` (`_ProactorBasePipeTransport`)
rapporté au même moment est un artefact Windows/Tornado bénin (fermeture
de socket websocket, généralement suite à un rafraîchissement de page
côté navigateur) sans lien avec le code applicatif — aucune action prise
sur ce point.

**Fichiers modifiés** : `tva_intracom/ui/display_mode.py`.

**Railway / scale-to-zero** : aucun impact — changement purement
`session_state`, pas de nouvelle connexion/thread/polling.

**Validation** : `py_compile` OK. Suite complète `pytest` : 174 passed /
4 failed — identique à la baseline, aucune régression.

---

## 2026-08-21 (4) — Instrumentation diagnostique : bug d'affichage persistant à la bascule de mode

Le correctif précédent (2026-08-21 (3)) n'a pas suffi — le bug
d'affichage persiste à la bascule Simple/Détaillé. Faute de traceback
exploitable (le problème ne semble pas être une exception Python non
gérée), ajout de logs `INFO`/`EXCEPTION` ciblés le long de tout le
chemin du toggle, pour capturer l'état exact au moment où Matthieu
reproduira le problème :

- **`app.py`**, bloc synchro préférences (langue + mode d'affichage) :
  marqueur `=== RUN START user=... ===` à chaque run, état complet des
  préférences (session vs compte), détection de première synchro,
  détection de rerun forcé.
- **`app.py`**, bloc barre de statut : état juste avant rendu (mode,
  nombre de fichiers, résultats en cache, période) et confirmation de fin
  de rendu sans rerun.
- **`tva_intracom/ui/display_mode.py`**, `render_mode_toggle()` : état de
  la clé du widget avant/après l'appel à `st.segmented_control`
  (présente ou non, valeur), la sélection retournée, et détection d'un
  changement de mode déclenchant `st.rerun()`. L'appel au widget est
  entouré d'un `try/except` qui logue puis relaie toute exception —
  jusqu'ici invisible si elle se produisait silencieusement côté client.

**Robustesse ajoutée en parallèle** (pas seulement du diagnostic) :
`tva_auth.set_display_mode()` est du code neuf, jamais éprouvé contre la
base de production, contrairement à `set_language()` qui a fait ses
preuves. Un échec de cet appel (DB indisponible, contrainte, etc.)
interrompait tout le script Streamlit en plein milieu de run — plausible
cause partielle du "bug d'affichage" selon la configuration client. Cet
appel est désormais protégé par un `try/except` qui logue l'échec
(`logger.exception`) et laisse le mode appliqué dans la session en
cours, sans le persister pour la prochaine connexion cette fois-ci,
plutôt que de faire planter tout le rendu de la page.

**Fichiers modifiés** : `app.py`, `tva_intracom/ui/display_mode.py`.

**Railway / scale-to-zero** : aucun impact — logs uniquement, aucune
connexion/thread/polling supplémentaire.

**Validation** : `py_compile` + `pyflakes` OK (aucun nouveau warning).
Suite complète `pytest` : 174 passed / 4 failed — identique à la
baseline, aucune régression.

**À faire une fois le bug identifié** : retirer les logs `DEBUG`
temporaires ci-dessus (clairement marqués comme tels dans les
commentaires), ne conserver que ceux jugés utiles en routine.

---

## 2026-08-21 (5) — Cause racine réelle trouvée et corrigée : bug d'affichage à la bascule de mode

Les logs de diagnostic ajoutés en (4) ont permis d'isoler la vraie cause,
distincte du correctif (3) qui n'était qu'un problème connexe (warning
d'interface) sans lien avec le symptôme principal.

**Symptôme confirmé par capture d'écran** : à la bascule Simple/Détaillé
(dans les deux sens), les tableaux, onglets et alertes disparaissaient
et l'écran revenait à l'état "aucun fichier importé" avec les
instructions d'accueil — alors que le fichier était toujours réellement
présent.

**Cause racine** : `render_mode_toggle()` (`display_mode.py`) appelait
`st.rerun()` directement après un changement de mode. Or `app.py`
distingue explicitement, via `preserve_upload_rerun()`
(`rerun_utils.py`, mécanisme déjà existant et documenté), un rerun
interne d'un vrai retrait de fichier par l'utilisateur — sans ce
marquage, son filet de sécurité traite tout rerun où le
`st.file_uploader` ressort vide comme "l'utilisateur a retiré son
fichier" et exécute le nettoyage complet de `session_state` (résultats,
période, etc.). Confirmé noir sur blanc par les logs de diagnostic : le
run consécutif au clic sur le toggle affichait `file_count=0` /
`has_results=False`, alors que le run juste avant montrait
`file_count=1` / `has_results=True` / `period='2026-03'`.

Corrigé : `render_mode_toggle()` appelle désormais
`preserve_upload_rerun()` au lieu de `st.rerun()`.

**Nettoyage** : les logs `INFO` de diagnostic temporaires ajoutés en (4)
(`=== RUN START ===`, `PREFS_SYNC début run`, `STATUS_BAR début/fin
rendu`, détail avant/après rendu du widget) sont retirés, leur rôle
ayant été rempli. Conservé : un seul `logger.info` sobre sur le
changement de mode effectif (utile en routine, faible volume), et la
protection `try/except` autour de `tva_auth.set_display_mode()` ajoutée
en (4) — robustesse réelle indépendante du bug ci-dessus, conservée.

**Fichiers modifiés** : `app.py`, `tva_intracom/ui/display_mode.py`.

**Railway / scale-to-zero** : aucun impact.

**Validation** : `py_compile` + `pyflakes` OK (aucun nouveau warning).
Suite complète `pytest` : 174 passed / 4 failed — identique à la
baseline, aucune régression.

## 2026-08-21 (6) — Sidebar cassée : IOSS/DDP/OSS sans effet ; nettoyage zone Entreprise & Paramètres

**Symptôme signalé** : aucun des boutons IOSS/DDP/OSS sous 10 000 €
ne déclenchait de recalcul — cocher/décocher n'avait visuellement aucun
effet sur les déclarations affichées.

**Cause racine confirmée** (code réel lu, pas de déduction) :
`_edit_siren_form_fragment` (`sidebar.py`) est un `@st.fragment` —
introduit lors d'un refactor précédent pour éviter qu'une frappe dans un
champ texte ne redessine les 6 onglets déjà calculés à chaque caractère.
Mais les 4 toggles fiscaux (IOSS actif, DDP, seuil OSS, OSS N-1) et le
multiselect des pays TVA vivaient EUX AUSSI dans ce fragment isolé. Un
fragment isolé ne redessine que lui-même : cliquer un toggle ne relance
jamais `render_sidebar()`, donc les valeurs "effectives" retournées par
`SidebarResult` (et donc `_cache_key` dans `app.py`) restaient celles de
`_match` (dernière sauvegarde en base) tant que "Enregistrer les
modifications" n'était pas cliqué (seul point relançant un rerun complet
et rechargeant `_match`). Régression directe de l'isolation en fragment.

**Corrigé** :
- Les 4 toggles + le multiselect pays TVA sont sortis du fragment et
  rendus en LIVE dans `render_sidebar()` : leur valeur courante est
  immédiatement utilisée pour le calcul. Un clic déclenche désormais un
  rerun complet — coût attendu et voulu, ces réglages changent le
  résultat fiscal (contrairement à la frappe d'un nom ou d'un numéro de
  TVA, qui reste isolée dans `_edit_siren_form_fragment`, désormais
  réduit à : saisie IOSS non verrouillée, saisie TVA des pays
  nouvellement ajoutés, bouton de sauvegarde).
- **Bug latent découvert au passage** : `ioss_own_number_active`
  influence bien `engine.compute_vat()` (voir `engine.py` ~l.312) mais
  était absent de `_cache_key` (`app.py`) — même une fois ce toggle rendu
  live, son changement n'aurait jamais invalidé le cache de calcul.
  Ajouté à `_cache_key`.
- **Zone "Pays où vous avez un numéro TVA local"** : recentrée sur
  l'ajout plutôt que la re-consultation d'un stock. Les pays déjà
  verrouillés (numéro enregistré) sont désormais résumés en une seule
  ligne compacte (`🔒 FR FR123456789 · DE DE987654321 — ...`) au lieu
  d'une ligne complète + éventuel champ texte par pays à chaque rendu ;
  le multiselect ne sert plus qu'à ajouter un nouveau pays pas encore
  enregistré (le retrait d'un pays de la liste reste possible via le
  multiselect, comportement inchangé).
- **Section "Période fiscale" retirée** de la sidebar : strictement
  redondante avec la barre de statut sous le titre (`app.py`, ajoutée le
  2026-08-21) qui affiche déjà la période détectée. `oss_period` reste
  figé à `"__auto__"` (aucun sélecteur manuel n'existait réellement, seul
  l'affichage informatif est retiré).
- Import `CalcCacheState` devenu inutile dans `sidebar.py` (ne servait
  qu'à la détection de période retirée) — supprimé.

**Évalué et non modifié** : `_new_siren_form_fragment` (formulaire de
création d'un SIREN pas encore enregistré) garde ses toggles à
l'intérieur du fragment. Contrairement au cas d'édition, il n'y a ici
aucun calcul déjà en cours à invalider avant le premier enregistrement —
risque de régression jugé disproportionné par rapport au gain pour un
cas d'usage (onboarding) qui n'était pas signalé comme cassé.

**Fichiers modifiés** : `tva_intracom/ui/sidebar.py`, `app.py`.

**Railway / scale-to-zero** : aucun impact (placement de widgets
Streamlit uniquement, aucune connexion/thread/polling touché).

**Validation** : `py_compile` + `pyflakes` OK (seul le faux positif
pré-existant documenté sur `_dt`/boucle dans `sidebar.py` remonte,
inchangé). Suite complète `pytest` : 174 passed / 4 failed — identique à
la baseline, aucune régression.

## 2026-08-21 (7) — Export Excel : ligne "Guichet IOSS" manquante dans le récapitulatif

**Symptôme signalé** : dans l'export Excel, la zone récapitulatif
n'affiche pas de ligne "Guichet IOSS (propre numéro vendeur)".

**Cause confirmée** : `ReportSummary` (`report.py`) calcule bien
`ioss_ht` / `ioss_vat` / `refund_ioss_ht` / `refund_ioss_vat` (branche
`Channel.IOSS`), et `total_you_owe` les intègre déjà correctement côté
modèle. Mais `_write_recap()` (`excel_report.py`) — qui construit le
tableau récapitulatif de l'export Excel — omettait entièrement la ligne
IOSS dans sa liste `data_structure`, et le total final ("TVA nette à
payer") ne sommait que CA3 + OSS + local, sans IOSS.

**Vérifié — n'affecte QUE ce tableau récapitulatif** : l'onglet Détail
des ventes (`_write_details_tab`) liste chaque `VatResult` sans filtre
par scénario — les ventes IOSS_DIRECT y apparaissent normalement, avec
leur vrai montant de TVA. Le calcul fiscal lui-même (`engine.py`,
`report.py`) n'est pas affecté, seul l'export Excel manquait cette
ligne.

**Corrigé** :
- Ajout de la ligne "Guichet IOSS (numéro propre vendeur)" dans
  `data_structure`, alimentée par `summary.ioss_ht` /
  `summary.refund_ioss_ht` / `summary.ioss_vat` /
  `summary.refund_ioss_vat`.
- Formule du total final ("TVA nette à payer") mise à jour pour inclure
  cette ligne, cohérent avec `ReportSummary.total_you_owe` côté modèle.
- Nouvelle clé i18n `xl_indicator_vat_ioss` ajoutée symétriquement dans
  les 7 fichiers TOML (fr/en/de/es/it/pl/pt), symétrie vérifiée via
  `toml.load()` (1146 clés dans chaque locale).

**Vérification complémentaire (question posée)** : IOSS et DDP
(`seller_is_importer` / `Scenario.IMPORT_SELLER_AS_IMPORTER`) dans la
CA3 (`ca3_report.py`) — DDP est correctement inclus (déjà couvert par un
correctif antérieur documenté sur place : filtre `channel ==
FR_DOMESTIC`, qui inclut nommément le cas DDP requalifié en
FR_DOMESTIC). IOSS est volontairement EXCLU de la CA3 (aucune branche
`Channel.IOSS` dans `_aggregate()`), ce qui est le comportement fiscal
correct — l'IOSS se déclare via son propre guichet, pas via la CA3.
Cependant, il n'existe à ce jour AUCUN document de déclaration IOSS
dédié généré par l'outil (pas d'export XML IOSS, pas d'onglet IOSS
spécifique) — seul le récapitulatif Excel (corrigé ici) et l'onglet
Détail des ventes en gardent trace. Roadmap déjà identifiée
("IOSS separate XML export").

**Fichiers modifiés** : `tva_intracom/excel_report.py`,
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun impact.

**Validation** : `py_compile` OK. `pyflakes` : seuls 3 avertissements
pré-existants sans lien (ailleurs dans le fichier, lignes ~1656/1751)
remontent, inchangés par rapport à avant cette modification. Suite
complète `pytest` : 174 passed / 4 failed — identique à la baseline,
aucune régression. Symétrie i18n vérifiée programmatiquement.

## 2026-08-22 — Nouvel onboarding guidé (le précédent avait été retiré :
ralentissait l'app, recalculait à chaque clic, désynchronisait une copie
parallèle des champs fiscaux)

**Contraintes posées avant conception** :
1. Le stepper doit vivre dans son propre `@st.fragment`, piloté
   uniquement par un flag d'affichage — jamais par un champ qui entre
   dans `calc_key`/`parse_key`.
2. Les vrais champs fiscaux (SIREN, IOSS…) affichés pendant l'onboarding
   doivent rester exactement les mêmes widgets que la sidebar normale
   (mêmes `key=`), pas une copie parallèle.
3. Le calcul ne doit se déclencher qu'au moment réel où il y a une
   valeur nouvelle à calculer, pas à chaque clic "suivant" du stepper.

**Décision de conception (validée avec Matthieu avant implémentation)** :
le bloc SIREN/IOSS/DDP/seuils OSS (`render_sidebar()`, ~450 lignes,
verrouillage fiscal irréversible, quotas cabinet, lookup DB) est resté
**intégralement inchangé**. Plutôt que d'extraire ce bloc pour le
rejouer dans une zone principale (chantier jugé disproportionné/risqué
sur du code fiscal sensible), le nouveau stepper **guide** vers la
sidebar (section "Entreprise", déjà `expanded=True` en permanence,
aucune ouverture forcée nécessaire) au lieu de dupliquer ses champs.
Conséquence directe : la contrainte 2 est satisfaite de la façon la
plus forte possible — les champs fiscaux ne sont ni déplacés ni recréés,
donc aucune désynchronisation n'est même possible par construction.

**Implémenté** :
- `tva_intracom/auth.py` : colonne `tva_users.onboarding_seen`
  (BOOLEAN, défaut FALSE) ajoutée au schéma (`CREATE TABLE IF NOT
  EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` pour les bases
  existantes, même pattern que `display_mode`). Nouveau champ sur
  `User`, colonne ajoutée à `_USER_SELECT_COLS`/`_row_to_user`, et
  nouvelle fonction `set_onboarding_seen(user_id, seen)`.
- `tva_intracom/ui/onboarding.py` (nouveau module) :
  - `ensure_onboarding_state(current_user, has_siren, has_results)` —
    calcule l'étape (`"fiscal"` / `"upload"` / `"done"`) à partir de
    signaux déjà fiables et déjà calculés ailleurs dans `app.py` :
    `has_siren` = `siren_quota_status.registered_count > 0` (un SIREN
    réellement **enregistré** en base, pas juste tapé dans un champ non
    sauvegardé), `has_results` = `bool(CalcCacheState.load().results)`
    (déjà utilisé par la barre de statut). Écrit uniquement
    `session_state["_onboarding_step"]` — jamais lu par `calc_key` ni
    `parse_key` (vérifié : aucune référence croisée dans
    `ui/calc_cache.py`). Persiste `onboarding_seen=True` en base
    uniquement au moment précis où l'étape "done" est atteinte pour la
    première fois (best-effort, non bloquant si la base est
    momentanément indisponible).
  - `render_onboarding_banner()` — `@st.fragment`, zone principale,
    juste au-dessus de l'uploader. Checklist 2 étapes (✅/🔵/⚪) + un
    texte pointant vers la section sidebar concernée. Ne crée AUCUN
    widget fiscal — lecture seule de `_onboarding_step`.
  - `restart_onboarding()` — remet le stepper à l'étape "fiscal" sans
    toucher à un seul champ fiscal déjà saisi.
- `tva_intracom/ui/sidebar.py` (`_render_account_dialog`) : bouton
  "Relancer la visite guidée" ajouté en tête de la modale "Compte &
  Confidentialité", appelle `restart_onboarding()` puis
  `preserve_upload_rerun()` (pas `st.rerun()` — cohérent avec le filet
  de sécurité upload existant).
- `app.py` : `ensure_onboarding_state()` appelé juste après le calcul de
  `_status_has_results` (barre de statut) ; `render_onboarding_banner()`
  appelé juste avant `st.file_uploader(...)`.
- i18n : 8 nouvelles clés (`onboarding_title`, `onboarding_step_fiscal`,
  `onboarding_step_upload`, `onboarding_hint_fiscal`,
  `onboarding_hint_upload`, `onboarding_restart_title`,
  `onboarding_restart_help`, `onboarding_restart_btn`) ajoutées
  symétriquement dans les 7 fichiers TOML (fr/en/de/es/it/pl/pt) —
  symétrie vérifiée programmatiquement via `toml.load()` : 1146 → 1154
  clés dans chacune des 7 locales.

**Pourquoi la contrainte 3 est déjà satisfaite sans code supplémentaire** :
le mécanisme `calc_key` existant dans `app.py` (comparaison avant tout
recalcul, voir `CalcCacheState`) ne déclenche déjà un calcul QUE si sa
valeur change. Le stepper n'écrit que `_onboarding_step`, qui n'entre
dans aucune des deux clés de cache — un clic "suivant" ne peut donc,
par construction, jamais provoquer de recalcul prématuré. Aucune
modification de `calc_key`/`parse_key` n'a été nécessaire ni effectuée.

**Railway / scale-to-zero** : aucun impact — aucune connexion, thread ou
polling ajouté. Le seul accès DB nouveau (`set_onboarding_seen`) est un
UPDATE ponctuel déclenché par une interaction utilisateur réelle (fin
d'onboarding ou clic sur "Relancer"), pas une vérification périodique.

**Fichiers modifiés** : `tva_intracom/auth.py`,
`tva_intracom/ui/onboarding.py` (nouveau), `tva_intracom/ui/sidebar.py`,
`app.py`, `tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Validation** : `py_compile` OK sur les 4 fichiers modifiés/créés.
`pyflakes` : seul le faux positif pré-existant documenté (`_dt` masqué
par une variable de boucle, `sidebar.py` L858, zone non touchée par ce
lot) remonte, inchangé. Suite complète `pytest` : 174 passed / 4 failed
— identique à la baseline, aucune régression. Symétrie i18n vérifiée
programmatiquement (1154 clés × 7 locales).

## 2026-08-22 (suite) — Bandeau onboarding illisible en mode sombre +
contenu insuffisant (retour utilisateur, capture d'écran)

**Bug signalé** : le bandeau onboarding utilisait des couleurs codées en
dur (fond `#F7F6FF`, texte `#26215C`) au lieu des variables de thème
Streamlit — en mode sombre, le fond du composant HTML restait clair
alors que le texte héritait du blanc du thème global : texte blanc sur
fond quasi-blanc, illisible (cf. capture).

**Cause confirmée** : contrairement à `.status-bar` (déjà correct dans
`theme.py`, basé sur `var(--secondary-background-color)` /
`var(--brand-blue)`, aucune couleur en dur), le bandeau onboarding avait
été écrit avec un `style=` inline en dur au lieu de réutiliser ce même
pattern de variables adaptatives.

**Corrigé** :
- Nouveau bloc CSS `.onboarding-banner` / `.onboarding-banner-title` /
  `.onboarding-banner-step` / `.onboarding-banner-substep` dans
  `tva_intracom/ui/theme.py`, calqué sur `.status-bar` — uniquement des
  variables de thème (`--secondary-background-color`, `--brand-blue`),
  aucune couleur figée. `onboarding.py` utilise désormais ces classes au
  lieu d'un `style=` inline.
- Contenu enrichi (2e retour utilisateur : le bandeau n'expliquait rien
  au-delà de 2 lignes génériques, sans mentionner ce que recouvre
  concrètement l'étape fiscale) : sous-liste affichée à l'étape
  "fiscal" — SIREN + TVA FR, numéro IOSS (optionnel), numéros de TVA
  locaux dans d'autres pays UE (optionnel) — plus une précision : la
  validation VIES des acheteurs B2B est automatique (aucune action
  requise, elle a lieu au moment du calcul), pour éviter toute confusion
  sur ce que l'utilisateur doit réellement saisir.
- 4 nouvelles clés i18n (`onboarding_step_fiscal_sub_siren`,
  `onboarding_step_fiscal_sub_ioss`, `onboarding_step_fiscal_sub_localvat`,
  `onboarding_hint_vies`) ajoutées symétriquement dans les 7 TOML.
  Symétrie vérifiée : 1154 → 1158 clés dans chacune des 7 locales.

**Fichiers modifiés** : `tva_intracom/ui/theme.py`,
`tva_intracom/ui/onboarding.py`,
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun impact (CSS + texte uniquement).

**Validation** : `py_compile` OK. `pyflakes` inchangé (même faux positif
pré-existant, sans lien). Suite complète `pytest` : 174 passed / 4
failed — identique à la baseline. Symétrie i18n vérifiée
programmatiquement (1158 clés × 7 locales).

## 2026-08-22 (suite 2) — Bloc HTML affiché en texte brut + onboarding
jugé sans intérêt réel (retour utilisateur, 2e capture d'écran)

**Bug signalé** : à l'étape "upload", la ligne `<p class="onboarding-
banner-step">...</p>` s'affichait littéralement en texte au lieu d'être
rendue.

**Cause confirmée** : le HTML était construit avec un f-string
multi-lignes indenté, avec un placeholder `{_fiscal_substeps}` vide à
l'étape "upload" — une ligne vide au milieu d'un bloc HTML met fin à ce
bloc pour le parseur Markdown de Streamlit ; la ligne suivante, encore
indentée par la mise en forme Python du f-string (8-12 espaces), était
alors interprétée comme un bloc de code (règle Markdown : 4 espaces
d'indentation = code), d'où l'affichage du tag brut.

**Corrigé** : le HTML est désormais assemblé via une liste de fragments
sans indentation ni ligne vide, jointe par `"".join(...)` — une seule
ligne HTML continue, structurellement impossible à casser de la même
façon.

**2e retour ("aucun intérêt, juste SIREN + upload")** : le bandeau ne
présentait effectivement rien du produit lui-même. Ajouté :
- une phrase d'intro (`onboarding_intro`) expliquant l'objectif (générer
  les déclarations OSS/CA3/IOSS à partir des exports Amazon, en 2
  étapes) ;
- à l'étape "upload", une sous-ligne (`onboarding_step_upload_sub_tabs`)
  annonçant les 6 onglets où les résultats apparaîtront (Déclarations,
  Détail Ventes, VIES, Audit, Téléchargements, Graphiques) — noms
  génériques, volontairement découplés du nom de plateforme dynamique
  (`platform_name`) pour rester traduisibles simplement ;
- un `st.caption` (`onboarding_hint_tabs`) détaillant en une phrase ce
  que couvre chaque onglet.

**Fichiers modifiés** : `tva_intracom/ui/onboarding.py`,
`tva_intracom/ui/theme.py`, `tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun impact (texte + CSS uniquement).

**Validation** : `py_compile` OK. `pyflakes` inchangé (même faux positif
pré-existant). Suite complète `pytest` : 174 passed / 4 failed —
identique à la baseline. Symétrie i18n vérifiée programmatiquement
(1161 clés × 7 locales).

## 2026-08-22 (suite 3) — Doublon avec le bloc "Comment utiliser cette
application ?" + checklist enrichie (retour utilisateur)

**Doublon signalé** : la checklist onboarding et l'ancien bloc statique
"Comment utiliser cette application ?" (affiché en bas d'app.py quand
aucun fichier n'est importé) faisaient doublon.

**Corrigé** : bloc `### {how_to_use_title}` + 4 étapes retiré d'app.py
(c'était un simple `st.markdown` isolé, sans lien avec le calcul — aucun
risque). Les 5 clés i18n `how_to_use_*` associées, devenues mortes,
retirées symétriquement des 7 TOML.

**Refonte de la checklist** (remplace le stepper séquentiel à 2 étapes) :
chaque item a désormais sa propre coche verte, indépendamment des
autres :
1. Nom de l'entreprise + SIREN (`_ob_entreprise_ok` = SIREN réellement
   enregistré en base ET nom/SIREN non vides)
2. Numéro de TVA local — FR (`_ob_tva_local_ok` = `tva_fr` non vide)
3. Numéro IOSS, optionnel (`_ob_ioss_filled` — n'affiche jamais 🔵
   bloquant, seulement ⚪/✅, et n'entre pas dans les critères de
   complétion globale)
4. Durée de validation du cache VIES — toujours ✅ (une valeur par
   défaut, 7 jours, est déjà appliquée dès la création du compte ; item
   purement informatif, pointe vers le réglage)
5. Importer un premier fichier Amazon (`_status_has_results`, déjà
   utilisé par la barre de statut)

Ces 4 booléens sont calculés dans `app.py` à partir des champs déjà
produits par `render_sidebar()` (`nom_entreprise`, `siren_entreprise`,
`tva_fr`, `ioss_number`, `ioss_own_number_active`,
`siren_quota_status.registered_count`) — aucun nouveau champ, toujours
zéro widget fiscal recréé.

**Cache VIES visible en mode Simple** : la durée de validité (slider
TTL) doit être visible/réglable dès la prise en main (item 4 de la
checklist), donc l'expander "Cache VIES" reste désormais toujours
visible (y compris en mode Simple) ; seuls les réglages avancés (stats
détaillées, purge, certificat PDF) restent réservés au mode Détaillé
(`if is_detailed():` déplacé pour ne plus couvrir que cette partie).
`_ttl_days` n'alimentant aucun champ de `SidebarResult`
(commentaire déjà présent, vérifié), ce déplacement ne touche à aucun
`calc_key`/`parse_key`.

**i18n** : 6 clés `onboarding_step_fiscal*`/`onboarding_step_upload*`
retirées (remplacées par la nouvelle checklist), 6 nouvelles clés
ajoutées (`onboarding_check_entreprise`, `onboarding_check_tva_local`,
`onboarding_check_ioss`, `onboarding_check_vies_ttl`,
`onboarding_check_vies_ttl_detail`, `onboarding_check_upload`) — solde
neutre, symétrie confirmée à 1156 clés × 7 locales.

**Fichiers modifiés** : `app.py`, `tva_intracom/ui/onboarding.py`
(entièrement réécrit), `tva_intracom/ui/sidebar.py` (cache VIES),
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun impact — pas de nouvelle connexion,
thread ou polling ; le seul accès DB reste l'UPDATE ponctuel de
`onboarding_seen` déjà en place.

**Validation** : `py_compile` OK sur les 4 fichiers modifiés. `pyflakes`
inchangé (même faux positif pré-existant, `sidebar.py` L858, zone non
touchée par ce lot). Suite complète `pytest` : 174 passed / 4 failed —
identique à la baseline, aucune régression. Symétrie i18n vérifiée
programmatiquement (1156 clés × 7 locales).

## 2026-08-22 (suite 4) — 3 retours utilisateur sur la checklist

**1. Bouton "Passer l'onboarding" manquant** : jusqu'ici seul un compte
déjà marqué "vu" en base masquait la checklist — impossible de la
fermer volontairement sans tout renseigner. Ajouté :
`onboarding.py::dismiss_onboarding(current_user)`, appelée par un
bouton "Passer" sous la checklist. Marque `onboarding_seen=True` en
base immédiatement (comme la complétion normale), sans exiger les 3
items obligatoires. Ne touche à aucun champ fiscal.

**2. Libellé "Numéro de TVA local (France)" trompeur** : le SIREN
implique une implantation française, mais rien n'impose que
`home_country` reste "FR" (sélecteur modifiable, voir
`sidebar.py::home_country_select` — convention Monaco notamment,
mentionnée dans les principes fiscaux du projet). Annoncer "(France)"
en dur était donc trompeur. Reformulé génériquement : "Numéro de TVA
intracommunautaire (le pays dépend de votre implantation)" — la valeur
vérifiée (`tva_fr`, champ toujours FR côté moteur de calcul — voir
`sidebar.py` L186/282, non modifié) reste inchangée, seul le texte
affiché change.

**3. `**gras**` affiché tel quel + mauvais emplacement pour "Cache
VIES"** : les `**...**` markdown ne sont jamais interprétés dans ce
bandeau car le HTML est inséré via des `<p>` bruts (pas de passage par
le rendu Markdown pour ce texte précis) — remplacés par `<strong>`.
Par ailleurs "Cache VIES" (`cache_vies_header`) est une section
**indépendante** de la sidebar, pas une sous-section d'"Entreprise" —
le texte "Entreprise › Cache VIES" était donc factuellement faux.
Corrigé en "la section **Cache VIES** de la barre latérale" (avec
`<strong>`).

**Bug introduit puis corrigé pendant ce lot** (attrapé par `pyflakes`,
avant tout envoi) : `_dismiss_col, _ = st.columns([1, 5])` écrasait la
variable `_` importée depuis `tva_intracom.i18n` (fonction de
traduction) par la variable de déballage inutilisée de la 2ᵉ colonne —
cassant tous les appels `_(...)` suivants dans la même portée. Renommé
en `_spacer_col`.

**i18n** : 2 clés existantes corrigées en place (`onboarding_check_tva_
local`, `onboarding_check_vies_ttl_detail`) + 1 nouvelle clé
(`onboarding_dismiss_btn`, "Passer"). Symétrie vérifiée : 1156 → 1157
clés dans chacune des 7 locales.

**Fichiers modifiés** : `tva_intracom/ui/onboarding.py`, `app.py`,
`tva_intracom/i18n/{fr,en,de,es,it,pl,pt}.toml`.

**Railway / scale-to-zero** : aucun impact — le seul accès DB nouveau
(`dismiss_onboarding`) est un UPDATE ponctuel déclenché par un clic
utilisateur réel, identique au mécanisme déjà en place pour la
complétion normale.

**Validation** : `py_compile` OK. `pyflakes` propre (bug `_` shadowing
détecté et corrigé avant livraison ; seul le faux positif pré-existant
`sidebar.py` L858 subsiste). Suite complète `pytest` : 174 passed / 4
failed — identique à la baseline. Symétrie i18n vérifiée
programmatiquement (1157 clés × 7 locales).
