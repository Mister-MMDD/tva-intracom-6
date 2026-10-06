"""Garde-fous de non-régression pour les 4 correctifs de performance /
cache du 2026-09-26 (2), qui avaient disparu du code sans qu'aucun test ne
casse (constaté le 2026-09-28, voir README - evolution.md).

Chaque test ci-dessous échoue si le correctif correspondant disparaît :

  1. engine._collect_vat_rate_prefetch_pairs  -> doit rester un GÉNÉRATEUR
     (pas de liste intermédiaire de couples (pays, date) en RAM).
  2. vat_rates_db._country_history_dates_cache -> cache parallèle des dates
     seules, peuplé / maintenu / vidé avec _country_history_cache, et utilisé
     par _db_get_rate (plus de reconstruction O(n) de la liste de dates).
     Contrôle aussi : UNE seule requête Postgres par (pays, type de taux),
     quel que soit le nombre de dates de transaction distinctes.
  3. ecb_rates.prefetch_closing_rates -> recherche dichotomique (bisect_left)
     sur les dates BCE disponibles, une fois par date de clôture demandée.
  4. vat_rates_db.vat_rate(tx_date=None) -> la date du jour est résolue à
     CHAQUE appel, avant le cache (pas de taux figé sous la clé None).

Aucun accès réseau ni Postgres réel : pool et appels BCE sont simulés.
Aucun thread, aucune connexion persistante (contrainte scale-to-zero).
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tva_intracom import ecb_rates
from tva_intracom import vat_rates_db as vdb
from tva_intracom.engine import _collect_vat_rate_prefetch_pairs
from tva_intracom.models import BuyerType, Sale


# ─────────────────────────────────────────────────────────────────────────────
# Outils
# ─────────────────────────────────────────────────────────────────────────────

def _make_sale(**kwargs) -> Sale:
    defaults = dict(
        sale_id="PERF-001",
        amount_ht=Decimal("100.00"),
        buyer_type=BuyerType.B2C,
        stock_country="FR",
        buyer_country="DE",
        seller_country="FR",
        buyer_vat_valid=False,
        buyer_vat_number="",
        transaction_date="2026-01-01",
        product_category="STANDARD",
    )
    defaults.update(kwargs)
    return Sale(**defaults)


def _fake_pool(rows: list[tuple]) -> tuple[MagicMock, MagicMock]:
    """Pool psycopg2 factice : chaque cur.execute() est comptabilisé, fetchall()
    renvoie `rows`. Retourne (pool, cursor) pour inspecter les appels."""
    cur = MagicMock(name="cursor")
    cur.fetchall.return_value = rows
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False

    conn = MagicMock(name="conn")
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    pool = MagicMock(name="pool")
    pool.getconn.return_value = conn
    return pool, cur


@pytest.fixture(autouse=True)
def _isolated_state():
    """Caches de vat_rates_db et d'ecb_rates remis à zéro avant/après chaque test."""
    vdb.clear_cache(persistent=False)
    with ecb_rates._cache_lock:
        ecb_rates._forward_rate_cache.clear()
    yield
    vdb.clear_cache(persistent=False)
    with ecb_rates._cache_lock:
        ecb_rates._forward_rate_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. _collect_vat_rate_prefetch_pairs reste un générateur
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectPairsIsGenerator:
    def test_is_generator_function(self):
        assert inspect.isgeneratorfunction(_collect_vat_rate_prefetch_pairs), (
            "_collect_vat_rate_prefetch_pairs doit rester un générateur (yield) : "
            "une liste intermédiaire de couples (pays, date) double l'empreinte RAM "
            "sur un gros import."
        )

    def test_returns_lazy_iterator_not_list(self):
        result = _collect_vat_rate_prefetch_pairs([_make_sale()])
        assert not isinstance(result, (list, tuple, set)), (
            f"Objet matérialisé ({type(result).__name__}) au lieu d'un itérateur paresseux."
        )
        assert iter(result) is result  # protocole itérateur (un générateur se consomme une fois)

    def test_content_unchanged_after_lazy_conversion(self):
        """Le passage en générateur ne doit rien changer au contenu produit."""
        sales = [
            _make_sale(stock_country="ES", buyer_country="DE", transaction_date="2026-03-10"),
            # Avoir : order_date prioritaire sur transaction_date.
            _make_sale(amount_ht=Decimal("-10.00"), stock_country="FR", buyer_country="IT",
                       transaction_date="2026-05-01", order_date="2026-02-15"),
            _make_sale(transaction_date=""),  # sans date : ignorée
        ]
        pairs = set(_collect_vat_rate_prefetch_pairs(sales))
        assert ("FR", date(2026, 3, 10)) in pairs
        assert ("ES", date(2026, 3, 10)) in pairs
        assert ("DE", date(2026, 3, 10)) in pairs
        assert ("IT", date(2026, 2, 15)) in pairs
        assert ("IT", date(2026, 5, 1)) not in pairs


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cache parallèle des dates + une seule requête Postgres par (pays, type)
# ─────────────────────────────────────────────────────────────────────────────

_HISTORY_ROWS = [
    (date(2020, 1, 1), Decimal("19.00")),
    (date(2023, 7, 1), Decimal("20.00")),
    (date(2025, 1, 1), Decimal("21.00")),
]


class TestCountryHistoryDatesCache:
    KEY = ("DE", "STANDARD")

    def _load(self, rows=None):
        pool, cur = _fake_pool(_HISTORY_ROWS if rows is None else rows)
        with patch.object(vdb, "_get_pool", return_value=pool):
            history = vdb._load_country_history(*self.KEY)
        return history, pool, cur

    def test_load_populates_both_caches_consistently(self):
        history, _, _ = self._load()
        assert len(history) == 3
        assert self.KEY in vdb._country_history_dates_cache, (
            "_country_history_dates_cache n'est plus peuplé par _load_country_history."
        )
        assert vdb._country_history_dates_cache[self.KEY] == [d for d, _ in history]

    def test_record_history_entry_keeps_both_caches_in_sync(self):
        self._load()
        # Insertion d'un nouveau milestone au milieu, tri conservé.
        vdb._record_history_entry(*self.KEY, date(2024, 1, 1), Decimal("20.50"))
        dates = vdb._country_history_dates_cache[self.KEY]
        history = vdb._country_history_cache[self.KEY]
        assert dates == [d for d, _ in history]
        assert dates == sorted(dates)
        assert date(2024, 1, 1) in dates
        # Remplacement d'un milestone existant : pas de doublon de date.
        vdb._record_history_entry(*self.KEY, date(2024, 1, 1), Decimal("20.60"))
        assert vdb._country_history_dates_cache[self.KEY].count(date(2024, 1, 1)) == 1
        assert dict(vdb._country_history_cache[self.KEY])[date(2024, 1, 1)] == Decimal("20.60")

    def test_clear_cache_empties_dates_cache(self):
        self._load()
        assert vdb._country_history_dates_cache
        vdb.clear_cache(persistent=False)
        assert not vdb._country_history_dates_cache
        assert not vdb._country_history_cache
        assert not vdb._country_history_loaded

    def test_db_get_rate_resolves_via_dates_cache(self):
        """_db_get_rate doit LIRE le cache de dates (pas le reconstruire)."""
        self._load()
        # Cache de dates volontairement décalé d'une entrée : si _db_get_rate
        # l'utilise réellement, le résultat suit ce cache ; s'il reconstruit
        # la liste depuis l'historique, il ignore cette modification.
        sentinel_dates = [date(2020, 1, 1), date(2023, 7, 1), date(2030, 1, 1)]
        vdb._country_history_dates_cache[self.KEY] = sentinel_dates
        with patch.object(vdb, "_get_pool", return_value=None):
            assert vdb._db_get_rate("DE", "STANDARD", date(2026, 6, 1)) == Decimal("20.00"), (
                "_db_get_rate ne s'appuie plus sur _country_history_dates_cache "
                "(reconstruction O(n) de la liste de dates à chaque appel ?)."
            )

    def test_db_get_rate_semantics(self):
        self._load()
        # Historique synthétique (19 -> 20 -> 21) : on isole ici la sémantique
        # pure de la recherche dichotomique. Le garde-fou « pas d'héritage
        # au-delà d'un changement statique connu » (audit 2026-10-04) est
        # testé à part dans test_vat_rates_db.py — DE a réellement changé de
        # taux le 2020-07-01 et le 2021-01-01, ce qui court-circuiterait
        # ces assertions.
        with patch.object(vdb, "_get_pool", return_value=None), \
             patch.object(vdb, "_static_change_dates", return_value=[]):
            assert vdb._db_get_rate("DE", "STANDARD", date(2019, 12, 31)) is None   # avant le 1er milestone
            assert vdb._db_get_rate("DE", "STANDARD", date(2020, 1, 1)) == Decimal("19.00")
            assert vdb._db_get_rate("DE", "STANDARD", date(2023, 6, 30)) == Decimal("19.00")
            assert vdb._db_get_rate("DE", "STANDARD", date(2023, 7, 1)) == Decimal("20.00")
            assert vdb._db_get_rate("DE", "STANDARD", date(2099, 1, 1)) == Decimal("21.00")

    def test_single_postgres_query_for_many_distinct_dates(self):
        """Pas d'amplification 'jour par jour' : 1 requête pour 5 000 dates distinctes."""
        pool, cur = _fake_pool(_HISTORY_ROWS)
        start = date(2019, 1, 1)
        with patch.object(vdb, "_get_pool", return_value=pool):
            for i in range(5_000):
                vdb._db_get_rate("DE", "STANDARD", start + timedelta(days=i))
        assert cur.execute.call_count == 1, (
            f"{cur.execute.call_count} requêtes Postgres pour un seul (pays, type) : "
            "l'amplification par date de transaction est revenue."
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. prefetch_closing_rates : recherche dichotomique
# ─────────────────────────────────────────────────────────────────────────────

class TestPrefetchClosingRatesBisect:
    def test_uses_bisect_left_once_per_requested_date_and_resolves_forward(self):
        # Jours publiés BCE : lundi-vendredi seulement (simulation).
        published = {
            date(2026, 3, 30): Decimal("1.10"),  # lundi
            date(2026, 3, 31): Decimal("1.11"),  # mardi
            date(2026, 4, 1): Decimal("1.12"),   # mercredi
            date(2026, 4, 6): Decimal("1.13"),   # lundi suivant
        }
        requested = [date(2026, 3, 31), date(2026, 4, 2), date(2026, 4, 5)]
        pairs = [("USD", d) for d in requested]

        real_bisect = ecb_rates.bisect
        spy = MagicMock(wraps=real_bisect)
        with patch.object(ecb_rates, "_fetch_ecb_batch", return_value={"USD": published}), \
             patch.object(ecb_rates, "bisect", spy):
            ecb_rates.prefetch_closing_rates(pairs)

        assert spy.bisect_left.call_count == len(requested), (
            "prefetch_closing_rates doit faire UNE recherche dichotomique par date "
            "de clôture demandée (et non filtrer la liste complète à chaque date)."
        )
        # Recherche EN AVANT (art. 5 bis Règl. UE 2020/194) : 1er jour publié >= date de clôture.
        with ecb_rates._cache_lock:
            cache = dict(ecb_rates._forward_rate_cache)
        assert cache[("USD", date(2026, 3, 31))] == Decimal("1.11")   # jour publié exact
        assert cache[("USD", date(2026, 4, 2))] == Decimal("1.13")    # prochain jour publié
        assert cache[("USD", date(2026, 4, 5))] == Decimal("1.13")    # week-end -> lundi

    def test_date_after_last_published_day_is_marked_failed_not_cached(self):
        published = {date(2026, 3, 30): Decimal("1.10")}
        with patch.object(ecb_rates, "_fetch_ecb_batch", return_value={"USD": published}), \
             patch.object(ecb_rates, "_mark_failed") as mark_failed:
            ecb_rates.prefetch_closing_rates([("USD", date(2026, 4, 15))])
        with ecb_rates._cache_lock:
            assert ("USD", date(2026, 4, 15)) not in ecb_rates._forward_rate_cache
        mark_failed.assert_called_once_with("closing", "USD", date(2026, 4, 15))


# ─────────────────────────────────────────────────────────────────────────────
# 4. vat_rate(tx_date=None) : date du jour résolue à chaque appel
# ─────────────────────────────────────────────────────────────────────────────

class _FrozenDate(date):
    """date dont today() est pilotable, sans casser l'arithmétique / la comparaison."""
    _today = date(2026, 1, 15)

    @classmethod
    def today(cls):
        return cls._today


class TestVatRateResolvesTodayEachCall:
    def test_no_stale_rate_frozen_under_none_key(self):
        vdb._vat_rate_cached.cache_clear()
        seen_dates: list[date] = []

        def fake_get_vat_rate(code, cat, d):
            seen_dates.append(d)
            # Le taux "change" au 1er février : simule un changement de taux effectif.
            return Decimal("20.00") if d < date(2026, 2, 1) else Decimal("21.00")

        with patch.object(vdb, "date", _FrozenDate), \
             patch.object(vdb, "get_vat_rate", side_effect=fake_get_vat_rate):
            _FrozenDate._today = date(2026, 1, 15)
            assert vdb.vat_rate("DE") == Decimal("20.00")
            assert vdb.vat_rate("DE") == Decimal("20.00")   # 2e appel : servi par le cache

            _FrozenDate._today = date(2026, 2, 2)           # le temps passe (minuit / 1er du mois)
            assert vdb.vat_rate("DE") == Decimal("21.00"), (
                "Taux figé sous la clé tx_date=None : la date du jour doit être résolue "
                "à chaque appel, AVANT le cache (@lru_cache sur _vat_rate_cached, pas sur vat_rate)."
            )
        assert seen_dates == [date(2026, 1, 15), date(2026, 2, 2)]

    def test_cache_key_is_resolved_date_and_cache_still_effective(self):
        vdb._vat_rate_cached.cache_clear()
        with patch.object(vdb, "get_vat_rate", return_value=Decimal("20.00")) as gvr:
            d = date(2026, 3, 1)
            for _ in range(10):
                vdb.vat_rate("de", "standard", d)   # normalisation code/catégorie -> même clé
            assert gvr.call_count == 1

    def test_clear_cache_clears_lru(self):
        with patch.object(vdb, "get_vat_rate", return_value=Decimal("20.00")) as gvr:
            vdb.vat_rate("DE", "STANDARD", date(2026, 3, 1))
            vdb.clear_cache(persistent=False)
            vdb.vat_rate("DE", "STANDARD", date(2026, 3, 1))
            assert gvr.call_count == 2
