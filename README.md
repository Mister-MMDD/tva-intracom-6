# TVA intracommunautaire — moteur de calcul (ventes marketplace / Amazon)

Modélise le flux de TVA intracommunautaire pour un vendeur (par défaut établi en
France) qui vend sur des places de marché type Amazon. À partir d'une liste de
ventes, le moteur détermine, pour chaque ligne, **qui doit collecter et reverser
la TVA, combien, à quelle administration et via quel canal**, puis produit un
récapitulatif global.

## Principe

Chaque vente est classée en croisant trois variables :

- **Où est le stock ?** (`stock_country`)
- **Qui est l'acheteur ?** B2C (particulier) ou B2B (entreprise) (`buyer_type`)
- **Où est l'acheteur ?** (`buyer_country`)

(+ le pays du vendeur `seller_country`, par défaut `FR`, et la validité du n° de
TVA acheteur `buyer_vat_valid` pour le B2B.)

## Les scénarios modélisés

| Scénario | Situation | Règle appliquée | Qui collecte | Canal |
|---|---|---|---|---|
| **DOMESTIC** | Stock et client dans le même pays UE | TVA locale du pays | Vendeur | CA3 (FR) ou immatriculation locale |
| **OSS_B2C** (Cas 1) | B2C, stock UE, client UE différent | TVA du **pays de destination** | Vendeur | Guichet **OSS** (déclaré en France) |
| **DEEMED_SUPPLIER** (Cas 2) | Vendeur hors UE, **ou** import ≤ 150 € depuis pays tiers (B2C) | Amazon est assujetti présumé | **Amazon** | — (vous recevez net) |
| **B2B_REVERSE_CHARGE** (Cas 3) | B2B intra-UE avec n° TVA valide (VIES) | Exonération, autoliquidation | Acheteur | — (facturation HT) |
| **EXPORT** | Client hors UE | Exonéré (export) | — | — |
| **IMPORT_STANDARD** | Import > 150 € depuis pays tiers (B2C) | TVA d'importation en douane | Importateur | — |

**Cas 4 (stocks FBA)** : tout pays UE (≠ FR) où réside du stock déclenche une
**obligation d'immatriculation TVA locale**, signalée dans le récapitulatif,
indépendamment de l'OSS.

> Note B2B : un B2B intra-UE **sans** n° de TVA valide ne peut pas bénéficier de
> l'autoliquidation et est traité comme une vente taxable (règles OSS/B2C).

## Installation

Aucune dépendance d'exécution (Python ≥ 3.10). Pour les tests : `pytest`.

```bash
pip install -e ".[dev]"   # optionnel
```

## Utilisation

### En ligne de commande

```bash
# Jeu de données d'exemple fourni
python -m tva_intracom.cli --details

# Sur votre propre fichier CSV
python -m tva_intracom.cli mes_ventes.csv --details
```

Format CSV attendu (voir `tva_intracom/data/ventes_exemple.csv`) :

```csv
sale_id,amount_ht,buyer_type,stock_country,buyer_country,seller_country,buyer_vat_valid,quantity
V001,100.00,B2C,FR,FR,FR,,1
V002,100.00,B2C,FR,DE,FR,,1
V003,200.00,B2B,FR,DE,FR,true,2
```

- `amount_ht` : base imposable (prix **hors taxe**), en euros.
- `seller_country` : optionnel, `FR` par défaut.
- `buyer_vat_valid` : `true`/`false` (B2B uniquement).

### En tant que bibliothèque

```python
from decimal import Decimal
from tva_intracom import Sale, BuyerType, compute_all, build_report, render_report

ventes = [
    Sale("V1", Decimal("100"), BuyerType.B2C, stock_country="FR", buyer_country="DE"),
    Sale("V2", Decimal("200"), BuyerType.B2B, stock_country="FR",
         buyer_country="DE", buyer_vat_valid=True),
]
resultats = compute_all(ventes)
print(render_report(build_report(resultats)))
```

## Exemple de sortie

```
--- Ce que VOUS devez reverser ---
Fisc francais - TVA domestique (CA3) : 50.00 EUR
Fisc francais - via guichet OSS (TVA pays destination) : 57.00 EUR
    dont DE : 57.00 EUR
Fisc locaux - immatriculation TVA requise :
    DE : 19.00 EUR
    PL : 18.40 EUR
=> Total TVA a reverser par vous : 144.40 EUR

--- Gere par des tiers / sans reversement de votre part ---
TVA collectee et reversee par Amazon (deemed supplier) : 29.00 EUR
...
--- Obligations d'immatriculation (stock FBA - Cas 4) ---
Numero de TVA local requis dans : DE, PL
```

## Tests

```bash
pytest -q
```

## Limites connues / pistes d'évolution

- **Taux standard uniquement** : les taux réduits par catégorie de produit ne
  sont pas modélisés (voir `tva_intracom/rates.py`).
- **Validation VIES** : `buyer_vat_valid` est fourni en entrée. Une intégration
  réelle au service VIES (vérification en ligne du n° de TVA) reste à brancher.
- **Seuil OSS de 10 000 €** : le moteur applique directement la TVA destination
  (OSS) pour les ventes B2C intra-UE transfrontalières ; le micro-seuil annuel
  de 10 000 € (en-dessous duquel la TVA du pays d'origine peut s'appliquer)
  n'est pas géré.
- Les taux de TVA sont ceux en vigueur en 2024/2025 et doivent être tenus à jour.

> Ce projet est un outil d'aide à la compréhension et au calcul. Il ne remplace
> pas un conseil fiscal professionnel.
