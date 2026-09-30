# Contrats d'interface entre modules

Ce document décrit les interfaces critiques entre les modules du système TVA intracommunautaire. Pour chaque contrat, nous documentons le flux de données, les types échangés, les conditions d'appel, la gestion des erreurs et les points d'extension.

---

## Auth → Billing

### Description
L'authentification transmet l'identité de l'utilisateur (org_id, user_id) au module de facturation pour déterminer les droits d'accès et les quotas.

### Flux de données
```
auth.py → billing.py
  org_id: str
  user_id: str
  email: str
```

### Types de données
- `org_id` (str) : Identifiant unique de l'organisation
- `user_id` (str) : Identifiant unique de l'utilisateur
- `email` (str) : Email de l'utilisateur

### Conditions d'appel
- L'utilisateur doit être authentifié
- L'organisation doit exister dans la base de données
- La session doit être valide (non expirée)

### Gestion des erreurs
- `OrganizationNotFound` : L'organisation n'existe pas
- `SessionExpired` : La session est expirée
- `DatabaseError` : Erreur lors de l'accès à la base de données

### Points d'extension
- Aucun point d'extension prévu

---

## Billing → Engine

### Description
Le module de facturation détermine si un calcul TVA peut être effectué en vérifiant les quotas SIREN et le statut d'abonnement.

### Flux de données
```
billing.py → engine.py
  can_export: bool
  billing_ok: bool
  gated_download: callable
```

### Types de données
- `can_export` (bool) : Permission d'exporter
- `billing_ok` (bool) : Statut billing OK
- `gated_download` (callable) : Fonction de gating pour les téléchargements

### Conditions d'appel
- L'utilisateur doit être authentifié
- Le SIREN doit être enregistré pour l'organisation
- Le quota SIREN ne doit pas être dépassé
- Pour PAYG : un crédit d'export doit être disponible pour la période

### Gestion des erreurs
- `QuotaExceededError` : Le quota SIREN est dépassé
- `CreditUnavailableError` : Aucun crédit d'export disponible
- `ComplianceError` : Conformité TVA/IOSS manquante

### Points d'extension
- `billing_gate.py` : Custom gating logic

---

## Engine → VIES

### Description
Le moteur TVA valide les numéros TVA des acheteurs B2B via le service VIES pour déterminer le régime applicable (reverse charge ou B2C).

### Flux de données
```
engine.py → vies_engine.py
  vat_number: str
  scope_id: str | None
  → dict {valid, name, address, request_date}
```

### Types de données
- `vat_number` (str) : Numéro TVA à valider
- `scope_id` (str | None) : ID du scope pour le cache privé
- Retour : dict avec `valid` (bool), `name` (str), `address` (str), `request_date` (date)

### Conditions d'appel
- Le numéro TVA doit être au format ISO
- Le cache peut être utilisé si disponible
- Le service VIES doit être accessible

### Gestion des erreurs
- `ValueError` : Numéro TVA invalide
- `ViesServiceUnavailable` : Service VIES indisponible
- `NetworkError` : Erreur réseau après retries

### Points d'extension
- Overrides manuels par scope (`set_vies_override`)
- Cache à double niveau (privé/global)

---

## Engine → ECB

### Description
Le moteur TVA convertit les montants en EUR via les taux de change de la BCE pour le calcul OSS.

### Flux de données
```
engine.py → ecb_rates.py
  currency: str
  date: date
  → Decimal | None
```

### Types de données
- `currency` (str) : Code devise ISO
- `date` (date) : Date du taux souhaité
- Retour : Decimal (taux) ou None

### Conditions d'appel
- La devise doit être un code ISO valide
- La date doit être valide
- Le cache peut être utilisé si disponible

### Gestion des erreurs
- `ValueError` : Devise ou date invalide
- `RateUnavailableError` : Taux non disponible
- `NetworkError` : Erreur réseau après retries

### Points d'extension
- Cache deux niveaux (mémoire + PostgreSQL)
- Prefetch parallèle

---

## Engine → TEDB

### Description
Le moteur TVA récupère les taux de TVA dynamiques via l'API TEDB avec repli sur les tables statiques.

### Flux de données
```
engine.py → vat_rates_db.py
  country: str
  date: date
  category: str
  → Decimal
```

### Types de données
- `country` (str) : Code pays ISO
- `date` (date) : Date du taux souhaité
- `category` (str) : Catégorie de produit
- Retour : Decimal (taux)

### Conditions d'appel
- Le pays doit être un code ISO valide
- La date doit être valide
- La catégorie doit être valide
- `VAT_DYNAMIC_TEDB_ENABLED` peut désactiver l'API

### Gestion des erreurs
- `ValueError` : Paramètres invalides
- `TedbRateInvalidError` : Taux TEDB invalide (garde-fou déclenché)
- `NetworkError` : Erreur réseau après retries

### Points d'extension
- Repli sur tables statiques (`rates.py`)
- Garde-fou de plausibilité (écart > 3 points)
- Mapping catégories → TEDB

---

## Engine → CA3 Report

### Description
Le moteur TVA transmet les résultats calculés au module CA3 pour générer le rapport et les lignes CA3.

### Flux de données
```
engine.py → ca3_report.py
  results: list[VatResult]
  refund_results: list[VatResult]
  all_fc_transfers: list
  → dict {ca3_lines, aic_base, aic_tva}
```

### Types de données
- `results` (list[VatResult]) : Résultats des ventes
- `refund_results` (list[VatResult]) : Résultats des remboursements
- `all_fc_transfers` (list) : Transferts FBA
- Retour : dict avec les lignes CA3 et les montants AIC

### Conditions d'appel
- Les résultats doivent être valides
- Les transferts FBA doivent être disponibles pour l'AIC
- Le vendeur doit être établi en France (module CA3 FR)

### Gestion des erreurs
- `ValueError` : Résultats invalides
- `AicCalculationError` : Erreur lors du calcul de l'AIC
- `Ca3EdiRegimeMismatchError` : Incohérence régime/période

### Points d'extension
- Option `ignore_aic_calculated` pour ignorer l'AIC calculée
- Déductions manuelles paramétrables

---

## Engine → Excel

### Description
Le moteur TVA transmet les résultats calculés au module Excel pour générer l'export multi-onglets.

### Flux de données
```
engine.py → excel_report.py
  results: list[VatResult]
  refund_results: list[VatResult]
  summary: ReportSummary
  all_fc_transfers: list
  → bytes (fichier Excel)
```

### Types de données
- `results` (list[VatResult]) : Résultats des ventes
- `refund_results` (list[VatResult]) : Résultats des remboursements
- `summary` (ReportSummary) : Résumé du rapport
- `all_fc_transfers` (list) : Transferts FBA
- Retour : bytes (contenu du fichier Excel)

### Conditions d'appel
- Les résultats doivent être valides
- Le résumé doit être calculé
- Les transferts FBA doivent être disponibles pour l'onglet AIC

### Gestion des erreurs
- `ValueError` : Paramètres invalides
- `ExcelGenerationError` : Erreur lors de la génération

### Points d'extension
- Personnalisation des onglets
- Formatage des montants en devise locale

---

## UI Tabs → Context

### Description
Les onglets de l'interface Streamlit partagent un contexte commun via `TabContext` stocké dans `st.session_state`.

### Flux de données
```
app.py → TabContext → st.session_state["_tab_ctx"]
  results, refund_results, summary, vies_summary, oss_summary
  period_label, period_detected_range
  can_export, billing_ok, gated_download
  nom_entreprise, siren_entreprise, tva_fr
  countries_with_vat, local_vat_numbers
  all_fc_transfers, all_invoice_credit_notes
  oss_tva_net_total
  vies_scope_id
```

### Types de données
- Voir la structure `TabContext` dans `docs/api/context.md`

### Conditions d'appel
- Le contexte doit être construit avant le rendu des onglets
- `render_declarations()` doit être appelé avant `render_telechargements()` (couplage intentionnel)

### Gestion des erreurs
- `AssertionError` : Si `oss_tva_net_total` est None dans `render_telechargements()`

### Points d'extension
- Ajout de champs au contexte
- Personnalisation du gating par onglet

---

## Notes générales

### Compatibilité
- Tous les modules utilisent des types Python standards (str, int, Decimal, date, etc.)
- Les structures de données sont définies dans `models.py` (dataclasses)

### Performance
- Les appels aux APIs externes (VIES, BCE, TEDB) sont mis en cache
- Le prefetch parallèle est utilisé pour les taux de change et TVA
- Les calculs lourds sont effectués en arrière-plan

### Sécurité
- Les PII sont chiffrés avec Fernet (`auth.py`)
- Les connexions à la base de données utilisent un pool partagé
- Les secrets sont gérés via `config.py` (Streamlit secrets ou variables d'environnement)
