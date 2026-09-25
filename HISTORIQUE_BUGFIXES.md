# Historique des Correctifs et Boguefixes (`HISTORIQUE_BUGFIXES.md`)

Ce document centralise l'ensemble des correctifs de bugs, ajustements réglementaires et optimisations techniques apportés au projet **TVA Intracom**. Il remplace les annotations temporaires `# BUGFIX` précédemment dispersées dans le code source de production et la suite de tests.

---

## Sommaire

1. [Moteur Fiscal & Calculs (`tva_intracom/engine.py`)](#1-moteur-fiscal--calculs-tva_intracomenginepy)
2. [Taux de Change & BCE (`tva_intracom/ecb_rates.py`)](#2-taux-de-change--bce-tva_intracomecb_ratespy)
3. [Déclarations OSS / IOSS (`tva_intracom/oss_export.py`)](#3-déclarations-oss--ioss-tva_intracomoss_exportpy)
4. [Moteur VIES (`tva_intracom/vies_engine.py`)](#4-moteur-vies-tva_intracomvies_enginepy)
5. [Déclarations CA3 (`tva_intracom/ca3_report.py`)](#5-déclarations-ca3-tva_intracomca3_reportpy)
6. [Rapports Excel & EMEBI (`tva_intracom/excel_report.py`)](#6-rapports-excel--emebi-tva_intracomexcel_reportpy)
7. [TVA Locale & FEC (`local_vat_report.py`, `fec_export.py`)](#7-tva-locale--fec-local_vat_reportpy-fec_exportpy)
8. [Billing & Authentification (`billing.py`, `auth.py`)](#8-billing--authentification-billingpy-authpy)
9. [Interface Utilisateur Streamlit (`app.py`, `tva_intracom/ui/`)](#9-interface-utilisateur-streamlit-apppy-tva_intracomui)
10. [Parsers e-Commerce (`tva_intracom/parsers/`)](#10-parsers-e-commerce-tva_intracomparsers)
11. [Scripts & Tests Automated (`scripts/`, `tests/`)](#11-scripts--tests-automated-scripts-tests)

---

## 1. Moteur Fiscal & Calculs (`tva_intracom/engine.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Avoirs multi-périodes** | Line 241, 1283 | Prise en compte du taux de TVA historique d'origine pour les avoirs émis sur une période fiscale ultérieure. |
| **Calculs Fiscaux Audit** | Line 369 | Correctif issu de l'audit fiscal du 2026-09-13 sur les montants de TVA autoliquidée. |
| **Directive 2006/112/CE** | Line 612 | Application rigoureuse de l'article 59 ter pour l'alignement du régime d'imposition au lieu de destination. |
| **Exonérations Ventes** | Line 696 | Correction d'un bug où `channel=EXONERATION` qualifiait à tort la vente en exonérée sans contrôle d'assujettissement. |
| **Origine du Stock & Monaco** | Line 815-840 | Traitement des règles de territorialité stock (art. 59 ter (1.b)) et équivalence fiscale explicite France/Monaco. |
| **Seuil OSS Franchi** | Line 967, 1211, 1220 | Maintien du statut `already_crossed` pour le seuil OSS au-delà de l'exercice fiscal en cours. |
| **Avoirs et Cumul OSS** | Line 1023, 1128, 1306 | Si un avoir réduit le cumul au-dessous du seuil OSS, le régime retombe en taux local de manière cohérente. |
| **Stockage Paramètres** | Line 1112 | Déplacement de la persistance des paramètres fiscaux de la session en base de données. |
| **Cache VIES & Fallback** | Line 1488, 1681, 1784 | Évaluation de `stale_fallback` avant validation du résultat `vr.valid` pour éviter des biais sur données périmées. |
| **Champs Saisie Manuelle** | Line 1659 | Correction du champ `manual_override_count` inexistant dans les anciennes versions de schéma. |

---

## 2. Taux de Change & BCE (`tva_intracom/ecb_rates.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Performance HTTP** | Line 50, 171 | Ajout d'un timeout strict sur `urlopen()` pour empêcher les blocages indéfinis sur de gros fichiers de transactions. |
| **Exécution Hors-ligne / Tests** | Line 282, 306, 391 | Drapeau global process évitant les retentatives et handshakes TLS inutiles lors des exécutions de tests locaux sans réseau. |
| **Règlement UE 2020/194** | Line 468, 523, 1088 | Conformité avec l'article 5 bis obligeant l'utilisation du taux de clôture publié par la BCE pour les déclarations OSS/IOSS. |
| **Optimisation Devises** | Line 553, 823 | Mémorisation explicite des conversions par ligne pour éviter de réinterroger la base BCE sur la même devise. |
| **Arrondis Croisés** | Line 902, 925 | Élimination des dérives de centimes lors des conversions indirectes via la devise EUR. |
| **Périodicité IOSS** | Line 1049 | Prise en compte de la déclaration IOSS obligatoirement mensuelle (contrairement à l'OSS trimestriel). |

---

## 3. Déclarations OSS / IOSS (`tva_intracom/oss_export.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Alerte Repli Taux** | Line 50 | Implémentation d'un compteur de repli silencieux avertissant l'utilisateur en cas d'absence du taux BCE exact. |
| **Devise Obligatoire EUR** | Line 103 | Traitement légal des déclarations OSS qui doivent être soumises obligatoirement en Euro (EUR). |
| **Conversions Sans Période** | Line 112, 262, 265, 389 | Autorisation de la conversion de devise même si `period=""` via le mécanisme de fallback sécurisé. |
| **Date de Référence IOSS** | Line 251, 255, 373 | Alignement de la date de conversion sur la date de la transaction effective. |
| **Buckets Négatifs** | Line 540 | Maintien et suivi des clés négatives (`neg_keys`) issues des avoirs supérieurs aux ventes. |
| **Différentiel Taux Avoir** | Line 572, 577, 605 | Application du taux de TVA de la vente initiale sur l'avoir, même en cas de changement de taux légal intermédiaire. |
| **Sélection Candidat Avoir** | Line 588 | Correction de l'indexation de sélection du candidat d'avoir (`candidates[0]`). |

---

## 4. Moteur VIES (`tva_intracom/vies_engine.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Fuite Mémoire TTL** | Line 102 | Correction de la croissance indéfinie du dictionnaire `_SCOPE_TTL_DAYS`. |
| **Sanitisation Numéros** | Line 122 | Suppression des parenthèses et espaces parasites dans les numéros de TVA avant soumission VIES. |
| **Dégradation Propre API** | Line 618 | Capture des exceptions réseau VIES sans interrompre le traitement global des déclarations. |
| **Faux Positifs FR** | Line 1281-1295 | Correction de la regex d'analyse VIES pour éviter la fausse invalidation des numéros FR. |
| **Gestion du Cache & TTL** | Line 1768, 1801, 1898 | Si l'utilisateur réduit la durée TTL du cache VIES, invalide immédiatement les résultats antérieurs. |
| **Mode Batch & Regressions** | Line 1968-2126 | Correction des régressions sur le mode batch et vérification systématique du drapeau `stale_fallback`. |

---

## 5. Déclarations CA3 (`tva_intracom/ca3_report.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Taux AIC Applicable** | Line 103, 167 | Application du taux réduit ou spécifique au lieu du taux standard systématique de 20% sur les AIC. |
| **Transferts FBA Monaco** | Line 205, 213, 228, 367 | Intégration et neutralisation des transferts de stock FBA entre la France et Monaco (territoire douanier unique). |
| **Sécurité XSS** | Line 482 | Échappement HTML des saisies utilisateur (`company_name`) intégrées aux rapports CA3. |
| **Normalisation Pays** | Line 530 | Normalisation systématique du champ `seller_country` en majuscules ISO-2. |

---

## 6. Rapports Excel & EMEBI (`tva_intracom/excel_report.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Page de Synthèse** | Line 362, 552 | Recalcul dynamique des totaux et correction de la ligne de restitution "Guichet IOSS". |
| **Règles EMEBI / Délais** | Line 1099, 1329 | Calcul exact du 10e jour ouvré du mois suivant (prise en compte des jours fériés légaux). |
| **Périodes Multiples** | Line 1220 | Prise en compte transparente des sélections multi-mois, semestrielles ou annuelles. |
| **Formats V5 Amazon** | Line 1384, 1400 | Support des colonnes `ship_from_country` et parsing des dates de transferts FC Amazon. |
| **Intégration AIC FBA** | Line 2080, 2120, 2144 | Correction des valeurs manquantes et élimination de l'erreur Excel `#VALEUR!` dans la colonne TVA Nette. |

---

## 7. TVA Locale & FEC (`local_vat_report.py`, `fec_export.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **AIC FBA Locales** | `local_vat_report.py` L.63, 126, 228 | Intégration des acquisitions intracommunautaires FBA dans les totaux à autoliquider par pays. |
| **Protection XSS** | `local_vat_report.py` L.165 | Échappement HTML de la raison sociale avant génération du rapport. |
| **Avoirs FEC** | `fec_export.py` L.103 | Alignement de la condition `<= 0` pour traiter correctement les avoirs au sens comptable FEC. |
| **Équilibrage Débit/Crédit** | `fec_export.py` L.289 | Inversion rigoureuse du sens des écriture comptables pour assurer l'égalité Débit = Crédit sur chaque pièce. |

---

## 8. Billing & Authentification (`billing.py`, `auth.py`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Durée Session Sécurisée** | `auth.py` L.44, 1148 | Réduction de la durée fixe de session (de 30 jours à 7 jours) et mise en place d'un renouvellement glissant. |
| **Persistance Session** | `auth.py` L.1347, 1402 | Fiabilisation du jeton d'authentification et de la session utilisateur. |
| **Scellement PAYG par SIREN** | `billing.py` L.232, 643 | Un crédit PAYG est obligatoirement scellé à la combinaison unique `(org_id, siren)`. |
| **Isolation Verrou Postgres** | `billing.py` L.888, 985, 999 | Utilisation d'un verrou avisé transactionnel Postgres (`pg_advisory_xact_lock`) lors des changements de quota. |
| **Gestion des Quotas Piégés** | `billing.py` L.763, 889, 1023 | Libération immédiate d'un SIREN retiré de l'organisation pour réallocation du quota. |

---

## 9. Interface Utilisateur Streamlit (`app.py`, `tva_intracom/ui/`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Gestion Cache & RAM** | `app.py` L.183, 544, 879 | Optimisation des appels à `release_memory()` et nettoyage ciblé des caches applicatifs. |
| **Scoping Widgets par SIREN** | `sidebar.py` L.309-388, 861-988 | Scoping dynamique des clés Streamlit par numéro SIREN pour éviter les collisions inter-comptes. |
| **Priorité Paywall Stripe** | `billing_gate.py` L.121, 124, 197 | Vérification stricte du statut du paiement Stripe avant d'autoriser tout téléchargement de rapport. |
| **Téléchargements à la Demande** | `telechargements.py` L.109, 138 | Génération des fichiers uniquement lors du clic utilisateur (évite le ralentissement au chargement de page). |

---

## 10. Parsers e-Commerce (`tva_intracom/parsers/`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Devise Cible Interne** | Tous parsers (`loader.py`, `aliexpress.py`, `mirakl.py`, etc.) | Forçage de `target_currency = "EUR"` comme devise pivot unique de calcul pour le moteur fiscal. |
| **Encodage & CSV** | `loader.py` L.564, 648 | Prise en compte des fichiers UTF-8 avec BOM et gestion des valeurs nulles (`restval`) dans `DictReader`. |
| **Fallback Dictionnaires** | `classify.py` L.173 | Utilisation de `row.get(col, "")` pour éviter les `KeyError` sur fichiers CSV tronqués. |

---

## 11. Scripts & Tests Automated (`scripts/`, `tests/`)

| Thème / Module | Ancien Marqueur | Description Technique du Correctif |
| :--- | :--- | :--- |
| **Migration Crédits** | `scripts/migrate_legacy_export_credits.py` | Migration sécurisée exigeant le rattachement au SIREN. |
| **Tests de Régression** | `tests/test_*.py` | Couverture par tests unitaires automatisés des 230 cas limites et régressions historiques documentés dans ce fichier. |
