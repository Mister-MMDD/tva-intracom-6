# Suppression de l'Onboarding

Ce plan détaille la suppression de la fonctionnalité de visite guidée (onboarding) de l'application afin d'améliorer les performances et la rapidité de chargement.

## User Review Required

> [!IMPORTANT]
> Cette modification supprime définitivement la visite guidée interactive. Les nouveaux utilisateurs n'auront plus de tutoriel pas-à-pas lors de leur première connexion.

## Proposed Changes

### [Moteur & UI]

#### [DELETE] [onboarding.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/ui/onboarding.py)
Suppression du module gérant les dialogues et la logique de la visite guidée.

#### [MODIFY] [app.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/app.py)
- Retrait des imports de `maybe_show_sidebar_tour` et `maybe_show_tabs_tour`.
- Suppression des appels à ces fonctions dans le flux principal.

#### [MODIFY] [auth.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/auth.py)
- Nettoyage du schéma de base de données (retrait des colonnes `onboarding_sidebar_seen` et `onboarding_tabs_seen` dans le code d'initialisation).
- Mise à jour de la dataclass `User` et des fonctions de mapping SQL.
- Suppression de la fonction `set_onboarding_seen`.

### [Documentation]

#### [MODIFY] [README.md](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/README.md)
- Retrait des mentions de la visite guidée dans la structure du projet et la liste des fonctionnalités.

## Verification Plan

### Manual Verification
- Lancer l'application et vérifier qu'aucun dialogue d'onboarding ne s'affiche à la connexion ou après un import.
- Vérifier les logs pour s'assurer qu'il n'y a pas d'erreurs d'import liées aux modules supprimés.
- Vérifier que la base de données s'initialise correctement (même si les colonnes existent déjà physiquement sur Supabase, le code ne doit plus essayer de les lire).
