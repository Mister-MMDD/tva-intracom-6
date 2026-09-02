# Mise à jour du site vitrine (`tva-site`) suite à l'audit 09/2026

Synchronisation du contenu du site vitrine avec les dernières évolutions techniques et fiscales documentées dans le `README.md` et le journal d'évolution (audit 2026-08-31 et 2026-09-01).

## Proposed Changes

### [Sécurité & Technologie]

#### [MODIFY] [securite.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/securite.html)
- Précision de l'algorithme de chiffrement : **Fernet (AES-128 CBC + HMAC-SHA256)**.
- Ajout de la protection **Fail-Safe** contre l'exposition accidentelle des PII.
- Ajout de la politique de **rétention limitée** (suppression automatique des PII après 365 jours).
- Mention de la protection contre l'**injection de formules Excel** (`=`, `+`, `-`, `@`).

---

### [Régimes Fiscaux]

#### [MODIFY] [regimes.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/regimes.html)
- **Monaco** : Précision sur le remplissage automatique de la **Ligne 18 (case 0038)** du CA3.
- **IOSS** : Clarification du comportement par défaut (**DEEMED_SUPPLIER** pour Amazon) et de l'activation explicite de l'**IOSS_DIRECT**.
- **VIES** : Mention de l'architecture résiliente à **3 niveaux** (Cache Privé / Global / API) et de la boucle de ré-essai automatique.

---

### [Interface & UX]

#### [MODIFY] [interface.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/interface.html)
- Ajout du **Mode Simple / Détaillé** (persistant par compte).
- Mention de l'**Onboarding guidé** (checklist interactive Lighthouse).
- Précision sur la gestion des gros fichiers : **File d'attente intelligente** et **respiration CPU**.
- Ajout de la **Barre de statut persistante** (fichiers, période, état du calcul).

---

### [Documentation & FAQ]

#### [MODIFY] [documentation.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/documentation.html)
- Mise à jour de l'**arborescence technique** (ajout de `amazon_adapter.py`, `vies_certificate.py`, etc.).
- Mise à jour de la **Checklist de Conformité** (statut post-paiement instantané).

#### [MODIFY] [faq.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/faq.html)
- Mention de la disponibilité de l'interface en **7 langues**.
- Précision sur les ré-essais automatiques VIES en arrière-plan.

---

### [Accueil]

#### [MODIFY] [index.html](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva-site/index.html)
- Mise à jour de la performance : capacité de traitement jusqu'à **150 Mo** par fichier (au lieu de 100k lignes).

## Verification Plan

### Automated Tests
- Validation HTML/CSS (non applicable ici car statique, mais vérification de la structure).
- `py_compile` non applicable (HTML).

### Manual Verification
- Relecture visuelle des pages modifiées pour vérifier la cohérence du ton et de l'affichage.
