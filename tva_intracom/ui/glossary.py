"""Module de gestion du glossaire TVA intracommunautaire.

Fournit :
- Les définitions des termes techniques (OSS, IOSS, VIES, DDP, etc.)
- Une modale complète avec tout le glossaire
- Des tooltips contextuels pour les termes dans l'interface
- Support multilingue via le système i18n existant
"""

from __future__ import annotations

import streamlit as st
from tva_intracom.i18n import _


# Liste des termes du glossaire (clés i18n)
GLOSSARY_TERM_KEYS = [
    "oss",
    "ioss",
    "reverse_charge",
    "deemed_supplier",
    "ca3",
    "oss_threshold",
    "excluded_territories",
    "amazon_formats",
    "emebi_intrastat",
    "ecb_rates",
    "vies",
    "nif",
    "eori",
    "incoterms",
    "import_150",
    "b2b_b2c",
    "distance_selling",
    "vat_number",
]


def render_glossary_dialog() -> None:
    """Affiche une modale avec le glossaire complet."""
    @st.dialog(title=_("glossary_title"))
    def _dialog() -> None:
        st.markdown(_("glossary_intro"))
        
        for term_key in GLOSSARY_TERM_KEYS:
            title = _(f"glossary_terms_{term_key}_title")
            definition = _(f"glossary_terms_{term_key}_definition")
            
            with st.expander(title, expanded=False):
                st.markdown(definition)
        
        st.divider()
        st.caption(_("glossary_footer"))
    
    _dialog()


def get_tooltip(term_key: str) -> str:
    """Récupère la définition courte pour un tooltip.
    
    Args:
        term_key: Clé du terme (ex: "oss", "ioss")
    
    Returns:
        Définition courte localisée
    """
    return _(f"glossary_terms_{term_key}_tooltip")


def render_tooltip_button(term_key: str, icon: str = "❓") -> None:
    """Affiche un bouton avec tooltip pour un terme.
    
    Args:
        term_key: Clé du terme (ex: "oss", "ioss")
        icon: Icône à afficher (défaut: ❓)
    """
    tooltip_text = get_tooltip(term_key)
    
    # Utiliser st.tooltip pour le comportement au survol
    st.tooltip(icon, help=tooltip_text)


def render_inline_tooltip(term_key: str, label: str | None = None) -> str:
    """Génère le HTML pour un tooltip inline à côté d'un texte.
    
    Args:
        term_key: Clé du terme (ex: "oss", "ioss")
        label: Texte à afficher (si None, utilise le titre du terme)
    
    Returns:
        HTML avec le tooltip intégré
    """
    if label is None:
        label = _(f"glossary_terms_{term_key}_title")
    
    tooltip_text = get_tooltip(term_key)
    
    html = f"""
    <span style="display: inline-flex; align-items: center; gap: 4px;">
        {label}
        <span style="cursor: help; color: var(--brand-primary); font-weight: bold;" 
              title="{tooltip_text}">❓</span>
    </span>
    """
    
    return html


def render_glossary_help_button() -> None:
    """Affiche un bouton d'aide dans la barre de statut."""
    if st.button("❓", key="glossary_help_btn", help=_("glossary_help_tooltip")):
        render_glossary_dialog()
