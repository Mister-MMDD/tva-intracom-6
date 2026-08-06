# Nettoyage des identifiants fiscaux nationaux (NIF) dans l'historique VIES

Ce plan vise à nettoyer les tables de cache et d'historique VIES des numéros qui sont en réalité des identifiants fiscaux nationaux (NIF) et non des numéros de TVA intracommunautaires. Suite à la séparation de la logique VIES et NIF, ces anciennes entrées polluent l'onglet "Historique VIES" et le certificat de validité.

## Proposed Changes

### VIES Engine

#### [MODIFY] [vies_engine.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/vies_engine.py)
- Ajout d'une fonction `purge_legacy_nifs()` qui exécute une requête SQL `DELETE` ciblée sur les formats NIF connus (Italie, Espagne, Pologne, etc.) lorsqu'ils sont marqués comme invalides dans les tables VIES.

### UI / Authentication Flow

#### [MODIFY] [auth_flow.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/auth_flow.py)
- Appel de `purge_legacy_nifs()` lors de l'initialisation de la session (en complément de la purge des entrées mal préfixées).

## Verification Plan

### Automated Tests
- Exécution manuelle de la fonction de purge via un script de test et vérification du nombre de lignes supprimées.

### Manual Verification
- Vérifier dans Supabase que les entrées comme `IT11739700968` (avec `valid=FALSE`) ont disparu de `vies_check_history`.
- Vérifier que l'onglet "Historique VIES" dans l'export Excel ne contient plus ces numéros.
