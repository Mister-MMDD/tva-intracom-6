# Mise à jour de `generate_dataset.py` pour 100 000 lignes

Ce plan vise à modifier le script `generate_dataset.py` pour générer un fichier de 100 000 lignes incluant des ventes, des remboursements et des transferts (FC Transfer).

## Modifications proposées

### [generate_dataset.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/generate_dataset.py)

1.  **Augmenter le nombre de lignes par défaut** : Passer `total_rows` de 2000 à 100 000 dans la fonction `generate_avsr_file`.
2.  **Ajouter des cas de transfert** : Intégrer des scénarios `TRANSFER_INTRA_EU` et `TRANSFER_DOMESTIC` dans la liste `CASES`.
3.  **Implémenter la logique de transfert** :
    *   `TRANSACTION_TYPE` fixé à `"FC_TRANSFER"`.
    *   Quantités positives.
    *   Montants financiers (prix, TVA) à 0.00 car les transferts de stock ne sont pas des ventes directes dans ce rapport.
    *   Pays de départ et d'arrivée aléatoires pour plus de réalisme.
4.  **Assurer la variété des remboursements** : Vérifier que les types `REFUND` sont bien générés.

## Plan de vérification

### Vérification Manuelle
- Exécuter le script : `python generate_dataset.py`.
- Vérifier que le fichier `vente_amazon_complet.csv` contient bien 100 001 lignes (en-tête + 100k données).
- Vérifier la présence des types de transaction : `SALE`, `REFUND`, `FC_TRANSFER`.
- Vérifier que les montants pour `FC_TRANSFER` sont bien à 0.
