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
| **IOSS_DIRECT** | Import ≤ 150 €, vendeur avec son propre numéro IOSS | Vendeur collecte via IOSS | Vendeur | Guichet IOSS |
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
├── tests/
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
│   ├── ecb_rates.py                  Taux BCE (cache mémoire + disque, convert_to_eur_for_oss)
│   ├── engine.py                     Moteur de classification fiscale (compute_vat, compute_all)
│   ├── excel_report.py               Export Excel multi-onglets
│   ├── historical_rates_widget.py    Composant UI Streamlit pour afficher l'historique des taux de change BCE appliqués
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
├── pyproject.toml
├── README.md
├── requirements.txt
└── vercel.json                       Config Vercel (includeFiles vers tva_intracom/billing.py)
```

---

## Architecture du moteur fiscal (`tva_intracom/`)

| Module | Rôle |
|---|---|
| `models.py` | Modèles de données (Pydantic) : Sale, VatResult, Scenario, BuyerType, Channel, Collector |
| `config.py` | Utilitaire de gestion des secrets (lwa, stripe, resend, postgres) avec fallback local |
| `engine.py` | Moteur de classification fiscale avec documentation légale intégrée (links Bofip/CGI/Dir) |
| `rates.py` | Taux TVA historisés par pays (vat_rate_at_date), is_eu, is_fiscal_eu, seuils |
| `security.py` | Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy) — chiffrement Fernet des PII |
| `vies_certificate.py` | Génération d'un "Certificat de Validité VIES" en PDF (preuve de bonne foi opposable) |
| `vies_engine.py` | Validation VIES : cache PostgreSQL à double niveau (privé/global), historique append-only pour piste d'audit, overrides manuels par scope, résoluteur de domaine et retry exponentiel |
| `ecb_rates.py` | Taux BCE : cache deux niveaux (mémoire + disque JSON), prefetch parallèle, convert_to_eur_for_oss (taux de clôture de période — Règl. UE 2020/194), retry exponentiel (3 tentatives, 1s/2s/4s) sur erreurs réseau/HTTP transitoires |
| `oss_export.py` | Agrégation OSS partagée (aggregate_oss_results), exports Excel + CSV URSSAF, détection des soldes négatifs (find_oss_negative_buckets) |
| `oss_xml.py` | Génération XML OSS officiel (Règl. UE 2021/965) avec multi-validation XSD (DGFIP/UE) |
| `ca3_report.py` | Génération du rapport CA3 (HTML uniquement — pas d'export EDI-TVA, voir Roadmap) : compute_ca3_lines_v2, AIC ligne 08 (transferts FBA), déductions manuelles, calcul du solde net, generate_ca3_html_report_v2 |
| `local_vat_report.py` | Équivalent générique du CA3 pour n'importe quel pays UE hors France (canal `LOCAL_REGISTRATION`, ou `FR_DOMESTIC` quand ce pays est le **pays d'origine** du compte) : `compute_local_vat_lines`, `generate_local_vat_html_report`. Ventilation base/TVA par taux réellement présent dans les données, style visuel harmonisé au CA3, mais **PAS un fac-similé du formulaire officiel** — un avertissement explicite figure dans chaque rapport généré. Codes de case indicatifs pour DE/ES/IT/PL/NL/BE/PT/SE/AT/CZ/RO/HU/IE (`rates.LOCAL_VAT_BOX_CODES`, non vérifiés exhaustivement contre un PDF officiel, contrairement au CA3) |
| `fec_export.py` | Export comptable au format FEC (journal des ventes agrégé par régime/pays/taux, écritures équilibrées débit/crédit) — pré-remplissage pour import dans un logiciel comptable tiers, alternative légère à l'EDI-TVA (voir Roadmap) |
| `excel_report.py` | Export Excel multi-onglets (voir détail onglets ci-dessous) |
| `historical_rates_widget.py` | Composant UI Streamlit pour afficher l'historique des taux de change BCE appliqués |
| `report.py` | ReportSummary, build_report, render_report — ventilation HT exhaustive par canal fiscal (ht_by_bucket) servant de contrôle de cohérence interne |
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
| `ui/formatting.py` | Helpers d'affichage réutilisés par plusieurs onglets : `_fmt`, `_country_label`, `_money_col`, `_pct_col`, `_smart_money_df`, `_gated_preview_table`, `_fec_period_end_date` |
| `ui/auth_flow.py` | `AuthContext` + `ensure_cookie_manager()` / `run_auth_flow()` — bypass dev local, restauration de session par cookie, consommation du lien magique, migration `?session_token=`, callback OAuth Amazon SP-API, écran de connexion (bloquant via `st.stop()`), bandeau connecté/déconnexion |
| `ui/onboarding.py` | `maybe_show_sidebar_tour` / `maybe_show_tabs_tour` — Visite guidée de première connexion utilisant `st.dialog` et `st.fragment` |
| `ui/rerun_utils.py` | `preserve_upload_rerun()` — Gestion fine des reruns pour éviter de perdre le fichier uploadé lors d'interactions sidebar |
| `ui/sidebar.py` | `SidebarResult` + `render_sidebar()` — tous les accordéons de la barre latérale : **Pays d'origine** (`home_country`, tout premier réglage, voir section dédiée ci-dessous), connexion SP-API, Validation & Devises, Cache VIES, Paramètres du fichier, Catalogue Produits, Entreprise & Paramètres avec gestion des SIREN, Abonnements & forfaits Stripe |
| `ui/billing_gate.py` | `BillingGate` + `build_billing_gate()` — détection de période, gating crédit PAYG/abonnement actif, gating quota SIREN, gating conformité (TVA locales/IOSS manquants), **rattachement anti-abus Compte Amazon <-> SIREN**, méthode `gated_download()` utilisée par tous les exports de tous les onglets |
| `ui/background_calc.py` | `start_background_job` / `render_job_progress` — Exécution des calculs longs (VIES/moteur) en thread séparé pour ne pas bloquer l'UI Streamlit |
| `ui/tabs/context.py` | `TabContext` — dataclass regroupant tout l'état nécessaire aux onglets (résultats moteur, statut billing, paramètres entreprise, données brutes d'import), construite une fois avant l'affichage des onglets |
| `ui/tabs/declarations.py` | Onglet **💶 Déclarations** — récapitulatif "Ce que vous devez reverser" (CA3, OSS par pays, IOSS, DDP, Fisc local), barre de seuil OSS, Contrôle de Cohérence Comptable |
| `ui/tabs/detail_ventes.py` | Onglet **📋 Détail ventes** — 4 sous-onglets : Ce que vous devez / Géré par des tiers / Ligne par ligne / Remboursements |
| `ui/tabs/vies.py` | Onglet **🛡️ VIES** — KPIs de validation, classification manuelle des numéros non vérifiés (`st.fragment`), overrides persistés, reclassifications B2B→B2C |
| `ui/tabs/audit.py` | Onglet **🔬 Audit Amazon** — écarts TVA Amazon par catégorie (taux, VIES, UK, autoliquidation art.194, TVA manquante), mouvements de stock FBA |
| `ui/tabs/telechargements.py` | Onglet **📥 Téléchargements** — génération de tous les exports (Excel complet, XML/Excel/CSV OSS, déclaration du **pays d'origine** en premier — CA3 HTML si FR, rapport HTML générique sinon —, déclarations locales HTML/CSV pour tous les autres pays (dont la France si elle n'est pas le pays d'origine), B2B, FEC) |
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
  volée vers la devise locale du pays d'origine (`rates.COUNTRY_CURRENCIES`),
  au taux BCE du jour de génération (`ecb_rates.convert_to_currency`,
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
- **Base de données partagée** : Postgres (Supabase), accessible à la fois depuis
  Streamlit Cloud (lecture des crédits/abonnements) et depuis la fonction serverless
  Vercel (écriture après paiement confirmé) — un SQLite local ne conviendrait pas
  puisque les deux environnements ne partagent aucun disque. Le pool de connexions
  (`auth.py` et `billing.py`) retente automatiquement une fois en cas de connexion
  fermée côté serveur par le pooler Supabase (PgBouncer, mode transaction, qui
  recycle agressivement les connexions inactives) — situation la plus visible en
  développement local, où le process Python (et donc le pool) survit longtemps
  entre deux requêtes.

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
*   **Classifications manuelles (vies_manual_overrides)** : Permet à l'utilisateur de forcer le statut d'un numéro indisponible ou inconclusif. Ces overrides sont strictement privés, ont une durée de vie indexée sur le TTL global, et **ne remontent jamais** dans le cache global.
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
- Cache deux niveaux : mémoire (`dict`) + disque (`~/.cache/tva_intracom/ecb_rates.json`).
- Écriture disque batché (toutes les 10 nouvelles entrées) pour éviter les I/O
  répétés sur les gros fichiers.
- **Warm-up du cache (Batch)** : Scanne les dates du fichier au démarrage et effectue
  une requête groupée vers l'API BCE pour toutes les devises concernées. Cette stratégie
  optimise radicalement le chargement pour les fichiers couvrant plusieurs années.
- **`convert_to_eur_for_oss()`** : taux BCE du **dernier jour de la période déclarée**
  (Règlement UE 2020/194, art. 5 bis) pour les ventes OSS en devise étrangère — au
  lieu du taux du jour de la vente. La CA3 conserve le taux du jour de l'opération.
- HRK (kuna croate) : taux fixe irrévocable 1 EUR = 7,53450 HRK depuis le 01/01/2023
  (Règl. UE 2022/1540).

### Import des fichiers Amazon

- **Performance extrême** : utilisation de **Polars** (moteur Rust ultra-rapide) pour le parsing des fichiers CSV volumineux, avec repli automatique sur Pandas et `csv.DictReader`.
- Détection automatique du format et du séparateur (tab / `;` / `,`).
- Support des fichiers jusqu'à **100 Mo**.
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
| 4 | **OSS par pays** | Agrégation par pays de destination + taux |
| 5 | **TVA locale par pays** | Immatriculations locales (stocks FBA hors FR) |
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

Dépendances principales : `streamlit`, `openpyxl`, `pandas`, `plotly`, `psycopg2-binary`
(base Postgres/Supabase pour l'auth et la facturation), `stripe` (paiements),
`requests` (appels à l'API Resend pour l'envoi des liens de connexion).

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
conversion BCE.

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
- **Optimisation Streamlit (`@st.fragment`)** : Utilisation intensive de fragments dans les onglets "Détail ventes" et "Téléchargements" pour isoler le rendu et éviter les reruns complets du script lors d'interactions locales (pagination, filtres de vue).
- **Mise en cache intelligente (TTL & Keys)** :
  - **Sidebar** : Cache TTL (20s) sur les appels coûteux (Amazon credentials, listes SIREN, quotas, abonnements Stripe, grille tarifaire) avec invalidation explicite immédiate après chaque mutation (ajout/suppression SIREN).
  - **Billing** : Réutilisation du cache SIREN/Abonnement déjà peuplé par la sidebar, éliminant les requêtes SQL dupliquées lors de la construction du tunnel de paiement.
  - **Téléchargements** : Mise en cache des 5 exports indépendants (Excel principal, OSS Excel, CA3/HTML local, B2B Excel, FEC) via une clé de téléchargement dédiée (`_dl_cache_key`).
- **Stabilisation du calcul** : Introduction de `calc_key` dans le `TabContext` (transmis depuis `app.py`) pour garantir la cohérence des résultats entre onglets et éviter les recalculs intempestifs.
- **Efficacité du moteur fiscal** : Optimisation de `engine.py` (résolution de la langue une seule fois par lot dans `_run_oss_loop` au lieu d'une résolution par vente dans `_note()`).
- **Stripe** : La session du portail de facturation (Billing Portal) est désormais créée uniquement au clic, au lieu d'être pré-générée à chaque rerun Streamlit.

### Correctifs & Expérience Utilisateur
- **Persistance de l'upload** : Correction d'un bug où le changement de langue supprimait les fichiers chargés (stabilisation de l'identité du widget `st.file_uploader` via une clé explicite `main_file_uploader` indépendante du label traduit).
- **Rendu des onglets** : Correction d'un blocage d'affichage lors du changement de pays d'origine (suppression d'un `st.rerun()` forcé qui interrompait le script avant le rendu).
- **Lisibilité des données** :
  - Colonnes "Note" et "Référence légale" élargies par défaut (`width="large"`) pour éviter la troncature des explications fiscales.
  - **Visualisations** : Amélioration de la légende des cartes (marge droite `r=90`, fond semi-opaque et bordure fixe) pour garantir la lisibilité sur petits écrans et en mode sombre.

---

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

- ~~Robustesse pool de connexions VIES (`vies.py`)~~ **Corrigé** : le pool
  Postgres utilisé pour les vérifications VIES parallèles (jusqu'à 25
  workers `ThreadPoolExecutor` concurrents) utilise désormais
  `psycopg2.pool.ThreadedConnectionPool` au lieu de `SimpleConnectionPool`
  (même API), qui n'était pas garanti thread-safe par psycopg2 dans ce
  scénario concurrent.

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

---

> Ce projet est un outil d'aide au calcul et à la préparation des déclarations.
> Il ne remplace pas un conseil fiscal professionnel.
> Les taux de TVA et seuils doivent être vérifiés et tenus à jour annuellement.