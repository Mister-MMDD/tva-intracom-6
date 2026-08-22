# Mise à jour du README.md à partir du journal d'évolution

Le but est de mettre à jour le fichier `README.md` pour qu'il reflète l'état actuel du projet tel que décrit dans `README - evolution.md`. Les changements récents concernent principalement l'expérience utilisateur (UX), la restructuration des modules UI, et l'ajout d'un mode d'affichage "Simple/Détaillé".

## Modifications proposées

### [README.md](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/README.md)

#### [MODIFY] [README.md](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/README.md)

1.  **Arborescence du dépôt** :
    *   Ajouter `display_mode.py` dans `tva_intracom/ui/`.
    *   Mettre à jour les descriptions des modules UI (ex: `sidebar.py`, `auth_flow.py`).
2.  **Architecture de l'interface Streamlit** :
    *   Ajouter `ui/display_mode.py` dans le tableau récapitulatif.
    *   Préciser le rôle des nouveaux composants (Barre de statut, Modal "Compte & Confidentialité").
3.  **Authentification & Facturation** :
    *   Préciser les providers OAuth supportés par Supabase Auth (Google, Microsoft, GitHub, Amazon).
    *   Mentionner la persistance du mode d'affichage par compte.
4.  **Optimisations de performance & UX** :
    *   Ajouter le mode "Simple / Détaillé".
    *   Ajouter la "Barre de statut persistante".
    *   Mentionner l'isolation par fragments (`st.fragment`) étendue.
5.  **Fonctionnalités clés** :
    *   Mettre à jour la section "Validation VIES" (Backend Postgres, Piste d'audit, Certificat PDF).
    *   Mettre à jour la section "Export XML OSS" (Validation XSD, Correction assistée des soldes négatifs).

## Plan de vérification

### Vérification Manuelle
*   Vérifier que les liens internes vers les fichiers dans le `README.md` sont corrects.
*   S'assurer que le ton et le style restent cohérents avec le reste du document.
