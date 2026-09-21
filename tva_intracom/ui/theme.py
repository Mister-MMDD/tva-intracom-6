"""Thème visuel de l'application : configuration de page Streamlit et CSS.

Regroupe la config de page et l'injection de style, pour que app.py n'ait
plus qu'à appeler `apply_theme()` en tête de script.

Refonte 2026-09-18 : contraste et hiérarchie visuelle repensés (fond bleu
ciel en mode clair, palette de bordures 3 niveaux, alertes professionnelles
4 catégories, ombres 4 niveaux), mode sombre optimisé en cohérence.

Refonte 2026-09-20 : palette Teal/Cyan moderne (style FinTech pro),
animations modérées (0.3-0.5s), amélioration lisibilité et hiérarchie visuelle.

Mécanisme de détection du thème (réécrit le 2026-09-18 après diagnostic) :
Streamlit 1.58.0 NE POSE AUCUN attribut ni classe CSS reflétant le choix
Clair/Sombre/Système sur `html`, `body` ou `.stApp` — vérifié empiriquement
(dump des attributs/classes/localStorage dans les 3 modes, aucune ligne
`[data-theme=...]` ne matche jamais). La seule source de vérité est
`localStorage["stActiveTheme-/-v2"]`, qui vaut la chaîne `"Light"`,
`"Dark"` ou `"System"`.

D'où le mécanisme en 2 parties :
  1. CSS : les variables suivent `[data-theme-actual="dark"]` /
     `[data-theme-actual="light"]`, un attribut qu'on pose NOUS-MÊMES sur
     `<html>` (jamais fourni par Streamlit) — plus un repli `@media
     (prefers-color-scheme: dark)` pour le tout premier rendu, avant que
     le script ci-dessous n'ait eu le temps de tourner.
  2. JS (`_sync_theme_attribute`, exécuté via `st.iframe` — un `<script>`
     inséré par `st.markdown`/innerHTML ne s'exécute JAMAIS, c'est une
     limitation du navigateur ; `st.iframe` est l'API publique qui a
     remplacé `components.v1.html`, déprécié depuis Streamlit 1.58.0 avec
     retrait prévu après le 2026-06-01) : lit cette clé localStorage
     (valeur textuelle déterministe, pas une heuristique de couleur —
     c'est cette heuristique, fragile, qui avait causé la casse du
     2026-09-17 dans `theme1.py`, supprimé), résout "System" via
     `matchMedia`, et pose `data-theme-actual` sur `window.parent.
     document.documentElement`. Un `setInterval` léger (lecture seule,
     écrit uniquement si la valeur a changé) permet de suivre un
     changement de thème sans attendre un rerun Streamlit — ce timer
     tourne uniquement dans le navigateur du client, ne fait aucun appel
     réseau et n'a donc AUCUN impact sur le scale-to-zero (qui ne
     concerne que l'inactivité du process serveur/des connexions
     websocket, pas les timers JS côté client).
Ordre CSS à respecter (section 1) : `:root` (clair) → `@media` (repli
avant exécution du JS) → `[data-theme-actual="dark"]` →
`[data-theme-actual="light"]` (doit rester après le `@media` pour pouvoir
l'annuler si l'utilisateur a choisi Clair alors que l'OS est en sombre).

Statut : confirmé fonctionnel par Matthieu le 2026-09-18 dans les 3 modes
(Système / Clair / Sombre). Ne pas réintroduire un mécanisme basé sur
`[data-theme=...]` (attribut/classe) sans le revérifier empiriquement au
préalable — Streamlit peut changer ce comportement d'une version à
l'autre, et c'est précisément l'hypothèse de départ qui s'est révélée
fausse cette fois.
"""

from __future__ import annotations

import streamlit as st

_PLATFORM_OPTIONS = [
    "Amazon VAT Transactions Report (TSV), txt, CSV",
]

_CSS = """
<style>
/* Typographie de marque (refonte 2026-09-20 quater, alignée sur la maquette
   fournie par Matthieu) : Manrope pour les titres/chiffres-clés (plus
   "fintech premium" que Space Grotesk sur ce rendu) + DM Sans pour le corps
   (remplace Inter, légèrement plus chaleureux). Chargées via Google Fonts —
   hôte autorisé par la CSP des artifacts publiés et sans impact sur le
   scale-to-zero : c'est le NAVIGATEUR du client qui fait cette requête au
   chargement de la page, pas le process serveur Streamlit (aucune
   connexion/thread/polling côté serveur). */
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;450;500;550;600;650;700&family=Manrope:wght@400;500;600;650;700;750;800&display=swap');

/* ══════════════════════════════════════════════════════════════════════
   1. VARIABLES DE THÈME
   Mode clair = valeurs par défaut sur :root.
   Mode sombre = surcharge unique sous [data-theme-actual="dark"] (posé
   par le script de synchronisation en fin de fichier, cf. docstring de
   module — Streamlit ne fournit aucun attribut de thème exploitable).
   Aucune autre condition (pas de media query, pas de :not()) ne doit
   redéfinir ces variables.
   ══════════════════════════════════════════════════════════════════════ */
:root {
    /* Palette Teal/Cyan moderne (style FinTech pro) - 2026-09-20 */
    --brand-primary: #0891b2;
    --brand-secondary: #06b6d4;
    --brand-accent: #0e7490;
    --brand-soft: color-mix(in srgb, #0891b2 12%, transparent);

    /* Alias pour compatibilité avec l'existant (transition progressive) */
    --brand-blue: var(--brand-primary);
    --brand-blue-soft: var(--brand-soft);

    /* Fond très clair inspiré du site web (slate-50) pour mode clair - 2026-09-20 */
    --bg-primary: #f0f5fc; /* Lavis bleu tva-site 4% (harmonisation 2026-09-20 ter) */
    --bg-secondary: #ffffff;
    --bg-tertiary: #e8eff9; /* Lavis bleu tva-site 5% (harmonisation 2026-09-20 ter) */

    /* Texte optimisé pour lisibilité (contraste WCAG AA) */
    --text-primary: #0f172a;
    --text-secondary: #334155;
    --text-muted: #64748b;

    /* Bordures 3 niveaux pour hiérarchie visuelle */
    --border-light: #cbd5e1;
    --border-medium: #94a3b8;
    --border-strong: #64748b;

    /* Alertes 4 catégories - info adapté à la palette Teal */
    --alert-error-bg: #fef2f2;
    --alert-error-border: #fecaca;
    --alert-error-text: #dc2626;

    --alert-warning-bg: #fffbeb;
    --alert-warning-border: #fde68a;
    --alert-warning-text: #d97706;

    --alert-success-bg: #f0fdf4;
    --alert-success-border: #bbf7d0;
    --alert-success-text: #16a34a;

    --alert-info-bg: #ecfeff;
    --alert-info-border: #a5f3fc;
    --alert-info-text: #0891b2;

    /* Ombres 4 niveaux pour profondeur — teintées Teal (refonte 2026-09-20 bis) :
       shadow-sm reste neutre (portée trop fine pour qu'une teinte se voie),
       md/lg/hover mixent la teinte de marque à l'ombre neutre au lieu d'un
       gris pur, pour une signature visuelle cohérente avec --brand-primary
       plutôt qu'une ombre Bootstrap générique. */
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.08);
    --shadow-md: 0 2px 10px color-mix(in srgb, var(--brand-primary) 10%, rgba(15, 23, 42, 0.12));
    --shadow-lg: 0 8px 24px color-mix(in srgb, var(--brand-primary) 12%, rgba(15, 23, 42, 0.15));
    --shadow-hover: 0 6px 18px color-mix(in srgb, var(--brand-primary) 16%, rgba(15, 23, 42, 0.18));

    /* Rayons de bordure — agrandis (refonte 2026-09-20 bis) pour un rendu
       moins "carré/austère", plus fintech premium. radius-sm inchangé
       (inputs/tags, doivent rester compacts). */
    --radius-sm: 6px;
    --radius-md: 14px;
    --radius-lg: 20px;

    /* Tags multiselect - vert conservé pour cohérence (2026-09-18) */
    --tag-bg: color-mix(in srgb, var(--accent-green) 18%, #ffffff);
    --tag-text: #047857; /* Emeraude tva-site --success, ajusté contraste AA (harmonisation 2026-09-20 ter) */
    --accent-green: var(--tag-text);
    --slider-fill-hue: 161.0538deg; /* Recalculé pour cibler #047857 (harmonisation 2026-09-20 ter) */
    --slider-fill-sat: 0.7407;
    --slider-fill-bri: 0.8197;

    /* Variable native Streamlit - adaptée à Teal (2026-09-20) */
    --primary-color: #0891b2;
}

/* Repli "Système" : l'OS/navigateur est en sombre mais Streamlit n'a posé
   aucun attribut data-theme (l'utilisateur n'a rien choisi explicitement
   dans son menu). Doit rester AVANT les blocs [data-theme=...] ci-dessous
   pour que ceux-ci puissent le surcharger en cas de choix explicite. */
@media (prefers-color-scheme: dark) {
    :root {
        /* Palette Teal/Cyan mode sombre - 2026-09-20 */
        --brand-primary: #22d3ee;
        --brand-secondary: #06b6d4;
        --brand-accent: #0891b2;
        --brand-soft: color-mix(in srgb, #22d3ee 16%, transparent);

        /* Alias pour compatibilité */
        --brand-blue: var(--brand-primary);
        --brand-blue-soft: var(--brand-soft);

        /* Fond bleu nuit pour mode sombre */
        --bg-primary: #0f172a;
        --bg-secondary: #1e293b;
        --bg-tertiary: #334155;

        /* Texte optimisé contraste mode sombre */
        --text-primary: #f1f5f9;
        --text-secondary: #cbd5e1;
        --text-muted: #94a3b8;

        /* Bordures mode sombre */
        --border-light: #334155;
        --border-medium: #475569;
        --border-strong: #64748b;

        /* Alertes mode sombre - info adapté Teal */
        --alert-error-bg: #1a1515;
        --alert-error-border: #3f2626;
        --alert-error-text: #fca5a5;

        --alert-warning-bg: #1a1912;
        --alert-warning-border: #3f3a24;
        --alert-warning-text: #fcd34d;

        --alert-success-bg: #0f1f15;
        --alert-success-border: #1f3f2b;
        --alert-success-text: #86efac;

        --alert-info-bg: #0f1f29;
        --alert-info-border: #1e4a5a;
        --alert-info-text: #67e8f9;

        /* Ombres mode sombre — teintées Teal cyan (refonte 2026-09-20 bis) */
        --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.4);
        --shadow-md: 0 2px 10px color-mix(in srgb, var(--brand-primary) 14%, rgba(0, 0, 0, 0.5));
        --shadow-lg: 0 8px 24px color-mix(in srgb, var(--brand-primary) 16%, rgba(0, 0, 0, 0.6));
        --shadow-hover: 0 8px 24px color-mix(in srgb, var(--brand-primary) 22%, rgba(0, 0, 0, 0.65));

        /* Tags mode sombre */
        --tag-bg: color-mix(in srgb, var(--accent-green) 24%, var(--bg-secondary));
        --tag-text: #34d399; /* Emeraude tva-site --success, variante sombre (harmonisation 2026-09-20 ter) */
        --accent-green: var(--tag-text);
        --slider-fill-hue: 155.4919deg; /* Recalculé pour cibler #34d399 (harmonisation 2026-09-20 ter) */
        --slider-fill-sat: 0.5474;
        --slider-fill-bri: 1.5258;

        /* Variable native Streamlit mode sombre */
        --primary-color: #22d3ee;
    }
}

[data-theme-actual="dark"] {
    /* Palette Teal/Cyan mode sombre - 2026-09-20 */
    --brand-primary: #22d3ee;
    --brand-secondary: #06b6d4;
    --brand-accent: #0891b2;
    --brand-soft: color-mix(in srgb, #22d3ee 16%, transparent);

    /* Alias pour compatibilité */
    --brand-blue: var(--brand-primary);
    --brand-blue-soft: var(--brand-soft);

    /* Fond bleu nuit pour mode sombre */
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-tertiary: #334155;

    /* Texte optimisé contraste mode sombre */
    --text-primary: #f1f5f9;
    --text-secondary: #cbd5e1;
    --text-muted: #94a3b8;

    /* Bordures mode sombre */
    --border-light: #334155;
    --border-medium: #475569;
    --border-strong: #64748b;

    /* Alertes mode sombre - info adapté Teal */
    --alert-error-bg: #1a1515;
    --alert-error-border: #3f2626;
    --alert-error-text: #fca5a5;

    --alert-warning-bg: #1a1912;
    --alert-warning-border: #3f3a24;
    --alert-warning-text: #fcd34d;

    --alert-success-bg: #0f1f15;
    --alert-success-border: #1f3f2b;
    --alert-success-text: #86efac;

    --alert-info-bg: #0f1f29;
    --alert-info-border: #1e4a5a;
    --alert-info-text: #67e8f9;

    /* Ombres mode sombre — teintées Teal cyan (refonte 2026-09-20 bis) */
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.4);
    --shadow-md: 0 2px 10px color-mix(in srgb, var(--brand-primary) 14%, rgba(0, 0, 0, 0.5));
    --shadow-lg: 0 8px 24px color-mix(in srgb, var(--brand-primary) 16%, rgba(0, 0, 0, 0.6));
    --shadow-hover: 0 8px 24px color-mix(in srgb, var(--brand-primary) 22%, rgba(0, 0, 0, 0.65));

    /* Tags mode sombre */
    --tag-bg: color-mix(in srgb, var(--accent-green) 24%, var(--bg-secondary));
    --tag-text: #34d399; /* Emeraude tva-site --success, variante sombre (harmonisation 2026-09-20 ter) */
    --accent-green: var(--tag-text);
    --slider-fill-hue: 155.4919deg; /* Recalculé pour cibler #34d399 (harmonisation 2026-09-20 ter) */
    --slider-fill-sat: 0.5474;
    --slider-fill-bri: 1.5258;

    /* Variable native Streamlit mode sombre */
    --primary-color: #22d3ee;
}

/* Clair forcé explicitement (l'utilisateur a choisi "Clair" dans le menu
   Streamlit alors que l'OS est en sombre) : doit rester APRÈS le @media
   ci-dessus pour pouvoir annuler son repli sombre sur ces mêmes variables. */
[data-theme-actual="light"] {
    /* Palette Teal/Cyan mode clair - 2026-09-20 */
    --brand-primary: #0891b2;
    --brand-secondary: #06b6d4;
    --brand-accent: #0e7490;
    --brand-soft: color-mix(in srgb, #0891b2 12%, transparent);

    /* Alias pour compatibilité */
    --brand-blue: var(--brand-primary);
    --brand-blue-soft: var(--brand-soft);

    /* Fond très clair inspiré du site web (slate-50) pour mode clair - 2026-09-20 */
    --bg-primary: #f0f5fc; /* Lavis bleu tva-site 4% (harmonisation 2026-09-20 ter) */
    --bg-secondary: #ffffff;
    --bg-tertiary: #e8eff9; /* Lavis bleu tva-site 5% (harmonisation 2026-09-20 ter) */

    /* Texte optimisé pour lisibilité (contraste WCAG AA) */
    --text-primary: #0f172a;
    --text-secondary: #334155;
    --text-muted: #64748b;

    /* Bordures 3 niveaux pour hiérarchie visuelle */
    --border-light: #cbd5e1;
    --border-medium: #94a3b8;
    --border-strong: #64748b;

    /* Alertes 4 catégories - info adapté à la palette Teal */
    --alert-error-bg: #fef2f2;
    --alert-error-border: #fecaca;
    --alert-error-text: #dc2626;

    --alert-warning-bg: #fffbeb;
    --alert-warning-border: #fde68a;
    --alert-warning-text: #d97706;

    --alert-success-bg: #f0fdf4;
    --alert-success-border: #bbf7d0;
    --alert-success-text: #16a34a;

    --alert-info-bg: #ecfeff;
    --alert-info-border: #a5f3fc;
    --alert-info-text: #0891b2;

    /* Ombres 4 niveaux pour profondeur — teintées Teal (refonte 2026-09-20 bis) */
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.08);
    --shadow-md: 0 2px 10px color-mix(in srgb, var(--brand-primary) 10%, rgba(15, 23, 42, 0.12));
    --shadow-lg: 0 8px 24px color-mix(in srgb, var(--brand-primary) 12%, rgba(15, 23, 42, 0.15));
    --shadow-hover: 0 6px 18px color-mix(in srgb, var(--brand-primary) 16%, rgba(15, 23, 42, 0.18));

    /* Tags multiselect - vert conservé pour cohérence (2026-09-18) */
    --tag-bg: color-mix(in srgb, var(--accent-green) 18%, #ffffff);
    --tag-text: #047857; /* Emeraude tva-site --success, ajusté contraste AA (harmonisation 2026-09-20 ter) */
    --accent-green: var(--tag-text);
    --slider-fill-hue: 161.0538deg; /* Recalculé pour cibler #047857 (harmonisation 2026-09-20 ter) */
    --slider-fill-sat: 0.7407;
    --slider-fill-bri: 0.8197;

    /* Variable native Streamlit - adaptée à Teal (2026-09-20) */
    --primary-color: #0891b2;
}

/* ══════════════════════════════════════════════════════════════════════
   2. SOCLE — fond, texte, typographie
   Améliorations lisibilité (2026-09-20) : line-height 1.7, letter-spacing optimisé
   ══════════════════════════════════════════════════════════════════════ */
.stApp {
    background-color: var(--bg-primary);
    font-family: 'DM Sans', sans-serif; /* Corps de texte (refonte 2026-09-20 quater) */
}
.main, .block-container {
    background-color: var(--bg-primary);
}
.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

.stMarkdown, .stMarkdown p, .stMarkdown li {
    color: var(--text-primary);
    line-height: 1.7; /* Amélioré pour lisibilité (2026-09-20) */
}

h1, h2, h3, h4,
[data-testid="stMetricValue"],
.kpi-value {
    font-family: 'Manrope', sans-serif; /* Titres/chiffres-clés (refonte 2026-09-20 quater) */
}

h1 {
    color: var(--text-primary);
    border-bottom: 3px solid var(--brand-primary);
    padding-bottom: 8px;
    font-weight: 800;
    letter-spacing: -0.01em;
}
h2, h3 {
    color: var(--text-primary);
    font-weight: 700;
    letter-spacing: -0.005em; /* Subtil amélioré (2026-09-20) */
}
h2 {
    border-bottom: 1px solid var(--border-light);
    padding-bottom: 6px;
}

hr {
    border: none;
    border-top: 1px solid var(--border-light);
    margin: 1.5rem 0;
}

#MainMenu { visibility: visible !important; }
header { visibility: visible !important; }
header[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"] {
    background-color: var(--bg-primary);
}

/* ══════════════════════════════════════════════════════════════════════
   3. SIDEBAR
   Refonte 2026-09-20 quater (lot 3, alignée sur la maquette de Matthieu) :
   fond distinct du contenu principal (surface claire au lieu du même
   fond bleuté que .stApp) pour que les "cards" (expanders) se détachent
   dessus, à la manière du panneau blanc de la maquette. Le commentaire
   précédent ("fond bleu-vert") ne correspondait déjà plus à la valeur
   réellement appliquée (var(--bg-primary), identique au fond de page) —
   corrigé ici plutôt que reconduit tel quel.
   ══════════════════════════════════════════════════════════════════════ */
section[data-testid="stSidebar"] {
    background-color: var(--bg-secondary);
    border-right: 1px solid var(--border-light);
    min-width: 400px !important;
    max-width: 450px !important;
}

section[data-testid="stSidebar"] * {
    color: var(--text-primary);
}

/* Rectangles pour tout élément de saisie (contraste contre le fond clair
   de la sidebar) : champs natifs + wrappers BaseWeb du selectbox et
   des combobox de recherche, que Streamlit ne rend pas comme <select>. */
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
section[data-testid="stSidebar"] div[data-baseweb="base-input"],
section[data-testid="stSidebar"] div[data-baseweb="input"] {
    background-color: var(--bg-tertiary) !important;
    color: var(--text-primary);
    border-color: var(--border-medium) !important;
    border-radius: var(--radius-sm);
    transition: border-color 0.3s ease, box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
section[data-testid="stSidebar"] input:focus,
section[data-testid="stSidebar"] textarea:focus,
section[data-testid="stSidebar"] div[data-baseweb="select"] > div:focus,
section[data-testid="stSidebar"] div[data-baseweb="base-input"]:focus,
section[data-testid="stSidebar"] div[data-baseweb="input"]:focus {
    border-color: var(--brand-primary) !important;
    box-shadow: 0 0 0 3px var(--brand-soft); /* Focus ring teal (2026-09-20) */
    outline: none;
}
/* Exception : le multiselect (ex. pays TVA) a un <input> de recherche
   invisible intercalé ENTRE les tags. La règle ci-dessus lui donnait un
   fond opaque, qui se retrouvait visuellement posé juste devant le
   1er tag et masquait son 1er caractère (le "F" de "FR") — corrigé le
   2026-09-18, retour Matthieu (le vrai coupable n'était donc pas la
   largeur du tag, corrigée pour rien au tour précédent, mais gardée :
   elle reste correcte en soi). Doit rester APRÈS la règle ci-dessus pour
   la surcharger (même spécificité par élément mais sélecteur plus
   profond ici : gagne dans tous les cas). */
section[data-testid="stSidebar"] div[data-baseweb="select"] input,
section[data-testid="stSidebar"] div[data-baseweb="multiselect"] input,
section[data-testid="stSidebar"] div[data-baseweb="popover"] input {
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* Titre "Options" de la sidebar (st.header) — accent de marque en pied,
   comme le bloc ".brand" de la maquette, plutôt que le h2 générique
   (bordure grise) appliqué au reste de l'app. */
section[data-testid="stSidebar"] h2 {
    font-size: 1.2rem;
    border-bottom: 2px solid var(--brand-primary);
    padding-bottom: 8px;
    margin-bottom: 4px;
}

/* Rappel de thème (st.caption) sous les sélecteurs pays/devise — texte
   discret, un peu plus d'air en dessous avant la 1ère card. */
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    margin-bottom: 12px;
}

/* Cards de section (st.expander : Entreprise, Cache VIES, Paramètres
   fichier...) — rayon plus généreux et fond distinct du fond de sidebar
   pour un rendu "card sur surface", comme .company/.side-settings dans
   la maquette. */
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: var(--bg-tertiary);
    border: 1px solid var(--border-light);
    border-radius: var(--radius-md);
    margin-bottom: 12px;
    overflow: hidden; /* le radius doit aussi s'appliquer à l'en-tête cliquable */
    transition: box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
section[data-testid="stSidebar"] div[data-testid="stExpander"]:hover {
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
/* En-tête de l'expander (titre + chevron) — un peu de respiration */
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
    padding: 4px 2px;
    font-weight: 650;
}

section[data-testid="stSidebar"] [role="switch"] {
    background-color: var(--border-medium);
    transition: background-color 0.3s ease; /* Animation modérée (2026-09-20) */
}

section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
    gap: 0.5rem;
}

/* Toggles / checkboxes "coché" — vert au lieu du rouge natif Streamlit.
   CORRIGÉ le 2026-09-19 (3e itération) : st.toggle et st.checkbox
   partagent tous deux data-baseweb="checkbox" mais PAS la même structure
   interne. Confirmé par deux outerHTML distincts :
   - st.checkbox (ex. "Historique complet des vérifications") : 1er
     enfant = <span> vide (la case), puis <input>, puis <div> = texte.
   - st.toggle (ex. "Vendeur = importateur officiel (DDP)") : 1er enfant
     = <div> (la piste ronde du switch) — structure jamais vérifiée par
     outerHTML mais c'est la seule hypothèse cohérente avec le fait que
     la toute première règle (`> div:first-child`, 2026-09-18) colorait
     bien le DDP alors qu'elle ne matchait jamais la checkbox.
   D'où DEUX règles nécessaires, une par variante — remplacer l'une par
   l'autre (erreur de la 2e itération) casse systématiquement l'un des
   deux widgets. */
label[data-baseweb="checkbox"]:has(input[type="checkbox"]:checked) > div:first-child,
label[data-baseweb="checkbox"]:has(input[type="checkbox"]:checked) > span:first-child {
    background-color: var(--accent-green) !important;
    border-color: var(--accent-green) !important;
}

/* Tags multiselect (ex : pays TVA) — vert, comme dans la version d'origine.
   `--tag-*` reste une paire de variables séparées de `--brand-blue` pour
   ne pas dépendre de l'accent de marque.
   Sélecteur élargi à `[data-baseweb="tag"]` (div OU span selon la version
   de Streamlit/BaseWeb) plutôt que `span[data-baseweb="tag"]` strict —
   c'était trop restrictif et laissait passer le rouge par défaut.
   Largeur forcée en `auto`/`fit-content` (2026-09-18) plutôt qu'un simple
   `overflow: visible` sur le texte : BaseWeb calcule une largeur FIXE
   pour le tag en anticipant une troncature ; avec juste overflow:visible
   le texte déborde de ce cadre trop étroit au lieu de l'agrandir, et le
   1er caractère du tout premier tag de la liste (ex. le "F" de "FR")
   déborde hors du conteneur côté gauche et se retrouve invisible. */
.stMultiSelect [data-baseweb="tag"],
div[data-baseweb="multiselect"] [data-baseweb="tag"] {
    background-color: var(--tag-bg) !important;
    color: var(--tag-text) !important;
    padding: 3px 10px !important;
    gap: 6px;
    width: auto !important;
    max-width: none !important;
    min-width: fit-content !important;
}
.stMultiSelect [data-baseweb="tag"] > span:first-child,
div[data-baseweb="multiselect"] [data-baseweb="tag"] > span:first-child {
    overflow: visible !important;
    text-overflow: unset !important;
    max-width: none !important;
    color: var(--tag-text) !important;
}
.stMultiSelect [data-baseweb="tag"] svg,
div[data-baseweb="multiselect"] [data-baseweb="tag"] svg {
    fill: var(--tag-text) !important;
    flex-shrink: 0;
}

/* Radio buttons — transforme le rouge en vert (2026-09-18) via le filtre fourni.
   Sélecteur relationnel :has() pour cibler la case colorée (div frère précédent). */
label[data-baseweb="radio"]:has(input[type="radio"]:checked) > div:first-child {
    filter: hue-rotate(var(--slider-fill-hue)) saturate(var(--slider-fill-sat)) brightness(var(--slider-fill-bri)) !important;
}

/* Sélecteur segmenté (Simple/Détaillé) et Pills — transforme le rouge en vert.
   CORRIGÉ le 2026-09-19 via inspection DOM directe : le bouton actif ne
   porte AUCUN attribut ARIA d'état (pas de aria-checked/aria-pressed).
   Streamlit 1.58 marque l'état actif via l'attribut `kind` lui-même :
   `kind="segmented_controlActive"` / `data-testid=
   "stBaseButton-segmented_controlActive"` (vs `kind="segmented_control"`
   pour un bouton inactif). D'où l'échec des deux tentatives précédentes
   basées sur des attributs ARIA qui n'existent pas sur ce composant. */
div[data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"],
div[data-testid="stPills"] button[data-testid="stBaseButton-pillsActive"] {
    filter: hue-rotate(var(--slider-fill-hue)) saturate(var(--slider-fill-sat)) brightness(var(--slider-fill-bri)) !important;
    background-color: rgb(255, 75, 75) !important; /* Force la couleur source pour que le filtre produise le vert exact */
    border-color: rgb(255, 75, 75) !important;
    color: #ffffff !important;
}
[data-theme-actual="dark"] div[data-testid="stSegmentedControl"] button[data-testid="stBaseButton-segmented_controlActive"],
[data-theme-actual="dark"] div[data-testid="stPills"] button[data-testid="stBaseButton-pillsActive"] {
    color: #0e1117 !important;
}

/* Ligne de soulignement (highlight) des onglets — force en vert.
   Cible le conteneur du soulignement rouge natif. */
.stTabs [data-baseweb="tab-highlight"] {
    background-color: var(--accent-green) !important;
}

/* ══════════════════════════════════════════════════════════════════════
   4. BOUTONS
   Améliorations (2026-09-20) : transitions modérées (0.3s), palette Teal
   ══════════════════════════════════════════════════════════════════════ */
button[kind="primary"] {
    background-color: var(--brand-primary) !important;
    border-color: var(--brand-primary) !important;
    color: #ffffff !important;
    width: 100%;
    font-weight: 600;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée fluide (2026-09-20) */
}
button[kind="primary"]:hover {
    filter: brightness(1.08);
    transform: translateY(-1px); /* Micro-élévation (2026-09-20) */
    box-shadow: var(--shadow-md);
}
[data-theme-actual="dark"] button[kind="primary"] {
    color: #0f172a !important; /* Adapté pour contraste mode sombre (2026-09-20) */
}

button[kind="secondary"] {
    border: 1px solid var(--border-medium);
    color: var(--text-primary);
    background-color: var(--bg-secondary);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée (2026-09-20) */
}
button[kind="secondary"]:hover {
    border-color: var(--brand-primary);
    color: var(--brand-primary);
    transform: translateY(-1px); /* Micro-élévation (2026-09-20) */
    box-shadow: var(--shadow-sm);
}

/* Liens donation personnalisés avec SVG - 2026-09-20 */
.donation-link {
    text-decoration: none !important;
    display: inline-block;
    padding: 8px 16px;
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    background-color: var(--bg-secondary);
    color: var(--text-primary);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.donation-link:hover {
    border-color: var(--brand-primary);
    transform: translateY(-1px);
    box-shadow: var(--shadow-sm);
}
.donation-link svg {
    width: 80px;
    height: 24px;
}

.stDownloadButton > button {
    width: 100% !important;
}

button[data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--brand-blue) !important;
    color: var(--brand-blue) !important;
}

/* Bouton déclencheur d'un st.popover (ex. menu "Pays d'origine & devise",
   sidebar.py, lot 7) — Streamlit ne semble pas lui appliquer les mêmes
   variables de thème que les boutons "secondary" classiques ci-dessus en
   mode sombre (retour Matthieu 2026-09-20 : rendu noir au lieu du fond/
   bordure teal attendus). Règle dédiée, plus spécifique et en !important,
   en plus de button[kind="secondary"] ci-dessus (pas à sa place : les deux
   coexistent au cas où seule l'une des deux sélectionne effectivement le
   bouton selon la structure DOM réelle du popover). Non vérifié
   visuellement (pas d'app exécutable dans l'environnement de dev) — à
   confirmer par Matthieu après déploiement.*/
div[data-testid="stPopover"] > button,
div[data-testid="stPopover"] button[kind="secondary"] {
    background-color: var(--bg-secondary) !important;
    border: 1px solid var(--border-medium) !important;
    color: var(--text-primary) !important;
}
div[data-testid="stPopover"] > button:hover,
div[data-testid="stPopover"] button[kind="secondary"]:hover {
    border-color: var(--brand-primary) !important;
    color: var(--brand-primary) !important;
}

/* ══════════════════════════════════════════════════════════════════════
   5. INPUTS / FORMULAIRES
   Améliorations (2026-09-20) : focus rings teal, transitions modérées
   ══════════════════════════════════════════════════════════════════════ */
.stTextInput > div > div > input,
.stSelectbox > div > div > select,
.stNumberInput > div > div > input {
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    transition: border-color 0.3s ease, box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--brand-primary); /* Focus ring teal (2026-09-20) */
    box-shadow: 0 0 0 3px var(--brand-soft);
    outline: none;
}

.stCheckbox > label,
.stRadio > div {
    color: var(--text-primary);
    font-weight: 500;
}

.stSlider > div > div > div {
    background-color: var(--border-light);
}
/* Curseur (thumb) du slider — vert, comme les toggles ci-dessus. Le
   sélecteur `[role="slider"]` est confirmé par dump DOM le 2026-09-18
   (`<div role="slider" aria-valuemax="30" ...>`) — c'était le seul des
   3 sélecteurs devinés précédemment à être correct ; les 2 autres
   (`> div > div` et `> div:first-child > div`) matchaient des conteneurs
   de la structure interne (probablement le wrapper du libellé de valeur)
   et faisaient disparaître les chiffres min/max — retirés, non
   réintroduits ici. */
div[data-testid="stSlider"] [role="slider"] {
    background-color: var(--accent-green) !important;
    border-color: var(--accent-green) !important;
    transition: background-color 0.3s ease, border-color 0.3s ease; /* Animation modérée (2026-09-20) */
}
/* Valeur courante affichée au-dessus du slider (ex: "250") — vert (2026-09-18). */
div[data-testid="stSlider"] [data-baseweb="slider"] > div:first-child {
    color: var(--accent-green) !important;
}
div[data-testid="stSlider"] [data-baseweb="slider"] > div:first-child * {
    color: var(--accent-green) !important;
}
/* Barre de remplissage — Streamlit la peint avec un `background:
   linear-gradient(to right, rgb(255,75,75) 0%, rgb(255,75,75) X%,
   rgba(151,166,195,0.25) X%, ...)`, X% étant la position recalculée en
   JS à chaque interaction. Confirmé par diagnostic de style calculé le
   2026-09-18 (getComputedStyle) après deux tentatives infructueuses sur
   `background-color`, qui n'a AUCUN effet sur un `background:
   linear-gradient()` posé en style inline (ce n'est pas la même
   propriété, et le rouge rgb(255,75,75) est la couleur "primaryColor"
   par défaut de Streamlit, câblée côté composant — pas via notre
   variable --primary-color).
   Le pourcentage étant dynamique, impossible de le réécrire en CSS
   statique. On utilise donc un filtre `hue-rotate` : il transforme la
   teinte rouge en vert quelle que soit sa position, et n'affecte quasi
   pas la portion grise translucide (peu saturée) — exactement l'effet
   demandé ("le vert doit remplacer le rouge, et c'est tout").
   Angle + saturate/brightness recalculés le 2026-09-18 (round 2, retour
   Matthieu : le point et le trait n'étaient toujours pas exactement de
   la même couleur) par résolution numérique exacte plutôt qu'une
   grille approximative : pour un angle donné, `brightness` optimal se
   calcule analytiquement (projection sur la cible), et une recherche
   fine sur l'angle + `saturate` trouve la meilleure combinaison. Résultat
   quasi parfait : rgb(255,75,75) → hue-rotate(137.5deg) saturate(0.44)
   brightness(1.4046) → rgb(79,187,118), IDENTIQUE au pixel près à
   --accent-green sombre. Un filtre ne peut normalement qu'approcher une
   couleur cible, mais l'espace de solutions se trouve ici contenir une
   quasi-solution exacte. Valeurs différentes en clair (135.5deg / 0.54 /
   1.041, tout aussi précises) car la cible --accent-green change de
   teinte/luminosité entre les 2 modes alors que le rouge source
   (rgb(255,75,75), fixe côté Streamlit) ne change pas — d'où les
   variables --slider-fill-hue/-sat/-bri, définies par thème comme les
   autres couleurs, plutôt qu'un filtre unique codé en dur.
   Sélecteur relationnel (`:has()` + `+`, plutôt que positionnel comme
   `:last-child`) : identifie le wrapper du curseur via le `[role=
   slider]` qu'il contient, puis cible sa div suivante — la barre de
   remplissage, quel que soit l'ordre réel des enfants. */
div[data-testid="stSlider"] [data-baseweb="slider"] > div > div > div:has(> [role="slider"]) + div {
    filter: hue-rotate(var(--slider-fill-hue)) saturate(var(--slider-fill-sat)) brightness(var(--slider-fill-bri));
    transition: filter 0.3s ease; /* Animation modérée (2026-09-20) */
}

/* Libellés min/max ("1"/"30") du slider, invisibles par défaut — forcés
   visibles en permanence plutôt que seulement au survol/focus (retour
   Matthieu 2026-09-18). Structure confirmée par dump : deux
   <p> dans un <div data-testid="stSliderTickBar"> toujours présent dans
   le DOM, donc masqué par une propriété CSS (opacity/visibility), pas
   absent. */
div[data-testid="stSliderTickBar"] {
    opacity: 1 !important;
    visibility: visible !important;
}
/* Le fond visible (petit carré) autour de "1"/"30" est le style "bulle"
   par défaut de ce conteneur (pensé pour une bulle au survol) — devenu
   visible en permanence maintenant qu'on force l'opacité (retour
   Matthieu 2026-09-18). On ne garde que le texte. */
div[data-testid="stSliderTickBar"],
div[data-testid="stSliderTickBar"] * {
    background: transparent !important;
    box-shadow: none !important;
}
div[data-testid="stSliderTickBar"] p {
    color: var(--text-secondary) !important;
}

.stFileUploader {
    border-radius: var(--radius-sm);
    background-color: var(--bg-secondary);
}
/* La bordure pointillée visible et son survol sont portés par la
   dropzone interne, pas par le wrapper .stFileUploader ci-dessus —
   c'est elle qui prenait le rouge par défaut de Streamlit. */
[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed var(--border-medium) !important;
    border-radius: var(--radius-sm);
    background-color: var(--bg-secondary);
    transition: border-color 0.3s ease, background-color 0.3s ease; /* Animation modérée (2026-09-20) */
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--brand-primary) !important; /* Hover teal (2026-09-20) */
    background-color: var(--bg-tertiary);
}

/* ══════════════════════════════════════════════════════════════════════
   6. CONTENEURS — expanders, métriques, dataframes, tables
   Améliorations (2026-09-20) : transitions modérées, ombres optimisées, palette Teal
   ══════════════════════════════════════════════════════════════════════ */
div[data-testid="stExpander"] {
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    background-color: var(--bg-secondary);
    box-shadow: var(--shadow-sm);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée fluide (2026-09-20) */
}
div[data-testid="stExpander"]:hover {
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
div[data-testid="stExpander"] > div {
    padding: 16px;
}

.streamlit-expanderHeader {
    font-weight: 600;
    color: var(--text-primary);
    transition: color 0.3s ease; /* Animation modérée (2026-09-20) */
}
.streamlit-expanderHeader:hover {
    color: var(--brand-primary); /* Hover teal (2026-09-20) */
}

div[data-testid="stMetric"] {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée fluide (2026-09-20) */
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-hover);
}
[data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
}
[data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
}

/* Container natif st.container(border=True) — utilisé par l'onglet
   Déclarations (lot 4, 2026-09-20) pour encadrer le récapitulatif dans un
   panel, comme les cartes ".panel" de la maquette de Matthieu. Best-effort
   NON VÉRIFIÉ visuellement (assets JS Streamlit minifiés, pas d'app
   complète exécutable pour capture d'écran dans cet environnement) : le
   sélecteur data-testid ci-dessous correspond au comportement documenté
   de Streamlit sur plusieurs versions récentes, mais n'a pas été confirmé
   sur cette install précise (1.58.0). Échec silencieux si le sélecteur ne
   matche pas : Streamlit affiche de toute façon sa bordure native par
   défaut, donc aucune casse visuelle possible, seulement un habillage en
   moins. À CONFIRMER VISUELLEMENT après déploiement. */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: var(--radius-lg) !important;
    box-shadow: var(--shadow-sm);
    padding: 4px;
}

/* Graphiques Plotly (onglet Visualisations, lot 5, 2026-09-20) — même
   traitement "card" que le reste de l'app (radius + ombre légère), sur un
   sélecteur Streamlit natif et documenté (contrairement au wrapper de
   container bordé ci-dessus). Le fond des figures elles-mêmes reste opaque
   clair (défaut Plotly, non modifié) : Python ne peut pas savoir si le
   thème clair/sombre est actif côté navigateur (bascule JS/CSS uniquement,
   cf. plus haut dans ce fichier), donc un fond transparent + texte de
   contraste fixe serait illisible dans l'un des deux thèmes. Même
   rationnel que le fond blanc fixe déjà appliqué à la légende de la carte
   choroplèthe (_build_fig_map, visualisations.py). */
div[data-testid="stPlotlyChart"] {
    border-radius: var(--radius-md);
    border: 1px solid var(--border-light);
    box-shadow: var(--shadow-sm);
    padding: 8px;
    background-color: var(--bg-secondary);
}

div[data-testid="stDataFrame"] {
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-medium);
    overflow-x: auto !important;
    box-shadow: var(--shadow-sm);
}

.stTable {
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    overflow: hidden;
}
.stTable thead th {
    background-color: var(--bg-tertiary);
    color: var(--text-primary);
    font-weight: 600;
    border-bottom: 1px solid var(--border-medium);
}
.stTable tbody tr {
    border-bottom: 1px solid var(--border-light);
    transition: background-color 0.3s ease; /* Animation modérée (2026-09-20) */
}
.stTable tbody tr:hover {
    background-color: var(--bg-tertiary);
}

.stCode {
    background-color: var(--bg-tertiary);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    padding: 12px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.9rem;
    color: var(--text-primary);
}

.stBlockquote {
    border-left: 3px solid var(--brand-primary); /* Teal accent (2026-09-20) */
    background-color: var(--bg-secondary);
    padding: 12px 16px;
    margin: 16px 0;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    color: var(--text-secondary);
    font-style: italic;
}

[data-testid="stTooltip"] {
    background-color: var(--text-primary);
    color: var(--bg-primary);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
    font-size: 0.85rem;
}

.stBadge {
    background-color: var(--brand-primary); /* Teal badge (2026-09-20) */
    color: #ffffff;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-weight: 600;
}

.stProgress > div > div > div > div {
    background-color: var(--brand-primary); /* Teal progress (2026-09-20) */
    border-radius: 4px;
}

/* ══════════════════════════════════════════════════════════════════════
   7. ONGLETS
   Améliorations (2026-09-20) : soulignement teal, transitions modérées
   ══════════════════════════════════════════════════════════════════════ */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    border-bottom: 1px solid var(--border-light);
}
.stTabs [data-baseweb="tab"] {
    background-color: transparent;
    border: none;
    border-bottom: 3px solid transparent;
    padding: 10px 18px;
    color: var(--text-secondary);
    font-weight: 500;
    transition: all 0.3s ease; /* Animation modérée (2026-09-20) */
}
.stTabs [data-baseweb="tab"][aria-selected="true"],
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--brand-primary) !important; /* Teal actif (2026-09-20) */
    border-bottom: 3px solid var(--brand-primary) !important;
    font-weight: 600;
}
.stTabs [data-baseweb="tab"]:hover,
button[data-baseweb="tab"]:hover {
    color: var(--brand-primary) !important; /* Hover teal (2026-09-20) */
}

/* ══════════════════════════════════════════════════════════════════════
   8. ALERTES (st.error / st.warning / st.success / st.info)
   ══════════════════════════════════════════════════════════════════════ */
div[data-testid="stAlert"] {
    border-radius: var(--radius-sm);
    border: 1px solid;
    padding: 12px 16px;
}
div[data-testid="stAlert"][data-status="error"] {
    background-color: var(--alert-error-bg);
    border-color: var(--alert-error-border);
    color: var(--alert-error-text);
}
div[data-testid="stAlert"][data-status="warning"] {
    background-color: var(--alert-warning-bg);
    border-color: var(--alert-warning-border);
    color: var(--alert-warning-text);
}
div[data-testid="stAlert"][data-status="success"] {
    background-color: var(--alert-success-bg);
    border-color: var(--alert-success-border);
    color: var(--alert-success-text);
}
div[data-testid="stAlert"][data-status="info"] {
    background-color: var(--alert-info-bg);
    border-color: var(--alert-info-border);
    color: var(--alert-info-text);
}

/* ══════════════════════════════════════════════════════════════════════
   9. ESPACEMENT GÉNÉRAL (conservé à l'identique)
   ══════════════════════════════════════════════════════════════════════ */
div[data-testid="stVerticalBlock"] > div {
    gap: 16px !important;
}
div[data-testid="stVerticalBlock"] > div > div {
    margin-bottom: 12px;
}
.stVerticalBlock {
    gap: 20px !important;
}
.stVerticalBlock > div[data-testid="stVerticalBlock"] {
    margin-top: 16px;
    margin-bottom: 16px;
}

/* ══════════════════════════════════════════════════════════════════════
   10. SCROLLBAR
   ══════════════════════════════════════════════════════════════════════ */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}
::-webkit-scrollbar-track {
    background: var(--bg-tertiary);
    border-radius: 4px;
}
::-webkit-scrollbar-thumb {
    background: var(--border-medium);
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
    background: var(--border-strong);
}

/* ══════════════════════════════════════════════════════════════════════
   11. COMPOSANTS MAISON (classes injectées depuis app.py / sidebar.py)
   Améliorations (2026-09-20) : palette Teal, transitions modérées, meilleure hiérarchie
   ══════════════════════════════════════════════════════════════════════ */

/* Badge "compte connecté" (email + forfait) */
.account-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    border-radius: 999px;
    padding: 5px 12px 5px 10px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    font-size: 0.82rem;
    line-height: 1.4;
    transition: box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
.account-badge:hover {
    box-shadow: var(--shadow-sm); /* Hover subtil (2026-09-20) */
}
.account-badge-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: #2f8f4e;
    flex-shrink: 0;
}
.account-badge-email {
    color: var(--text-secondary);
}
.account-badge-plan {
    font-weight: 700;
    padding: 1px 9px;
    border-radius: 999px;
    font-size: 0.75rem;
}
.account-badge-plan.plan-free {
    background-color: var(--bg-tertiary);
    color: var(--text-secondary);
}
.account-badge-plan.plan-business {
    background-color: var(--brand-soft); /* Teal soft (2026-09-20) */
    color: var(--brand-primary);
}
.account-badge-plan.plan-cabinet {
    background-color: color-mix(in srgb, #b8860b 20%, transparent);
    color: #b8860b;
}
.account-badge-plan.plan-achat {
    background-color: color-mix(in srgb, #6b46c1 18%, transparent);
    color: #7c5cd4;
}

/* Bandeau contextuel en tête de tableau de bord (eyebrow + fil d'Ariane)
   — refonte graphique lot 6 (2026-09-20), aligné sur la maquette fournie
   par Matthieu. Injecté par app.py juste avant le st.header() existant
   (recapitulatif_header) : pur habillage, aucune donnée métier. */
.dashboard-breadcrumb {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-bottom: 6px;
}
.dashboard-eyebrow {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--brand-primary);
    text-transform: uppercase;
    margin-bottom: 4px;
}

/* KPIs (extrait de app.py, section KPIs) — refonte 2026-09-20 quater,
   alignée sur la maquette fournie par Matthieu : bordure d'accent en haut
   (plus proche du modèle "carte produit" que le liseré latéral précédent),
   rayon plus généreux, variante "featured" pour la carte mise en avant
   (fond teinté + valeur colorée). L'accent (var(--kpi-accent)) reste
   défini par _kpi_card() dans app.py — sémantique inchangée par KPI. */
.kpi-card {
    border-radius: var(--radius-lg);
    padding: 18px 18px 16px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-top: 3px solid var(--kpi-accent, var(--brand-primary));
    box-shadow: var(--shadow-sm);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée fluide (2026-09-20) */
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-hover);
}
/* Carte mise en avant (ex. "TVA à votre charge") — fond légèrement teinté
   par l'accent de la carte plutôt que la surface neutre, comme le
   traitement ".kpi.featured" de la maquette. Activée via un 5e paramètre
   optionnel de _kpi_card() (app.py), n'affecte aucune carte existante par
   défaut. */
.kpi-card.featured {
    background-color: color-mix(in srgb, var(--kpi-accent, var(--brand-primary)) 8%, var(--bg-secondary));
}
.kpi-card.featured .kpi-value {
    color: var(--kpi-accent, var(--brand-primary));
}
.kpi-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 10px;
}
.kpi-label {
    font-size: 0.8rem;
    color: var(--text-secondary);
    font-weight: 550;
}
.kpi-icon {
    font-size: 1rem;
    line-height: 1;
    opacity: 0.85;
    flex-shrink: 0;
}
.kpi-value {
    font-size: 1.6rem;
    font-weight: 750;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
}
.badge-alert {
    display: inline-block;
    background-color: color-mix(in srgb, #c0392b 14%, transparent);
    color: #c0392b;
    border-radius: 999px;
    padding: 3px 12px;
    font-size: 0.78rem;
    font-weight: 600;
    margin-top: 6px;
}

/* Barre de statut persistante (fichier / période / mode) */
.status-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    border-radius: var(--radius-md);
    padding: 10px 16px;
    margin-bottom: 14px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--brand-primary); /* Teal accent (2026-09-20) */
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
.status-bar:hover {
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
.status-bar-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.85rem;
    color: var(--text-primary);
}
.status-bar-item .status-bar-label {
    color: var(--text-muted);
}
.status-bar-item .status-bar-value {
    font-weight: 600;
}
.status-bar-sep {
    color: var(--border-strong);
}
.status-bar-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    transition: background-color 0.3s ease; /* Animation modérée (2026-09-20) */
}
.status-bar-dot.ok { background-color: #2f8f4e; }
.status-bar-dot.pending { background-color: #c8850f; }
.status-bar-dot.off { background-color: var(--border-strong); }

/* Bandeau onboarding (checklist démarrage) */
.onboarding-banner {
    border-radius: var(--radius-md);
    padding: 14px 18px;
    margin-bottom: 14px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--brand-primary); /* Teal accent (2026-09-20) */
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
.onboarding-banner:hover {
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
.onboarding-banner-title {
    margin: 0 0 10px;
    font-weight: 700;
    font-size: 1rem;
    color: var(--text-primary);
}
.onboarding-banner-intro {
    margin: 0 0 10px;
    font-size: 0.85rem;
    color: var(--text-secondary);
}
.onboarding-banner-step {
    margin: 0 0 6px;
    font-size: 0.9rem;
    color: var(--text-primary);
}
.onboarding-banner-substep {
    margin: 2px 0 6px 26px;
    font-size: 0.8rem;
    color: var(--text-muted);
}

/* Guidage visuel "Lighthouse" (onboarding) — pur CSS, sans JS ni requête
   réseau : aucun impact sur la détection d'inactivité de l'hébergeur. */
@keyframes onboarding-pulse {
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--brand-primary) 45%, transparent); } /* Teal pulse (2026-09-20) */
    50%      { box-shadow: 0 0 0 6px color-mix(in srgb, var(--brand-primary) 0%, transparent); }
}
.st-key-onb_pulse_entreprise + div[data-testid="stExpander"],
.st-key-onb_pulse_vies + div[data-testid="stExpander"],
.st-key-onb_pulse_upload + div[data-testid="stFileUploaderDropzone"],
.st-key-onb_pulse_upload + div[data-testid="stFileUploader"] {
    border-radius: var(--radius-md);
    animation: onboarding-pulse 2.2s ease-in-out infinite;
}

/* Dashboard "premier lancement" (zéro state, aucun fichier importé) */
.zero-state-grid {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin: 6px 0 18px;
}
.zero-state-card {
    flex: 1 1 220px;
    border-radius: var(--radius-lg);
    padding: 16px 18px;
    background-color: var(--bg-secondary);
    background-image: linear-gradient(160deg, var(--brand-soft) 0%, transparent 60%); /* Dégradé discret (refonte 2026-09-20 bis) */
    border: 1px solid var(--border-medium);
    border-top: 3px solid var(--brand-primary); /* Teal accent (2026-09-20) */
    box-shadow: var(--shadow-sm);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); /* Animation modérée (2026-09-20) */
}
.zero-state-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
.zero-state-card.done {
    border-top-color: #2f8f4e;
    opacity: 0.75;
}
.zero-state-card-title {
    font-weight: 700;
    font-size: 0.95rem;
    margin: 0 0 6px;
    color: var(--text-primary);
}
.zero-state-card-body {
    font-size: 0.82rem;
    color: var(--text-secondary);
    margin: 0;
}

/* Bloc marque (logo + nom + accroche) en tête de sidebar — refonte
   graphique lot 7 (2026-09-20), aligné sur la maquette fournie par
   Matthieu. Injecté par sidebar.py juste avant le st.header("Options"). */
.sidebar-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border-light);
}
.sidebar-brand-logo {
    width: 34px;
    height: 34px;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
}
.sidebar-brand-name {
    font-family: 'Manrope', sans-serif;
    font-weight: 750;
    font-size: 1.05rem;
    color: var(--text-primary);
    line-height: 1.2;
}
.sidebar-brand-tagline {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-muted);
}

/* Pied de sidebar (support/site web) — carte blanche cohérente avec les
   autres blocs de la sidebar (qui est en fond bleu-vert depuis le 2026-09-20),
   à la place de st.divider()/st.caption() qui restaient nus sur le fond
   (retour Matthieu 2026-09-18). */
.sidebar-support-card {
    margin-top: 16px;
    border-radius: var(--radius-md);
    padding: 14px 16px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.3s ease; /* Animation modérée (2026-09-20) */
}
.sidebar-support-card:hover {
    box-shadow: var(--shadow-md); /* Hover subtil (2026-09-20) */
}
.sidebar-support-title {
    font-weight: 700;
    font-size: 0.9rem;
    color: var(--text-primary);
    margin-bottom: 6px;
}
.sidebar-support-email {
    font-size: 0.82rem;
    color: var(--text-secondary);
    margin-bottom: 8px;
}
.sidebar-support-link {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--brand-primary); /* Teal link (2026-09-20) */
    text-decoration: none;
    transition: color 0.3s ease; /* Animation modérée (2026-09-20) */
}
.sidebar-support-link:hover {
    color: var(--brand-secondary); /* Hover cyan (2026-09-20) */
    text-decoration: underline;
}

/* Tableau "Taux de change BCE utilisés" — remplace une liste de
   st.caption() sans séparation visuelle entre les lignes (retour
   Matthieu 2026-09-18). Rendu par app.py via st.markdown(unsafe_allow_html),
   classe dédiée pour ne pas affecter .stTable (tableaux natifs Streamlit). */
.bce-rates-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    margin-top: 4px;
}
.bce-rates-table td {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border-light);
    color: var(--text-primary);
    transition: background-color 0.3s ease; /* Animation modérée (2026-09-20) */
}
.bce-rates-table tr:last-child td {
    border-bottom: none;
}
.bce-rates-table tr:hover td {
    background-color: var(--bg-tertiary);
}
.bce-rates-table td:first-child {
    font-weight: 700;
    width: 15%;
}
.bce-rates-table td:last-child {
    color: var(--text-muted);
    text-align: right;
    width: 25%;
}
</style>
"""


def apply_theme() -> None:
    """Configure la page Streamlit (titre, icône, layout) et injecte le CSS
    de marque. À appeler une seule fois, en tout premier dans app.py (avant
    tout autre appel st.*), exactement comme l'ancien bloc en tête de script.
    """
    st.set_page_config(
        page_title="TVA Intracommunautaire",
        page_icon="\U0001f1ea\U0001f1fa",
        layout="wide",
    )
    st.markdown(_CSS, unsafe_allow_html=True)
    _sync_theme_attribute()


# ═══════════════════════════════════════════════════════════════════════
# SYNCHRONISATION DU THÈME RÉEL (2026-09-18, migré vers st.iframe le
# 2026-09-18 — components.v1.html est déprécié depuis Streamlit 1.58.0 au
# profit de st.iframe, retrait prévu après le 2026-06-01, cf. le module
# streamlit.components.v1 : `html`/`iframe` pointent vers
# `deprecate_func_name(..., name_override="iframe")`. Le HTML brut passé
# à st.iframe est auto-détecté et rendu de la même façon, donc simple
# renommage sans changement de comportement.)
# Streamlit 1.58.0 ne pose aucun attribut/classe exploitable en CSS pur
# pour le choix Clair/Sombre/Système (diagnostic du 2026-09-18, cf.
# docstring en tête de fichier) : la seule source de vérité est
# `localStorage["stActiveTheme-/-v2"]` ("Light" | "Dark" | "System").
# st.iframe (comme l'ancien components.v1.html) est utilisé car un
# <script> inséré par `st.markdown` (via innerHTML) ne s'exécute jamais —
# limitation du navigateur, pas de Streamlit.
# Lecture déterministe (valeur textuelle exacte), pas une heuristique de
# couleur — c'est cette différence qui évite de reproduire l'incident du
# 2026-09-17. `setInterval` est un timer 100% client (aucune requête
# réseau, aucun impact sur le scale-to-zero, qui ne concerne que
# l'inactivité du process serveur / des connexions websocket) ; il
# n'écrit l'attribut QUE si la valeur résolue a changé.
# ═══════════════════════════════════════════════════════════════════════
def _sync_theme_attribute() -> None:
    st.iframe(
        """
        <script>
        (function() {
            var LS_KEY = 'stActiveTheme-/-v2';
            var lastApplied = null;

            function resolve() {
                var raw = null;
                try { raw = window.parent.localStorage.getItem(LS_KEY); } catch (e) { return null; }
                var value = raw ? raw.replace(/"/g, '') : 'System';
                if (value === 'System') {
                    var prefersDark = false;
                    try { prefersDark = window.parent.matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) {}
                    return prefersDark ? 'dark' : 'light';
                }
                return value.toLowerCase();
            }

            function sync() {
                var resolved = resolve();
                if (resolved && resolved !== lastApplied) {
                    try {
                        window.parent.document.documentElement.setAttribute('data-theme-actual', resolved);
                        lastApplied = resolved;
                    } catch (e) {}
                }
            }

            sync();
            setInterval(sync, 1000);
        })();
        </script>
        """,
        height=1,  # st.iframe n'accepte pas 0 (StreamlitInvalidHeightError :
        # entier positif, "stretch" ou "content" uniquement) —
        # contrairement à l'ancien components.v1.html. 1px reste
        # visuellement invisible.
    )