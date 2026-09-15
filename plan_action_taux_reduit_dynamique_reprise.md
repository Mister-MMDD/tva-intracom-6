# Plan d'action — Chantier taux réduit dynamique (CN/CPA) — reprise de session

Document de reprise pour tva-intracom-6, branche `dev`. Statut au 2026-09-15
(fin de session). Fait suite à `synthese_taux_reduit_dynamique_1.md` (cadrage
initial) — ce document consolide tout ce qui a été codé, découvert et décidé
depuis, et prépare la suite.

## 1. Statut d'ensemble

| Point du plan initial | Statut |
|---|---|
| 1. Colonnes brutes (commodity_code, product_tax_code, item_description) | **FAIT, validé** |
| 2. Suppression catalogue manuel + table product_tax_code_category | **FAIT, validé** |
| 3. Module de classification (map_product_tax_code_to_category) | **FAIT** (réalisé en même temps que le point 2) |
| 4. Activation progressive TEDB REDUCED (safe-list pays × catégorie) | **Non commencé** — bloqué sur la validation cabinet du mapping PTC→catégorie (section 4 ci-dessous) |
| 5. Tests de non-régression dédiés | **Non commencé** |
| Nouveau : gap A_GEN_NOTAX (hors champ TVA) | **Identifié, non traité** — hors scope CN/CPA, projet à part |

Validation technique en l'état : `pyflakes` propre sur tous les fichiers
modifiés, suite pytest complète **367 passed / 15 pre-existing failures**
(confirmé identique sur le repo non modifié — nouvelle baseline officielle,
l'ancienne 275/4 documentée dans `ways-of-working.md` était obsolète).

## 2. Ce qui a été codé (points 1 et 2)

### Point 1 — colonnes brutes
- `tva_intracom/parsers/amazon/constants.py` — `commodity_code`,
  `product_tax_code`, `item_description` ajoutés à `NEEDED_COLUMNS`
- `tva_intracom/parsers/amazon/parsers.py` — 3 accesseurs ajoutés sur la
  classe de base `_RowParser` (commun à tous les formats 1-5)
- `tva_intracom/parsers/amazon/loader.py` — lecture par ligne, agrégée dans
  `AmazonImportResult.product_tax_code_counts` / `.commodity_code_counts`
  (Counter, diagnostic léger — pas O(nb_lignes))

### Point 2 — suppression catalogue manuel, remplacé par product_tax_code_category

**Découverte critique avant codage** : la synthèse initiale ne listait qu'un
point de lookup catégorie (`loader.py`). En réalité il y en avait deux —
`engine.py::_run_oss_loop` RECALCULAIT `product_category` à la volée depuis
`asin_to_category` à chaque calcul, et ce résultat primait sur
`sale.product_category` fixé au parsing. Objectif de ce double mécanisme :
permettre de corriger le catalogue et relancer le calcul sans reparser le
fichier.

**Décision Matthieu (2026-09-15)** : ne PAS reproduire ce mécanisme.
`product_category` est résolue **une seule fois**, à l'import, et devient
la seule source de vérité (`Sale.product_category`). Une correction de
mapping en base impose de ré-importer le fichier — risque jugé acceptable
(repli STANDARD = taux le plus sûr, jamais de sous-déclaration).

**Fichiers modifiés :**

| Fichier | Changement |
|---|---|
| `tva_intracom/product_tax_code_category.py` (NOUVEAU) | Module de classification, pattern L1 mémoire (table entière chargée une fois) + L2 Postgres calqué sur `vat_rates_db.py`. Fonction publique `map_product_tax_code_to_category(code)`. **Aucun mapping pré-rempli** — décision explicite de ne pas inventer de correspondance fiscale sans validation cabinet. Tout code inconnu → `STANDARD` + audit `source='unresolved_default'` en base. |
| `parsers/amazon/loader.py` | `product_category = map_product_tax_code_to_category(row_product_tax_code)`, résolu une fois à l'import. Paramètre `asin_to_category` supprimé de `_process_rows`/`load_amazon_report` |
| `ui/sidebar.py` | Bloc UI "Catalogue Produits" supprimé (expander, uploader, `_parse_catalog_bytes`, `_MAX_CATALOG_MB`). `asin_to_category` retiré de `SidebarResult`. Import `sniff_upload_rejection_reason` devenu orphelin, retiré |
| `engine.py` | `asin_to_category` retiré de `_run_oss_loop` et `compute_all_with_vies` (API publique). Recalcul par ASIN remplacé par `product_category = sale.product_category or "STANDARD"` |
| `app.py` | 4 sites d'appel nettoyés. `_asin_catalog_sig` retiré de `_parse_cache_key` ET `_compute_cache_key()` (l'ancien bugfix du 2026-09-11 devient obsolète). `_asin_catalog_data` retiré du `_WHITELIST` session_state |
| i18n (7 TOML) | 6 clés orphelines supprimées (`catalog_header`, `catalog_upload`, `catalog_help`, `catalog_success`, `catalog_error`, `catalog_too_large`). Comptage passé de 1075 à 1069 clés/fichier, symétrie vérifiée par `tomllib.load` sur les 7 |

**Schéma table Postgres** (créée automatiquement au premier accès, comme
`vat_rate_cache`) :
```sql
CREATE TABLE IF NOT EXISTS product_tax_code_category (
    product_tax_code   VARCHAR(64) PRIMARY KEY,
    category           VARCHAR(20) NOT NULL,
    source             VARCHAR(20) NOT NULL,   -- 'known_mapping' | 'manual_override' | 'unresolved_default'
    resolved_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
Table **vide** au départ, aucune ligne seedée par le code.

**Validation effectuée** : `py_compile` + `pyflakes` propres. Suite pytest
complète 367/15 (identique baseline non modifiée). Smoke tests manuels :
`map_product_tax_code_to_category` (repli STANDARD sans DB configurée,
normalisation casse, cache L1) + `amazon.parse()` sur `amazon_sample.tsv`
réel et sur une variante fabriquée avec colonne `PRODUCT_TAX_CODE` ajoutée
— chaîne loader→classification→`Sale.product_category` confirmée
fonctionnelle de bout en bout.

## 3. Comment fonctionne réellement TEDB (clarification importante)

TEDB ne prend PAS un code produit en entrée. Il répond à : *"pour le pays X,
à la date Y, quel est le taux de la catégorie fiscale Z ?"* où Z est un
identifiant fixe parmi ~45-87 valeurs possibles (`FOODSTUFFS`,
`PHARMACEUTICAL_PRODUCTS`, etc.) — un appel = tout un pays, jamais un appel
par produit. Le cache existant (`vat_rate_cache`, un appel par (pays, mois),
résultat mis en base pour ne plus jamais rappeler l'API) fonctionne déjà
exactement comme le mécanisme envisagé pour `product_tax_code_category` —
rien de nouveau à inventer à ce niveau.

Le vrai travail de ce chantier est donc en 2 étapes indépendantes :
1. **PRODUCT_TAX_CODE Amazon → catégorie interne** (table `product_tax_code_category`, déjà codée au point 2)
2. **catégorie interne → catégorie TEDB** (déjà en place pour STANDARD/FOOD/MEDICINES via `_CATEGORY_TO_TEDB` dans `vat_rates_db.py`, à étendre si de nouvelles catégories internes sont créées)

TEDB gère lui-même "quel palier de taux s'applique dans quel pays" — pas
besoin de connaître à l'avance la structure des paliers par pays (standard/
réduit 1/réduit 2/super-réduit) pour l'utiliser, seulement d'interroger la
bonne catégorie.

## 4. Référentiels réels obtenus (2026-09-15) — le contenu complet est dans la mémoire du projet, fichier `tedb-amazon-ptc-reference.md`

Deux documents officiels/réels fournis par Matthieu, bien plus riches que
l'échantillon initial (qui ne contenait que `A_GEN_STANDARD`) :

1. **La vraie liste des ~80 `PRODUCT_TAX_CODE` Amazon Seller Central**,
   organisée par domaine (Général, Livres, Vêtements, Alimentaire,
   Santé/beauté, Extérieur, Bébé, Non concernés par la TVA)
2. **Le vrai dump des 87 `category_id` TEDB** (`diag_tedb_full_catalog.py`,
   situation 2026-01-01)

### 4.1 Findings clés de l'analyse croisée

- **`FOODSTUFFS` est une catégorie TEDB UNIQUE** — confirmé sur les 87
  `category_id` réels, aucune sous-catégorie alimentaire n'existe côté
  TEDB. L'ambiguïté FOOD (jusqu'à 3 taux par pays) ne peut donc PAS être
  résolue par la granularité des PTC Amazon (peu importe qu'on sache
  distinguer confiserie de produit de base côté Amazon, TEDB ne renvoie
  qu'UN SEUL taux `FOODSTUFFS` par pays) — seule la safe-list pays déjà
  établie (section 5 ci-dessous) permet de contenir le risque.
- **Piste `A_MED_REIMBURSED`/`A_MED_DRUGS` INVALIDÉE** — ces codes évoqués
  en cours de session n'existent PAS dans la vraie liste Amazon fournie
  par Matthieu. Ne pas la reprendre sans nouvelle source vérifiée.
- **`PHARMACEUTICAL_PRODUCTS` (26 pays) et `MEDICAL_EQUIPMENT` (23 pays)
  sont deux catégories TEDB distinctes** — opportunité réelle : séparer
  "médicaments" (`A_HLTH_PILLCAPSULETABLET`, `A_HPC_MEDICINE`) de
  "matériel médical" (`A_HPC_CONTACTLENSES`, `A_HPC_CORRECTIVEGLASSES`,
  `A_HPC_THERMOMETER`, `A_HPC_WALKINGSTICK`, `A_HPC_WHEELCHAIR`) — nouvelle
  catégorie interne `MEDICAL_EQUIPMENT` à créer si validé.
- **3 correspondances PTC↔TEDB propres et directes trouvées** :
  - `A_BABY_CARSEAT` ↔ TEDB `CHILDREN_CAR_SEATS` (5 pays : CY,CZ,EL,HR,PL)
  - `A_OUTDOOR_SOLARPANEL300` ↔ TEDB `SOLAR_PANELS` (5 pays : AT,FR,IE,LU,NL)
  - `A_BOOK_MAGAZINE` ↔ TEDB `PERIODICALS` (11 pays : AT,CY,CZ,DE,FI,FR,HR,LT,LV,MT,PL)
- **Gap confirmé : `A_GEN_NOTAX`** ("non concernés par la TVA") — vérifié
  par grep exhaustif sur `rates.py`/`models.py`/`engine.py` : AUCUNE notion
  d'exonération/hors-champ TVA n'existe dans le moteur actuel. Un produit
  `A_GEN_NOTAX` mappé par erreur vers STANDARD serait fiscalement FAUX dans
  le sens inverse du risque habituel (sur-déclaration de TVA sur un produit
  qui n'en doit pas). **Projet séparé, hors scope CN/CPA** : nécessite un
  vrai statut "hors champ TVA" sur `Sale`, impact probable sur la
  déclaration CA3/OSS.

### 4.2 Codes à trancher cabinet AVANT tout mapping (ambiguïté fiscale réelle)

- `A_FOOD_CNDY`, `A_FOOD_CEREALCHOCBARS`, `A_FOOD_CHOCEREAL`,
  `A_FOOD_SODAJUICE`/`UNSWEET` — confiserie/boissons sucrées souvent
  exclues du taux réduit alimentaire selon le pays
- `A_HLTH_VITAMINS`, `A_HPC_DIETARYSUPPL` — classification incohérente
  selon les pays (FOOD, MEDICINES ou STANDARD selon le pays)
- `A_HPC_CONTRACEPTIVE`, `A_HPC_SANITARYPRODUCTS`/`WASHABLE` — sujet
  "tampon tax", aucune catégorie TEDB dédiée trouvée, à rattacher au cas
  par cas
- `A_FOOD_ANIMALFOOD`/`ANIMALMED`/`ANIMALVITAMINS`/`PETFOOD` — alimentation
  animale, souvent hors régime alimentaire humain réduit
- Codes "livres" hors `A_BOOKS_GEN` (coloriage adulte, atlas, globe, plan,
  livre audio) — statut "livre" pas garanti dans tous les pays

## 5. Safe-list pays × catégorie déjà établie (dump TEDB, 28 territoires, 2026-01-01)

- **FOOD (FOODSTUFFS)** — sûrs (1 seul taux) : BG,CY,CZ,DE,ES,FI,FR,HR,LU,LV,NL,RO,SE,SI. Ambigus : AT,BE,EL,HU,IE,IT,MT,PL,PT,SK
- **MEDICINES (PHARMACEUTICAL_PRODUCTS)** — sûrs : AT,BG,CY,CZ,DE,EE,ES,FI,HU,LT,LU,LV,NL,PT,RO,SI,SK,XI. Ambigus : BE,EL,FR,HR,IE,IT,MT,PL
- BOOKS (1 seul pays CY), CLOTHING (aucune catégorie générale TEDB), SUPER_REDUCED (regroupement interne, pas une nature de bien) : repli statique permanent, hors scope points 4/5
- PARKING : aucune catégorie "PARKING" généraliste trouvée dans le dump réel, priorité basse, reporté

```python
_TEDB_CATEGORY_SAFE_COUNTRIES = {
    "FOOD": frozenset({"BG","CY","CZ","DE","ES","FI","FR","HR","LU","LV","NL","RO","SE","SI"}),
    "MEDICINES": frozenset({"AT","BG","CY","CZ","DE","EE","ES","FI","HU","LT","LU","LV","NL","PT","RO","SI","SK","XI"}),
}
```

**Précision importante sur les pays "ambigus"** : ils ne retombent PAS sur
STANDARD. `vat_rates_db.get_vat_rate()` retombe sur la table statique
`rates.py::REDUCED_VAT_RATES`, déjà peuplée pour la quasi-totalité des pays
ambigus (valeur unique, maintenue à la main, déjà en production). Le point
4 ne "corrige" donc pas des pays actuellement faux — il MODERNISE
(source dynamique au lieu de statique) les pays sûrs, en laissant les pays
ambigus sur leur valeur statique déjà correcte dans sa forme actuelle.
**Seule vraie exception : la Grèce (EL)**, absente de `REDUCED_VAT_RATES`
(aucune entrée FOOD ni MEDICINES) → retombe réellement sur STANDARD
aujourd'hui. Gap indépendant du chantier CN/CPA, proposé à Matthieu, pas
encore traité.

**Point ouvert non résolu** : cette safe-list reflète le dump du
2026-01-01 à un instant T — fréquence de revalidation à trancher.

## 6. Plan d'action détaillé pour la prochaine session

1. **Lire ce document + `tedb-amazon-ptc-reference.md` (mémoire projet) en
   entier** avant toute décision ou tout code.
2. Décider avec Matthieu du séquencement de validation cabinet :
   - Option A : valider tout le mapping PTC→catégorie en un bloc
   - Option B : par vagues (ex. MEDICINES en réutilisant la safe-list déjà
     connue, pendant que les cas ambigus de la section 4.2 sont étudiés à
     part par le cabinet)
3. Une fois un lot de mappings validé : les insérer directement dans la
   table Postgres `product_tax_code_category` avec `source='known_mapping'`
   — PAS en dur dans le code (cohérent avec le principe déjà établi que
   cette table est la seule source de vérité)
4. Coder le point 4 du plan initial dans `vat_rates_db.py` :
   a. Corriger le filtre `rtype` (l.532 `_parse_tedb_response`) — élargir
      en whitelist d'inclusion `("DEFAULT","REDUCED_RATE","SUPER_REDUCED_RATE","EXEMPTED")`,
      ne pas repasser en logique d'exclusion
   b. Faire évoluer `_is_tedb_eligible()` de `(category)` vers
      `(category, country_code)` avec `_TEDB_CATEGORY_SAFE_COUNTRIES`
   c. Réactiver `_PARSE_REDUCED_CATEGORIES = True`, FOOD d'abord, puis
      MEDICINES séparément
5. Si Matthieu veut avancer sur les nouvelles catégories internes
   (`MEDICAL_EQUIPMENT`, `PERIODICALS`, `CHILDREN_CAR_SEATS`,
   `SOLAR_PANELS`) : étendre `_VALID_CATEGORIES`
   (`product_tax_code_category.py`), `_CATEGORY_TO_TEDB`
   (`vat_rates_db.py`), et **vérifier que `rates.py::REDUCED_VAT_RATES` a
   un repli statique pour ces catégories dans les pays hors safe-list**
   (probablement absent actuellement — à vérifier avant toute activation,
   sinon un pays hors safe-list pour une nouvelle catégorie retomberait
   sur STANDARD au lieu d'une valeur statique correcte)
6. Point 5 du plan initial : tests unitaires classification +
   `_is_tedb_eligible`/`_parse_tedb_response` avec catégories réduites,
   couvrant explicitement un pays sûr et un pays ambigu par catégorie.
   Suite complète à revalider contre le nouveau baseline (367 passed / 15
   pre-existing failures).
7. Traiter `A_GEN_NOTAX` comme un chantier séparé, à planifier avec
   Matthieu — pas dans le scope CN/CPA.
8. **Une fois tout ce chantier (points 1 à 5) traité intégralement (fait,
   abandonné ou reporté) : mettre à jour le README évolution.md du repo**
   (règle systématique de Matthieu à chaque liste de points traitée).

## 7. Fichiers livrés en session (zips)

- `point1_cn_cpa_colonnes_brutes.zip` — constants.py, parsers.py, loader.py
- `point2_suppression_catalogue_manuel.zip` — app.py, engine.py,
  product_tax_code_category.py (nouveau), loader.py, sidebar.py, 7 TOML i18n
