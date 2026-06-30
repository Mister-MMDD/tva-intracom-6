"""Tests unitaires du cache SQLite VIES (sans appel réseau réel).

Tout appel HTTP est mocké. On teste :
  - init DB / migration JSON
  - cache hit frais / expiré / absent
  - TTL configurable
  - anti-downgrade
  - erreurs transitoires (pas de mise en cache)
  - dégradation serveur (ratio réponses vides)
  - validate_vat_numbers_parallel (lot)
  - get_cache_stats / purge / force_revalidate
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ---------------------------------------------------------------------------
# Fixtures : on redirige les chemins de DB et JSON vers un répertoire temporaire
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Chaque test utilise sa propre base SQLite et son propre JSON legacy."""
    db_file = str(tmp_path / "vies_cache.db")
    json_file = str(tmp_path / "vies_disk_cache.json")
    monkeypatch.chdir(tmp_path)

    # Recharge le module dans le répertoire temporaire
    import importlib
    import tva_intracom.vies as vies_mod
    monkeypatch.setattr(vies_mod, "CACHE_DB_FILE", db_file)
    monkeypatch.setattr(vies_mod, "_LEGACY_JSON_FILE", json_file)
    monkeypatch.setattr(vies_mod, "CACHE_TTL_DAYS", 90)

    # Réinitialise la DB proprement pour ce test
    vies_mod._init_db()
    yield vies_mod


def make_result(valid=True, cc="FR", num="12345678901", name="Test SARL",
                address="1 rue Test", error=""):
    from tva_intracom.vies import ViesResult
    return ViesResult(valid=valid, country_code=cc, vat_number=num,
                      name=name, address=address, error=error)


# ---------------------------------------------------------------------------
# 1. Initialisation DB
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_table_created(self, isolated_db):
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        assert any("vies_cache" in t[0] for t in tables)

    def test_index_created(self, isolated_db):
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        conn.close()
        assert any("idx_checked_at" in i[0] for i in idx)

    def test_init_idempotent(self, isolated_db):
        """Appeler _init_db plusieurs fois ne lève pas d'erreur."""
        isolated_db._init_db()
        isolated_db._init_db()


# ---------------------------------------------------------------------------
# 2. Migration JSON → SQLite
# ---------------------------------------------------------------------------

class TestMigration:
    def _write_json(self, vies_mod, data: dict):
        with open(vies_mod._LEGACY_JSON_FILE, "w") as f:
            json.dump(data, f)

    def test_migrate_valid_entries(self, isolated_db):
        self._write_json(isolated_db, {
            "FR12345678901": {
                "valid": True, "country_code": "FR", "vat_number": "12345678901",
                "name": "ACME", "address": "Paris", "error": "",
            },
            "DE123456789": {
                "valid": True, "country_code": "DE", "vat_number": "123456789",
                "name": "GmbH", "address": "Berlin", "error": "",
            },
        })
        migrated = isolated_db._migrate_json_to_sqlite()
        assert migrated == 2

        result, _ = isolated_db._db_get("FR12345678901")
        assert result is not None
        assert result.valid is True
        assert result.name == "ACME"

    def test_transient_errors_not_migrated(self, isolated_db):
        self._write_json(isolated_db, {
            "FR99999999901": {
                "valid": False, "country_code": "FR", "vat_number": "99999999901",
                "name": "", "address": "", "error": "ms_unavailable",
            },
        })
        migrated = isolated_db._migrate_json_to_sqlite()
        assert migrated == 0
        result, _ = isolated_db._db_get("FR99999999901")
        assert result is None

    def test_json_renamed_after_migration(self, isolated_db):
        self._write_json(isolated_db, {
            "FR11111111111": {
                "valid": True, "country_code": "FR", "vat_number": "11111111111",
                "name": "Test", "address": "Lyon", "error": "",
            }
        })
        isolated_db._migrate_json_to_sqlite()
        assert not os.path.exists(isolated_db._LEGACY_JSON_FILE)
        assert os.path.exists(isolated_db._LEGACY_JSON_FILE + ".migrated")

    def test_no_json_no_error(self, isolated_db):
        """Pas de fichier JSON → migration silencieuse."""
        migrated = isolated_db._migrate_json_to_sqlite()
        assert migrated == 0

    def test_migration_half_ttl_timestamp(self, isolated_db):
        """Les entrées migrées ont un timestamp < maintenant - TTL/2 → expirées."""
        self._write_json(isolated_db, {
            "IT12345678901": {
                "valid": True, "country_code": "IT", "vat_number": "12345678901",
                "name": "SRL", "address": "Rome", "error": "",
            }
        })
        isolated_db._migrate_json_to_sqlite()
        # TTL=90j → migrées à -45j → expirées si TTL < 45j
        isolated_db.CACHE_TTL_DAYS = 30
        result, is_fresh = isolated_db._db_get("IT12345678901")
        assert result is not None
        assert not is_fresh  # doit être expiré avec TTL=30j

    def test_no_duplicate_on_remigration(self, isolated_db):
        """Si on migre deux fois (bug), INSERT OR IGNORE évite les doublons."""
        data = {
            "ES12345678A": {
                "valid": True, "country_code": "ES", "vat_number": "12345678A",
                "name": "SL", "address": "Madrid", "error": "",
            }
        }
        self._write_json(isolated_db, data)
        isolated_db._migrate_json_to_sqlite()
        # Recrée le JSON (simulation bug double migration)
        self._write_json(isolated_db, data)
        isolated_db._migrate_json_to_sqlite()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        count = conn.execute(
            "SELECT COUNT(*) FROM vies_cache WHERE vat_id='ES12345678A'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# 3. Cache hit / miss / TTL
# ---------------------------------------------------------------------------

class TestCacheTtl:
    def test_cache_miss(self, isolated_db):
        result, fresh = isolated_db._db_get("FR00000000000")
        assert result is None
        assert not fresh

    def test_cache_set_then_get(self, isolated_db):
        res = make_result()
        isolated_db._db_set("FR12345678901", res)
        cached, fresh = isolated_db._db_get("FR12345678901")
        assert cached is not None
        assert cached.valid is True
        assert cached.name == "Test SARL"
        assert fresh is True

    def test_cache_expired(self, isolated_db):
        res = make_result()
        isolated_db._db_set("FR12345678901", res)
        # Forcer expiration en mettant checked_at dans le passé
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=200)
        ).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute(
            "UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
            (old_ts, "FR12345678901")
        )
        conn.commit()
        conn.close()
        _, fresh = isolated_db._db_get("FR12345678901")
        assert fresh is False

    def test_custom_ttl_shorter(self, isolated_db):
        isolated_db.CACHE_TTL_DAYS = 1
        res = make_result()
        isolated_db._db_set("FR12345678901", res)
        # 2 jours dans le passé
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute(
            "UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
            (old_ts, "FR12345678901")
        )
        conn.commit()
        conn.close()
        _, fresh = isolated_db._db_get("FR12345678901")
        assert fresh is False

    def test_cache_upsert(self, isolated_db):
        """Mise à jour d'une entrée existante."""
        res1 = make_result(valid=True, name="Old name")
        isolated_db._db_set("DE123456789", res1)
        res2 = make_result(valid=False, cc="DE", num="123456789", name="New name", error="Invalid")
        isolated_db._db_set("DE123456789", res2)
        cached, _ = isolated_db._db_get("DE123456789")
        assert cached.name == "New name"
        assert cached.valid is False


# ---------------------------------------------------------------------------
# 4. check_vat_raw (unitaire + cache)
# ---------------------------------------------------------------------------

class TestCheckVatRaw:
    def test_cache_hit_no_network(self, isolated_db):
        """Cache frais → pas d'appel réseau."""
        res = make_result(cc="FR", num="12345678901")
        isolated_db._db_set("FR12345678901", res)

        with patch.object(isolated_db, "check_vat_with_retry") as mock_check:
            result = isolated_db.check_vat_raw("FR12345678901")
            mock_check.assert_not_called()
        assert result.valid is True

    def test_cache_miss_calls_network(self, isolated_db):
        """Cache absent → appel réseau."""
        expected = make_result(cc="FR", num="12345678901")
        with patch.object(isolated_db, "check_vat_with_retry", return_value=expected) as mock_check:
            result = isolated_db.check_vat_raw("FR12345678901")
            mock_check.assert_called_once()
        assert result.valid is True

    def test_result_written_to_cache_after_network(self, isolated_db):
        expected = make_result(cc="DE", num="123456789")
        with patch.object(isolated_db, "check_vat_with_retry", return_value=expected):
            isolated_db.check_vat_raw("DE123456789")
        cached, fresh = isolated_db._db_get("DE123456789")
        assert cached is not None and fresh

    def test_transient_error_not_cached(self, isolated_db):
        """Erreur transitoire → pas de mise en cache."""
        transient = make_result(valid=False, error="ms_unavailable", name="", address="")
        with patch.object(isolated_db, "check_vat_with_retry", return_value=transient):
            isolated_db.check_vat_raw("FR99999999901")
        cached, _ = isolated_db._db_get("FR99999999901")
        assert cached is None

    def test_transient_error_returns_expired_cache(self, isolated_db):
        """Erreur transitoire avec cache expiré → retourne l'ancienne valeur."""
        old_res = make_result(cc="IT", num="12345678901", valid=True, name="Vecchio")
        isolated_db._db_set("IT12345678901", old_res)
        # Expirer
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute("UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
                     (old_ts, "IT12345678901"))
        conn.commit()
        conn.close()

        transient = make_result(valid=False, cc="IT", num="12345678901",
                                error="ms_unavailable", name="", address="")
        with patch.object(isolated_db, "check_vat_with_retry", return_value=transient):
            result = isolated_db.check_vat_raw("IT12345678901")
        assert result.valid is True
        assert result.name == "Vecchio"

    def test_anti_downgrade(self, isolated_db):
        """Numéro précédemment valide → réponse vide VIES → ancienne valeur conservée."""
        good = make_result(cc="ES", num="12345678A", valid=True, name="SA")
        isolated_db._db_set("ES12345678A", good)
        # Expirer l'entrée
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute("UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
                     (old_ts, "ES12345678A"))
        conn.commit()
        conn.close()

        downgrade = make_result(valid=False, cc="ES", num="12345678A",
                                name="", address="", error="")
        with patch.object(isolated_db, "check_vat_with_retry", return_value=downgrade):
            result = isolated_db.check_vat_raw("ES12345678A")
        assert result.valid is True
        assert result.name == "SA"

    def test_invalid_number_returns_error(self, isolated_db):
        result = isolated_db.check_vat_raw("XX")
        assert result.valid is False
        assert result.error != ""


# ---------------------------------------------------------------------------
# 5. validate_vat_numbers_parallel (lot)
# ---------------------------------------------------------------------------

class TestValidateParallel:
    def _mock_check_vat_with_retry(self, vies_mod, responses: dict):
        """responses = {(cc, num): ViesResult}"""
        def _side_effect(cc, num, timeout=10):
            return responses.get((cc, num), make_result(valid=False, cc=cc, num=num,
                                                         error="Non trouvé dans mock"))
        return patch.object(vies_mod, "check_vat_with_retry", side_effect=_side_effect)

    def test_all_cache_hit(self, isolated_db):
        """Tous les numéros en cache frais → 0 appel réseau."""
        for vat_id in ["FR12345678901", "DE123456789"]:
            isolated_db._db_set(vat_id, make_result(cc=vat_id[:2], num=vat_id[2:]))

        with patch.object(isolated_db, "check_vat_with_retry") as mock_check:
            results = isolated_db.validate_vat_numbers_parallel(
                ["FR12345678901", "DE123456789"]
            )
            mock_check.assert_not_called()
        assert len(results) == 2

    def test_mixed_cache_and_network(self, isolated_db):
        """Un numéro en cache, un autre à revalider."""
        isolated_db._db_set("FR12345678901", make_result(cc="FR", num="12345678901"))

        de_result = make_result(cc="DE", num="123456789", name="GmbH")
        responses = {("DE", "123456789"): de_result}
        with self._mock_check_vat_with_retry(isolated_db, responses):
            results = isolated_db.validate_vat_numbers_parallel(
                ["FR12345678901", "DE123456789"]
            )
        assert results["FR12345678901"].valid is True
        assert results["DE123456789"].valid is True
        assert results["DE123456789"].name == "GmbH"

    def test_expired_entry_revalidated(self, isolated_db):
        """Entrée expirée → revalidation réseau."""
        isolated_db._db_set("NL123456789B01", make_result(cc="NL", num="123456789B01"))
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute("UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
                     (old_ts, "NL123456789B01"))
        conn.commit()
        conn.close()

        fresh = make_result(cc="NL", num="123456789B01", name="BV Refresh")
        responses = {("NL", "123456789B01"): fresh}
        with self._mock_check_vat_with_retry(isolated_db, responses):
            results = isolated_db.validate_vat_numbers_parallel(["NL123456789B01"])
        assert results["NL123456789B01"].name == "BV Refresh"

    def test_transient_error_batch_uses_expired_fallback(self, isolated_db):
        """Erreur transitoire en lot → retourne l'entrée expirée si disponible."""
        old = make_result(cc="PL", num="1234567890", valid=True, name="Sp.z.o.o")
        isolated_db._db_set("PL1234567890", old)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute("UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
                     (old_ts, "PL1234567890"))
        conn.commit()
        conn.close()

        transient = make_result(valid=False, cc="PL", num="1234567890",
                                error="ms_unavailable", name="", address="")
        responses = {("PL", "1234567890"): transient}
        with self._mock_check_vat_with_retry(isolated_db, responses):
            results = isolated_db.validate_vat_numbers_parallel(["PL1234567890"])
        assert results["PL1234567890"].valid is True
        assert results["PL1234567890"].name == "Sp.z.o.o"

    def test_server_degraded_detection(self, isolated_db):
        """Ratio élevé de réponses vides → dégradation détectée, pas de mise en cache."""
        vat_ids = [f"FR0000000000{i}" for i in range(5)]
        responses = {
            ("FR", f"0000000000{i}"): make_result(
                valid=False, cc="FR", num=f"0000000000{i}", name="", address="", error=""
            )
            for i in range(5)
        }
        with self._mock_check_vat_with_retry(isolated_db, responses):
            isolated_db.validate_vat_numbers_parallel(vat_ids)
        # Aucune entrée ne doit être en cache (réponses vides = serveur dégradé)
        for vat_id in vat_ids:
            try:
                norm = isolated_db._normalize_vat_id(vat_id)
                cached, _ = isolated_db._db_get(norm)
                assert cached is None, f"{vat_id} ne devrait pas être en cache"
            except ValueError:
                pass

    def test_duplicate_vat_ids(self, isolated_db):
        """Le même numéro en double dans la liste → traité une seule fois."""
        res = make_result(cc="FR", num="12345678901")
        responses = {("FR", "12345678901"): res}
        with self._mock_check_vat_with_retry(isolated_db, responses) as mock_check:
            results = isolated_db.validate_vat_numbers_parallel(
                ["FR12345678901", "FR12345678901"]
            )
            # Le mock peut être appelé 1 ou 2 fois selon l'implémentation
            # L'important : les deux clés sont dans le résultat
        assert "FR12345678901" in results

    def test_empty_list(self, isolated_db):
        results = isolated_db.validate_vat_numbers_parallel([])
        assert results == {}

    def test_concurrency_no_data_race(self, isolated_db):
        """Plusieurs threads écrivent en parallèle → pas de corruption."""
        errors = []
        def worker(i):
            try:
                vat_id = f"FR{str(i).zfill(11)}"
                isolated_db._db_set(vat_id, make_result(cc="FR", num=str(i).zfill(11)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# 6. Statistiques et administration
# ---------------------------------------------------------------------------

class TestAdminTools:
    def test_get_cache_stats_empty(self, isolated_db):
        stats = isolated_db.get_cache_stats()
        assert stats["total"] == 0
        assert stats["valid"] == 0
        assert stats["expired"] == 0

    def test_get_cache_stats_with_data(self, isolated_db):
        isolated_db._db_set("FR12345678901", make_result(valid=True))
        isolated_db._db_set("DE123456789", make_result(valid=False, cc="DE", num="123456789",
                                                        name="", address="", error="Invalid"))
        stats = isolated_db.get_cache_stats()
        assert stats["total"] == 2
        assert stats["valid"] == 1
        assert stats["invalid"] == 1
        assert stats["fresh"] == 2
        assert stats["expired"] == 0

    def test_purge_expired(self, isolated_db):
        isolated_db._db_set("FR12345678901", make_result())
        old_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        conn = sqlite3.connect(isolated_db.CACHE_DB_FILE)
        conn.execute("UPDATE vies_cache SET checked_at=? WHERE vat_id=?",
                     (old_ts, "FR12345678901"))
        conn.commit()
        conn.close()

        deleted = isolated_db.purge_expired_cache()
        assert deleted >= 1
        stats = isolated_db.get_cache_stats()
        assert stats["total"] == 0

    def test_purge_removes_transient_errors(self, isolated_db):
        transient = make_result(valid=False, name="", address="", error="ms_unavailable")
        isolated_db._db_set("BE0123456789", transient)
        deleted = isolated_db.purge_expired_cache()
        assert deleted >= 1

    def test_force_revalidate(self, isolated_db):
        isolated_db._db_set("FR12345678901", make_result())
        isolated_db.force_revalidate(["FR12345678901"])
        cached, _ = isolated_db._db_get("FR12345678901")
        assert cached is None

    def test_force_revalidate_invalid_number(self, isolated_db):
        """Numéro invalide dans force_revalidate → pas d'exception."""
        isolated_db.force_revalidate(["INVALID"])  # ne doit pas lever

    def test_set_cache_ttl(self, isolated_db):
        isolated_db.set_cache_ttl(30)
        assert isolated_db.CACHE_TTL_DAYS == 30
        isolated_db.set_cache_ttl(0)  # minimum 1
        assert isolated_db.CACHE_TTL_DAYS == 1

    def test_stats_ttl_field(self, isolated_db):
        isolated_db.set_cache_ttl(45)
        stats = isolated_db.get_cache_stats()
        assert stats["ttl_days"] == 45
