"""Tests des deux correctifs de sécurité identifiés lors du checkup du
2026-08-26 sur tva_intracom.auth :

1. `set_user_role()` n'avait aucun contrôle serveur (contrairement à
   `delete_account()`, `billing._require_write_access`,
   `vies_engine.set_manual_override`) — un lecteur pouvait, en théorie,
   changer un rôle en appelant directement la fonction serveur si le
   gating UI (sidebar.py) était un jour contourné. Corrigé par l'ajout
   d'un paramètre `acting_user_id`, même pattern que `delete_account()`.

2. `lock_org_for_user()` faisait un check-then-act (SELECT locked_at puis
   INSERT/UPDATE) sans verrou, exposé à une race condition entre deux
   webhooks Stripe concurrents pour deux comptes différents de la même
   organisation (deux premiers paiements quasi simultanés). Corrigé par
   un verrou avisé Postgres transactionnel (`pg_advisory_xact_lock`).

Approche : comme test_billing_payment_quotas.py, on isole la dépendance
Postgres via monkeypatch de `auth._get_pool()` (MagicMock) plutôt que de
simuler une vraie base — l'objectif est de vérifier la LOGIQUE (qui est
autorisé à faire quoi, quelles requêtes sont déclenchées dans quel ordre),
pas le comportement réel de Postgres. Ces tests ne nécessitent aucune base
Supabase réelle (`SUPABASE_DB_URL` non requise).
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom import auth


# ---------------------------------------------------------------------------
# Fixture : pool Postgres factice — même pattern que
# tests/test_billing_payment_quotas.py::fake_db.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    """Mocke auth._get_pool() : retourne un objet dont .getconn() donne une
    connexion factice utilisable en `with conn, conn.cursor() as cur`."""
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor

    pool = MagicMock()
    pool.getconn.return_value = conn
    pool.putconn.return_value = None

    monkeypatch.setattr(auth, "_get_pool", lambda: pool)
    return SimpleNamespace(pool=pool, conn=conn, cursor=cursor)


def _fake_user(user_id: str, role: str) -> auth.User:
    return auth.User(id=user_id, email=f"{user_id}@cabinet-exemple.fr", org_id="domain:cabinet-exemple.fr", role=role)


# ---------------------------------------------------------------------------
# add_allowed_email() / remove_allowed_email() — contrôle serveur
# (CORRECTIF 2026-08-26, repéré lors de l'audit systématique "tous les
# blocages UI ont-ils un verrouillage serveur ?")
# ---------------------------------------------------------------------------

class TestAllowedEmailServerSideEnforcement:

    def test_add_allowed_email_requires_admin(self, fake_db, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "reader"))

        with pytest.raises(PermissionError):
            auth.add_allowed_email("domain:cabinet-exemple.fr", "new@cabinet-exemple.fr", "admin", added_by="reader-user")

        insert_calls = [c for c in fake_db.cursor.execute.call_args_list if "INSERT INTO tva_org_allowed_emails" in c.args[0]]
        assert insert_calls == []

    def test_add_allowed_email_allowed_for_admin(self, fake_db, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))

        auth.add_allowed_email("domain:cabinet-exemple.fr", "new@cabinet-exemple.fr", "reader", added_by="admin-user")

        insert_calls = [c for c in fake_db.cursor.execute.call_args_list if "INSERT INTO tva_org_allowed_emails" in c.args[0]]
        assert len(insert_calls) == 1

    def test_remove_allowed_email_requires_admin_when_acting_user_id_given(self, fake_db, monkeypatch):
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "reader"))

        with pytest.raises(PermissionError):
            auth.remove_allowed_email("domain:cabinet-exemple.fr", "old@cabinet-exemple.fr", acting_user_id="reader-user")

        delete_calls = [c for c in fake_db.cursor.execute.call_args_list if "DELETE FROM tva_org_allowed_emails" in c.args[0]]
        assert delete_calls == []

    def test_remove_allowed_email_backward_compatible_without_acting_user_id(self, fake_db):
        """acting_user_id omis (défaut None) : comportement historique
        préservé, aucune vérification."""
        auth.remove_allowed_email("domain:cabinet-exemple.fr", "old@cabinet-exemple.fr")

        delete_calls = [c for c in fake_db.cursor.execute.call_args_list if "DELETE FROM tva_org_allowed_emails" in c.args[0]]
        assert len(delete_calls) == 1


# ---------------------------------------------------------------------------
# set_user_role() — contrôle serveur (CORRECTIF 2026-08-26)
# ---------------------------------------------------------------------------

class TestSetUserRoleServerSideEnforcement:

    def test_admin_acting_user_can_change_role(self, fake_db, monkeypatch):
        """Un admin peut bien changer le rôle d'un autre compte : la requête
        UPDATE est exécutée."""
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "admin"))

        auth.set_user_role("target-user", "admin", acting_user_id="admin-user")

        update_calls = [c for c in fake_db.cursor.execute.call_args_list if "UPDATE tva_users SET role" in c.args[0]]
        assert len(update_calls) == 1
        assert update_calls[0].args[1] == ("admin", "target-user")

    def test_reader_acting_user_is_rejected(self, fake_db, monkeypatch):
        """Un lecteur (role='reader') ne peut PAS changer un rôle — la
        fonction lève PermissionError et n'exécute aucune requête UPDATE."""
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: _fake_user(uid, "reader"))

        with pytest.raises(PermissionError):
            auth.set_user_role("target-user", "admin", acting_user_id="reader-user")

        update_calls = [c for c in fake_db.cursor.execute.call_args_list if "UPDATE tva_users SET role" in c.args[0]]
        assert update_calls == []

    def test_unknown_acting_user_is_rejected(self, fake_db, monkeypatch):
        """Un acting_user_id ne correspondant à aucun compte (compte
        supprimé entre-temps, jeton falsifié...) est traité comme non-admin :
        PermissionError, aucune écriture."""
        monkeypatch.setattr(auth, "get_user_by_id", lambda uid: None)

        with pytest.raises(PermissionError):
            auth.set_user_role("target-user", "admin", acting_user_id="ghost-user")

        update_calls = [c for c in fake_db.cursor.execute.call_args_list if "UPDATE tva_users SET role" in c.args[0]]
        assert update_calls == []

    def test_acting_user_id_none_is_backward_compatible(self, fake_db, monkeypatch):
        """acting_user_id=None (défaut) : aucune vérification — comportement
        historique préservé pour les appels internes/scripts existants."""
        get_user_by_id_spy = MagicMock(side_effect=AssertionError("ne doit pas être appelé"))
        monkeypatch.setattr(auth, "get_user_by_id", get_user_by_id_spy)

        auth.set_user_role("target-user", "admin")  # acting_user_id omis

        get_user_by_id_spy.assert_not_called()
        update_calls = [c for c in fake_db.cursor.execute.call_args_list if "UPDATE tva_users SET role" in c.args[0]]
        assert len(update_calls) == 1


# ---------------------------------------------------------------------------
# lock_org_for_user() — verrou avisé transactionnel (CORRECTIF 2026-08-26)
# ---------------------------------------------------------------------------

class TestLockOrgForUserAdvisoryLock:

    def _configure_fetchone_sequence(self, fake_db, *, org_id="domain:cabinet-exemple.fr",
                                      email="a@cabinet-exemple.fr", already_locked=False):
        """Configure les réponses successives de cur.fetchone() dans l'ordre
        où lock_org_for_user() les consomme :
          1) SELECT org_id, email FROM tva_users ...
          2) SELECT locked_at FROM tva_orgs ...   (après le verrou avisé)
        """
        locked_row = (1234567890.0,) if already_locked else None
        fake_db.cursor.fetchone.side_effect = [(org_id, email), locked_row]

    def test_advisory_lock_acquired_before_locked_at_check(self, fake_db):
        """Le verrou avisé (pg_advisory_xact_lock) doit être demandé APRÈS
        avoir résolu org_id mais AVANT le SELECT locked_at qui décide si
        l'organisation est déjà verrouillée — sinon il ne protège rien."""
        self._configure_fetchone_sequence(fake_db, already_locked=False)

        auth.lock_org_for_user("user-1")

        executed_sql = [c.args[0] for c in fake_db.cursor.execute.call_args_list]
        idx_advisory = next(i for i, sql in enumerate(executed_sql) if "pg_advisory_xact_lock" in sql)
        idx_locked_at_check = next(i for i, sql in enumerate(executed_sql) if "SELECT locked_at FROM tva_orgs" in sql)
        assert idx_advisory < idx_locked_at_check

    def test_advisory_lock_scoped_to_org_id(self, fake_db):
        """Le verrou est bien scopé sur l'org_id résolu (hashtext(org_id)),
        pas un verrou global qui bloquerait des organisations différentes
        entre elles sans raison."""
        self._configure_fetchone_sequence(fake_db, org_id="domain:cabinet-exemple.fr", already_locked=False)

        auth.lock_org_for_user("user-1")

        advisory_calls = [c for c in fake_db.cursor.execute.call_args_list if "pg_advisory_xact_lock" in c.args[0]]
        assert len(advisory_calls) == 1
        assert advisory_calls[0].args[1] == ("domain:cabinet-exemple.fr",)

    def test_noop_if_already_locked_even_after_acquiring_lock(self, fake_db):
        """Si l'organisation est déjà verrouillée (vue APRÈS acquisition du
        verrou — cas d'une transaction concurrente qui vient de committer),
        aucune écriture de rôle ne doit avoir lieu : la fonction doit rester
        idempotente, verrou ou pas."""
        self._configure_fetchone_sequence(fake_db, already_locked=True)

        auth.lock_org_for_user("user-1")

        write_calls = [
            c for c in fake_db.cursor.execute.call_args_list
            if "UPDATE tva_users SET role" in c.args[0] or "INSERT INTO tva_orgs" in c.args[0]
        ]
        assert write_calls == []

    def test_no_advisory_lock_for_solo_org(self, fake_db):
        """Une organisation solo (domaine public) sort avant même d'atteindre
        le verrou avisé — pas besoin de sérialiser un cas qui ne verrouille
        jamais rien."""
        fake_db.cursor.fetchone.side_effect = [("solo:perso@gmail.com", "perso@gmail.com")]

        auth.lock_org_for_user("user-1")

        advisory_calls = [c for c in fake_db.cursor.execute.call_args_list if "pg_advisory_xact_lock" in c.args[0]]
        assert advisory_calls == []

    def test_locks_org_and_demotes_others_when_not_yet_locked(self, fake_db):
        """Cas nominal (non concurrent) : l'organisation n'est pas encore
        verrouillée après acquisition du verrou -> le compte souscripteur
        devient/reste admin, les autres comptes de l'org basculent lecteur."""
        self._configure_fetchone_sequence(fake_db, org_id="domain:cabinet-exemple.fr",
                                           email="a@cabinet-exemple.fr", already_locked=False)

        auth.lock_org_for_user("user-1")

        executed_sql = [c.args[0] for c in fake_db.cursor.execute.call_args_list]
        assert any("INSERT INTO tva_orgs" in sql for sql in executed_sql)
        assert any("UPDATE tva_users SET role='admin' WHERE id=%s" in sql for sql in executed_sql)
        assert any("UPDATE tva_users SET role='reader' WHERE org_id=%s AND id<>%s" in sql for sql in executed_sql)
        fake_db.conn.commit.assert_called()
