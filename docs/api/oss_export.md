# Module `tva_intracom.oss_export`

## Description
Module d'agrégation OSS (One Stop Shop) et génération d'exports. Agrège les résultats TVA par pays et par canal, génère les exports Excel et CSV URSSAF, détecte les soldes négatifs et fournit des suggestions de correction.

## Fonctions publiques

### `aggregate_oss_results(results: list[VatResult]) -> dict`
**Description courte** : Agrège les résultats TVA pour l'OSS par pays et par taux.

**Description détaillée :**
- Filtre les résultats du canal OSS
- Agrège par pays et par taux de TVA
- Calcule le total HT et le total TVA par (pays, taux)
- Retourne un dictionnaire structuré
- Utilisé pour générer les exports OSS et le XML officiel

**Préconditions :**
- results doit être une liste de VatResult valide

**Postconditions :**
- Aucun (fonction pure)

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `results` (list[VatResult]) : Liste des résultats TVA

**Retour :** dict - Agrégation OSS par pays et taux

**Exceptions :**
- `ValueError` : results invalide

---

### `aggregate_ioss_results(results: list[VatResult]) -> dict`
**Description courte** : Agrège les résultats TVA pour l'IOSS par pays et par taux.

**Description détaillée :**
- Filtre les résultats du canal IOSS
- Agrège par pays et par taux de TVA
- Calcule le total HT et le total TVA par (pays, taux)
- Retourne un dictionnaire structuré
- Utilisé pour générer les exports IOSS séparés de l'OSS

**Préconditions :**
- results doit être une liste de VatResult valide

**Postconditions :**
- Aucun (fonction pure)

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `results` (list[VatResult]) : Liste des résultats TVA

**Retour :** dict - Agrégation IOSS par pays et taux

**Exceptions :**
- `ValueError` : results invalide

---

### `aggregate_by_month_and_country(results: list[VatResult]) -> dict`
**Description courte** : Agrège les résultats TVA par mois et par pays.

**Description détaillée :**
- Agrège par mois (YYYY-MM) et par pays
- Calcule le total HT et le total TVA par (mois, pays)
- Utile pour le reporting temporel
- Retourne un dictionnaire structuré

**Préconditions :**
- results doit être une liste de VatResult valide

**Postconditions :**
- Aucun (fonction pure)

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `results` (list[VatResult]) : Liste des résultats TVA

**Retour :** dict - Agrégation par mois et pays

**Exceptions :**
- `ValueError` : results invalide

---

### `build_oss_excel(oss_aggregation: dict, period_label: str, seller_country: str) -> bytes`
**Description courte** : Génère un fichier Excel pour l'OSS.

**Description détaillée :**
- Crée un fichier Excel multi-onglets
- Onglet par pays avec détails par taux
- Onglet récapitulatif avec totaux
- Formate les montants en EUR
- Inclut la période et le pays vendeur

**Préconditions :**
- oss_aggregation doit être valide
- period_label doit être au format YYYY-MM
- seller_country doit être un code pays valide

**Postconditions :**
- Aucun (génération de fichier)

**Effets de bord :**
- Aucun (génération de fichier en mémoire)

**Paramètres :**
- `oss_aggregation` (dict) : Agrégation OSS
- `period_label` (str) : Période (ex: "2026-01")
- `seller_country` (str) : Pays du vendeur

**Retour :** bytes - Contenu du fichier Excel

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelGenerationError` : Erreur lors de la génération

---

### `build_ioss_excel(ioss_aggregation: dict, period_label: str, seller_country: str) -> bytes`
**Description courte** : Génère un fichier Excel pour l'IOSS.

**Description détaillée :**
- Crée un fichier Excel multi-onglets
- Onglet par pays avec détails par taux
- Onglet récapitulatif avec totaux
- Formate les montants en EUR
- Inclut la période et le pays vendeur

**Préconditions :**
- ioss_aggregation doit être valide
- period_label doit être au format YYYY-MM
- seller_country doit être un code pays valide

**Postconditions :**
- Aucun (génération de fichier)

**Effets de bord :**
- Aucun (génération de fichier en mémoire)

**Paramètres :**
- `ioss_aggregation` (dict) : Agrégation IOSS
- `period_label` (str) : Période (ex: "2026-01")
- `seller_country` (str) : Pays du vendeur

**Retour :** bytes - Contenu du fichier Excel

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelGenerationError` : Erreur lors de la génération

---

### `build_b2b_excel(b2b_results: list[VatResult], period_label: str) -> bytes`
**Description courte** : Génère un fichier Excel pour les ventes B2B.

**Description détaillée :**
- Crée un fichier Excel avec les ventes B2B
- Inclut les numéros TVA des acheteurs
- Inclut les montants HT et TVA
- Formate les montants en EUR

**Préconditions :**
- b2b_results doit être une liste de VatResult valide
- period_label doit être au format YYYY-MM

**Postconditions :**
- Aucun (génération de fichier)

**Effets de bord :**
- Aucun (génération de fichier en mémoire)

**Paramètres :**
- `b2b_results` (list[VatResult]) : Liste des résultats B2B
- `period_label` (str) : Période (ex: "2026-01")

**Retour :** bytes - Contenu du fichier Excel

**Exceptions :**
- `ValueError` : Paramètres invalides
- `ExcelGenerationError` : Erreur lors de la génération

---

### `find_oss_negative_buckets(oss_aggregation: dict) -> list[dict]`
**Description courte** : Détecte les soldes négatifs dans l'agrégation OSS.

**Description détaillée :**
- Parcourt l'agrégation OSS
- Détecte les (pays, taux) avec un solde TVA négatif
- Retourne une liste des anomalies détectées
- Utile pour le pré-remplissage du XML OSS

**Préconditions :**
- oss_aggregation doit être valide

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Aucun (fonction pure)

**Paramètres :**
- `oss_aggregation` (dict) : Agrégation OSS

**Retour :** list[dict] - Liste des soldes négatifs

**Exceptions :**
- `ValueError` : oss_aggregation invalide

---

### `get_oss_rate_fallback_stats() -> dict`
**Description courte** : Retourne les statistiques de repli de taux OSS.

**Description détaillée :**
- Compte le nombre de taux OSS provenant du cache
- Compte le nombre de taux OSS calculés dynamiquement
- Retourne un dictionnaire avec les statistiques
- Utile pour le monitoring

**Préconditions :**
- Aucune

**Postconditions :**
- Aucun (lecture seule)

**Effets de bord :**
- Aucun (lecture des compteurs en mémoire)

**Paramètres :**
- Aucun

**Retour :** dict - Statistiques de repli

**Exceptions :**
- Aucune

---

### `reset_oss_rate_fallback_stats() -> None`
**Description courte** : Réinitialise les statistiques de repli de taux OSS.

**Description détaillée :**
- Remet à zéro les compteurs de repli
- Utile pour les tests ou le monitoring par période

**Préconditions :**
- Aucune

**Postconditions :**
- Les compteurs sont à zéro

**Effets de bord :**
- Aucun (mise à jour des compteurs en mémoire)

**Paramètres :**
- Aucun

**Retour :** None

**Exceptions :**
- Aucune

---

## Configuration requise

- Aucune configuration spécifique requise
- Les fonctions sont pures et ne dépendent pas de variables d'environnement

## Notes

- Le module ne gère pas la génération du XML OSS officiel (voir `oss_xml.py`)
- Les exports Excel sont générés en mémoire et retournés sous forme de bytes
- Les soldes négatifs sont normaux pour les remboursements, mais doivent être déclarés correctement
