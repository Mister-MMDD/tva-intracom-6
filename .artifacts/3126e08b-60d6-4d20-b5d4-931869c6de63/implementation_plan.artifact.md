# Correction des problèmes de langue et d'affichage

Ce plan vise à corriger trois problèmes signalés :
1. Le certificat VIES PDF est toujours en français.
2. Le changement de langue réinitialise les calculs (purge du cache).
3. Une erreur `KeyError: 'Scenario'` survient dans l'onglet Audit lors d'un changement de langue.

## Changements proposés

### 1. Internationalisation du Certificat VIES PDF

#### [MODIFY] [vies_certificate.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/vies_certificate.py)
- Ajouter un paramètre `_` (fonction de traduction) à `generate_vies_certificate_pdf`.
- Remplacer toutes les chaînes codées en dur par des appels à `_("clé")`.

#### [MODIFY] [fr.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/fr.toml) & [en.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/en.toml)
- Ajouter les nouvelles clés de traduction pour le certificat VIES.

#### [MODIFY] [vies_ui.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/vies_ui.py) & [sidebar.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/sidebar.py)
- Passer la fonction de traduction `_` lors de l'appel à `generate_vies_certificate_pdf`.

### 2. Stabilisation du changement de langue

#### [MODIFY] [i18n.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/i18n.py)
- Utiliser `preserve_upload_rerun()` au lieu de `st.rerun()` dans `language_selector()` pour éviter que le mécanisme de nettoyage d'Amazon (dans `app.py`) ne purge les résultats calculés lors d'un simple changement de langue.

### 3. Correction du KeyError dans l'onglet Audit

#### [MODIFY] [audit.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/audit.py)
- Utiliser les clés traduites (`_("col_scenario")`, etc.) lors de la création du dictionnaire `row_d` pour qu'elles correspondent aux clés utilisées lors de l'accès (notamment pour l'export CSV).
- Ajouter les clés de traduction manquantes si nécessaire.

## Plan de vérification

### Tests Manuels
- Changer la langue et vérifier que les calculs ne disparaissent pas.
- Générer un certificat VIES en anglais et vérifier que le PDF est bien traduit.
- Vérifier que l'export CSV dans l'onglet Audit (écarts Amazon manquante) fonctionne sans erreur quel que soit la langue.
