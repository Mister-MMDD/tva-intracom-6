# Mise à jour de tva-site

Ce plan vise à mettre à jour le site statique `tva-site` pour refléter les dernières améliorations techniques apportées au moteur fiscal (`tva_intracom`), notamment en matière de sécurité et de précision de calcul, puis à régénérer les fichiers HTML de production.

## User Review Required

> [!IMPORTANT]
> Les modifications proposées ajoutent des détails techniques sur la sécurité (protection brute-force) et la précision des calculs (taux BCE de clôture). Ces ajouts visent à renforcer la confiance des utilisateurs et des experts-comptables.

## Proposed Changes

### [tva-site]

Mise à jour des fragments de contenu source et exécution du script de build.

#### [MODIFY] [securite.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/src/pages/securite.html)
- Ajout d'une carte sur la **Protection Brute-Force** (limitation des tentatives de connexion).
- Mise à jour de la carte **SaaS High-Load Ready** pour mentionner l'optimisation du pool de connexions PostgreSQL (évitement des deadlocks).

#### [MODIFY] [documentation.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/src/pages/documentation.html)
- Précision dans la section "Roadmap & Évolutions" sur la correction des écarts d'arrondis liés aux taux BCE de clôture pour l'OSS.

#### [BUILD] Exécution de `build.py`
- Régénération des 12 fichiers HTML à la racine de `tva-site/` pour synchroniser les changements avec les versions de production.

## Verification Plan

### Automated Tests
- Exécution de `python tva-site/build.py` et vérification du code de sortie (0 attendu).
- Vérification de la présence des nouveaux textes dans les fichiers générés à la racine (ex: `tva-site/securite.html`).

### Manual Verification
- Relecture des sections modifiées dans l'aperçu du site pour s'assurer de la cohérence visuelle.
