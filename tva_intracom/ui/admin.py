"""Module Administration — gestion des rôles (admin/lecteur) et de la
liste des e-mails autorisés à créer un compte pour une organisation.

Une organisation = un domaine e-mail professionnel (voir
tva_intracom.auth.resolve_org_id, même principe que
vies_engine.resolve_scope_id). Réservé aux comptes dont `role == "admin"` —
`render_admin_dialog` ne fait aucun contrôle d'accès lui-même : c'est à
l'appelant (sidebar.py) de ne proposer le bouton d'ouverture que si
`tva_auth.is_admin(current_user)`. Toutes les fonctions de mutation ici
appellent malgré tout les fonctions serveur de auth.py, elles-mêmes
appelables directement — il n'y a donc pas de contrôle serveur additionnel
au-delà de "l'appelant est déjà authentifié" ; c'est un choix pragmatique
cohérent avec le reste de l'app (aucune notion d'API séparée), mais à garder
en tête si ce module est un jour exposé autrement que via cette UI.

Pas d'impact scale-to-zero : aucune connexion persistante, aucun thread, une
poignée de requêtes ponctuelles déclenchées par des clics.
"""
from __future__ import annotations

import time

import streamlit as st

from tva_intracom import auth as tva_auth
from tva_intracom.i18n import _


@st.dialog(title=_("admin_module_header"))
def render_admin_dialog(current_user: "tva_auth.User") -> None:
    org_id = current_user.org_id
    locked = tva_auth.is_org_locked(org_id)

    if locked:
        st.caption(_("admin_org_locked_caption"))
    else:
        st.caption(_("admin_org_free_caption"))

    st.divider()
    st.markdown(f"**{_('admin_members_title')}**")

    members = tva_auth.list_org_members(org_id)
    for _m in members:
        _col_email, _col_role, _col_action = st.columns([3, 2, 2])
        _col_email.write(_m.email + (" 👑" if _m.id == current_user.id else ""))
        _role_label = _("admin_member_role_admin") if _m.role == "admin" else _("admin_member_role_reader")
        _col_role.write(_role_label)

        if _m.id == current_user.id:
            continue  # un admin ne modifie/supprime pas son propre compte ici

        with _col_action:
            _new_role = "reader" if _m.role == "admin" else "admin"
            _toggle_label = _("admin_demote_btn") if _m.role == "admin" else _("admin_promote_btn")
            if st.button(_toggle_label, key=f"admin_toggle_role_{_m.id}"):
                tva_auth.set_user_role(_m.id, _new_role)
                st.rerun()

            _confirm_key = f"admin_confirm_delete_{_m.id}"
            if not st.session_state.get(_confirm_key):
                if st.button(_("admin_remove_member_btn"), key=f"admin_remove_{_m.id}"):
                    st.session_state[_confirm_key] = True
                    st.rerun()
            else:
                st.warning(_("admin_remove_member_confirm", email=_m.email))
                _c1, _c2 = st.columns(2)
                if _c1.button(_("cancel_btn"), key=f"admin_cancel_remove_{_m.id}"):
                    st.session_state[_confirm_key] = False
                    st.rerun()
                if _c2.button(_("confirm_delete_btn"), key=f"admin_confirm_remove_{_m.id}", type="primary"):
                    tva_auth.delete_account(_m.id)
                    st.session_state[_confirm_key] = False
                    st.success(_("admin_remove_member_success", email=_m.email))
                    time.sleep(0.4)
                    st.rerun()

    st.divider()
    st.markdown(f"**{_('admin_add_email_title')}**")
    st.caption(_("admin_add_email_help"))

    _new_email = st.text_input(_("admin_add_email_input_label"), key="admin_new_allowed_email")
    _new_is_admin = st.checkbox(_("admin_add_email_role_checkbox"), key="admin_new_allowed_email_is_admin")
    if st.button(_("admin_add_email_btn"), key="admin_btn_add_allowed_email"):
        _email = (_new_email or "").strip().lower()
        if not _email or "@" not in _email:
            st.error(_("admin_add_email_invalid"))
        else:
            tva_auth.add_allowed_email(
                org_id, _email, "admin" if _new_is_admin else "reader", current_user.id,
            )
            st.success(_("admin_add_email_success", email=_email))
            st.rerun()

    _allowed = tva_auth.list_allowed_emails(org_id)
    if _allowed:
        st.markdown(f"**{_('admin_allowed_emails_title')}**")
        for _entry in _allowed:
            _c_email, _c_role, _c_action = st.columns([3, 2, 2])
            _c_email.write(_entry["email"])
            _c_role.write(_("admin_member_role_admin") if _entry["role"] == "admin" else _("admin_member_role_reader"))
            if _c_action.button(_("admin_remove_allowed_email_btn"), key=f"admin_remove_allowed_{_entry['email']}"):
                tva_auth.remove_allowed_email(org_id, _entry["email"])
                st.rerun()
