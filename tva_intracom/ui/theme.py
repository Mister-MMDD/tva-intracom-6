"""Thème visuel de l'application : configuration de page Streamlit et CSS.

Extrait tel quel de app.py (aucune modification de comportement) — regroupe
la config de page et l'injection de style, pour que app.py n'ait plus qu'à
appeler `apply_theme()` en tête de script.
"""

from __future__ import annotations

import streamlit as st

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

[data-theme="dark"] button[kind="primary"], .stApp[data-theme="dark"] button[kind="primary"] {
    color: #0e1117 !important;
}

/* Forcer le noir sur les boutons de téléchargement Streamlit en mode sombre aussi */
[data-theme="dark"] .stDownloadButton > button, .stApp[data-theme="dark"] .stDownloadButton > button {
    color: #0e1117 !important;
    background-color: var(--brand-blue) !important;
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
    width: 100%;
}

/* Bouton st.download_button (secondary par défaut dans Streamlit) doit aussi être full width s'il est utilisé en mode download principal */
.stDownloadButton > button {
    width: 100% !important;
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

/* ---- KPIs (extrait de app.py, section KPIs — aucune modification) ---- */
.kpi-card {
    border-radius: 10px;
    padding: 14px 18px;
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--primary-color) 15%, transparent);
    border-left: 4px solid var(--kpi-accent, var(--primary-color));
    box-shadow: 0 1px 3px color-mix(in srgb, var(--primary-color) 8%, transparent);
}
.kpi-label {
    font-size: 0.8rem;
    opacity: 0.7;
    margin-bottom: 4px;
}
.kpi-value {
    font-size: 1.6rem;
    font-weight: 700;
}
.badge-alert {
    display: inline-block;
    background-color: color-mix(in srgb, #d62728 15%, transparent);
    color: #d62728;
    border-radius: 999px;
    padding: 3px 12px;
    font-size: 0.78rem;
    font-weight: 600;
    margin-top: 6px;
}

/* ---- Barre de statut persistante (fichier / période / mode) ---- */
.status-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    border-radius: 10px;
    padding: 10px 16px;
    margin-bottom: 14px;
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--brand-blue) 18%, transparent);
    border-left: 4px solid var(--brand-blue);
    box-shadow: 0 1px 3px color-mix(in srgb, var(--brand-blue) 8%, transparent);
}
.status-bar-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.85rem;
}
.status-bar-item .status-bar-label {
    opacity: 0.65;
}
.status-bar-item .status-bar-value {
    font-weight: 600;
}
.status-bar-sep {
    opacity: 0.25;
}
.status-bar-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
}
.status-bar-dot.ok { background-color: #2ca02c; }
.status-bar-dot.pending { background-color: #d97706; }
.status-bar-dot.off { background-color: color-mix(in srgb, currentColor 35%, transparent); }

/* ---- Bandeau onboarding (checklist démarrage) ----
   Mêmes variables de thème que .status-bar (pas de couleur codée en dur) :
   BUGFIX 2026-08-22 — un fond/texte en dur (#F7F6FF / #26215C) restait
   clair en mode sombre alors que le texte, lui, devenait blanc via le
   thème global -> texte invisible (blanc sur blanc). */
.onboarding-banner {
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 14px;
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--brand-blue) 25%, transparent);
    border-left: 4px solid var(--brand-blue);
    box-shadow: 0 1px 3px color-mix(in srgb, var(--brand-blue) 8%, transparent);
}
.onboarding-banner-title {
    margin: 0 0 10px;
    font-weight: 700;
    font-size: 1rem;
}
.onboarding-banner-intro {
    margin: 0 0 10px;
    font-size: 0.85rem;
    opacity: 0.8;
}
.onboarding-banner-step {
    margin: 0 0 6px;
    font-size: 0.9rem;
}
.onboarding-banner-substep {
    margin: 2px 0 6px 26px;
    font-size: 0.8rem;
    opacity: 0.75;
}

/* ---- Guidage visuel "Lighthouse" (onboarding) ----
   Un marqueur invisible (st.container(key=...)) est placé juste avant
   l'élément à mettre en avant (expander sidebar ou uploader) ; sa classe
   générée par Streamlit (.st-key-<key>) sert de point d'ancrage au
   sélecteur "+ " ci-dessous, qui cible le frère direct suivant dans le
   DOM. Pur CSS, aucun JS, aucune requête serveur — n'a donc aucun impact
   sur le mécanisme de scale-to-zero de Railway. */
@keyframes onboarding-pulse {
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--brand-blue) 45%, transparent); }
    50%      { box-shadow: 0 0 0 6px color-mix(in srgb, var(--brand-blue) 0%, transparent); }
}
.st-key-onb_pulse_entreprise + div[data-testid="stExpander"],
.st-key-onb_pulse_vies + div[data-testid="stExpander"],
.st-key-onb_pulse_upload + div[data-testid="stFileUploaderDropzone"],
.st-key-onb_pulse_upload + div[data-testid="stFileUploader"] {
    border-radius: 10px;
    animation: onboarding-pulse 2.2s ease-in-out infinite;
}

/* ---- Dashboard "premier lancement" (zéro state, aucun fichier importé) ---- */
.zero-state-grid {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin: 6px 0 18px;
}
.zero-state-card {
    flex: 1 1 220px;
    border-radius: 12px;
    padding: 16px 18px;
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--brand-blue) 20%, transparent);
    border-top: 3px solid var(--brand-blue);
}
.zero-state-card.done {
    border-top-color: #2ca02c;
    opacity: 0.75;
}
.zero-state-card-title {
    font-weight: 700;
    font-size: 0.95rem;
    margin: 0 0 6px;
}
.zero-state-card-body {
    font-size: 0.82rem;
    opacity: 0.8;
    margin: 0;
}

/* ---- Badge "compte connecté" (email + forfait) ----
   Remplace le texte brut "✅ email — Forfait X" par une pilule compacte,
   dans le même langage visuel que .status-bar ci-dessus (mêmes variables
   de thème, aucune couleur codée en dur). */
.account-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    border-radius: 999px;
    padding: 5px 12px 5px 10px;
    background-color: var(--secondary-background-color);
    border: 1px solid color-mix(in srgb, var(--brand-blue) 18%, transparent);
    font-size: 0.82rem;
    line-height: 1.4;
}
.account-badge-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: #2ca02c;
    flex-shrink: 0;
}
.account-badge-email {
    opacity: 0.85;
}
.account-badge-plan {
    font-weight: 700;
    padding: 1px 9px;
    border-radius: 999px;
    font-size: 0.75rem;
}
.account-badge-plan.plan-free {
    background-color: color-mix(in srgb, currentColor 12%, transparent);
    opacity: 0.75;
}
.account-badge-plan.plan-business {
    background-color: color-mix(in srgb, var(--brand-blue) 22%, transparent);
    color: var(--brand-blue);
}
.account-badge-plan.plan-cabinet {
    background-color: color-mix(in srgb, #b8860b 25%, transparent);
    color: #d4a017;
}
.account-badge-plan.plan-achat {
    background-color: color-mix(in srgb, #6b46c1 22%, transparent);
    color: #8b5cf6;
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
