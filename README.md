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
│   ├── amazon_adapter.py             Passerelle de compatibilité modèles de données.
│   ├── amazon_spapi.py               Intégration Amazon Selling Partner API (SP-API).
│   ├── auth.py                       Authentification historique (magic link) et gestion du chiffrement Fernet.
│   ├── auth_supabase.py              Authentification Supabase Auth (Mot de passe, OAuth Google, MS, GitHub, Amazon).
│   ├── billing.py                    Facturation Stripe (PAYG, Pro, Cabinet) et quotas SIREN.
│   ├── ca3_report.py                 Génération du rapport CA3 (HTML).
│   ├── local_vat_report.py           Rapports TVA locaux pour pays UE hors France.
│   ├── fec_export.py                 Export comptable FEC (journal des ventes).
│   ├── cli.py                        Interface en ligne de commande (CLI).
│   ├── config.py                     Gestion des secrets et variables d'environnement.
│   ├── database.py                   Pooling Postgres centralisé (NonPoolingConnectionPool).
│   ├── ecb_rates.py                  Taux BCE (cache multi-niveaux).
│   ├── engine.py                     Moteur de classification fiscale.
│   ├── excel_report.py               Export Excel multi-onglets.
│   ├── historical_rates_widget.py    Composant UI Streamlit pour l'historique BCE.
│   ├── mem_utils.py                  Utilitaires d'optimisation mémoire.
│   ├── models.py                     Dataclasses (Sale, VatResult, Scenario...).
│   ├── oss_export.py                 Agrégation OSS et exports CSV/Excel.
│   ├── oss_xml.py                    Génération XML OSS officiel.
│   ├── rates.py                      Taux TVA historisés par pays.
│   ├── report.py                     Construction et rendu des rapports de synthèse.
│   ├── security.py                   Sécurité Amazon DPP (Chiffrement Fernet).
│   ├── vies_certificate.py           Génération de certificat VIES PDF.
│   ├── vies_engine.py                Validation VIES avec cache Postgres.
│   ├── ui/                           Interface Streamlit modulaire.
│   │   ├── theme.py                  Thème et CSS.
│   │   ├── formatting.py             Helpers d'affichage.
│   │   ├── auth_flow.py              Flux d'authentification UI.
│   │   ├── onboarding.py             Visite guidée.
│   │   ├── sidebar.py                Barre latérale de configuration.
│   │   ├── billing_gate.py           Gating de facturation et conformité.
│   │   ├── background_calc.py        Calculs longs en thread séparé.
│   │   └── tabs/                     Modules d'onglets (Déclarations, Ventes, VIES, etc.).
│   
├── vercel_webhook/
│   └── api/
│       └── stripe_webhook.py         Endpoint webhook Stripe pour Vercel.
├── app.py                            Orchestrateur Streamlit.
├── mise.toml                         Config gestionnaire d'outils mise.
├── nixpacks.toml                     Config build Nixpacks.
├── Procfile                          Processus de démarrage.
├── pyproject.toml
├── railway.toml                      Config Railway.
├── README.md
├── requirements.txt
└── vercel.json                       Config Vercel.
```

---

## Architecture du moteur fiscal (`tva_intracom/`)

| Module | Rôle |
|---|---|
| `engine.py` | Moteur de classification fiscale avec documentation légale (Bofip/CGI/Dir). |
| `database.py` | Gestion centralisée Postgres : `NonPoolingConnectionPool` compatible scale-to-zero. |
| `security.py` | Sécurité Amazon DPP : chiffrement Fernet Fail-Safe des données personnelles (PII). |
| `vies_engine.py` | Validation VIES : cache PostgreSQL multi-niveau (privé/global) et piste d'audit. |
| `ecb_rates.py` | Taux BCE : cache multi-niveau et respect de la conformité OSS (taux de clôture). |
| `oss_xml.py` | Génération XML OSS conforme Règl. UE 2021/965 avec multi-validation XSD. |
| `ca3_report.py` | Rapport CA3 (HTML) avec cases Cerfa vérifiées (A1, F2, E1, B2, 17, 08, 09, etc.). |
| `fec_export.py` | Export FEC (journal des ventes) agrégé par régime/pays/taux. |
| `parsers/amazon/` | Import Amazon formats 1 à 5 avec pré-agrégation V5 et détection automatique. |
| `auth_supabase.py` | Authentification PKCE via Supabase (Social OAuth & Password). |
| `billing.py` | Facturation Stripe et protection anti-abus (Account Linking Amazon <-> SIREN). |

---

## Pays d'origine du compte (`home_country`)

Réglage global persistant permettant de définir le pays d'établissement du vendeur.

- **Impact Fiscal** : `home_country` définit le régime domestique (`DOMESTIC`). Les ventes sont classées comme domestiques si le stock ou la destination correspondent au pays d'origine.
- **Interface & Déclarations** : La déclaration du pays d'origine s'affiche en priorité (ex: CA3 pour la France). Les montants sont affichés dans la devise du pays par défaut.
- **Devise de calcul** : Le moteur calcule **toujours en EUR** pour la conformité fiscale, la conversion en devise d'affichage n'intervenant que dans la couche présentation.

---

## Authentification & Facturation

- **Supabase Auth** : Authentification sécurisée via flux PKCE. Supporte e-mail/mot de passe et OAuth (Google, Microsoft, GitHub, Amazon).
- **Stripe** : Modèle économique à 3 niveaux :
    - **Pay-as-you-go** : Déblocage par période fiscale.
    - **Pro** : Abonnement illimité pour 1 SIREN.
    - **Cabinet** : Abonnement multi-SIREN avec tarif dégressif.
- **Protection Amazon DPP** : Chiffrement applicatif Fernet (AES-128 CBC + HMAC-SHA256) des PII. Rétention des données limitée à 365 jours.

---

## Validation VIES & Devises

- **Validation VIES** : Architecture résiliente à trois niveaux (Cache Privé > Cache Global > API UE) avec ThreadPool parallèle (25 workers) et retry exponentiel.
- **Conversion BCE** : API ECB SDW avec cache persistant. Application du taux du dernier jour de la période pour l'OSS (Règl. UE 2020/194).

---

## Fonctionnalités Clés

### Moteur Fiscal
- Typage strict via **Pydantic** et précision **Decimal**.
- Taux TVA historisés et gestion des territoires hors UE fiscale.
- Documentation légale intégrée dans les notes de résultats.
- Seuil OSS 10 000 € avec suivi multi-année.
- Gestion des soldes OSS négatifs avec proposition de correction assistée par `sale_id`.

### Exports & Rapports
- **XML OSS** : Multi-validation XSD (DGFIP/UE).
- **Excel complet** : 12 onglets incluant détail ventes, audit écarts Amazon, AIC FBA, Intrastat/EMEBI et Calendrier Fiscal.
- **FEC** : Export comptable conforme, prêt pour import logicielle (Sage, ACD, etc.).
- **CA3** : Rapport HTML avec fac-similé fidèle au Cerfa officiel.
- **Certificat VIES** : Preuve PDF de validité pour chaque transaction B2B.

---

## Installation & Usage

```bash
pip install -e ".[dev]"
streamlit run app.py
```

### Usage Bibliothèque
```python
from tva_intracom.parsers.amazon import load_amazon_report
from tva_intracom.engine import compute_all

result = load_amazon_report("rapport.csv", seller_country="FR")
status = compute_all(result.sales)
```

---

## Optimisations de performance & UX

- **Veille Automatique (Auto-Sleep)** : Libération de la RAM après 30 min d'inactivité (Purge session_state).
- **Polars** : Parsing haute performance des fichiers CSV volumineux (jusqu'à 150 Mo).
- **Fragments Streamlit** : Isolation du rendu pour une interface réactive sans reruns complets.
- **String Interning** : Réduction drastique de l'empreinte RAM sur les gros volumes (100k+ lignes).
- **Cache Intelligent** : Mise en cache Sidebar, Catalogue ASIN et exports pour éviter les calculs redondants.
- **Multi-validation XSD** : Validation automatique du flux XML OSS par rapport aux schémas officiels.

---

## Roadmap

- **EDI-TVA** : Le rapport CA3 actuel est au format HTML (saisie manuelle EFI). L'export EDI-TVA pour télétransmission directe n'est pas implémenté (utiliser l'export FEC pour intégration logicielle).
- **Ventes Monaco** : Gestion spécifique via la convention franco-monégasque (assimilé FR).
- **Corrections OSS** : Génération du bloc `CorrectionsOfVatReturns` basée sur le `sale_id` (à valider selon les cas particuliers).

---

> Ce projet est un outil d'aide au calcul et à la préparation des déclarations.
> Il ne remplace pas un conseil fiscal professionnel.
> Les taux de TVA et seuils doivent être vérifiés et tenus à jour annuellement.