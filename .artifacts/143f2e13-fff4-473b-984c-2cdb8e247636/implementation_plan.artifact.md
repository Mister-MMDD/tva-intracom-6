# Plan d'implémentation - Finalisation du durcissement PII

Ce plan vise à clôturer l'optimisation n°8 ("Durcissement du déchiffrement PII") en mettant à jour la documentation et en nettoyant les commentaires historiques dans le code, le durcissement technique étant déjà effectif.

## Changements proposés

### Documentation

#### [MODIFY] [optimisations_en_attente.md](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/optimisations_en_attente.md)
*   Déplacer l'item 8 ("Durcissement du déchiffrement PII") de la section "Sécurité" vers une nouvelle section "Optimisations Clôturées" (ou le marquer comme [CLOS]).
*   Mettre à jour le statut pour indiquer que la migration a été confirmée et la tolérance retirée le 2026-08-16.

### Code Source

#### [MODIFY] [security.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/security.py)
*   Simplifier les commentaires de `decrypt_data` : retirer les mentions "audit" et la description détaillée de l'ancienne heuristique fail-open, pour ne garder que la logique de validation actuelle (plus propre pour la production).

#### [MODIFY] [auth.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/auth.py)
*   Nettoyer la docstring de `get_amazon_credentials` qui mentionne encore le "retrait récent" du fail-open comme une nouveauté.

#### [MODIFY] [billing.py](file:///D:/Utilisateurs/matth/Visual%20Studio%20projets/tva-intracom%206/tva_intracom/billing.py)
*   Nettoyer le commentaire de `register_siren` relatif à la sécurité PII.

## Plan de vérification

### Vérification Manuelle
*   Vérifier que le fichier `optimisations_en_attente.md` est à jour.
*   Vérifier que les commentaires dans `security.py`, `auth.py` et `billing.py` sont plus concis et ne traitent plus le durcissement comme une opération en cours ou récente.
