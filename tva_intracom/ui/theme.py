"""Thème visuel de l'application : configuration de page Streamlit et CSS.

Regroupe la config de page et l'injection de style, pour que app.py n'ait
plus qu'à appeler `apply_theme()` en tête de script.

Refonte 2026-09-18 : contraste et hiérarchie visuelle repensés (fond bleu
ciel en mode clair, palette de bordures 3 niveaux, alertes professionnelles
4 catégories, ombres 4 niveaux), mode sombre optimisé en cohérence.

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
    --brand-blue: #1f4e79;
    --brand-blue-soft: color-mix(in srgb, #1f4e79 12%, transparent);

    --bg-primary: #d6ebfa;
    --bg-secondary: #ffffff;
    --bg-tertiary: #c3ddf4;
    --text-primary: #14202e;
    --text-secondary: #47566b;
    --text-muted: #7c8aa0;

    --border-light: #cddbe8;
    --border-medium: #b3c4d6;
    --border-strong: #94a9c0;

    --alert-error-bg: #fdf2f2;
    --alert-error-border: #f3caca;
    --alert-error-text: #9c2b2b;

    --alert-warning-bg: #fdf7ec;
    --alert-warning-border: #eed9ab;
    --alert-warning-text: #8a5a10;

    --alert-success-bg: #f0f8f2;
    --alert-success-border: #bfdec8;
    --alert-success-text: #24693c;

    --alert-info-bg: #eef3fb;
    --alert-info-border: #c4d5ec;
    --alert-info-text: #1f4e79;

    --shadow-sm: 0 1px 2px rgba(20, 32, 46, 0.06);
    --shadow-md: 0 2px 8px rgba(20, 32, 46, 0.09);
    --shadow-lg: 0 6px 20px rgba(20, 32, 46, 0.12);
    --shadow-hover: 0 4px 14px rgba(20, 32, 46, 0.16);
    --radius-sm: 6px;
    --radius-md: 10px;

    --tag-bg: color-mix(in srgb, var(--accent-green) 18%, #ffffff);  /* teinté avec le même vert que le toggle/slider (2026-09-18) */
    --tag-text: #2e7d32;
    --accent-green: var(--tag-text);  /* alias : même vert que les tags pays (2026-09-18, harmonisation) */
    --slider-fill-hue: 135.5deg;  /* filtre hue-rotate calculé par optimisation numérique pour matcher --accent-green exactement (2026-09-18) */
    --slider-fill-sat: 0.54;
    --slider-fill-bri: 1.041;

    /* Réutilise la variable native de Streamlit pour que TOUS ses
       composants natifs (uploader, checkbox/radio, slider, tags
       multiselect, focus ring, liens) suivent notre bleu au lieu du
       rouge/rose par défaut (#FF4B4B) — sans passer par .streamlit/
       config.toml [theme], qui masquerait le sélecteur clair/sombre. */
    --primary-color: #1f4e79;
}

/* Repli "Système" : l'OS/navigateur est en sombre mais Streamlit n'a posé
   aucun attribut data-theme (l'utilisateur n'a rien choisi explicitement
   dans son menu). Doit rester AVANT les blocs [data-theme=...] ci-dessous
   pour que ceux-ci puissent le surcharger en cas de choix explicite. */
@media (prefers-color-scheme: dark) {
    :root {
        --brand-blue: #6fa8d6;
        --brand-blue-soft: color-mix(in srgb, #6fa8d6 16%, transparent);

        --bg-primary: #10151d;
        --bg-secondary: #1a212c;
        --bg-tertiary: #232b38;
        --text-primary: #e7ecf3;
        --text-secondary: #aab6c6;
        --text-muted: #7d8ba0;

        --border-light: #2a3341;
        --border-medium: #37424f;
        --border-strong: #4a5766;

        --alert-error-bg: #241618;
        --alert-error-border: #4a2b2e;
        --alert-error-text: #e59a9a;

        --alert-warning-bg: #241f14;
        --alert-warning-border: #4a3d1f;
        --alert-warning-text: #e0bf78;

        --alert-success-bg: #142219;
        --alert-success-border: #26432f;
        --alert-success-text: #8fcba1;

        --alert-info-bg: #14202f;
        --alert-info-border: #274257;
        --alert-info-text: #9dc2e8;

        --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.35);
        --shadow-md: 0 2px 10px rgba(0, 0, 0, 0.4);
        --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.5);
        --shadow-hover: 0 8px 24px rgba(0, 0, 0, 0.55);

        --tag-bg: color-mix(in srgb, var(--accent-green) 24%, var(--bg-secondary));  /* teinté avec le même vert que le toggle/slider (2026-09-18) */
        --tag-text: #7cd992;
        --accent-green: var(--tag-text);  /* alias : même vert que les tags pays (2026-09-18, harmonisation) */
        --slider-fill-hue: 137.5deg;  /* filtre hue-rotate calculé par optimisation numérique pour matcher --accent-green exactement (2026-09-18) */
        --slider-fill-sat: 0.44;
        --slider-fill-bri: 1.4046;

        --primary-color: #6fa8d6;
    }
}

[data-theme-actual="dark"] {
    --brand-blue: #6fa8d6;
    --brand-blue-soft: color-mix(in srgb, #6fa8d6 16%, transparent);

    --bg-primary: #10151d;
    --bg-secondary: #1a212c;
    --bg-tertiary: #232b38;
    --text-primary: #e7ecf3;
    --text-secondary: #aab6c6;
    --text-muted: #7d8ba0;

    --border-light: #2a3341;
    --border-medium: #37424f;
    --border-strong: #4a5766;

    --alert-error-bg: #241618;
    --alert-error-border: #4a2b2e;
    --alert-error-text: #e59a9a;

    --alert-warning-bg: #241f14;
    --alert-warning-border: #4a3d1f;
    --alert-warning-text: #e0bf78;

    --alert-success-bg: #142219;
    --alert-success-border: #26432f;
    --alert-success-text: #8fcba1;

    --alert-info-bg: #14202f;
    --alert-info-border: #274257;
    --alert-info-text: #9dc2e8;

    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.35);
    --shadow-md: 0 2px 10px rgba(0, 0, 0, 0.4);
    --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.5);
    --shadow-hover: 0 8px 24px rgba(0, 0, 0, 0.55);

    --tag-bg: color-mix(in srgb, var(--accent-green) 24%, var(--bg-secondary));  /* teinté avec le même vert que le toggle/slider (2026-09-18) */
    --tag-text: #7cd992;
    --accent-green: var(--tag-text);  /* alias : même vert que les tags pays (2026-09-18, harmonisation) */
    --slider-fill-hue: 137.5deg;  /* filtre hue-rotate calculé par optimisation numérique pour matcher --accent-green exactement (2026-09-18) */
    --slider-fill-sat: 0.44;
    --slider-fill-bri: 1.4046;

    --primary-color: #6fa8d6;
}

/* Clair forcé explicitement (l'utilisateur a choisi "Clair" dans le menu
   Streamlit alors que l'OS est en sombre) : doit rester APRÈS le @media
   ci-dessus pour pouvoir annuler son repli sombre sur ces mêmes variables. */
[data-theme-actual="light"] {
    --brand-blue: #1f4e79;
    --brand-blue-soft: color-mix(in srgb, #1f4e79 12%, transparent);

    --bg-primary: #d6ebfa;
    --bg-secondary: #ffffff;
    --bg-tertiary: #c3ddf4;
    --text-primary: #14202e;
    --text-secondary: #47566b;
    --text-muted: #7c8aa0;

    --border-light: #cddbe8;
    --border-medium: #b3c4d6;
    --border-strong: #94a9c0;

    --alert-error-bg: #fdf2f2;
    --alert-error-border: #f3caca;
    --alert-error-text: #9c2b2b;

    --alert-warning-bg: #fdf7ec;
    --alert-warning-border: #eed9ab;
    --alert-warning-text: #8a5a10;

    --alert-success-bg: #f0f8f2;
    --alert-success-border: #bfdec8;
    --alert-success-text: #24693c;

    --alert-info-bg: #eef3fb;
    --alert-info-border: #c4d5ec;
    --alert-info-text: #1f4e79;

    --shadow-sm: 0 1px 2px rgba(20, 32, 46, 0.06);
    --shadow-md: 0 2px 8px rgba(20, 32, 46, 0.09);
    --shadow-lg: 0 6px 20px rgba(20, 32, 46, 0.12);
    --shadow-hover: 0 4px 14px rgba(20, 32, 46, 0.16);

    --tag-bg: color-mix(in srgb, var(--accent-green) 18%, #ffffff);  /* teinté avec le même vert que le toggle/slider (2026-09-18) */
    --tag-text: #2e7d32;
    --accent-green: var(--tag-text);  /* alias : même vert que les tags pays (2026-09-18, harmonisation) */
    --slider-fill-hue: 135.5deg;  /* filtre hue-rotate calculé par optimisation numérique pour matcher --accent-green exactement (2026-09-18) */
    --slider-fill-sat: 0.54;
    --slider-fill-bri: 1.041;

    --primary-color: #1f4e79;
}

/* ══════════════════════════════════════════════════════════════════════
   2. SOCLE — fond, texte, typographie
   ══════════════════════════════════════════════════════════════════════ */
.stApp {
    background-color: var(--bg-primary);
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
    line-height: 1.6;
}

h1 {
    color: var(--text-primary);
    border-bottom: 3px solid var(--brand-blue);
    padding-bottom: 8px;
    font-weight: 700;
    letter-spacing: -0.01em;
}
h2, h3 {
    color: var(--text-primary);
    font-weight: 600;
    letter-spacing: -0.01em;
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
   ══════════════════════════════════════════════════════════════════════ */
section[data-testid="stSidebar"] {
    background-color: var(--bg-primary);
    border-right: 1px solid var(--border-medium);
    min-width: 400px !important;
    max-width: 450px !important;
}

section[data-testid="stSidebar"] * {
    color: var(--text-primary);
}

/* Rectangles blancs pour tout élément de saisie (contraste contre le fond
   bleu de la sidebar) : champs natifs + wrappers BaseWeb du selectbox et
   des combobox de recherche, que Streamlit ne rend pas comme <select>. */
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
section[data-testid="stSidebar"] div[data-baseweb="base-input"],
section[data-testid="stSidebar"] div[data-baseweb="input"] {
    background-color: var(--bg-secondary) !important;
    color: var(--text-primary);
    border-color: var(--border-medium) !important;
    border-radius: var(--radius-sm);
}
/* Exception : le multiselect (ex. pays TVA) a un <input> de recherche
   invisible intercalé ENTRE les tags. La règle ci-dessus lui donnait un
   fond blanc opaque, qui se retrouvait visuellement posé juste devant le
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

section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    margin-bottom: 10px;
}

section[data-testid="stSidebar"] [role="switch"] {
    background-color: var(--border-medium);
}

section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
    gap: 0.5rem;
}

/* Toggles / checkboxes "coché" — vert au lieu du rouge natif Streamlit
   (2026-09-18). Structure réelle confirmée par dump DOM le 2026-09-18 :
   c'est un st.checkbox (data-testid="stCheckbox"), sans aucun rôle ARIA
   switch/checkbox — seul l'<input type="checkbox"> caché porte
   aria-checked, et la case colorée visible (1er <div> du <label>) est un
   FRÈRE PRÉCÉDENT de cet input dans le DOM. Un combinateur CSS classique
   (+ / ~) ne peut cibler qu'un frère SUIVANT ; on utilise donc :has(),
   qui permet de remonter du parent <label> vers son enfant <input>
   coché, puis de redescendre vers le 1er <div> (la case). Remplace les
   anciens sélecteurs [role="switch"]/[role="checkbox"] qui ne
   matchaient jamais rien (confirmé par dump : "AUCUN sélecteur ne
   matche"). Portée globale (pas juste la sidebar) : ce composant existe
   aussi hors sidebar.
   Seul le 1er <div> (le "track"/la case) est coloré — PAS son enfant
   (le "knob"/cercle qui glisse à l'intérieur) : les colorer tous les
   deux de la même teinte les rendait indiscernables, le bouton perdant
   tout relief visuel et ne ressemblant plus à un bouton (retour
   Matthieu 2026-09-18). Le knob garde sa couleur native (blanc), seul
   contraste qui montre encore qu'il s'agit d'un bouton. */
label[data-baseweb="checkbox"]:has(input[type="checkbox"]:checked) > div:first-child {
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

/* Sélecteur segmenté (ex: Simple/Détaillé) et Pills — transforme le rouge en vert. */
div[data-testid="stSegmentedControl"] button[aria-checked="true"],
div[data-testid="stPills"] button[aria-checked="true"] {
    filter: hue-rotate(var(--slider-fill-hue)) saturate(var(--slider-fill-sat)) brightness(var(--slider-fill-bri)) !important;
    background-color: rgb(255, 75, 75) !important; /* Force la couleur source pour que le filtre produise le vert exact */
    color: #ffffff !important;
}
[data-theme-actual="dark"] div[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-theme-actual="dark"] div[data-testid="stPills"] button[aria-checked="true"] {
    color: #0e1117 !important;
}

/* Ligne de soulignement (highlight) des onglets — force en vert.
   Cible le conteneur du soulignement rouge natif. */
.stTabs [data-baseweb="tab-highlight"] {
    background-color: var(--accent-green) !important;
}

/* ══════════════════════════════════════════════════════════════════════
   4. BOUTONS
   ══════════════════════════════════════════════════════════════════════ */
button[kind="primary"] {
    background-color: var(--brand-blue) !important;
    border-color: var(--brand-blue) !important;
    color: #ffffff !important;
    width: 100%;
    font-weight: 600;
    transition: filter 0.15s ease;
}
button[kind="primary"]:hover {
    filter: brightness(1.08);
}
[data-theme-actual="dark"] button[kind="primary"] {
    color: #0e1117 !important;
}

button[kind="secondary"] {
    border: 1px solid var(--border-medium);
    color: var(--text-primary);
    background-color: var(--bg-secondary);
    transition: border-color 0.15s ease, color 0.15s ease;
}
button[kind="secondary"]:hover {
    border-color: var(--brand-blue);
    color: var(--brand-blue);
}

.stDownloadButton > button {
    width: 100% !important;
}
[data-theme-actual="dark"] .stDownloadButton > button {
    background-color: var(--brand-blue) !important;
    color: #0e1117 !important;
}

button[data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--brand-blue) !important;
    color: var(--brand-blue) !important;
}

/* ══════════════════════════════════════════════════════════════════════
   5. INPUTS / FORMULAIRES
   ══════════════════════════════════════════════════════════════════════ */
.stTextInput > div > div > input,
.stSelectbox > div > div > select,
.stNumberInput > div > div > input {
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-sm);
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--brand-blue);
    box-shadow: 0 0 0 3px var(--brand-blue-soft);
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
    transition: border-color 0.15s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--brand-blue) !important;
}

/* ══════════════════════════════════════════════════════════════════════
   6. CONTENEURS — expanders, métriques, dataframes, tables
   ══════════════════════════════════════════════════════════════════════ */
div[data-testid="stExpander"] {
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    background-color: var(--bg-secondary);
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease;
}
div[data-testid="stExpander"] > div {
    padding: 16px;
}

.streamlit-expanderHeader {
    font-weight: 600;
    color: var(--text-primary);
}
.streamlit-expanderHeader:hover {
    color: var(--brand-blue);
}

div[data-testid="stMetric"] {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-radius: var(--radius-md);
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
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
    border-left: 3px solid var(--brand-blue);
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
    background-color: var(--brand-blue);
    color: #ffffff;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-weight: 600;
}

.stProgress > div > div > div > div {
    background-color: var(--brand-blue);
    border-radius: 4px;
}

/* ══════════════════════════════════════════════════════════════════════
   7. ONGLETS
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
}
.stTabs [data-baseweb="tab"][aria-selected="true"],
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--brand-blue) !important;
    border-bottom: 3px solid var(--brand-blue) !important;
    font-weight: 600;
}
.stTabs [data-baseweb="tab"]:hover,
button[data-baseweb="tab"]:hover {
    color: var(--brand-blue) !important;
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
   Interface publique inchangée — seules les couleurs/ombres/bordures
   sont mises à jour pour suivre la nouvelle palette.
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
    background-color: var(--brand-blue-soft);
    color: var(--brand-blue);
}
.account-badge-plan.plan-cabinet {
    background-color: color-mix(in srgb, #b8860b 20%, transparent);
    color: #b8860b;
}
.account-badge-plan.plan-achat {
    background-color: color-mix(in srgb, #6b46c1 18%, transparent);
    color: #7c5cd4;
}

/* KPIs (extrait de app.py, section KPIs) */
.kpi-card {
    border-radius: var(--radius-md);
    padding: 14px 18px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--kpi-accent, var(--brand-blue));
    box-shadow: var(--shadow-sm);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-hover);
}
.kpi-label {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-bottom: 4px;
}
.kpi-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text-primary);
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
    border-left: 4px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
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
    border-left: 4px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
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
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--brand-blue) 45%, transparent); }
    50%      { box-shadow: 0 0 0 6px color-mix(in srgb, var(--brand-blue) 0%, transparent); }
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
    border-radius: var(--radius-md);
    padding: 16px 18px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-top: 3px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
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

/* Pied de sidebar (support/site web) — carte blanche cohérente avec les
   autres blocs de la sidebar (qui est en fond bleu depuis le 2026-09-18),
   à la place de st.divider()/st.caption() qui restaient nus sur le fond
   bleu (retour Matthieu 2026-09-18). */
.sidebar-support-card {
    margin-top: 16px;
    border-radius: var(--radius-md);
    padding: 14px 16px;
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    box-shadow: var(--shadow-sm);
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
    color: var(--brand-blue);
    text-decoration: none;
}
.sidebar-support-link:hover {
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


