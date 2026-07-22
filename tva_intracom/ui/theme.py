"""Thème visuel de l'application : configuration de page Streamlit et CSS.

Extrait tel quel de app.py (aucune modification de comportement) — regroupe
la config de page et l'injection de style, pour que app.py n'ait plus qu'à
appeler `apply_theme()` en tête de script.
"""

from __future__ import annotations

import streamlit as st
from tva_intracom.i18n import _

_PLATFORM_OPTIONS = [
    "Amazon VAT Transactions Report (TSV), txt, CSV",
]

_CSS = """
<style>
/* ---- Définition de la couleur de marque (adaptative) ---- */
:root {
    --brand-blue: #1f4e79;
}

@media (prefers-color-scheme: dark) {
    :root {
        --brand-blue: #38bdf8;
    }
}

/* On surcharge si Streamlit est en mode sombre (basé sur la couleur de fond) */
[data-theme="dark"], .stApp[data-theme="dark"] {
    --brand-blue: #38bdf8;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

/* ---- Boutons primaires aux couleurs de la marque ---- */
button[kind="primary"] {
    background-color: var(--brand-blue) !important;
    border-color: var(--brand-blue) !important;
    color: white !important;
}

/* ---- Titres avec accent de marque ---- */
h1 {
    color: var(--brand-blue);
    border-bottom: 3px solid var(--brand-blue);
    padding-bottom: 8px;
}
h2, h3 {
    color: var(--brand-blue);
}

/* ---- Onglets : accent net sur l'onglet actif ---- */
button[data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 3px solid var(--brand-blue) !important;
    color: var(--brand-blue) !important;
    font-weight: 600;
}
button[data-baseweb="tab"]:hover {
    color: var(--brand-blue) !important;
}

/* ---- Sidebar width (élargie pour éviter les coupures) ---- */
[data-testid="stSidebar"], section[data-testid="stSidebar"] {
    min-width: 400px !important;
    max-width: 450px !important;
}

div[data-testid="stExpander"] {
    border: 1px solid color-mix(in srgb, var(--brand-blue) 20%, transparent);
    border-radius: 10px;
    box-shadow: 0 1px 3px color-mix(in srgb, var(--brand-blue) 8%, transparent);
    background-color: var(--secondary-background-color);
}

div[data-testid="stMetric"] {
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--brand-blue) 18%, transparent);
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 1px 3px color-mix(in srgb, var(--brand-blue) 8%, transparent);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px color-mix(in srgb, var(--brand-blue) 15%, transparent);
}

/* ── Sidebar : séparation nette ────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    border-right: 1px solid color-mix(in srgb, var(--primary-color) 15%, transparent);
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    margin-bottom: 10px;
}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
    gap: 0.5rem;
}

/* On s'assure que le menu Streamlit reste bien visible */
#MainMenu { visibility: visible !important; }
header { visibility: visible !important; }

/* ---- Boutons primaires ---- */
button[kind="primary"] {
    transition: opacity 0.15s ease;
}
button[kind="primary"]:hover {
    opacity: 0.85;
}

/* ---- Dataframes : coins arrondis + bordure discrète ---- */
div[data-testid="stDataFrame"] {
    border-radius: 8px;
    border: 1px solid color-mix(in srgb, var(--primary-color) 12%, transparent);
    overflow-x: auto !important;
}

/* ---- Alertes (st.error / st.warning / st.success / st.info) : coins arrondis ---- */
div[data-testid="stAlert"] {
    border-radius: 8px;
}

/* ---- Séparateurs plus discrets que le défaut ---- */
hr {
    margin: 1.5rem 0;
    opacity: 0.3;
}

/* ---- Boutons de téléchargement : petit accent visuel ---- */
button[data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--primary-color) !important;
    color: var(--primary-color) !important;
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
