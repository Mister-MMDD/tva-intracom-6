"""Point de vérité unique pour le mode d'affichage Simplifié / Détaillé.

Avant ce module, `display_mode` (session_state) n'était initialisé qu'à
l'intérieur du bloc `if uploaded_files:` de app.py (donc inexistant tant
qu'aucun fichier n'était chargé) et le toggle lui-même était rendu au milieu
du bloc KPI, après calcul. Ce module sépare les trois responsabilités :

- `ensure_display_mode()` : initialise la valeur par défaut, à appeler tôt
  dans app.py (avant tout composant qui pourrait lire le mode, y compris la
  barre de statut).
- `is_detailed()` : lecture pure, utilisable depuis n'importe quel module
  (sidebar.py, telechargements.py, app.py...) sans dépendance circulaire.
- `render_mode_toggle()` : le widget lui-même (extrait à l'identique de
  l'ancien bloc app.py, même comportement de rerun).

IMPORTANT (invariant conservé) : `display_mode` ne doit JAMAIS entrer dans
`_parse_cache_key` ni `_cache_key`/`calc_key` — basculer de mode ne doit
déclencher aucun recalcul, uniquement un nouveau rendu de la présentation.
"""

from __future__ import annotations

import logging

import streamlit as st

from tva_intracom.i18n import _

logger = logging.getLogger(__name__)

_SS_KEY = "display_mode"
_WIDGET_KEY = "_display_mode_widget"


def ensure_display_mode() -> None:
    """Initialise `display_mode` à "simple" s'il n'existe pas encore.
    Idempotent — à appeler une fois par run, tôt dans app.py."""
    if _SS_KEY not in st.session_state:
        st.session_state[_SS_KEY] = "simple"


def is_detailed() -> bool:
    """Lecture seule du mode courant. Ne suppose PAS que
    ensure_display_mode() a déjà été appelé dans ce run (fallback "simple"
    si absent), pour rester utilisable en toute sécurité depuis n'importe
    quel module importé avant app.py."""
    return st.session_state.get(_SS_KEY) == "detaille"


def render_mode_toggle() -> None:
    """Rendu du sélecteur Simple/Détaillé (st.segmented_control).

    BUGFIX (2026-08-21, 1/2) : la version précédente passait `default=` à
    CHAQUE run alors que la clé du widget (`_WIDGET_KEY`) existait déjà en
    session_state dès le 2e rendu — combinaison que Streamlit désapprouve
    explicitement une fois le widget créé. Pattern corrigé : la clé du
    widget n'est initialisée qu'une seule fois par session (avant sa toute
    première instanciation), jamais réécrite ensuite — le widget devient
    seul maître de sa propre valeur entre deux clics, comme documenté par
    Streamlit pour un widget à clé stable.

    BUGFIX (2026-08-21, 2/2) — cause racine réelle du "bug d'affichage"
    (tableaux/onglets qui disparaissaient à la bascule de mode, écran
    ramené à "aucun fichier importé") : cette fonction appelait
    `st.rerun()` directement lors d'un changement de mode. Or `app.py`
    distingue un rerun interne d'un vrai retrait de fichier par
    l'utilisateur via `preserve_upload_rerun()` (voir rerun_utils.py) —
    sans ce marquage, son filet de sécurité traitait la bascule de mode
    comme "l'utilisateur a retiré son fichier" et vidait tout
    session_state (résultats, période...). Confirmé par logs de
    diagnostic (retirés depuis) : le run consécutif au rerun montrait
    `file_count=0` / `has_results=False` alors que le fichier était
    toujours présent juste avant le clic.
    """
    ensure_display_mode()
    _mode_options = [_("display_mode_simple"), _("display_mode_detailed")]
    # 1. Initialisation ou restauration si l'utilisateur a tenté de désélectionner (valeur None)
    # Ce bloc doit s'exécuter AVANT st.segmented_control pour éviter l'erreur de modification post-instanciation.
    if _WIDGET_KEY not in st.session_state or st.session_state.get(_WIDGET_KEY) is None:
        st.session_state[_WIDGET_KEY] = _mode_options[1] if is_detailed() else _mode_options[0]

    def _handle_change():
        # Signale à app.py de préserver les fichiers (le rerun est automatique en callback)
        st.session_state["_preserve_upload_on_rerun"] = True
        
        val = st.session_state.get(_WIDGET_KEY)
        if val is not None:
            # Changement effectif : on met à jour le point de vérité (_SS_KEY)
            new_mode = "detaille" if val == _mode_options[1] else "simple"
            st.session_state[_SS_KEY] = new_mode
        # Si val est None, on ne fait rien ici : le bloc d'initialisation au début 
        # du prochain run détectera le None et restaurera la valeur précédente 
        # AVANT que st.segmented_control ne soit appelé, évitant l'erreur 
        # "cannot be modified after instantiation".

    st.segmented_control(
        _("display_mode_label"),
        _mode_options,
        key=_WIDGET_KEY,
        label_visibility="collapsed",
        on_change=_handle_change
    )
