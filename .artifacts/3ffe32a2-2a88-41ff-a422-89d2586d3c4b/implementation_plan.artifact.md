# Filtrage des taux TVA historiques par pertinence temporelle

Ce plan vise à corriger un bug d'affichage dans l'encart "📅 Taux TVA historiques détectés". Actuellement, des taux très anciens (ex: commençant en 2000) sont affichés simplement parce qu'ils couvrent la période des ventes, même s'ils n'ont pas changé récemment.

L'objectif est de ne considérer comme "changements historiques" que les modifications de taux survenues depuis l'année précédant la commande la plus ancienne de chaque pays.

## Changements Proposés

### Moteur d'affichage UI

#### [MODIFY] [historical_rates_widget.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/historical_rates_widget.py)

Modification de la logique de boucle dans `render_historical_rates_alert` pour appliquer le nouveau filtre :

1.  Pour chaque pays, calculer `window_start = date(T_min.year - 1, 1, 1)`, où `T_min` est la date de la vente la plus ancienne pour ce pays dans le fichier importé.
2.  Ignorer toute période (`_VatPeriod`) dont la date de début (`date_from`) est strictement antérieure à `window_start`.
3.  Mettre à jour la légende en bas de l'encart pour refléter cette règle de filtrage dynamique plutôt qu'une date fixe (01/01/2024).

## Vérification Plan

### Manuel
1.  Importer un fichier contenant des ventes en Autriche uniquement en 2026.
    *   **Attendu** : Si le dernier changement en Autriche date de 2000, l'Autriche ne doit plus apparaître dans l'encart des taux historiques (car 2000 < 2025).
2.  Importer un fichier contenant des ventes en Espagne de 2004 à 2026.
    *   **Attendu** : Afficher les changements survenus depuis 2003 (N-1 de 2004). Le changement de 2010 (18%) et 2012 (21%) devrait apparaître, mais pas celui de 2000 (16%).
3.  Vérifier que le titre de l'encart ("X pays concerné(s)") se met à jour correctement.
4.  Vérifier que si un changement récent existe mais qu'aucune vente ne tombe dedans, il reste masqué (comportement actuel préservé).
