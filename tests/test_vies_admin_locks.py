"""Tests des correctifs de sécurité identifiés lors de l'audit systématique
du 2026-08-26 "tous les blocages UI ont-ils un verrouillage serveur ?" sur
`tva_intracom.vies_engine` :

- `set_cache_ttl()` (réglage du TTL du cache VIES, partagé par toute
  l'organisation) n'avait aucun contrôle serveur — seul le gating UI
  (expander réservé à `is_admin(current_user)` dans ui/sidebar.py)
  protégeait ce réglage.
- `purge_expired_cache()` (bouton de purge manuelle dans le même bloc
  admin-only) avait le même trou.

Les deux acceptent désormais un `acting_user_id` optionnel (défaut None,
rétrocompatible — notamment pour l'appel de purge automatique au chargement
de l'onglet VIES, ui/tabs/vies_ui.py::render_vies, qui reste volontairement
ouvert à tout membre), même pattern que `set_manual_override()`.

Approche : isolation de la dépendance Postgres via monkeypatch de
`vies_engine._conn` (MagicMock), comme pour test_auth_roles.py — objectif :
vérifier la logique d'autorisation et l'ordre des opérations, pas le
comportement réel de Postgres. Aucune base Supabase réelle requise.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom import auth
from tva_intracom import vies_engine


@pytest.fixture
def fake_conn(monkeypatch):
    """Mocke vies_engine._conn() : `with _conn() as conn, conn.cursor() as cur`
    renvoie une connexion/curseur factices."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(vies_engine, "_conn", lambda: ctx)
    return SimpleNamespace(conn=conn, cursor=cursor)


def _fake_user(user_id: str, role: str) -> auth.User:
    return auth.User(id=user_id, email=f"{user_id}@cabinet-exemple.fr", org_id="domain:cabinet-exemple.fr", role=role)


class TestSetCacheTtlServerSideEnforcement:

    def test_reader_acting_user_is_rejected(self, fake_conn, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "reader"))

        with pytest.raises(PermissionError):
            vies_engine.set_cache_ttl("domain:cabinet-exemple.fr", 15, acting_user_id="reader-user")

        insert_calls = [c for c in fake_conn.cursor.execute.call_args_list if "INSERT INTO vies_scope_settings" in c.args[0]]
        assert insert_calls == []
        # Le TTL en mémoire ne doit pas non plus avoir été modifié.
        assert vies_engine._SCOPE_TTL_DAYS.get("domain:cabinet-exemple.fr") != 15

    def test_admin_acting_user_can_change_ttl(self, fake_conn, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))

        vies_engine.set_cache_ttl("domain:cabinet-exemple.fr", 15, acting_user_id="admin-user")

        insert_calls = [c for c in fake_conn.cursor.execute.call_args_list if "INSERT INTO vies_scope_settings" in c.args[0]]
        assert len(insert_calls) == 1
        assert vies_engine._SCOPE_TTL_DAYS["domain:cabinet-exemple.fr"] == 15

    def test_acting_user_id_none_is_backward_compatible(self, fake_conn, monkeypatch):
        """acting_user_id=None (défaut) : aucune vérification — comportement
        historique préservé (scripts internes, tests existants)."""
        get_user_by_id_spy = MagicMock(side_effect=AssertionError("ne doit pas être appelé"))
        monkeypatch.setattr(auth, "get_user_by_id", get_user_by_id_spy)

        vies_engine.set_cache_ttl("domain:cabinet-exemple.fr", 10)  # acting_user_id omis

        get_user_by_id_spy.assert_not_called()
        assert vies_engine._SCOPE_TTL_DAYS["domain:cabinet-exemple.fr"] == 10

    def test_ttl_still_capped_at_30_days_for_admin(self, fake_conn, monkeypatch):
        """Le plafond de 30 jours (2026-08-23) reste appliqué même pour un
        admin passant acting_user_id — le nouveau contrôle serveur ne
        remplace pas les gardes existantes, il s'ajoute."""
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))

        vies_engine.set_cache_ttl("domain:cabinet-exemple.fr", 9999, acting_user_id="admin-user")

        assert vies_engine._SCOPE_TTL_DAYS["domain:cabinet-exemple.fr"] == 30


class TestPurgeExpiredCacheServerSideEnforcement:

    def test_reader_acting_user_is_rejected(self, fake_conn, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "reader"))

        with pytest.raises(PermissionError):
            vies_engine.purge_expired_cache("domain:cabinet-exemple.fr", acting_user_id="reader-user")

        delete_calls = [c for c in fake_conn.cursor.execute.call_args_list if "DELETE FROM vies_scope_cache" in c.args[0]]
        assert delete_calls == []

    def test_admin_acting_user_can_purge(self, fake_conn, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))
        fake_conn.cursor.rowcount = 0

        vies_engine.purge_expired_cache("domain:cabinet-exemple.fr", acting_user_id="admin-user")

        # _db_delete_expired_scope exécute 1 DELETE pour le TTL + 1 DELETE
        # par motif d'erreur transitoire (vies_unavailable, timeout...) —
        # on vérifie juste qu'au moins la purge TTL a bien eu lieu.
        delete_calls = [c for c in fake_conn.cursor.execute.call_args_list if "DELETE FROM vies_scope_cache" in c.args[0]]
        assert len(delete_calls) >= 1

    def test_acting_user_id_none_is_backward_compatible(self, fake_conn, monkeypatch):
        """acting_user_id=None (défaut) : comportement historique préservé —
        notamment la purge automatique au chargement de l'onglet VIES
        (ui/tabs/vies_ui.py::render_vies), ouverte à tout membre, lecteur
        inclus, qui ne doit surtout pas se mettre à lever PermissionError."""
        get_user_by_id_spy = MagicMock(side_effect=AssertionError("ne doit pas être appelé"))
        monkeypatch.setattr(auth, "get_user_by_id", get_user_by_id_spy)
        fake_conn.cursor.rowcount = 0

        vies_engine.purge_expired_cache("domain:cabinet-exemple.fr")  # acting_user_id omis

        get_user_by_id_spy.assert_not_called()
        delete_calls = [c for c in fake_conn.cursor.execute.call_args_list if "DELETE FROM vies_scope_cache" in c.args[0]]
        assert len(delete_calls) >= 1

    def test_purge_never_touches_manual_overrides_table(self, fake_conn, monkeypatch):
        """Non-régression documentaire : la purge ne doit jamais toucher
        `vies_manual_overrides` (classifications manuelles), table
        distincte et volontairement hors du périmètre de cette fonction."""
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))
        fake_conn.cursor.rowcount = 0

        vies_engine.purge_expired_cache("domain:cabinet-exemple.fr", acting_user_id="admin-user")

        override_calls = [c for c in fake_conn.cursor.execute.call_args_list if "vies_manual_overrides" in c.args[0]]
        assert override_calls == []
