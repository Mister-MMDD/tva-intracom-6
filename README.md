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

## Architecture du projet

```
tva_intracom/
├── models.py          Dataclasses : Sale, VatResult, Scenario, BuyerType, Channel, Collector
├── engine.py          Moteur de classification fiscale (compute_vat, compute_all, compute_all_with_vies)
├── rates.py           Taux TVA historisés par pays (vat_rate_at_date), is_eu, is_fiscal_eu, seuils
├── vies.py            Validation VIES : cache SQLite (WAL), historique append-only, overrides manuels,
│                      retry exponentiel, batch degradation detection, 3 états (valid/invalid/unverified)
├── ecb_rates.py       Taux BCE : cache deux niveaux (mémoire + disque JSON), prefetch parallèle,
│                      convert_to_eur_for_oss (taux de clôture de période — Règl. UE 2020/194)
├── oss_export.py      Agrégation OSS partagée (aggregate_oss_results), exports Excel + CSV URSSAF
├── oss_xml.py         Génération XML OSS officiel (Règl. UE 2021/965), validation période,
│                      détection des soldes négatifs (CorrectionsOfVatReturns)
├── excel_report.py    Export Excel multi-onglets (voir détail onglets ci-dessous)
├── report.py          ReportSummary, build_report, render_report
├── amazon/
│   └── v2/
│       ├── loader.py      Point d'entrée : load_amazon_report(), AmazonImportResult
│       ├── detect.py      Détection format (1–5) et séparateur CSV, normalisation headers
│       ├── parsers.py     Parsers par format (Format1–5Parser) — extraction champs bruts
│       ├── classify.py    Classification acheteur (B2B/B2C), conversion devise, Sale builder
│       ├── aggregate.py   Pré-agrégation multi-juridictions format V5
│       └── constants.py   Constantes, SALE_TYPES, REFUND_TYPES, EU_VAT_PREFIXES, safe_decimal
└── app.py             Interface Streamlit
```

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

- Cache SQLite en mode WAL + `threading.local()` pour les appels parallèles.
- Table `vies_cache` : TTL configurable, UPSERT.
- Table `vies_check_history` : **append-only**, jamais écrasée — chaque vérification
  est journalisée avec horodatage UTC pour constituer une piste d'audit (preuve de
  bonne foi en cas de contrôle fiscal).
- Overrides manuels (`vies_manual_overrides`) : permet de forcer le statut d'un
  numéro VIES indisponible temporairement (serveur UE saturé).
- Purge au démarrage des entrées de cache malformées.
- 25 workers `ThreadPoolExecutor` en parallèle.
- Batch degradation detection + retry exponentiel.
- 3 états UI : valide / invalide / non vérifié.
- Normalisation `_normalize_full_vat()` : évite les faux rejets quand le préfixe
  EU diffère du pays de destination (ex: vente FR→DE avec numéro TVA IT).

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
  antérieure).

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

Dépendances principales : `streamlit`, `openpyxl`, `pandas`, `plotly`.

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
from tva_intracom.amazon.v2.loader import load_amazon_report

result = load_amazon_report(
    "rapport_amazon.csv",
    seller_country="FR",
    convert_currencies=True,   # conversion BCE automatique
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