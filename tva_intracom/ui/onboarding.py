"""Visite guidée de première connexion.

Deux étapes indépendantes, chacune affichée UNE SEULE FOIS par compte
(persisté en base via tva_auth.set_onboarding_seen — pas juste en
session_state, sinon la visite réapparaîtrait à chaque nouvelle session
navigateur) :

  1. `maybe_show_sidebar_tour(user)` — actions à mener dans la barre
     latérale (pays d'origine, entreprise/SIREN, abonnement). Affichée juste
     après render_sidebar(), donc AVANT l'upload de fichier.
  2. `maybe_show_tabs_tour(user)` — explication des 6 onglets. Affichée une
     fois que le premier calcul a réussi (résultats en session_state),
     donc après le tout premier upload traité avec succès.

Utilise st.dialog (natif Streamlit ≥ 1.37, modal réel) plutôt qu'un overlay
HTML/JS custom : plus robuste (pas d'injection dans l'iframe des composants
existants, pas de risque de conflit avec extra_streamlit_components déjà
utilisé pour les cookies — voir sidebar.py).

BUGFIX (rerun complet à chaque "Suivant") : dans la première version, chaque
clic sur "Suivant" appelait st.rerun() sans préciser de scope, ce qui
réexécute TOUT le script — y compris le redessin complet des 6 onglets
(tableaux, graphiques) déjà affichés derrière le dialog. Le calcul TVA/VIES
lui-même n'était jamais relancé (le cache `_calc_key` tenait bon), mais le
temps de redessin de l'affichage donnait l'illusion d'un recalcul. Le
contenu de chaque étape est désormais isolé dans un `st.fragment` : les clics
"Suivant" intermédiaires ne font qu'un rerun scope="fragment" (on ne redessine
que le dialog). Seuls "Passer" et le "Terminer" de la dernière étape
déclenchent un rerun complet — nécessaire pour fermer réellement le dialog et
faire relire à app.py le nouveau statut onboarding_*_seen — mais cela n'arrive
qu'une seule fois par visite, pas à chaque étape.
"""

from __future__ import annotations

import streamlit as st
from tva_intracom.i18n import _
from tva_intracom import auth as tva_auth


def _dialog_progress(step: int, total: int) -> None:
    st.caption(_("onboarding_step_progress", step=step, total=total))


_SIDEBAR_STEPS = [
    ("onboarding_sidebar_step1_title", "onboarding_sidebar_step1_body"),
    ("onboarding_sidebar_step2_title", "onboarding_sidebar_step2_body"),
    ("onboarding_sidebar_step3_title", "onboarding_sidebar_step3_body"),
    ("onboarding_sidebar_step4_title", "onboarding_sidebar_step4_body"),
]

_TABS_STEPS = [
    ("onboarding_tabs_step1_title", "onboarding_tabs_step1_body"),
    ("onboarding_tabs_step2_title", "onboarding_tabs_step2_body"),
    ("onboarding_tabs_step3_title", "onboarding_tabs_step3_body"),
    ("onboarding_tabs_step4_title", "onboarding_tabs_step4_body"),
    ("onboarding_tabs_step5_title", "onboarding_tabs_step5_body"),
    ("onboarding_tabs_step6_title", "onboarding_tabs_step6_body"),
]


@st.fragment
def _tour_step_fragment(*, kind: str, user, steps: list[tuple[str, str]]) -> None:
    """Contenu d'UNE étape (titre, texte, boutons) — isolé en fragment pour
    que "Suivant" ne redessine que ce bloc, pas toute la page derrière le
    dialog. `kind` vaut "sidebar" ou "tabs" (détermine les clés de session /
    l'argument passé à auth.set_onboarding_seen)."""
    _step_key = f"_onboarding_{kind}_step"
    _step = st.session_state.get(_step_key, 0)
    _title_key, _body_key = steps[_step]
    st.subheader(_(_title_key))
    st.markdown(_(_body_key))
    _dialog_progress(_step + 1, len(steps))

    _is_last = _step == len(steps) - 1
    _seen_kwargs = {kind: True}

    _c_skip, _c_spacer, _c_next = st.columns([1, 2, 1])

    if _c_skip.button(_("onboarding_skip_btn"), key=f"onb_{kind}_skip"):
        tva_auth.set_onboarding_seen(user.id, **_seen_kwargs)
        setattr(user, f"onboarding_{kind}_seen", True)
        st.session_state.pop(_step_key, None)
        st.rerun()  # rerun complet volontaire : il faut fermer le dialog

    _next_label = _("onboarding_finish_btn") if _is_last else _("onboarding_next_btn")
    if _c_next.button(_next_label, key=f"onb_{kind}_next", type="primary"):
        if _is_last:
            tva_auth.set_onboarding_seen(user.id, **_seen_kwargs)
            setattr(user, f"onboarding_{kind}_seen", True)
            st.session_state.pop(_step_key, None)
            st.rerun()  # rerun complet volontaire : dernière étape, on ferme
        else:
            st.session_state[_step_key] = _step + 1
            st.rerun(scope="fragment")  # ne redessine que ce fragment


@st.dialog(" ", width="large")
def _sidebar_tour_dialog(user) -> None:
    _tour_step_fragment(kind="sidebar", user=user, steps=_SIDEBAR_STEPS)


@st.dialog(" ", width="large")
def _tabs_tour_dialog(user) -> None:
    _tour_step_fragment(kind="tabs", user=user, steps=_TABS_STEPS)


def maybe_show_sidebar_tour(user) -> None:
    """À appeler dans app.py juste après render_sidebar(), avant le
    file_uploader. N'affiche rien si déjà vu par ce compte."""
    if getattr(user, "onboarding_sidebar_seen", False):
        return
    _sidebar_tour_dialog(user)


def maybe_show_tabs_tour(user) -> None:
    """À appeler dans app.py juste après la construction réussie de
    `results` (premier calcul terminé pour ce compte). N'affiche rien si
    déjà vu, ni tant que la visite sidebar n'a pas été close (pour ne pas
    empiler deux dialogues)."""
    if getattr(user, "onboarding_tabs_seen", False):
        return
    if not getattr(user, "onboarding_sidebar_seen", False):
        return
    _tabs_tour_dialog(user)
