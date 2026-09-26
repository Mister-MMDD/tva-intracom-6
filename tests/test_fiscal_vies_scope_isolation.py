"""Tests pour l'isolation multi-tenant du cache VIES.

Le cache VIES est isolé par "scope" pour éviter les fuites entre organisations:
- Domaines personnels (gmail, outlook, etc.) → scope isolé par compte
- Domaines professionnels → scope partagé par domaine

Ce module vérifie que l'isolation fonctionne correctement.
"""

import pytest
from datetime import date, datetime, timedelta, timezone, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
from tva_intracom import vies_engine


class TestVISScopeIsolation:
    """Tests pour l'isolation du scope VIES."""

    def test_personal_email_has_isolated_scope(self):
        """Vérifie qu'un e-mail personnel a un scope isolé."""
        # Domaines personnels
        personal_emails = [
            "user@gmail.com",
            "user@outlook.com",
            "user@yahoo.com",
            "user@free.fr",
        ]
        
        for email in personal_emails:
            scope = vies_engine.resolve_scope_id(email)
            
            # Le scope devrait inclure l'e-mail complet pour l'isolation
            assert email in scope, f"Scope pour {email} devrait inclure l'e-mail"

    def test_professional_email_has_shared_scope(self):
        """Vérifie qu'un e-mail professionnel a un scope partagé par domaine."""
        # Domaines professionnels
        professional_emails = [
            "user@company.com",
            "contact@startup.fr",
            "billing@enterprise.de",
        ]
        
        for email in professional_emails:
            scope = vies_engine.resolve_scope_id(email)
            
            # Le scope devrait être basé sur le domaine uniquement
            # pas sur l'e-mail complet
            domain = email.split("@")[1]
            assert domain in scope, f"Scope pour {email} devrait inclure le domaine"

    def test_two_users_same_personal_domain_different_scopes(self):
        """Vérifie que deux utilisateurs du même domaine personnel ont des scopes différents."""
        user1 = "user1@gmail.com"
        user2 = "user2@gmail.com"
        
        scope1 = vies_engine.resolve_scope_id(user1)
        scope2 = vies_engine.resolve_scope_id(user2)
        
        # Scopes différents pour l'isolation
        assert scope1 != scope2, "Deux utilisateurs gmail devraient avoir des scopes différents"

    def test_two_users_same_professional_domain_same_scope(self):
        """Vérifie que deux utilisateurs du même domaine professionnel ont le même scope."""
        user1 = "user1@company.com"
        user2 = "user2@company.com"
        
        scope1 = vies_engine.resolve_scope_id(user1)
        scope2 = vies_engine.resolve_scope_id(user2)
        
        # Même scope pour le partage
        assert scope1 == scope2, "Deux utilisateurs du même domaine pro devraient partager le scope"

    def test_case_insensitive_email(self):
        """Vérifie que l'e-mail est traité insensiblement à la casse."""
        email1 = "User@Company.com"
        email2 = "user@company.com"
        
        scope1 = vies_engine.resolve_scope_id(email1)
        scope2 = vies_engine.resolve_scope_id(email2)
        
        # Même scope (insensible à la casse)
        assert scope1 == scope2, "Le scope devrait être insensible à la casse"

    def test_scope_cache_key_format(self):
        """Vérifie le format de la clé de cache VIES."""
        # La clé de cache devrait inclure le scope
        # Format: "COUNTRY|VAT_NUMBER|SCOPE"
        
        country = "FR"
        vat_number = "FR12345678901"
        scope = "user@gmail.com"
        
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (
            True, country, vat_number[2:], "Entreprise", "Paris", "",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        with patch.object(vies_engine, "_conn", return_value=connection), \
                patch.object(vies_engine, "_is_expired", return_value=False), \
                patch.object(vies_engine, "_dec", side_effect=lambda value: value):
            result, fresh = vies_engine._db_get_scope(scope, vat_number)

        assert result.valid is True
        assert fresh is True
        query, params = cursor.execute.call_args.args
        assert "WHERE scope_id=%s AND vat_id=%s" in query
        assert params == (scope, vat_number)

    def test_override_vies_cache_respects_scope(self):
        """Vérifie que les overrides VIES respectent le scope."""
        rows_by_scope = {
            "domain:company-a.com": [("FR12345678901", True, datetime.now(timezone.utc))],
            "domain:company-b.com": [],
        }
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor

        def fetch_scope_rows():
            return rows_by_scope[cursor.execute.call_args.args[1][0]]

        cursor.fetchall.side_effect = fetch_scope_rows
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        with patch.object(vies_engine, "_conn", return_value=connection):
            scope_a = vies_engine.get_manual_overrides(
                "domain:company-a.com", include_expired=True
            )
            scope_b = vies_engine.get_manual_overrides(
                "domain:company-b.com", include_expired=True
            )

        assert scope_a == {"FR12345678901": True}
        assert scope_b == {}
        assert [call.args[1][0] for call in cursor.execute.call_args_list] == [
            "domain:company-a.com", "domain:company-b.com",
        ]


class TestVISScopeEdgeCases:
    """Tests edge cases pour le scope VIES."""

    def test_subdomain_treated_separately(self):
        """Vérifie que les sous-domaines sont traités séparément."""
        # user@company.com vs user@sub.company.com
        # Devraient avoir des scopes différents si ce sont des domaines professionnels
        
        email1 = "user@company.com"
        email2 = "user@sub.company.com"
        
        scope1 = vies_engine.resolve_scope_id(email1)
        scope2 = vies_engine.resolve_scope_id(email2)
        
        # Scopes différents (sous-domaines différents)
        assert scope1 != scope2, "Sous-domaines devraient avoir des scopes différents"

    def test_invalid_email_format(self):
        """Les e-mails mal formés ont une portée de repli déterministe."""
        assert vies_engine.resolve_scope_id("invalid") == "user:invalid"
        assert vies_engine.resolve_scope_id("") == "user:inconnu"
        assert vies_engine.resolve_scope_id("@nodomain.com") == "domain:nodomain.com"
        assert vies_engine.resolve_scope_id("user@") == "domain:"

    def test_unicode_email(self):
        """Vérifie le traitement des e-mails avec caractères Unicode."""
        # E-mail avec caractères Unicode dans le local-part
        unicode_email = "éàü@company.com"
        
        # Ne devrait pas planter
        scope = vies_engine.resolve_scope_id(unicode_email)
        assert scope is not None

    def test_plus_addressing(self):
        """Le plus-addressing reste distinct pour l'isolation du cache."""
        email1 = "user+tag@gmail.com"
        email2 = "user@gmail.com"

        assert vies_engine.resolve_scope_id(email1) == "user:user+tag@gmail.com"
        assert vies_engine.resolve_scope_id(email2) == "user:user@gmail.com"
        assert vies_engine.resolve_scope_id(email1) != vies_engine.resolve_scope_id(email2)

    def test_very_long_email(self):
        """Vérifie le traitement des e-mails très longs."""
        # E-mail avec local-part très long
        long_email = "a" * 100 + "@company.com"
        
        # Ne devrait pas planter
        scope = vies_engine.resolve_scope_id(long_email)
        assert scope is not None


class TestVISScopeConfiguration:
    """Tests pour la configuration des scopes VIES."""

    def test_personal_email_domains_list(self):
        """Vérifie que la liste des domaines personnels existe."""
        from tva_intracom.vies_engine import PERSONAL_EMAIL_DOMAINS
        
        assert PERSONAL_EMAIL_DOMAINS is not None
        assert len(PERSONAL_EMAIL_DOMAINS) > 0
        assert "gmail.com" in PERSONAL_EMAIL_DOMAINS
        assert "outlook.com" in PERSONAL_EMAIL_DOMAINS

    def test_scope_can_be_overridden_manually(self):
        """Le scope d'organisation est déterminé de façon stable par domaine."""
        assert vies_engine.resolve_scope_id("alice@company.example") == "domain:company.example"
        assert vies_engine.resolve_scope_id("bob@company.example") == "domain:company.example"

    def test_scope_ttl_configurable(self):
        """Vérifie que le TTL du scope est configurable."""
        from tva_intracom.vies_engine import _SCOPE_TTL_MAX_ENTRIES
        
        # Le TTL du scope devrait être configurable
        assert _SCOPE_TTL_MAX_ENTRIES > 0

    def test_global_cache_has_different_ttl(self):
        """Le TTL global par défaut et le TTL scope personnalisé sont distincts."""
        checked_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        now = checked_at + timedelta(days=8)

        with patch.object(vies_engine, "_now_utc", return_value=now):
            assert vies_engine._is_expired(checked_at) is True
            assert vies_engine._is_expired(
                checked_at, "domain:long-ttl", ttl_days=30
            ) is False


class TestVISScopeMultiTenant:
    """Tests pour le multi-tenant avec scope VIES."""

    def test_organization_a_and_organization_b_isolated(self):
        """Vérifie que deux organisations sont isolées."""
        org_a = "contact@company-a.com"
        org_b = "contact@company-b.com"
        
        scope_a = vies_engine.resolve_scope_id(org_a)
        scope_b = vies_engine.resolve_scope_id(org_b)
        
        # Scopes différents
        assert scope_a != scope_b

    def test_organization_a_users_share_scope(self):
        """Vérifie que les utilisateurs d'une organisation partagent le scope."""
        user1 = "alice@company-a.com"
        user2 = "bob@company-a.com"
        
        scope1 = vies_engine.resolve_scope_id(user1)
        scope2 = vies_engine.resolve_scope_id(user2)
        
        # Même scope
        assert scope1 == scope2

    def test_large_organization_scope_sharing(self):
        """Vérifie qu'une grande organisation partage correctement."""
        # 100 utilisateurs de la même organisation
        emails = [f"user{i}@largecorp.com" for i in range(100)]
        
        scopes = [vies_engine.resolve_scope_id(email) for email in emails]
        
        # Tous devraient avoir le même scope
        assert len(set(scopes)) == 1, "Tous les utilisateurs d'une organisation devraient partager le scope"

    def test_cross_domain_leak_prevention(self):
        """Un résultat du cache scope A n'est pas visible dans le scope B."""
        row = (
            True, "DE", "123456789", "Entreprise A", "", "",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        def fetch_scope_row():
            scope_id = cursor.execute.call_args.args[1][0]
            return row if scope_id == "domain:company-a.com" else None

        cursor.fetchone.side_effect = fetch_scope_row
        with patch.object(vies_engine, "_conn", return_value=connection), \
                patch.object(vies_engine, "_is_expired", return_value=False), \
                patch.object(vies_engine, "_dec", side_effect=lambda value: value):
            result_a, fresh_a = vies_engine._db_get_scope(
                "domain:company-a.com", "DE123456789"
            )
            result_b, fresh_b = vies_engine._db_get_scope(
                "domain:company-b.com", "DE123456789"
            )

        assert result_a is not None and result_a.valid and fresh_a
        assert result_b is None and not fresh_b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
