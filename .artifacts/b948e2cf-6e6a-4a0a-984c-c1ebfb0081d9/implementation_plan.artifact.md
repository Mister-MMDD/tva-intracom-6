# Refonte du Gating (Gratuit vs Payant) dans les Tableaux

Cette refonte vise à rendre l'application plus convaincante pour les utilisateurs gratuits en affichant les données de chiffre d'affaires (CA) et les conversions de devises, tout en gardant le calcul de la TVA protégé (cœur de la valeur ajoutée).

## User Review Required

> [!IMPORTANT]
> Les changements affectent directement la visibilité des données pour les utilisateurs non-payants.
> - **Déclarations** : Le CA par pays sera désormais visible.
> - **Détails des ventes** : Les colonnes HT, Devise et Montant original seront visibles pour toutes les lignes.

## Proposed Changes

### [Formatting Helper]

#### [MODIFY] [formatting.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/formatting.py)
- Ajouter un paramètre `extra_safe_cols` à la fonction `_gated_preview_table`.
- Mettre à jour la logique de masquage pour inclure ces colonnes dans la liste des colonnes "sûres" (non verrouillées).

---

### [Onglet Déclarations]

#### [MODIFY] [declarations.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/declarations.py)
- Modifier la logique de rendu de `_recap_preview` pour ne plus masquer les colonnes `ca_cols` sur les lignes de détail par pays (lignes `type_pays`).
- Les colonnes `tva_cols` resteront verrouillées (🔒).

---

### [Onglet Détails des Ventes]

#### [MODIFY] [detail_ventes.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/detail_ventes.py)
- Passer les libellés traduits des colonnes **HT**, **Devise** et **Montant orig.** à `_gated_preview_table` via le nouveau paramètre `extra_safe_cols`.
- Appliquer cela aux sous-onglets : "Ce que vous devez", "Ligne par ligne", et "Remboursements".

## Verification Plan

### Automated Tests
- N/A (Changements d'UI Streamlit).

### Manual Verification
1. Lancer l'application en mode "Gratuit" (simuler `can_export = False`).
2. Naviguer vers l'onglet **Déclarations** et vérifier que les montants HT par pays sont visibles.
3. Naviguer vers l'onglet **Détail ventes** et vérifier que :
    - La colonne **HT (EUR)** est visible.
    - La colonne **Devise** est visible.
    - La colonne **Montant orig.** est visible.
    - La colonne **TVA** reste verrouillée (🔒).
