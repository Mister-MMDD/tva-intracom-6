# Plan d'implémentation - Correction de la sidebar (TVA dynamique et seuil OSS)

Ce plan vise à corriger l'affichage du libellé pour l'option du seuil OSS dans la sidebar, en rendant le pays dynamique selon le pays d'origine choisi, et à vérifier la cohérence du calcul sous-jacent.

## User Review Required

> [!IMPORTANT]
> J'ai choisi d'utiliser le code pays (ex: FR, ES, DE) dans le libellé pour rester cohérent avec l'affichage actuel "TVA FR". Cela permet une lecture claire et technique (ex: "Appliquer la TVA ES sous le seuil OSS").

## Proposed Changes

### Internationalisation (i18n)

Mise à jour de toutes les traductions pour accepter une variable `{country}` dans le libellé `oss_threshold_apply_label`.

#### [MODIFY] [fr.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/fr.toml)
#### [MODIFY] [en.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/en.toml)
#### [MODIFY] [de.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/de.toml)
#### [MODIFY] [es.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/es.toml)
#### [MODIFY] [it.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/it.toml)
#### [MODIFY] [pl.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/pl.toml)
#### [MODIFY] [pt.toml](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/i18n/pt.toml)

### Interface Utilisateur (Sidebar)

Mise à jour de l'appel à la fonction de traduction pour passer le pays d'origine sélectionné.

#### [MODIFY] [sidebar.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/sidebar.py)
Passage du paramètre `country=home_country` lors de l'appel à `_("oss_threshold_apply_label", ...)`.

## Verification Plan

### Automated Tests
- Vérification du code de `engine.py` : s'assurer que `sale.seller_country` est bien utilisé pour déterminer le taux de TVA appliqué sous le seuil.
- Vérification de `app.py` : s'assurer que `home_country` est bien transmis au constructeur de `Sale`.

### Manual Verification
- Changer le pays d'origine dans la sidebar (ex: Espagne) et vérifier que le texte devient "Appliquer TVA ES ...".
- Passer en Anglais et vérifier que le texte devient "Apply ES VAT ...".
- Vérifier qu'une vente sous le seuil pour un vendeur espagnol applique bien le taux espagnol (21% standard) et non français.
