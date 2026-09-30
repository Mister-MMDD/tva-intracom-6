# Module `tva_intracom.excel_report`

## Description
Module d'export Excel multi-onglets. Génère un fichier Excel complet avec le récapitulatif, le détail des ventes, les remboursements, l'AIC FBA, et les données Intrastat/EMEBI. Utilise openpyxl pour la génération.

## Fonctions publiques

### `export_xlsx(results: list[VatResult], refund_results: list[VatResult], summary: ReportSummary, period_label: str, seller_country: str, all_fc_transfers: list = None) -> bytes`
**Description courte** : Génère un fichier Excel complet avec tous les onglets.

**Description détaillée :**
- Crée un fichier Excel multi-onglets
- Onglet "Récapitulatif" : totaux par canal et par pays
- Onglet "Détail ventes" : détail de chaque vente
- Onglet "Remboursements" : détail des remboursements
- Onglet "AIC FBA" : acquisitions intracommunautaires
- Onglet "Intrastat" : données Intrastat/EMEBI
- Formate les montants en devise locale ou EUR
- Inclut la période et le pays vendeur

**Préconditions :**
- results doit être une liste de VatResult valide
- refund_results doit être une liste de VatResult valide
- summary doit être un ReportSummary valide
- period_label doit être au format YYYY-MM
- seller_country doit être un code pays valide

**Postconditions :**
- Aucun (génération de fichier)

**Effets de bord :**
- Aucun (génération de fichier en mémoire)

**Paramètres :**
- `results` (list[VatResult]) : Liste des résultats TVA
- `refund_results` (list[VatResult]) : Liste des résultats de remboursement
- `summary` (ReportSummary) : Résumé du rapport
- `period_label` (str) : Période (ex: "2026-01")
- `seller_country` (str) : Pays du vendeur
- `all_fc_transfers` (list) : Liste des transferts FBA (optionnel)

**Retour :** bytes - Contenu du fichier Excel

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelGenerationError` : Erreur lors de la génération

---

### `_build_asin_avg_price(results: list) -> dict[str, Decimal]`
**Description courte** : Calcule le prix de vente HT moyen par ASIN.

**Description détaillée :**
- Calcule le prix moyen HT PAR UNITÉ par ASIN
- Utilisé comme approximation de la base imposable AIC/Intrastat
- Divise la somme des montants HT par le nombre d'articles vendus
- Exclut les remboursements (montant > 0 uniquement)
- Retourne un dictionnaire {asin: avg_price}

**Préconditions :**
- results doit être une liste de VatResult valide

**Postconditions :**
- Aucun (fonction pure)

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `results` (list) : Liste des résultats TVA

**Retour :** dict[str, Decimal] - Prix moyen par ASIN

**Exceptions :**
- `ValueError` : results invalide

---

### `_write_fba_aic_tab(workbook: Workbook, all_fc_transfers: list, avg_price: dict, period_label: str) -> None`
**Description courte** : Écrit l'onglet AIC FBA dans le classeur Excel.

**Description détaillée :**
- Crée l'onglet "AIC FBA"
- Liste les transferts de stock FBA
- Calcule la base AIC : qty_transfert * avg_price
- Calcule la TVA AIC : base_aic * taux
- Formate les montants en EUR
- Inclut la période

**Préconditions :**
- workbook doit être un Workbook openpyxl valide
- all_fc_transfers doit être une liste valide
- avg_price doit être un dictionnaire valide
- period_label doit être au format YYYY-MM

**Postconditions :**
- L'onglet est ajouté au classeur

**Effets de bord :**
- Modifie le classeur (ajoute un onglet)

**Paramètres :**
- `workbook` (Workbook) : Classeur Excel
- `all_fc_transfers` (list) : Liste des transferts FBA
- `avg_price` (dict) : Prix moyen par ASIN
- `period_label` (str) : Période (ex: "2026-01")

**Retour :** None

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelWriteError` : Erreur lors de l'écriture

---

### `_write_intrastat_tab(workbook: Workbook, all_fc_transfers: list, avg_price: dict, period_label: str) -> None`
**Description courte** : Écrit l'onglet Intrastat/EMEBI dans le classeur Excel.

**Description détaillée :**
- Crée l'onglet "Intrastat"
- Liste les transferts de stock FBA
- Calcule la valeur statistique : qty_transfert * avg_price
- Détermine le flux (arrivée/départ)
- Formate les montants en EUR
- Inclut la période

**Préconditions :**
- workbook doit être un Workbook openpyxl valide
- all_fc_transfers doit être une liste valide
- avg_price doit être un dictionnaire valide
- period_label doit être au format YYYY-MM

**Postconditions :**
- L'onglet est ajouté au classeur

**Effets de bord :**
- Modifie le classeur (ajoute un onglet)

**Paramètres :**
- `workbook` (Workbook) : Classeur Excel
- `all_fc_transfers` (list) : Liste des transferts FBA
- `avg_price` (dict) : Prix moyen par ASIN
- `period_label` (str) : Période (ex: "2026-01")

**Retour :** None

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelWriteError` : Erreur lors de l'écriture

---

### `_to_home_currency(amount: Decimal, currency_code: str, conv_date: date) -> Decimal`
**Description courte** : Convertit un montant vers la devise locale du vendeur.

**Description détaillée :**
- Convertit un montant EUR vers la devise locale
- Utilise le taux BCE en vigueur à la date de conversion
- En cas d'indisponibilité du taux, retourne le montant EUR
- Utilisé pour formater les montants dans la devise du vendeur

**Préconditions :**
- amount doit être un Decimal valide
- currency_code doit être un code devise valide
- conv_date doit être une date valide

**Postconditions :**
- Aucun (fonction pure avec appel de cache)

**Effets de bord :**
- Appelle ecb_rates.get_rate

**Paramètres :**
- `amount` (Decimal) : Montant à convertir
- `currency_code` (str) : Code devise cible
- `conv_date` (date) : Date de conversion

**Retour :** Decimal - Montant converti

**Exceptions :**
- `ValueError` : Paramètres invalides
- `RateUnavailableError` : Taux non disponible

---

## Configuration requise

- Aucune configuration spécifique requise
- Dépend de `ecb_rates` pour la conversion de devises

## Notes

- Le module utilise openpyxl en mode write_only pour optimiser la mémoire
- Les prix moyens ASIN sont partagés avec le module CA3 pour éviter le double calcul
- Les montants sont arrondis à 2 décimales
