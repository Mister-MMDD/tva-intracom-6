# Suppression du commit 4203a53 contenant des fichiers volumineux

Le commit `4203a53` contient 7 fichiers CSV dans le dossier `data/` qui pèsent environ 70 Mo au total. L'objectif est de supprimer ce commit de l'historique GitHub pour alléger le dépôt, tout en conservant les fichiers localement (ils sont déjà dans le `.gitignore`).

## User Review Required

> [!IMPORTANT]
> Cette opération réécrit l'historique Git. Un **force push** sera nécessaire pour mettre à jour GitHub. Si d'autres personnes travaillent sur cette branche (`dev`), elles devront effectuer un `git pull --rebase` pour synchroniser leur historique.

## Proposed Changes

### Git History Cleanup

L'approche consiste à utiliser `git rebase` pour sauter le commit `4203a53` (qui ne contient que ces fichiers CSV) et ré-appliquer les commits suivants.

#### [MODIFY] Git History
- Utilisation de `git rebase --onto` pour supprimer le commit de l'historique de la branche `dev`.
- Force push vers `origin dev`.

## Verification Plan

### Automated Tests
- `git log --oneline` pour vérifier que le commit `4203a53` a disparu.
- `git ls-tree -r HEAD` pour vérifier que les fichiers CSV ne sont plus suivis par Git.

### Manual Verification
- Vérifier sur l'interface GitHub que le commit n'apparaît plus dans l'historique.
