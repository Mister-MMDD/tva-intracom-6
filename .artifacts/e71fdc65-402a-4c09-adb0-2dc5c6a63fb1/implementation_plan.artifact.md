# Plan d'implémentation - Harmonisation des rapports VIES et NIF

Ce plan vise à harmoniser les tableaux VIES et NIF dans l'onglet "VIES", à inclure les NIF dans les graphiques de TVA récupérée, et à enrichir l'export CSV.

## Changements proposés

### Internationalisation (i18n)

#### [MODIFY] [fr.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/fr.toml)
- Mettre à jour `vies_dl_btn` pour mentionner les NIF.
- Ajouter `vies_col_type` pour la nouvelle colonne dans l'export.
- Mettre à jour `vies_col_national_id` pour "Identifiant national NIF".

### Interface Utilisateur (UI)

#### [MODIFY] [vies_ui.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/vies_ui.py)
- **Tableau NIF** : Mettre à jour le second tableau (dans l'expander des identifiants nationaux) pour afficher les mêmes colonnes que le premier tableau (HT, TVA récupérée, Statut, Explication).
- **Graphique** : Modifier la logique du graphique pour inclure toutes les reclassifications ayant une TVA récupérée (`vat_delta > 0`), qu'il s'agisse de rejets VIES ou de NIF nationaux.
- **Export CSV** :
    - Ajouter une colonne "Type" (NIF ou VIES) pour chaque ligne.
    - S'assurer que le bouton d'export utilise le nouveau libellé.

## Plan de vérification

### Vérification manuelle
1. Charger un fichier contenant à la fois des rejets VIES et des NIF nationaux (ex: ventes domestiques ES/IT sans préfixe).
2. Vérifier dans l'onglet VIES que :
    - Le tableau des NIF (dans l'expander) contient bien toutes les colonnes financières et explicatives.
    - Le graphique en bas de page cumule les montants des deux types de rejets.
3. Exporter le rapport CSV et vérifier :
    - Le nom du fichier/bouton est correct.
    - La nouvelle colonne "NIF ou VIES" est bien présente et correctement remplie.
