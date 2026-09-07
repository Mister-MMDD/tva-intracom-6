# Correction de la suppression différée des SIREN hors-quota

L'objectif est de permettre une suppression immédiate des SIREN lorsqu'une organisation dépasse son quota de licences. Actuellement, si un abonnement est actif, la suppression est systématiquement différée à la fin de la période en cours, ce qui maintient le blocage "premium" (over-quota) inutilement longtemps.

## User Review Required

> [!IMPORTANT]
> Ce changement permet à un utilisateur hors-quota (ex: après un downgrade d'abonnement) de débloquer son compte immédiatement en supprimant les SIREN excédentaires, sans attendre la fin du mois/année de facturation.

## Proposed Changes

### Logic Facturation

#### [MODIFY] [billing.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/billing.py)

Modification de `request_siren_removal` :
- Appel à `get_siren_quota_status(org_id)` pour vérifier si l'organisation est hors-quota.
- Si `over_quota_by > 0`, l'échéance de suppression (`effective_at`) devient immédiate (`time.time()`), même si l'abonnement est actif.
- Cette logique s'applique également aux comptes PAYG s'ils se retrouvent par accident avec plus d'un SIREN (autorisant la suppression jusqu'à revenir à 1 SIREN, puis le verrou PAYG standard s'applique à nouveau).

### Tests

#### [MODIFY] [test_billing_payment_quotas.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tests/test_billing_payment_quotas.py)

- Ajout d'un cas de test `test_immediate_removal_if_over_quota_even_with_active_subscription`.
- Ajout d'un cas de test pour le déverrouillage partiel PAYG si hors-quota.

## Verification Plan

### Automated Tests
- Lancement des tests unitaires avec pytest :
  `pytest tests/test_billing_payment_quotas.py`

### Manual Verification
- Simulation dans l'interface (si possible via des logs ou en vérifiant le retour de la fonction dans un environnement de test).
