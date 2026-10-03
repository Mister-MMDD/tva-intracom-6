"""Onboarding wizard interactif avec données de démo."""

from __future__ import annotations

import streamlit as st
from tva_intracom.i18n import _

ONBOARDING_STEPS = [
    "configure_company",
    "import_first_file",
    "interpret_results"
]


def render_onboarding_wizard(current_user) -> None:
    """Rendu complet du wizard d'onboarding interactif."""
    # État du wizard
    if "onboarding_wizard_step" not in st.session_state:
        st.session_state["onboarding_wizard_step"] = 0
    if "onboarding_demo_mode" not in st.session_state:
        st.session_state["onboarding_demo_mode"] = False
    
    _step = st.session_state["onboarding_wizard_step"]
    
    # Barre de progression
    _render_progress_bar(_step, len(ONBOARDING_STEPS))
    
    # Contenu de l'étape courante
    if _step == 0:
        _render_step_configure_company()
    elif _step == 1:
        _render_step_import_first_file()
    elif _step == 2:
        _render_step_interpret_results()
    
    # Boutons de navigation
    _render_navigation_buttons(_step, len(ONBOARDING_STEPS), current_user)


def _render_progress_bar(current_step: int, total_steps: int) -> None:
    """Affiche la barre de progression du wizard."""
    progress = (current_step + 1) / total_steps
    st.progress(progress)
    st.caption(_("onboarding_wizard_progress", step=current_step + 1, total=total_steps))


def _render_step_configure_company() -> None:
    """Étape 2 : Configurer son entreprise."""
    st.subheader(_("onboarding_step2_title"))
    st.markdown(_("onboarding_step2_intro"))
    
    st.info(_("onboarding_step2_skip_info"))
    st.caption(_("onboarding_step2_skip_hint"))


def _render_step_import_first_file() -> None:
    """Étape 3 : Importer son premier fichier."""
    st.subheader(_("onboarding_step3_title"))
    st.markdown(_("onboarding_step3_intro"))
    
    # Toggle mode démo
    _demo_mode = st.toggle(
        _("onboarding_demo_mode_toggle"),
        value=st.session_state.get("onboarding_demo_mode", False),
        key="onboarding_demo_toggle"
    )
    st.session_state["onboarding_demo_mode"] = _demo_mode
    
    if _demo_mode:
        st.info(_("onboarding_demo_mode_info"))
        _load_demo_data()
    else:
        st.info(_("onboarding_step3_upload_info"))


def _render_step_interpret_results() -> None:
    """Étape 4 : Interpréter les résultats."""
    st.subheader(_("onboarding_step4_title"))
    st.markdown(_("onboarding_step4_intro"))
    
    if not st.session_state.get("onboarding_demo_mode", False):
        st.warning(_("onboarding_step4_no_data"))
        return
    
    # Tour guidé des onglets
    _tab_tour = st.selectbox(
        _("onboarding_tab_tour_label"),
        options=[
            _("onboarding_tour_declarations"),
            _("onboarding_tour_detail"),
            _("onboarding_tour_viz"),
            _("onboarding_tour_vies"),
            _("onboarding_tour_audit"),
            _("onboarding_tour_downloads")
        ],
        key="onboarding_tab_tour"
    )
    
    _render_tab_guide(_tab_tour)


def _render_navigation_buttons(current_step: int, total_steps: int, current_user) -> None:
    """Affiche les boutons de navigation du wizard."""
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        if current_step > 0:
            if st.button(_("onboarding_prev_btn"), key="wizard_prev"):
                st.session_state["onboarding_wizard_step"] = current_step - 1
                st.rerun()
    
    with col2:
        if st.button(_("onboarding_skip_btn"), key="wizard_skip"):
            from tva_intracom.ui.onboarding import dismiss_onboarding
            dismiss_onboarding(current_user)
            st.rerun()
    
    with col3:
        if current_step < total_steps - 1:
            if st.button(_("onboarding_next_btn"), key="wizard_next", type="primary"):
                st.session_state["onboarding_wizard_step"] = current_step + 1
                st.rerun()
        else:
            if st.button(_("onboarding_finish_btn"), key="wizard_finish", type="primary"):
                from tva_intracom.ui.onboarding import dismiss_onboarding
                dismiss_onboarding(current_user)
                st.rerun()


# Fonctions utilitaires
def _load_demo_data() -> None:
    """Charge les données de démo dans le contexte."""
    st.info(_("onboarding_demo_coming_soon"))


def _render_tab_guide(tab_name: str) -> None:
    """Affiche le guide pour un onglet spécifique."""
    guides = {
        _("onboarding_tour_declarations"): _("onboarding_guide_declarations"),
        _("onboarding_tour_detail"): _("onboarding_guide_detail"),
        _("onboarding_tour_viz"): _("onboarding_guide_viz"),
        _("onboarding_tour_vies"): _("onboarding_guide_vies"),
        _("onboarding_tour_audit"): _("onboarding_guide_audit"),
        _("onboarding_tour_downloads"): _("onboarding_guide_downloads"),
    }
    st.markdown(guides.get(tab_name, ""))
