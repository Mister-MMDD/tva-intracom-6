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
| **IOSS_DIRECT** | Import ≤ 150 €, vendeur ayant explicitement activé son propre numéro IOSS (`ioss_own_number_active`, sinon `DEEMED_SUPPLIER` par défaut) | Vendeur collecte via IOSS | Vendeur | Guichet **IOSS** (mensuel, déclaration et export séparés de l'OSS) |
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
│   ├── amazon_adapter.py             Passerelle de compatibilité entre les anciens modèles de données et le nouveau package.
│   ├── amazon_spapi.py               Intégration Amazon Selling Partner API (SP-API) — OAuth 2.0 & Reports.
│   ├── auth.py                       Authentification historique par magic link + jeton de session (Postgres/Supabase).
│   │                                 Gère le chiffrement Fernet des PII (Amazon DPP).
│   ├── auth_supabase.py              Authentification par mot de passe et OAuth (Google, Microsoft, GitHub, Amazon)
│   │                                 déléguée à Supabase Auth. Flux PKCE.
│   ├── billing.py                    Facturation Stripe (PAYG + Pro + Cabinet, quotas SIREN, grille tarifaire).
│   │                                 Gère aussi le rattachement anti-abus Compte Amazon <-> SIREN.
│   ├── ca3_report.py                 Génération du rapport CA3 (HTML) : compute_ca3_lines_v2, AIC ligne 08.
│   ├── local_vat_report.py           Équivalent générique du CA3 pour tout pays UE hors France (rapport HTML harmonisé).
│   ├── fec_export.py                 Export comptable FEC (art. A47 A-1 LPF) : journal des ventes agrégé.
│   ├── cli.py                        Interface en ligne de commande (CLI).
│   ├── config.py                     Utilitaire de gestion des secrets.
│   ├── database.py                   Pooling Postgres centralisé (NonPoolingConnectionPool, run_with_retry).
│   ├── ecb_rates.py                  Taux BCE (cache mémoire + disque, convert_to_eur_for_oss).
│   ├── engine.py                     Moteur de classification fiscale (compute_vat, compute_all).
│   ├── excel_report.py               Export Excel multi-onglets.
│   ├── historical_rates_widget.py    Composant UI Streamlit pour l'historique des taux de change BCE.
│   ├── mem_utils.py                  Utilitaires d'analyse et d'optimisation de la mémoire (interning, RAM stats).
│   ├── models.py                     Dataclasses : Sale, VatResult, Scenario, BuyerType…
│   ├── oss_export.py                 Agrégation OSS partagée, exports Excel + CSV URSSAF.
│   ├── oss_xml.py                    Génération XML OSS officiel (Règl. UE 2021/965).
│   ├── rates.py                      Taux TVA historisés par pays (vat_rate_at_date).
│   ├── report.py                     ReportSummary, build_report, render_report.
│   ├── security.py                   Utilitaires de sécurité pour la conformité Amazon DPP (Data Protection Policy).
│   ├── vies_certificate.py           Génération de certificat de validité VIES en PDF (preuve de bonne foi).
│   ├── vies_engine.py                Validation VIES (Backend Postgres multi-niveaux, historique d'audit).
│   ├── ui/                           Découpage modulaire de l'interface Streamlit.
│   │   ├── theme.py                  Configuration de page + CSS de marque.
│   │   ├── formatting.py             Helpers d'affichage partagés.
│   │   ├── auth_flow.py              Authentification complète : mot de passe et OAuth via Supabase Auth.
│   │   ├── rerun_utils.py            Gestion fine des st.rerun().
│   │   ├── sidebar.py                Barre latérale complète (SIREN, IOSS, VIES, abonnements Stripe).
│   │   ├── billing_gate.py           Gating de facturation et conformité.
│   │   ├── background_calc.py        Exécution des calculs longs en thread séparé.
│   │   └── tabs/                     Modules d'onglets (Déclarations, Ventes, VIES, etc.).
│   
├── vercel_webhook/
│   └── api/
│       └── stripe_webhook.py         Endpoint webhook Stripe pour Vercel.
├── app.py                            Interface Streamlit — orchestrateur.
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
| `models.py` | Modèles de données (Pydantic) : Sale, VatResult, Scenario, BuyerType, Channel, Collector |
| `config.py` | Utilitaire de gestion des secrets (lwa, stripe, resend, postgres) avec fallback local |
| `database.py` | Gestion centralisée des connexions Postgres : `NonPoolingConnectionPool` compatible scale-to-zero |
| `engine.py` | Moteur de classification fiscale avec documentation légale intégrée (links Bofip/CGI/Dir) |
| `rates.py` | Taux TVA historisés par pays (vat_rate_at_date), is_eu, is_fiscal_eu, seuils |
| `security.py` | Sécurité Amazon DPP : chiffrement Fernet des PII avec protection Fail-Safe |
| `vies_certificate.py` | Génération de certificat de validité VIES en PDF (preuve de bonne foi) |
| `vies_engine.py` | Validation VIES : cache PostgreSQL à double niveau (privé/global), piste d'audit |
| `ecb_rates.py` | Taux BCE : cache deux niveaux (mémoire + Postgres), compliance OSS (taux de clôture) |
| `oss_export.py` | Agrégation OSS partagée, exports Excel + CSV URSSAF, détection soldes négatifs |
| `oss_xml.py` | Génération XML OSS officiel avec multi-validation XSD (DGFIP/UE) |
| `ca3_report.py` | Rapport CA3 (HTML) avec cases Cerfa vérifiées (A1, F2, E1, B2, 17, 08, 09, etc.) |
| `local_vat_report.py` | Rapport TVA générique pour pays UE hors France, ventilation HT/TVA par taux |
| `fec_export.py` | Export comptable FEC (journal des ventes agrégé, écritures équilibrées) |
| `excel_report.py` | Export Excel multi-onglets complet |

---

## Architecture de l'interface Streamlit (`tva_intracom/ui/`)

| Module | Rôle |
|---|---|
| `ui/theme.py` | Thème visuel et injection du CSS de marque |
| `ui/formatting.py` | Helpers d'affichage vectorisés, formatage monétaire et tri robuste |
| `ui/auth_flow.py` | Flux d'authentification complet (PKCE Supabase, Social OAuth, Sessions) |
| `ui/sidebar.py` | Barre latérale de configuration (SIREN, IOSS, Catalogue, Abonnements) |
| `ui/billing_gate.py` | Gating crédit PAYG, quotas SIREN et conformité fiscale |
| `ui/background_calc.py` | Exécution asynchrone des calculs longs avec suivi de progression |
| `ui/tabs/` | Modules d'onglets isolés consommant un `TabContext` partagé |

---

## Pays d'origine du compte (`home_country`)

Réglage global persistant permettant de définir le pays d'établissement du vendeur.

- **Impact Fiscal** : Définit le régime domestique (`DOMESTIC`). Les ventes sont classées comme domestiques si le stock ou la destination correspondent au pays d'origine.
- **Interface & Déclarations** : La déclaration du pays d'origine s'affiche en priorité (ex: CA3 pour la France, rapport local générique pour les autres pays).
- **Devise de calcul** : Le moteur calcule **toujours en EUR** pour la conformité fiscale. La conversion vers une devise d'affichage locale intervient uniquement dans la couche présentation.

---

## Authentification & Facturation

- **Supabase Auth** : Authentification sécurisée via flux PKCE. Supporte e-mail/mot de passe et OAuth (Google, Microsoft, GitHub, Amazon). Gestion des sessions persistantes par cookie (30 jours).
- **Facturation Stripe** :
    - **Pay-as-you-go** : Déblocage par période fiscale détectée dans les transactions.
    - **Pro** : Abonnement illimité pour 1 SIREN client.
    - **Cabinet** : Abonnement multi-SIREN avec tarif dégressif selon la quantité.
- **Quotas & Profils SIREN** : Mémorisation des paramètres par SIREN (IOSS, DDP, seuil OSS). Rattachement anti-abus Compte Amazon <-> SIREN pour limiter l'usage détourné.
- **Contenu gratuit limité** : Aperçu bridé des résultats (10 premières lignes, montants verrouillés) tant que la période n'est pas débloquée.
- **Base de données partagée** : Postgres (Supabase) centralisé, compatible scale-to-zero, utilisé par Streamlit et les webhooks Vercel.

---

## Formats Amazon supportés

| Format | Description | Clé de détection |
|---|---|---|
| **1** | Ancien format TSV | `departure_country`, `tax_calculation_date` |
| **2** | Format intermédiaire | `activity_period` |
| **3** | TSV/CSV 2024 | `transaction_complete_date` + `tax_collection_model` |
| **4** | CSV 2025+ | `transaction_complete_date` + `tax_collection_responsibility` |
| **5** | Rapport fiscal détaillé V5 | `our_price_tax_exclusive_selling_price` + `transaction_id` |

---

## Fonctionnalités clés

### Moteur fiscal
- **Typage strict Pydantic** et précision absolue via **Decimal**.
- **Documentation fiscale intégrée** : Références légales précises (Bofip, CGI, Directives UE) dans les notes de résultats.
- **Gestion des seuils** : Seuil OSS 10 000 € avec suivi multi-année et rattachement à l'année N-1.
- **Reverse charge domestique** : Gestion de l'art. 194 pour les pays concernés (ES, IT, PL, etc.).
- **Détection géographique** : Identification des territoires hors UE fiscale via code postal.

### Validation VIES
- **Architecture résiliente** à trois niveaux : Cache Privé > Cache Global > API UE.
- **Piste d'audit** : Historique append-only des vérifications pour justifier les exonérations B2B.
- **Certificat PDF** : Génération de preuve de validité opposable en cas de contrôle.

### Conversion devises
- **API BCE SDW** : Utilisation des taux officiels avec cache persistant.
- **Compliance OSS** : Application du taux du dernier jour de la période (Règl. UE 2020/194).
- **Multi-période** : Calcul automatique de la date de clôture propre à chaque trimestre.

### Import des fichiers Amazon
- **Polars** : Parsing haute performance (Rust) supportant des fichiers jusqu'à 150 Mo.
- **Détection intelligente** : Encodage (UTF-8/CP1252), format et séparateur automatiques.
- **Analyse d'exigibilité** : Distinction entre date de commande et date d'expédition.

### Export XML OSS officiel
- Structure conforme au Règlement UE 2021/965.
- **Multi-validation XSD** (DGFIP/UE) intégrée.
- **Correction assistée** : Rattachement automatique avoirs -> ventes d'origine pour résoudre les soldes négatifs via le bloc `CorrectionsOfVatReturns`.

---

## Export Excel — onglets générés

| # | Onglet | Contenu |
|---|---|---|
| 1 | **Récapitulatif** | Synthèse TVA par canal et Audit d'intégrité technique |
| 2 | **Détail ventes** | Ligne par ligne avec scénario, taux, canal, note légale |
| 3 | **Détail remboursements** | Avoirs détaillés avec structure identique |
| 4 | **OSS par pays** | Agrégation par pays de destination + taux, détail mensuel net |
| 5 | **TVA locale par pays** | Immatriculations locales (stocks FBA) avec détail mensuel net |
| 6 | **Audit Écarts Amazon** | Ventes où la TVA calculée diffère de celle collectée par Amazon |
| 7 | **Historique VIES** | Toutes les vérifications VIES horodatées (piste d'audit) |
| 8 | **Analyse AIC FBA** | AIC estimées par flux (art. 17 Dir. 2006/112/CE) |
| 9 | **Transferts FBA Détail** | Liste brute des mouvements de stock entre entrepôts |
| 10 | **Intrastat (EMEBI)** | Aide au remplissage : introductions et expéditions par mois/ASIN |
| 11 | **INVOICE & CREDIT_NOTE** | Détail des écritures de service Amazon |
| 12 | **Calendrier Fiscal** | Échéances OSS, CA3, Intrastat, ESL avec compte à rebours |

---

## Export comptable (FEC)

Génération d'un **journal des ventes au format FEC** (art. A47 A-1 LPF) pour import dans Sage, ACD, Quadratus, etc.
- Agrégation par période/régime/pays/taux pour limiter le volume d'écritures.
- Équilibre débit/crédit garanti, avec gestion des inversions de sens pour les soldes négatifs.
- Plan comptable paramétrable (racines 707 ventilées par pays).

---

## Calendrier fiscal généré automatiquement

Le moteur déduit les échéances directement des transactions :
- **OSS** : Dernier jour du mois suivant le trimestre.
- **CA3** : 24 du mois suivant.
- **Intrastat** : 10e jour ouvré du mois suivant.
- **ESL / Relevé TVA** : 24 du mois suivant.

---

## Intrastat / EMEBI (onglet 10)

Séparation stricte des deux obligations :
- **EMEBI** : Enquête statistique basée sur les seuils annuels (460 000 €).
- **État récapitulatif TVA (ESL)** : Obligation fiscale dès le 1er euro pour les livraisons B2B.

L'onglet fournit les flux UE->FR et FR->UE agrégés par ASIN, avec calcul de la valeur statistique estimée.

---

## Conformité Amazon DPP

Le moteur respecte les exigences de protection des données personnelles (PII) :
- **Chiffrement au Repos** : Algorithme Fernet (AES-128 CBC + HMAC-SHA256) pour les données sensibles.
- **Sécurité Fail-Safe** : Interdiction de traitement si la clé de chiffrement est absente.
- **Rétention limitée** : Suppression automatique des PII après 365 jours.
- **TLS/SSL forcé** pour tous les échanges avec la base de données.

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

## Optimisations de performance & UX

### Performance & Réactivité
- **String Interning** : Réduction drastique de l'empreinte RAM (codes pays, devises) via `sys.intern()`.
- **Fragments Streamlit** : Isolation du rendu pour éviter les reruns complets lors des interactions locales.
- **Cache intelligent** : Mise en cache des parsers, du catalogue ASIN et des exports via signatures MD5.
- **Pooling thread-safe** : Gestion optimisée des connexions Postgres pour supporter les calculs parallèles.

### UX & Fiabilité
- **MD5 Upload Signature** : Détection fiable des modifications de fichiers pour invalider le cache.
- **Persistance multilingue** : Interface et exports localisés en 7 langues (FR, EN, DE, ES, IT, PL, PT).
- **Warm-up BCE** : Chargement batch des taux de change au démarrage pour optimiser les exports multi-années.

---

## Tests

```bash
pytest -q
```
La suite couvre la classification fiscale, le cache VIES, le seuil OSS, les formats Amazon 1–5, la conversion BCE et la thread-safety du pool DB.

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
- **b2b_lines** : Découpage mensuel de l'état récapitulatif B2B (actuellement agrégé sur la période importée).

---
> Ce projet est un outil d'aide au calcul et à la préparation des déclarations.
> Il ne remplace pas un conseil fiscal professionnel.
