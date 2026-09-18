"""Thème visuel de l'application : configuration de page Streamlit et CSS.

Regroupe la config de page et l'injection de style, pour que app.py n'ait
plus qu'à appeler `apply_theme()` en tête de script.

Refonte 2026-09-18 : contraste et hiérarchie visuelle repensés (fond bleu
ciel en mode clair, palette de bordures 3 niveaux, alertes professionnelles
4 catégories, ombres 4 niveaux), mode sombre optimisé en cohérence.

Règle de fond (héritée de l'incident du 2026-09-17, cf. `theme1.py`,
supprimé) : une SEULE source de vérité pour le thème. Le clair est la
valeur par défaut sur `:root`, le sombre surcharge sous `[data-theme="dark"]`
et ses variantes de sélecteur. Ne jamais réintroduire `@media
(prefers-color-scheme: dark)` ni un `:not([data-theme="dark"])` hors
`:root` : les deux mécanismes se contredisent selon l'ordre du CSS généré
et produisent un mode sombre à moitié appliqué. Aucun JavaScript ne doit
forcer ces variables — un inline style JS a une spécificité supérieure au
CSS et fige le thème si la détection se trompe une fois (c'est la cause
confirmée de la casse précédente).
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
   Mode sombre = surcharge unique sous [data-theme="dark"] (+ variantes
   de sélecteur pour couvrir les différents endroits où Streamlit peut
   poser l'attribut selon la version).
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

    --tag-bg: #e8f5e9;
    --tag-text: #2e7d32;
}

[data-theme="dark"],
.stApp[data-theme="dark"],
[data-testid="stApp"][data-theme="dark"],
body[data-theme="dark"],
html[data-theme="dark"] {
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

    --tag-bg: #163a1f;
    --tag-text: #7cd992;
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

/* ══════════════════════════════════════════════════════════════════════
   3. SIDEBAR
   ══════════════════════════════════════════════════════════════════════ */
section[data-testid="stSidebar"] {
    background-color: var(--bg-secondary);
    border-right: 1px solid var(--border-medium);
    min-width: 400px !important;
    max-width: 450px !important;
}

section[data-testid="stSidebar"] * {
    color: var(--text-primary);
}

section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] select,
section[data-testid="stSidebar"] textarea {
    background-color: var(--bg-primary);
    color: var(--text-primary);
    border-color: var(--border-medium) !important;
}

section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: var(--bg-primary);
    border: 1px solid var(--border-medium);
    margin-bottom: 10px;
}

section[data-testid="stSidebar"] [role="switch"] {
    background-color: var(--border-medium);
}
section[data-testid="stSidebar"] [role="switch"][aria-checked="true"] {
    background-color: var(--brand-blue);
}

section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
    gap: 0.5rem;
}

/* Tags multiselect (ex : pays TVA) — vert, comme dans la version d'origine.
   `--tag-*` reste une paire de variables séparées de `--brand-blue` pour
   ne pas dépendre de l'accent de marque. */
div[data-baseweb="multiselect"] span[data-baseweb="tag"] {
    background-color: var(--tag-bg) !important;
    color: var(--tag-text) !important;
    padding: 3px 8px 3px 10px !important;
    gap: 6px;
}
div[data-baseweb="multiselect"] span[data-baseweb="tag"] > span:first-child {
    overflow: visible !important;
    text-overflow: unset !important;
    padding-right: 4px;
    color: var(--tag-text) !important;
}
div[data-baseweb="multiselect"] span[data-baseweb="tag"] svg {
    fill: var(--tag-text) !important;
    flex-shrink: 0;
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
[data-theme="dark"] button[kind="primary"],
.stApp[data-theme="dark"] button[kind="primary"] {
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
[data-theme="dark"] .stDownloadButton > button,
.stApp[data-theme="dark"] .stDownloadButton > button {
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

.stFileUploader {
    border: 1.5px dashed var(--border-medium);
    border-radius: var(--radius-sm);
    background-color: var(--bg-secondary);
    transition: border-color 0.15s ease;
}
.stFileUploader:hover {
    border-color: var(--brand-blue);
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
