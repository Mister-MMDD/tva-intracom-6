# Plan d'augmentation des tests et vérification des bugs

Ce plan vise à augmenter la couverture de tests pour les cas limites (territoires spéciaux, changements de taux historiques, etc.) et à identifier les comportements potentiellement erronés ou incomplets dans le moteur de calcul de TVA.

## User Review Required

> [!IMPORTANT]
> La liste `DOMESTIC_REVERSE_CHARGE_COUNTRIES` dans `rates.py` semble incomplète. Actuellement, elle n'inclut pas l'Allemagne (DE), la Belgique (BE) ou les Pays-Bas (NL), qui appliquent pourtant l'autoliquidation nationale (Art. 194) pour les vendeurs non-établis. Est-ce un choix délibéré ou un oubli ?

## Proposed Changes

### Tests

#### [NEW] [test_bugs_and_edge_cases.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tests/test_bugs_and_edge_cases.py)
Création d'un nouveau fichier de tests couvrant :
- **Territoires spéciaux** : Vérification des codes postaux pour les Canaries, Heligoland, Livigno, Åland, et les DOM français (Martinique, Guadeloupe, etc.).
- **Taux historiques** : Vérification des changements de taux récents ou à venir (Finlande sept. 2024, Slovaquie janv. 2025, Estonie juil. 2025).
- **Monaco** : Cas plus complexes (ex: vente depuis l'Allemagne vers Monaco).
- **IOSS** : Vérification du seuil de 150€ avec conversion de devise.
- **Normalisation TVA** : Cas limites de numéros de TVA avec des formats inhabituels.

### Moteur de calcul (Optionnel, après validation)

#### [MODIFY] [rates.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/rates.py)
- Mise à jour potentielle de `DOMESTIC_REVERSE_CHARGE_COUNTRIES` si confirmé par l'utilisateur.
- Vérification des expressions régulières ou des listes de préfixes pour les territoires spéciaux.

## Verification Plan

### Automated Tests
- Exécution de `pytest tests/test_bugs_and_edge_cases.py`
- Exécution de l'ensemble de la suite de tests existante pour s'assurer de l'absence de régression.

### Manual Verification
- Comparaison des résultats des nouveaux tests avec les sources fiscales officielles (CGI, Directives Européennes).
