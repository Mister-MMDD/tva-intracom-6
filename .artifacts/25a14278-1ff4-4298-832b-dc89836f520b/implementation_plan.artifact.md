# Mise à jour de tva-site pour les sources officielles de taux de TVA

Ce plan vise à mettre à jour le site vitrine `tva-site` pour mentionner explicitement que les taux de TVA sont récupérés via des sources officielles (TEDB/Commission Européenne) et sont toujours à jour.

## User Review Required

> [!NOTE]
> Les modifications portent sur les fichiers sources dans `tva-site/src/pages/` qui sont ensuite utilisés par le script `build.py` pour régénérer les fichiers HTML à la racine du dossier `tva-site/`.

## Proposed Changes

### [tva-site]

#### [MODIFY] [faq.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/src/pages/faq.html)
- Mise à jour de la réponse sur la gestion des taux de TVA pour mentionner la synchronisation avec la base de données officielle **TEDB** (Taxes in Europe Database) de la Commission Européenne.

#### [MODIFY] [securite.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/src/pages/securite.html)
- Mise à jour de la carte "Sources Officielles" pour inclure **TEDB** en complément de **VIES** et **BCE**.

#### [MODIFY] [index.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/src/pages/index.html)
- Ajout d'une mention sur la mise à jour automatique des taux de TVA via les sources officielles dans la section d'introduction.

## Verification Plan

### Manual Verification
- Exécution du script `python tva-site/build.py` pour régénérer les fichiers.
- Vérification visuelle des fichiers HTML générés à la racine (`tva-site/faq.html`, `tva-site/securite.html`, `tva-site/index.html`).
