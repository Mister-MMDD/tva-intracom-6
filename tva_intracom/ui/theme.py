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
    
    /* Palette de couleurs professionnelles - MODE CLAIR PAR DÉFAUT */
    --bg-primary: #f1f5f9;
    --bg-secondary: #e2e8f0;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #64748b;
    
    /* Palette de bordures améliorée */
    --border-light: color-mix(in srgb, var(--brand-blue) 18%, transparent);
    --border-medium: color-mix(in srgb, var(--brand-blue) 28%, transparent);
    --border-strong: color-mix(in srgb, var(--brand-blue) 38%, transparent);
    
    /* Palette d'alertes professionnelles */
    --alert-error-bg: #fef2f2;
    --alert-error-border: #fecaca;
    --alert-error-text: #991b1b;
    
    --alert-warning-bg: #fffbeb;
    --alert-warning-border: #fde68a;
    --alert-warning-text: #92400e;
    
    --alert-success-bg: #f0fdf4;
    --alert-success-border: #bbf7d0;
    --alert-success-text: #166534;
    
    --alert-info-bg: #eff6ff;
    --alert-info-border: #bfdbfe;
    --alert-info-text: #1e40af;
    
    /* Ombres améliorées */
    --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
    --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    --shadow-hover: 0 8px 16px -4px rgba(0, 0, 0, 0.15), 0 4px 8px -2px rgba(0, 0, 0, 0.1);
}

/* Force mode clair par défaut */
[data-theme="light"], .stApp[data-theme="light"], :not([data-theme="dark"]) {
    --brand-blue: #1f4e79;
    --bg-primary: #f1f5f9;
    --bg-secondary: #e2e8f0;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #64748b;
    
    --border-light: color-mix(in srgb, var(--brand-blue) 18%, transparent);
    --border-medium: color-mix(in srgb, var(--brand-blue) 28%, transparent);
    --border-strong: color-mix(in srgb, var(--brand-blue) 38%, transparent);
    
    --alert-error-bg: #fef2f2;
    --alert-error-border: #fecaca;
    --alert-error-text: #991b1b;
    
    --alert-warning-bg: #fffbeb;
    --alert-warning-border: #fde68a;
    --alert-warning-text: #92400e;
    
    --alert-success-bg: #f0fdf4;
    --alert-success-border: #bbf7d0;
    --alert-success-text: #166534;
    
    --alert-info-bg: #eff6ff;
    --alert-info-border: #bfdbfe;
    --alert-info-text: #1e40af;
    
    --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
    --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    --shadow-hover: 0 8px 16px -4px rgba(0, 0, 0, 0.15), 0 4px 8px -2px rgba(0, 0, 0, 0.1);
}

/* ── Sidebar : fond clair forcé en mode clair ────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background-color: #f1f5f9 !important;
    border-right: 1px solid var(--border-medium);
}

[data-theme="light"] section[data-testid="stSidebar"],
.stApp[data-theme="light"] section[data-testid="stSidebar"],
:not([data-theme="dark"]) section[data-testid="stSidebar"] {
    background-color: #f1f5f9 !important;
}

/* Texte dans la sidebar - mode clair */
section[data-testid="stSidebar"] * {
    color: var(--text-primary) !important;
}

[data-theme="light"] section[data-testid="stSidebar"] *,
.stApp[data-theme="light"] section[data-testid="stSidebar"] *,
:not([data-theme="dark"]) section[data-testid="stSidebar"] * {
    color: var(--text-primary) !important;
}

/* Inputs dans la sidebar - fond blanc */
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] select,
section[data-testid="stSidebar"] textarea {
    background-color: white !important;
    color: var(--text-primary) !important;
    border-color: var(--border-medium) !important;
}

/* Expanders dans la sidebar - fond blanc */
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: white !important;
    border: 1px solid var(--border-medium) !important;
}

/* Toggle switches dans la sidebar */
section[data-testid="stSidebar"] [role="switch"] {
    background-color: var(--border-medium) !important;
}

section[data-testid="stSidebar"] [role="switch"][aria-checked="true"] {
    background-color: var(--brand-blue) !important;
}

/* ── Multiselect Tags (Pays TVA) : Vert foncé au lieu de rouge ───────────────── */
span[data-baseweb="tag"] {
    background-color: #e8f5e9 !important; /* Vert très clair */
    color: #2e7d32 !important;            /* Vert foncé */
}

span[data-baseweb="tag"] svg {
    fill: #2e7d32 !important;             /* Icône de suppression en vert foncé */
}

@media (prefers-color-scheme: dark) {
    :root {
        --brand-blue: #38bdf8;
        --bg-primary: #0f172a;
        --bg-secondary: #1e293b;
        --text-primary: #f8fafc;
        --text-secondary: #cbd5e1;
        --text-muted: #94a3b8;
        
        --border-light: color-mix(in srgb, var(--brand-blue) 25%, transparent);
        --border-medium: color-mix(in srgb, var(--brand-blue) 35%, transparent);
        --border-strong: color-mix(in srgb, var(--brand-blue) 45%, transparent);
        
        --alert-error-bg: #1a1010;
        --alert-error-border: #452323;
        --alert-error-text: #fca5a5;
        
        --alert-warning-bg: #1c1917;
        --alert-warning-border: #451a03;
        --alert-warning-text: #fcd34d;
        
        --alert-success-bg: #052e16;
        --alert-success-border: #166534;
        --alert-success-text: #86efac;
        
        --alert-info-bg: #1e3a8a;
        --alert-info-border: #1e40af;
        --alert-info-text: #93c5fd;
        
        --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.3);
        --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -1px rgba(0, 0, 0, 0.3);
        --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -2px rgba(0, 0, 0, 0.4);
        --shadow-hover: 0 8px 16px -4px rgba(0, 0, 0, 0.6), 0 4px 8px -2px rgba(0, 0, 0, 0.5);
    }
}

/* On surcharge si Streamlit est en mode sombre (basé sur la couleur de fond) */
[data-theme="dark"], .stApp[data-theme="dark"] {
    --brand-blue: #38bdf8;
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --text-primary: #f8fafc;
    --text-secondary: #cbd5e1;
    --text-muted: #94a3b8;
    
    --border-light: color-mix(in srgb, var(--brand-blue) 25%, transparent);
    --border-medium: color-mix(in srgb, var(--brand-blue) 35%, transparent);
    --border-strong: color-mix(in srgb, var(--brand-blue) 45%, transparent);
    
    --alert-error-bg: #1a1010;
    --alert-error-border: #452323;
    --alert-error-text: #fca5a5;
    
    --alert-warning-bg: #1c1917;
    --alert-warning-border: #451a03;
    --alert-warning-text: #fcd34d;
    
    --alert-success-bg: #052e16;
    --alert-success-border: #166534;
    --alert-success-text: #86efac;
    
    --alert-info-bg: #1e3a8a;
    --alert-info-border: #1e40af;
    --alert-info-text: #93c5fd;
    
    --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.3);
    --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -1px rgba(0, 0, 0, 0.3);
    --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -2px rgba(0, 0, 0, 0.4);
    --shadow-hover: 0 8px 16px -4px rgba(0, 0, 0, 0.6), 0 4px 8px -2px rgba(0, 0, 0, 0.5);
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
    box-shadow: var(--shadow-md);
    transition: all 0.2s ease;
}

button[kind="primary"]:hover {
    box-shadow: var(--shadow-hover);
    transform: translateY(-1px);
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
    font-weight: 700;
}
h2, h3 {
    color: var(--brand-blue);
    font-weight: 600;
}

/* ---- Amélioration du corps du texte ---- */
.stMarkdown {
    color: var(--text-primary);
    line-height: 1.6;
}

.stMarkdown p {
    color: var(--text-primary);
}

.stMarkdown li {
    color: var(--text-primary);
    margin-bottom: 8px;
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

/* ── Sidebar : mode clair SEULEMENT ────────────────────────────────────────── */
[data-theme="light"] section[data-testid="stSidebar"],
.stApp[data-theme="light"] section[data-testid="stSidebar"],
:not([data-theme="dark"]) section[data-testid="stSidebar"] {
    background-color: #f1f5f9 !important;
    border-right: 1px solid var(--border-medium);
}

/* Texte dans la sidebar - mode clair SEULEMENT */
[data-theme="light"] section[data-testid="stSidebar"] *,
.stApp[data-theme="light"] section[data-testid="stSidebar"] *,
:not([data-theme="dark"]) section[data-testid="stSidebar"] * {
    color: var(--text-primary) !important;
}

/* Inputs dans la sidebar - fond blanc - mode clair SEULEMENT */
[data-theme="light"] section[data-testid="stSidebar"] input,
.stApp[data-theme="light"] section[data-testid="stSidebar"] input,
:not([data-theme="dark"]) section[data-testid="stSidebar"] input,
[data-theme="light"] section[data-testid="stSidebar"] select,
.stApp[data-theme="light"] section[data-testid="stSidebar"] select,
:not([data-theme="dark"]) section[data-testid="stSidebar"] select,
[data-theme="light"] section[data-testid="stSidebar"] textarea,
.stApp[data-theme="light"] section[data-testid="stSidebar"] textarea,
:not([data-theme="dark"]) section[data-testid="stSidebar"] textarea {
    background-color: white !important;
    color: var(--text-primary) !important;
    border-color: var(--border-medium) !important;
}

/* Expanders dans la sidebar - fond blanc - mode clair SEULEMENT */
[data-theme="light"] section[data-testid="stSidebar"] div[data-testid="stExpander"],
.stApp[data-theme="light"] section[data-testid="stSidebar"] div[data-testid="stExpander"],
:not([data-theme="dark"]) section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: white !important;
    border: 1px solid var(--border-medium) !important;
}

/* Toggle switches dans la sidebar - mode clair SEULEMENT */
[data-theme="light"] section[data-testid="stSidebar"] [role="switch"],
.stApp[data-theme="light"] section[data-testid="stSidebar"] [role="switch"],
:not([data-theme="dark"]) section[data-testid="stSidebar"] [role="switch"] {
    background-color: var(--border-medium) !important;
}

[data-theme="light"] section[data-testid="stSidebar"] [role="switch"][aria-checked="true"],
.stApp[data-theme="light"] section[data-testid="stSidebar"] [role="switch"][aria-checked="true"],
:not([data-theme="dark"]) section[data-testid="stSidebar"] [role="switch"][aria-checked="true"] {
    background-color: var(--brand-blue) !important;
}

/* ── Sidebar : mode sombre ────────────────────────────────────────── */
[data-theme="dark"] section[data-testid="stSidebar"],
.stApp[data-theme="dark"] section[data-testid="stSidebar"] {
    background-color: var(--bg-primary) !important;
    border-right: 1px solid var(--border-medium);
}

[data-theme="dark"] section[data-testid="stSidebar"] *,
.stApp[data-theme="dark"] section[data-testid="stSidebar"] * {
    color: var(--text-primary) !important;
}

[data-theme="dark"] section[data-testid="stSidebar"] input,
.stApp[data-theme="dark"] section[data-testid="stSidebar"] input,
[data-theme="dark"] section[data-testid="stSidebar"] select,
.stApp[data-theme="dark"] section[data-testid="stSidebar"] select,
[data-theme="dark"] section[data-testid="stSidebar"] textarea,
.stApp[data-theme="dark"] section[data-testid="stSidebar"] textarea {
    background-color: var(--bg-secondary) !important;
    color: var(--text-primary) !important;
    border-color: var(--border-medium) !important;
}

[data-theme="dark"] section[data-testid="stSidebar"] div[data-testid="stExpander"],
.stApp[data-theme="dark"] section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: var(--bg-secondary) !important;
    border: 1px solid var(--border-medium) !important;
}

div[data-testid="stExpander"] {
    border: 1px solid var(--border-medium);
    border-radius: 10px;
    box-shadow: var(--shadow-sm);
    background-color: var(--secondary-background-color);
    transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

div[data-testid="stExpander"]:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-md);
}

div[data-testid="stMetric"] {
    background-color: var(--secondary-background-color);
    border: 1px solid var(--border-medium);
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
    transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-hover);
    border-color: var(--border-strong);
}

/* ── Main content area : correction mode clair ────────────────────────────────────────── */
[data-theme="light"] .main,
.stApp[data-theme="light"] .main,
:not([data-theme="dark"]) .main {
    background-color: #f1f5f9 !important;
}

[data-theme="light"] .block-container,
.stApp[data-theme="light"] .block-container,
:not([data-theme="dark"]) .block-container {
    background-color: #f1f5f9 !important;
}

/* Correction du texte principal en mode clair */
[data-theme="light"] .stMarkdown,
.stApp[data-theme="light"] .stMarkdown,
:not([data-theme="dark"]) .stMarkdown {
    color: var(--text-primary) !important;
}

[data-theme="light"] h1, [data-theme="light"] h2, [data-theme="light"] h3,
.stApp[data-theme="light"] h1, .stApp[data-theme="light"] h2, .stApp[data-theme="light"] h3,
:not([data-theme="dark"]) h1, :not([data-theme="dark"]) h2, :not([data-theme="dark"]) h3 {
    color: var(--brand-blue) !important;
}

/* Correction des données et métriques en mode clair */
[data-theme="light"] [data-testid="stMetricValue"],
.stApp[data-theme="light"] [data-testid="stMetricValue"],
:not([data-theme="dark"]) [data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
}

[data-theme="light"] [data-testid="stMetricLabel"],
.stApp[data-theme="light"] [data-testid="stMetricLabel"],
:not([data-theme="dark"]) [data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
}

/* ── Espacement dans le récapitulatif ────────────────────────────────────────── */
div[data-testid="stVerticalBlock"] > div {
    gap: 16px !important;
}

/* Espacement spécifique pour les métriques et les blocs de contenu */
div[data-testid="stVerticalBlock"] > div > div {
    margin-bottom: 12px;
}

/* Espacement entre les sections principales */
.stVerticalBlock {
    gap: 20px !important;
}

/* Espacement entre les éléments dans les expanders */
div[data-testid="stExpander"] > div {
    padding: 16px;
}

/* Espacement entre les lignes dans les tableaux */
.stTable tbody tr {
    margin-bottom: 8px;
}

/* Espacement entre les cartes et conteneurs */
.stVerticalBlock > div[data-testid="stVerticalBlock"] {
    margin-top: 16px;
    margin-bottom: 16px;
}

/* Espacement dans la sidebar */
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
    transition: all 0.2s ease;
}
button[kind="primary"]:hover {
    opacity: 0.9;
    transform: translateY(-1px);
}

/* ---- Dataframes : coins arrondis + bordure discrète ---- */
div[data-testid="stDataFrame"] {
    border-radius: 8px;
    border: 1px solid var(--border-light);
    overflow-x: auto !important;
    box-shadow: var(--shadow-sm);
    transition: border-color 0.2s ease;
}

div[data-testid="stDataFrame"]:hover {
    border-color: var(--border-medium);
}

/* ---- Alertes (st.error / st.warning / st.success / st.info) : coins arrondis ---- */
div[data-testid="stAlert"] {
    border-radius: 8px;
    border: 1px solid;
    padding: 12px 16px;
    box-shadow: var(--shadow-sm);
}

/* Alertes d'erreur */
div[data-testid="stAlert"][data-status="error"] {
    background-color: var(--alert-error-bg);
    border-color: var(--alert-error-border);
    color: var(--alert-error-text);
}

/* Alertes de warning */
div[data-testid="stAlert"][data-status="warning"] {
    background-color: var(--alert-warning-bg);
    border-color: var(--alert-warning-border);
    color: var(--alert-warning-text);
}

/* Alertes de succès */
div[data-testid="stAlert"][data-status="success"] {
    background-color: var(--alert-success-bg);
    border-color: var(--alert-success-border);
    color: var(--alert-success-text);
}

/* Alertes d'info */
div[data-testid="stAlert"][data-status="info"] {
    background-color: var(--alert-info-bg);
    border-color: var(--alert-info-border);
    color: var(--alert-info-text);
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
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--kpi-accent, var(--brand-blue));
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}

.kpi-card:hover {
    box-shadow: var(--shadow-hover);
    border-color: var(--border-strong);
    transform: translateY(-2px);
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
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease;
}

.status-bar:hover {
    box-shadow: var(--shadow-md);
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
    border: 1px solid var(--border-medium);
    border-left: 4px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease;
}

.onboarding-banner:hover {
    box-shadow: var(--shadow-md);
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
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--brand-blue) 50%, transparent); }
    50%      { box-shadow: 0 0 0 8px color-mix(in srgb, var(--brand-blue) 0%, transparent); }
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
    border: 1px solid var(--border-medium);
    border-top: 3px solid var(--brand-blue);
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}

.zero-state-card:hover {
    box-shadow: var(--shadow-hover);
    border-color: var(--border-strong);
    transform: translateY(-2px);
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
    border: 1px solid var(--border-medium);
    font-size: 0.82rem;
    line-height: 1.4;
    box-shadow: var(--shadow-sm);
    transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.account-badge:hover {
    box-shadow: var(--shadow-md);
    border-color: var(--border-strong);
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

/* ---- Amélioration des inputs et formulaires ---- */
.stTextInput > div > div > input,
.stSelectbox > div > div > select,
.stNumberInput > div > div > input {
    border: 1px solid var(--border-medium);
    border-radius: 8px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--brand-blue);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--brand-blue) 20%, transparent);
    outline: none;
}

/* ---- Amélioration des scrollbars ---- */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--bg-secondary);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb {
    background: var(--border-medium);
    border-radius: 4px;
    transition: background 0.2s ease;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--border-strong);
}

/* ---- Amélioration des sliders ---- */
.stSlider > div > div > div {
    background-color: var(--border-light);
}

/* ---- Amélioration des checkboxes ---- */
.stCheckbox > label {
    color: var(--text-primary);
    font-weight: 500;
}

/* ---- Amélioration des radios ---- */
.stRadio > div {
    color: var(--text-primary);
}

/* ---- Amélioration des tooltips ---- */
[data-testid="stTooltip"] {
    background-color: var(--text-primary);
    color: var(--bg-primary);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 0.85rem;
    box-shadow: var(--shadow-lg);
}

/* ---- Amélioration des badges ---- */
.stBadge {
    background-color: var(--brand-blue);
    color: white;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-weight: 600;
}

/* ---- Amélioration des sélecteurs de fichiers ---- */
.stFileUploader {
    border: 2px dashed var(--border-medium);
    border-radius: 8px;
    background-color: var(--secondary-background-color);
    transition: border-color 0.2s ease, background-color 0.2s ease;
}

.stFileUploader:hover {
    border-color: var(--brand-blue);
    background-color: var(--bg-primary);
}

/* ---- Amélioration des progress bars ---- */
.stProgress > div > div > div > div {
    background-color: var(--brand-blue);
    border-radius: 4px;
}

/* ---- Amélioration des tables de données ---- */
.stTable {
    border: 1px solid var(--border-light);
    border-radius: 8px;
    overflow: hidden;
}

.stTable thead th {
    background-color: var(--bg-secondary);
    color: var(--text-primary);
    font-weight: 600;
    border-bottom: 2px solid var(--border-medium);
}

.stTable tbody tr {
    border-bottom: 1px solid var(--border-light);
    transition: background-color 0.2s ease;
}

.stTable tbody tr:hover {
    background-color: var(--bg-secondary);
}

/* ---- Amélioration des expander headers ---- */
.streamlit-expanderHeader {
    font-weight: 600;
    color: var(--text-primary);
    transition: color 0.2s ease;
}

.streamlit-expanderHeader:hover {
    color: var(--brand-blue);
}

/* ---- Amélioration des tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
}

.stTabs [data-baseweb="tab"] {
    background-color: var(--secondary-background-color);
    border: 1px solid var(--border-light);
    border-radius: 8px 8px 0 0;
    padding: 10px 20px;
    color: var(--text-secondary);
    font-weight: 500;
    transition: all 0.2s ease;
}

.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background-color: var(--bg-primary);
    border-color: var(--border-medium);
    color: var(--brand-blue);
    font-weight: 600;
    border-bottom: 2px solid var(--brand-blue);
}

.stTabs [data-baseweb="tab"]:hover {
    border-color: var(--border-medium);
    color: var(--text-primary);
}

/* ---- Amélioration des boutons secondaires ---- */
button[kind="secondary"] {
    border: 1px solid var(--border-medium);
    color: var(--text-primary);
    background-color: var(--secondary-background-color);
    transition: all 0.2s ease;
}

button[kind="secondary"]:hover {
    border-color: var(--brand-blue);
    color: var(--brand-blue);
    background-color: var(--bg-primary);
}

/* ---- Amélioration globale du conteneur principal ---- */
.main {
    background-color: var(--bg-primary);
}

/* ---- Amélioration des blocs de code ---- */
.stCode {
    background-color: var(--bg-secondary);
    border: 1px solid var(--border-medium);
    border-radius: 8px;
    padding: 12px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.9rem;
    color: var(--text-primary);
}

/* ---- Amélioration des citations/blockquotes ---- */
.stBlockquote {
    border-left: 4px solid var(--brand-blue);
    background-color: var(--secondary-background-color);
    padding: 12px 16px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    color: var(--text-primary);
    font-style: italic;
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
