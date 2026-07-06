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
| **DOMESTIC** | Stock et acheteur dans le même pays UE | TVA locale du pays | Vendeur | CA3 (FR) ou immatriculation locale |
| **OSS_B2C** | B2C intra-UE transfrontalier, stock EU, acheteur EU différent | TVA du pays de **destination** | Vendeur | Guichet **OSS** (déclaré en France) |
| **DEEMED_SUPPLIER** | Vendeur hors UE, ou import ≤ 150 € marketplace B2C | Amazon collecte et reverse | **Amazon** | — (vous recevez net) |
| **B2B_REVERSE_CHARGE** | B2B intra-UE avec n° TVA VIES valide | Exonération, autoliquidation acheteur | Acheteur | — (facturation HT) |
| **EXPORT** | Acheteur hors UE | Exonéré | — | — |
| **IMPORT_STANDARD** | Import > 150 € hors UE, B2C | TVA d'importation (douane) | Importateur | — |
| **IOSS_DIRECT** | Import ≤ 150 €, vendeur avec son propre numéro IOSS | Vendeur collecte via IOSS | Vendeur | Guichet IOSS |
| **IMPORT_SELLER_AS_IMPORTER** | Import > 150 €, vendeur = importateur officiel (DDP) | Vente domestique dans le pays de destination | Vendeur | Immatriculation locale |

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
│   ├── amazon_adapter.py
│   ├── auth.py                       Authentification magic link + jeton de session
│   │                                 (Postgres/Supabase), envoi d'e-mail via l'API Resend
│   ├── billing.py                    Facturation Stripe (PAYG + Pro + Cabinet, Customer
│   │                                 Portal, quotas SIREN, grille tarifaire, webhooks,
│   │                                 quotas d'export en base Postgres/Supabase)
│   ├── ca3_report.py                 Génération du rapport CA3 (HTML) : compute_ca3_lines_v2,
│   │                                 AIC ligne 08, deductions manuelles, generate_ca3_html_report_v2
│   ├── cli.py
│   ├── ecb_rates.py                  Taux BCE (cache mémoire + disque, convert_to_eur_for_oss)
│   ├── engine.py                     Moteur de classification fiscale (compute_vat, compute_all)
│   ├── excel_report.py               Export Excel multi-onglets
│   ├── historical_rates_widget.py
│   ├── models.py                     Dataclasses : Sale, VatResult, Scenario, BuyerType…
│   ├── oss_export.py                 Agrégation OSS partagée, exports Excel + CSV URSSAF
│   ├── oss_xml.py                    Génération XML OSS officiel (Règl. UE 2021/965)
│   ├── rates.py                      Taux TVA historisés par pays (vat_rate_at_date)
│   ├── report.py                     ReportSummary, build_report, render_report
│   ├── vies.py                       Validation VIES (Backend Postgres multi-niveaux, historique d'audit)
│   
├── vercel_webhook/
│   └── api/
│       ├── requirements.txt          Dépendances de la fonction serverless (stripe, psycopg2-binary)
│       └── stripe_webhook.py         Endpoint webhook Stripe, déployé sur Vercel — charge
│                                     tva_intracom/billing.py par chemin de fichier (monorepo)
├── .gitignore
├── app.py                            Interface Streamlit (auth, calcul, exports gatés par abonnement)
├── conftest.py
├── generer_donnees_10k.py
├── generer_donnees_multian.py
├── pyproject.toml
├── README.md
├── requirements.txt
└── vercel.json                       Config Vercel (includeFiles vers tva_intracom/billing.py)
```

> `amazon_adapter.py`, `cli.py` et `historical_rates_widget.py` sont présents dans le
> dépôt ; leur rôle exact par rapport au sous-package `parsers/amazon/` n'est pas
> documenté ici — se référer directement à leur code.

---

## Architecture du moteur fiscal (`tva_intracom/`)

| Module | Rôle |
|---|---|
| `models.py` | Dataclasses : Sale, VatResult, Scenario, BuyerType, Channel, Collector |
| `engine.py` | Moteur de classification fiscale (compute_vat, compute_all, compute_all_with_vies) |
| `rates.py` | Taux TVA historisés par pays (vat_rate_at_date), is_eu, is_fiscal_eu, seuils |
| `vies.py` | Validation VIES : cache PostgreSQL à double niveau (privé/global), historique append-only pour piste d'audit, overrides manuels par scope, résoluteur de domaine et retry exponentiel |
| `ecb_rates.py` | Taux BCE : cache deux niveaux (mémoire + disque JSON), prefetch parallèle, convert_to_eur_for_oss (taux de clôture de période — Règl. UE 2020/194), retry exponentiel (3 tentatives, 1s/2s/4s) sur erreurs réseau/HTTP transitoires |
| `oss_export.py` | Agrégation OSS partagée (aggregate_oss_results), exports Excel + CSV URSSAF, détection des soldes négatifs (find_oss_negative_buckets) |
| `oss_xml.py` | Génération XML OSS officiel (Règl. UE 2021/965), validation période, garde-fou soldes négatifs (CorrectionsOfVatReturns) |
| `ca3_report.py` | Génération du rapport CA3 (HTML) : compute_ca3_lines_v2, AIC ligne 08 (transferts FBA), déductions manuelles, calcul du solde net, generate_ca3_html_report_v2 |
| `excel_report.py` | Export Excel multi-onglets (voir détail onglets ci-dessous) |
| `report.py` | ReportSummary, build_report, render_report — ventilation HT exhaustive par canal fiscal (ht_by_bucket) servant de contrôle de cohérence interne |
| `parsers/amazon/` | Sous-package d'import Amazon (formats 1–5) — voir arborescence ci-dessus |
| `auth.py` | Authentification par magic link (Postgres/Supabase), envoi d'e-mail via l'API Resend |
| `billing.py` | Facturation Stripe : Checkout PAYG, Pro et Cabinet (mensuel/annuel, paliers dégressifs), Customer Portal, quotas SIREN par compte, grille tarifaire lue en direct sur Stripe, traitement des webhooks (`checkout.session.completed`, `customer.subscription.*`), quotas stockés en Postgres/Supabase, pool de connexions résilient aux coupures du pooler |
| `app.py` | Interface Streamlit (racine du dépôt, pas dans `tva_intracom/`) |

---

## Couche monétisation (SaaS)

- **Auth** : connexion par lien magique envoyé par e-mail (API Resend), jeton à usage
  unique valable 15 minutes, comptes stockés dans Supabase (table `tva_users`).
  Un jeton de session distinct (30 jours, réutilisable, porté dans l'URL
  `?session_token=`) permet de rester connecté après une redirection externe
  (paiement Stripe) ou un rafraîchissement de page, sans consommer un nouveau
  lien magique à usage unique. En développement local uniquement, le secret
  `LOCAL_DEV_BYPASS_AUTH` (jamais défini en production, à réserver au
  `.streamlit/secrets.toml` local non commité) permet de se connecter avec
  n'importe quelle adresse e-mail sans passer par Resend.
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
    Stripe (bouton "Gérer mon abonnement"), jamais via un nouveau Checkout —
    ce qui créerait un second abonnement Stripe indépendant plutôt que de
    modifier l'existant.
  - Aucun essai gratuit sur les abonnements (retiré : faussait les tests de
    bout en bout en environnement Stripe test, aucune transaction n'étant
    générée avant la fin de la période d'essai).
- **Quotas SIREN** : chaque compte enregistre les SIREN de ses clients
  (nom d'entreprise, SIREN, n° de TVA) dans la limite de son forfait — 1 pour
  PAYG/Pro, la quantité achetée pour Cabinet. Un compte au-dessus de son
  quota (ex. abonnement Cabinet redescendu à une quantité inférieure) voit
  tous ses exports bloqués tant qu'il n'est pas revenu dans les clous. Le
  retrait d'un SIREN par un compte Cabinet est différé (lazy deletion) : il
  reste utilisable jusqu'à la date anniversaire de l'abonnement en cours, pour
  éviter les ajouts/retraits à volonté en cours de période.
- **Grille tarifaire** : les montants affichés dans l'app (achat unique, Pro,
  paliers Cabinet) sont récupérés en direct depuis l'API Stripe
  (`billing.get_pricing_grid()`), jamais recopiés en dur, pour ne jamais
  diverger du tarif réellement configuré dans le Dashboard Stripe.
- **Contenu gratuit limité** : tant qu'une période n'est pas débloquée, les
  tableaux de résultats affichent un aperçu limité à 15 % des lignes (minimum
  1) via un rendu statique (sans bouton d'export CSV natif), et le détail par
  pays des déclarations (OSS, TVA locale) reste masqué — seules les lignes
  agrégées par canal restent visibles. Ceci limite la valeur d'un usage non
  payant sans bloquer l'analyse ; ce n'est pas une protection technique
  étanche (une sélection manuelle du texte affiché reste possible).
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

- Taux TVA historisés par pays avec gestion des changements de taux dans le temps
  (`vat_rate_at_date`).
- Taux réduits par catégorie produit (`product_category` : STANDARD, REDUCED,
  SUPER_REDUCED, ZERO, EXEMPT).
- Reverse charge domestique art. 194 pour ES, IT, PL, CZ, SK, HU, RO, BG, HR,
  LT, LV.
- Détection des territoires hors UE fiscale (Canaries, DOM-TOM, Åland, Helgoland…)
  via code postal (`is_non_fiscal_eu`).
- Seuil OSS 10 000 € opt-in, suivi multi-année avec `oss_ht_by_year`.
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
*   **Classifications manuelles (vies_manual_overrides)** : Permet à l'utilisateur de forcer le statut d'un numéro indisponible ou inconclusif. Ces overrides sont strictement privés, ont une durée de vie indexée sur le TTL global, et **ne remontent jamais** dans le cache global pour ne pas polluer les calculs des autres comptes.
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
- `prefetch_rates()` : pré-charge en parallèle (8 threads) toutes les devises/dates
  d'un fichier avant le traitement ligne par ligne.
- **`convert_to_eur_for_oss()`** : taux BCE du **dernier jour de la période déclarée**
  (Règlement UE 2020/194, art. 5 bis) pour les ventes OSS en devise étrangère — au
  lieu du taux du jour de la vente. La CA3 conserve le taux du jour de l'opération.
- HRK (kuna croate) : taux fixe irrévocable 1 EUR = 7,53450 HRK depuis le 01/01/2023
  (Règl. UE 2022/1540).

### Import des fichiers Amazon

- Détection automatique du format et du séparateur (tab / `;` / `,`).
- Filtrage des placeholders Amazon (`FRINV…`, `ITINV…`) et des NIF fiscaux nationaux
  (codice fiscale IT, NIF ES, NIP PL…) — ces derniers ne sont pas interrogeables VIES.
- Détection des territoires d'exception TVA via code postal de destination
  (`arrival_post_code`).
- `order_date` conservée distinctement de `transaction_date` (date d'exigibilité =
  date d'expédition, art. 65 Dir. 2006/112/CE) — permet de détecter les commandes à
  cheval sur deux périodes de déclaration (`period_mismatches`).
- Avertissements surfacés dans l'UI Streamlit pour les commandes à cheval.

### Export XML OSS officiel

- Structure conforme Règlement UE 2021/965 :
  `SupplyFromMemberState` → `SuppliesPerMemberStateOfConsumption` → `GoodsSupplies`.
- Qualification `STANDARD` / `REDUCED` basée sur `STANDARD_VAT_RATES[arrival_country]`
  (et non un seuil fixe).
- Validation de la période avant génération (formats : `YYYY-QN`, `YYYY-TN`, `YYYY-SN`,
  `YYYY`, `YYYY-QN_QM`, `YYYY-YYYY`).
- **Garde-fou soldes négatifs** : lève une erreur explicite si un couple (pays/taux)
  ressort en négatif (montants négatifs non acceptés dans le corps OSS — à traiter
  comme `CorrectionsOfVatReturns` sur le portail si l'avoir se rapporte à une période
  antérieure). Dans l'UI Streamlit, cette détection (`find_oss_negative_buckets`) est
  effectuée en amont du clic sur le bouton de génération, avec un bloc d'alerte
  explicatif affiché avant toute tentative.

### Interface Streamlit — contrôles complémentaires

- **Barre de progression** sur le parsing des rapports Amazon volumineux, via le
  paramètre `progress_callback` de `load_amazon_report()`.
- **Contrôle de cohérence comptable** : ventilation exhaustive et mutuellement
  exclusive du CA HT par canal fiscal (`ReportSummary.ht_by_bucket` dans `report.py`),
  recalculée indépendamment du total global. Un écart révèle un scénario de vente non
  couvert par la classification plutôt qu'une erreur silencieuse. Ce contrôle vérifie
  la cohérence *interne* du moteur — il ne remplace pas un rapprochement avec le
  relevé de règlements Amazon (commissions, frais, remises non couverts).

---

## Export Excel — onglets générés

| # | Onglet | Contenu |
|---|---|---|
| 1 | **Récapitulatif** | Synthèse TVA par canal (CA3, OSS, local, Amazon, douane) |
| 2 | **Détail ventes** | Ligne par ligne avec scénario, taux, canal, note |
| 3 | **Détail remboursements** | Avoirs avec même structure |
| 4 | **OSS par pays** | Agrégation par pays de destination + taux |
| 5 | **TVA locale par pays** | Immatriculations locales (stocks FBA hors FR) |
| 6 | **Audit Écarts Amazon** | Ventes où la TVA calculée diffère de celle collectée par Amazon |
| 7 | **Historique VIES** | Toutes les vérifications VIES horodatées (piste d'audit) |
| 8 | **Analyse AIC FBA** | AIC estimées par flux (art. 17 Dir. 2006/112/CE), TVA AIC à autodéclarer |
| 9 | **Transferts FBA Détail** | Liste brute des mouvements de stock FC |
| 10 | **Intrastat (DEB)** | Aide au remplissage : introductions et expéditions par mois/ASIN/flux |
| 11 | **Calendrier Fiscal** | Prochaines échéances OSS, CA3, Intrastat, ESL avec jours restants |
| 12 | **Historique VIES** | Piste d'audit VIES (append-only, preuve de bonne foi en contrôle fiscal) |

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

## Intrastat (DEB)

L'onglet Intrastat est pré-rempli à partir des mouvements de stock FC détectés :

- **Introductions** (flux UE → FR) et **Expéditions** (flux FR → UE) séparées.
- Agrégation par mois, pays et ASIN.
- Nature de transaction : `11 — Transfert stock (art. 17 Dir. 2006/112/CE)`.
- Valeur statistique estimée = prix de vente HT moyen × quantité (Amazon ne fournit
  pas la valeur d'achat — approximation par excès, art. 83 Dir. 2006/112/CE).
- **Code NC (CN8) et masse nette** : colonnes `À COMPLÉTER` manuellement (non
  disponibles dans les fichiers Amazon).
- Seuils 2024 : 460 000 €/an (introductions et expéditions).
- Dépôt : [pro.douane.gouv.fr](https://pro.douane.gouv.fr).

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

> Ce projet est un outil d'aide au calcul et à la préparation des déclarations.
> Il ne remplace pas un conseil fiscal professionnel.
> Les taux de TVA et seuils doivent être vérifiés et tenus à jour annuellement.