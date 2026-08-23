"""Onboarding guidé (2026-08-22, révisé).

Remplace l'ancien `onboarding.py` (supprimé — ralentissait l'app,
recalculait à chaque clic, désynchronisait les champs fiscaux d'une copie
parallèle). Approche actuelle : une checklist à 5 items, chacun avec sa
propre coche verte dès qu'il est renseigné, plutôt qu'un stepper
séquentiel à 2 étapes (retiré après retour utilisateur — jugé trop pauvre
en contenu et redondant avec l'ancien bloc "Comment utiliser cette
application ?", supprimé du même coup dans app.py).

Invariants conservés :
- AUCUN widget fiscal n'est recréé ici — les 4 booléens d'état
  (entreprise_ok, tva_local_ok, ioss_filled, upload_ok) sont calculés
  dans app.py à partir des champs DÉJÀ produits par `render_sidebar()`
  (mêmes `key=`, même emplacement). Ce module ne fait que les afficher.
- `session_state["_onboarding_step"]` ("active"/"done") ne rentre
  JAMAIS dans `calc_key` ni `parse_key` (voir ui/calc_cache.py) — c'est
  un flag d'affichage pur.
- Le calcul se déclenche par le mécanisme `calc_key` déjà en place dans
  app.py, jamais par une interaction avec ce bandeau.
- L'état "vu" est persisté en base (`tva_users.onboarding_seen`, voir
  auth.py::set_onboarding_seen) avec un bouton de relance manuel dans
  "Compte & Confidentialité" (sidebar.py::_render_account_dialog).
"""
from __future__ import annotations

import streamlit as st

from tva_intracom.i18n import _
from tva_intracom import auth as tva_auth


def ensure_onboarding_state(
    current_user, *, entreprise_ok: bool, tva_local_ok: bool, upload_ok: bool
) -> None:
    """À appeler une fois par run, juste après render_sidebar() (pour
    connaître les champs fiscaux) — mais AVANT le rendu de la checklist.
    Le numéro IOSS est volontairement exclu des critères de complétion
    (il est optionnel, ne doit jamais bloquer/masquer la checklist)."""
    if "_onboarding_restarted" not in st.session_state:
        st.session_state["_onboarding_restarted"] = False

    if current_user.onboarding_seen and not st.session_state["_onboarding_restarted"]:
        st.session_state["_onboarding_step"] = "done"
        return

    _all_mandatory_done = entreprise_ok and tva_local_ok and upload_ok
    st.session_state["_onboarding_step"] = "done" if _all_mandatory_done else "active"

    if _all_mandatory_done and not current_user.onboarding_seen:
        try:
            tva_auth.set_onboarding_seen(current_user.id, True)
            current_user.onboarding_seen = True
            st.session_state["_onboarding_restarted"] = False
        except Exception:
            # Non bloquant : si l'écriture échoue (base momentanément
            # indisponible), la checklist reste affichée une fois de plus
            # au prochain run — pas de blocage de l'app, pas de
            # retentative en boucle ici.
            pass


def dismiss_onboarding(current_user) -> None:
    """Appelé par le bouton "Passer" du bandeau. Marque directement
    onboarding_seen=True en base, sans attendre que les 3 items
    obligatoires soient complétés — un utilisateur pressé (ex. cabinet
    déjà familier de l'outil) doit pouvoir masquer la checklist sans
    rien renseigner. Ne touche à aucun champ fiscal."""
    st.session_state["_onboarding_step"] = "done"
    st.session_state["_onboarding_restarted"] = False
    try:
        tva_auth.set_onboarding_seen(current_user.id, True)
        current_user.onboarding_seen = True
    except Exception:
        # Non bloquant : si l'écriture échoue, le flag reste local à la
        # session (masqué ici et maintenant) et sera retenté au prochain
        # passage par ensure_onboarding_state ci-dessus.
        pass


def restart_onboarding() -> None:
    """Appelé par le bouton "Relancer la visite guidée" (sidebar.py). Ne
    touche à aucun champ fiscal déjà saisi — remet seulement la checklist
    à l'écran."""
    st.session_state["_onboarding_restarted"] = True
    st.session_state["_onboarding_step"] = "active"


def compute_pulse_target(
    *, step: str, entreprise_ok: bool, tva_local_ok: bool, upload_ok: bool
) -> str | None:
    """Détermine quel élément mettre en avant ("Lighthouse") au PROCHAIN run.

    Volontairement calculé et stocké en session_state à la fin d'un run
    pour être relu au DÉBUT du suivant, avant `render_sidebar()` — voir
    app.py. Cela évite toute dépendance circulaire (la sidebar doit savoir
    quel expander pulser avant que ses propres champs n'aient produit les
    booléens entreprise_ok/tva_local_ok/upload_ok du run courant). Un
    décalage d'un run est sans conséquence : c'est un indice visuel, pas
    une donnée fiscale.

    BUGFIX (2026-08-23) : la cible "vies_ttl" (section Cache VIES) était
    déjà câblée côté rendu (sidebar.py, theme.py) mais jamais retournée
    ici — le pulse correspondant ne s'affichait donc jamais, alors que le
    TTL du cache VIES (7 jours par défaut, voir onboarding_check_vies_ttl)
    est une info que l'utilisateur doit voir tôt. Affiché une seule fois
    par session, juste après que la fiche entreprise soit complète et
    avant le premier import de fichier — flag
    `_onboarding_vies_ttl_pulse_done` pour ne pas rester bloqué dessus
    indéfiniment (l'étape n'a pas de condition de "complétion" propre,
    contrairement à entreprise/upload).
    """
    if step == "done":
        return None
    if not (entreprise_ok and tva_local_ok):
        return "entreprise"  # SIREN + n° TVA locale vivent dans le même expander
    if not upload_ok:
        if not st.session_state.get("_onboarding_vies_ttl_pulse_done"):
            st.session_state["_onboarding_vies_ttl_pulse_done"] = True
            return "vies_ttl"
        return "upload"
    return None


def _check_row(done: bool, optional: bool, label: str, detail: str = "") -> str:
    if done:
        icon = "✅"
    elif optional:
        icon = "⚪"
    else:
        icon = "🔵"
    _detail_html = f'<p class="onboarding-banner-substep">{detail}</p>' if detail else ""
    return f'<p class="onboarding-banner-step">{icon} {label}</p>{_detail_html}'


@st.fragment
def render_onboarding_banner(
    current_user, *, entreprise_ok: bool, tva_local_ok: bool, ioss_filled: bool, upload_ok: bool
) -> None:
    """Checklist de démarrage, zone principale, au-dessus de l'uploader.

    Fragment : isolé du reste du run. Ne lit les 4 booléens qu'en
    paramètres (calculés dans app.py) et `_onboarding_step` qu'en lecture
    — n'écrit jamais rien dans calc_key/parse_key.

    BUGFIX (2026-08-22) : le HTML est assemblé via une liste de fragments
    sans indentation ni ligne vide, jointe par "".join(...) — une ligne
    vide au milieu d'un bloc HTML met fin à ce bloc pour le parseur
    Markdown de Streamlit, et la ligne suivante (indentée par un f-string
    Python) était alors rendue comme un bloc de code brut au lieu d'être
    interprétée comme du HTML.
    """
    _step = st.session_state.get("_onboarding_step", "done")
    if _step == "done":
        return

    _parts = [
        '<div class="onboarding-banner">',
        f'<p class="onboarding-banner-title">👋 {_("onboarding_title")}</p>',
        f'<p class="onboarding-banner-intro">{_("onboarding_intro")}</p>',
        _check_row(entreprise_ok, False, _("onboarding_check_entreprise")),
        _check_row(tva_local_ok, False, _("onboarding_check_tva_local")),
        _check_row(ioss_filled, True, _("onboarding_check_ioss")),
        _check_row(True, False, _("onboarding_check_vies_ttl"), _("onboarding_check_vies_ttl_detail")),
        _check_row(upload_ok, False, _("onboarding_check_upload")),
        '</div>',
    ]
    st.markdown("".join(_parts), unsafe_allow_html=True)

    if not (entreprise_ok and tva_local_ok):
        st.info(_("onboarding_hint_fiscal"))
        st.caption(_("onboarding_hint_vies"))
    elif not upload_ok:
        st.info(_("onboarding_hint_upload"))
        st.caption(_("onboarding_hint_tabs"))

    _dismiss_col, _spacer_col = st.columns([1, 5])
    with _dismiss_col:
        if st.button(_("onboarding_dismiss_btn"), key="btn_dismiss_onboarding"):
            dismiss_onboarding(current_user)
