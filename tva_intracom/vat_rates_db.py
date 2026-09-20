"""Taux de TVA dynamiques via l'API TEDB (Taxes in Europe Database) de la
Commission européenne, avec repli sur les tables statiques de rates.py.

Remplace l'usage direct de rates.vat_rate() dans les appelants (voir
engine.py, ca3_report.py, excel_report.py, oss_xml.py) : ce module expose
une fonction vat_rate() de signature identique, utilisable en remplacement
direct — rates.py reste la source de repli (jamais supprimée).

TEDB n'expose PAS d'API REST/JSON (contrairement à la BCE dans
ecb_rates.py) : c'est un service SOAP.
  WSDL     : https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService.wsdl
  Endpoint : https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService
  Action   : RetrieveVatRates

Particularités TEDB vérifiées contre la documentation officielle (SOA
Service Specification Document, Commission européenne) avant toute
implémentation — voir échanges avec Matthieu du 2026-09-12 :

  1. Codes ISO : TEDB utilise "EL" pour la Grèce (comme VIES), pas "GR"
     (ISO 3166-1 utilisé partout ailleurs dans ce projet). Traduit ici,
     jamais propagé au reste du code (cache/fallback restent en "GR").
  2. Catégories : TEDB catégorise par nature de bien/service (~45
     identifiants fixes : FOODSTUFFS, PHARMACEUTICAL_PRODUCTS, PARKING...),
     PAS par palier de taux. Certaines catégories internes à ce projet
     n'ont AUCUNE correspondance TEDB satisfaisante — voir
     _CATEGORY_TO_TEDB ci-dessous pour le détail et la justification de
     chaque cas non mappé (repli statique systématique, aucun appel réseau
     tenté pour ces cas — important pour le scale-to-zero).
  3. rate.value n'est fiable que si rate.type == DEFAULT ou EXEMPTED (avec
     valeur explicite, ex. 0.0 — confirmé par l'exemple officiel de
     réponse). NOT_APPLICABLE / OUT_OF_SCOPE ou absence de value -> ignoré,
     repli statique.

A VALIDER PAR LE CABINET COMPTABLE avant mise en production (classification
fiscale — cf. principe du projet, le cabinet tranche en dernier ressort) :
  - MEDICINES -> PHARMACEUTICAL_PRODUCTS : choix le plus proche disponible
    côté TEDB pour des PRODUITS pharmaceutiques vendus (vs MEDICAL_CARE,
    qui couvre des PRESTATIONS de soins médicaux/dentaires, jugé moins
    pertinent pour un catalogue Amazon).
"""

from __future__ import annotations

import bisect
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Optional

import certifi
import psycopg2
import psycopg2.extras

from .config import get_secret
from .database import NonPoolingConnectionPool, get_shared_pool, close_idle_connections as _database_close_idle
from .rates import vat_rate_at_date as _static_vat_rate_at_date
from .rates import STANDARD_VAT_RATES as _STATIC_STANDARD_RATES
from .rates import REDUCED_VAT_RATES as _STATIC_REDUCED_RATES

logger = logging.getLogger(__name__)

TEDB_ENDPOINT = "https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService"

# BUGFIX (2026-09-20, voir ecb_rates.py — même correctif dupliqué ici pour
# la même raison que _is_permanent_ssl_error, cf. son docstring) : urlopen()
# sans context SSL explicite dépend du magasin CA système, absent/périmé sur
# certaines images de conteneur -> CERTIFICATE_VERIFY_FAILED systématique,
# y compris en production. On force le bundle certifi.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
TEDB_SOAP_ACTION = "urn:ec.europa.eu:taxud:tedb:services:v1:VatRetrievalService/RetrieveVatRates"

# Codes ISO couverts par TEDB (27 États membres + XI = Irlande du Nord).
# Tout pays absent de cet ensemble bascule DIRECTEMENT sur le repli statique
# — aucun appel réseau tenté (scale-to-zero : pas de connexion sortante
# pour des pays qu'on sait ne jamais pouvoir résoudre dynamiquement).
_TEDB_SUPPORTED = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK", "XI",
}
# Traduction code interne -> code TEDB. Appliquée UNIQUEMENT au moment de
# construire la requête SOAP ; le cache (mémoire + Postgres) et le repli
# statique continuent d'utiliser le code interne ("GR") partout ailleurs.
_ISO_TO_TEDB = {"GR": "EL"}

# Mapping catégorie interne (rates.py) -> identifiant de catégorie TEDB.
# "STANDARD" est un cas particulier (type=STANDARD dans la réponse, pas de
# catégorie) et n'a pas d'entrée ici — voir _is_tedb_eligible().
#
# Catégories volontairement NON mappées (repli statique systématique) :
#   - BOOKS : pas de catégorie générale "livres" côté TEDB. Seule
#     LOAN_LIBRARIES (prêt en bibliothèque) existe — ce n'est PAS le taux
#     réduit à la vente. NEWSPAPERS/PERIODICALS existent mais ne couvrent
#     pas les livres. Une recherche par code CN nécessiterait de connaître
#     le bon code CN par pays — risque de donnée fiscale erronée, non fait.
#   - CLOTHING : aucune catégorie générale "habillement" côté TEDB (seule
#     CLOTHING_REPAIR = réparation existe, hors sujet). CHILD_WEAR
#     ci-dessous est une catégorie TEDB distincte et réelle, pas un
#     synonyme de CLOTHING.
#   - SUPER_REDUCED : regroupement interne à ce projet (palier de taux),
#     pas une catégorie TEDB (qui catégorise par nature de bien/service).
#
# Reprise chantier CN/CPA (2026-09-16) : catégories ajoutées sur la base du
# mapping PTC->TEDB de Matthieu (premier jet, non encore validé cabinet
# pour le mapping produit — voir tedb-amazon-ptc-reference.md) mais dont la
# safe-list pays elle-même est un fait TEDB, pas une décision fiscale.
_CATEGORY_TO_TEDB: dict[str, str] = {
    "FOOD": "FOODSTUFFS",
    "MEDICINES": "PHARMACEUTICAL_PRODUCTS",  # à valider cabinet, voir docstring module
    "PARKING": "PARKING",
    "PERIODICALS": "PERIODICALS",
    "MEDICAL_EQUIPMENT": "MEDICAL_EQUIPMENT",
    "CHILDREN_CAR_SEATS": "CHILDREN_CAR_SEATS",
    "SOLAR_PANELS": "SOLAR_PANELS",
    "PLANT": "PLANT",
    "FOSSIL_FUEL": "FOSSIL_FUEL",
    "CHEMICAL_FERTILISERS": "CHEMICAL_FERTILISERS",
    "CHEMICAL_PESTICIDES_ENVIRONMENT": "CHEMICAL_PESTICIDES_ENVIRONMENT",
    "CERTAIN_AGRICULTURAL_INPUT": "CERTAIN_AGRICULTURAL_INPUT",
    "CHILD_WEAR": "CHILD_WEAR",
    "AGRICULTURAL_PRODUCTION": "AGRICULTURAL_PRODUCTION",
}

# Safe-list pays par catégorie (dump TEDB réel, situation 2026-01-01 pour
# FOOD/MEDICINES ; complété le 2026-09-16 par Matthieu pour les nouvelles
# catégories, sortie de sa propre commande diag). Un pays hors de cette
# liste pour une catégorie donnée n'est PAS interrogé en dynamique TEDB :
# comportement inchangé (repli rates.py::REDUCED_VAT_RATES si une entrée
# statique existe pour cette catégorie, sinon STANDARD — jamais de
# sous-déclaration). Ce sont des FAITS TEDB (nombre de taux distincts par
# pays), pas des décisions fiscales — la décision fiscale porte sur le
# mapping PRODUCT_TAX_CODE -> catégorie (product_tax_code_category, table
# Postgres, "known_mapping"), pas sur cette liste.
_TEDB_CATEGORY_SAFE_COUNTRIES: dict[str, frozenset[str]] = {
    "FOOD": frozenset({"BG", "CY", "CZ", "DE", "ES", "FI", "FR", "HR", "LU", "LV", "NL", "RO", "SE", "SI"}),
    "MEDICINES": frozenset({"AT", "BG", "CY", "CZ", "DE", "EE", "ES", "FI", "HU", "LT", "LU", "LV", "NL", "PT", "RO", "SI", "SK", "XI"}),
    "AGRICULTURAL_PRODUCTION": frozenset({"AT", "CY", "ES", "FR", "HR", "IE", "IT", "LU", "PL", "PT", "RO", "SI"}),
    "PERIODICALS": frozenset({"AT", "CY", "CZ", "DE", "FI", "FR", "HR", "LT", "LV", "MT"}),
    "MEDICAL_EQUIPMENT": frozenset({"BE", "CZ", "DE", "EE", "ES", "FR", "HR", "HU", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}),
    "SOLAR_PANELS": frozenset({"AT", "FR", "IE", "LU", "NL"}),
    "FOSSIL_FUEL": frozenset({"IT", "PT"}),
    "PLANT": frozenset({"BE", "CZ", "DE", "LU", "NL", "SI"}),
    "CHILDREN_CAR_SEATS": frozenset({"CY", "CZ", "EL", "HR", "PL"}),
    "CERTAIN_AGRICULTURAL_INPUT": frozenset({"LU"}),
    "CHEMICAL_FERTILISERS": frozenset({"IT", "LU"}),
    "CHEMICAL_PESTICIDES_ENVIRONMENT": frozenset({"LU"}),
    "CHILD_WEAR": frozenset({"LU"}),
}

# Extraction des catégories REDUCED (_CATEGORY_TO_TEDB ci-dessus) réactivée
# le 2026-09-16 en même temps que _is_tedb_eligible() ci-dessous accepte
# désormais des catégories autres que STANDARD (restriction du 2026-09-13
# levée). Voir _TEDB_CATEGORY_SAFE_COUNTRIES pour le périmètre réel par
# pays/catégorie — la désactivation n'était qu'un verrou temporaire, pas
# une remise en cause de l'extraction elle-même.
_PARSE_REDUCED_CATEGORIES = True

# ------------------------------------------------------------------
# Cache L1 (mémoire/process) + Verrou de thread
# ------------------------------------------------------------------
# Clé : "COUNTRY|RATE_TYPE|YYYY-MM-DD" -> Decimal(rate)
_vat_memory_cache: dict[str, Decimal] = {}
_cache_lock = threading.Lock()

# Cache L1bis : historique complet {(pays, type_taux): [(date, taux), ...]}
# trié par date. Une seule requête Postgres par (pays, type_taux) au lieu
# d'une par jour distinct rencontré — corrige le ralentissement "jour par
# jour" constaté après passage à la granularité journalière (2026-09-14).
_country_history_cache: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
_country_history_loaded: set[tuple[str, str]] = set()

_pool_lock = threading.Lock()
_schema_ready = False
_db_unavailable = False  # Sticky flag si BDD injoignable ou non configurée


def _cache_key(country: str, rate_type: str, d: date) -> str:
    return f"{country.upper()}|{rate_type.upper()}|{d.isoformat()}"


def _get_pool() -> Optional[NonPoolingConnectionPool]:
    """Retourne le pool Postgres partagé ou dégrade silencieusement vers L1/statique."""
    global _schema_ready, _db_unavailable
    if _db_unavailable:
        return None
    dsn = get_secret("SUPABASE_DB_URL")
    if not dsn:
        logger.debug("[VAT_RATES] SUPABASE_DB_URL non défini — cache mémoire / statique uniquement.")
        _db_unavailable = True
        return None
    try:
        pool = get_shared_pool(dsn)
        if not _schema_ready:
            with _pool_lock:
                if not _schema_ready:
                    _init_schema(pool)
                    _schema_ready = True
    except Exception as exc:
        logger.warning("[VAT_RATES] Cache Postgres indisponible (%s) — repli statique.", exc)
        _db_unavailable = True
        return None
    return pool


def close_idle_connections() -> None:
    """Libère les connexions inactives au début de chaque run (appelé par app.py)."""
    _database_close_idle()


_EXPECTED_COLUMNS = {"country_code", "rate_type", "situation_date", "rate", "fetched_at"}


def _init_schema(pool: NonPoolingConnectionPool) -> None:
    """Crée vat_rate_cache si absente, et la RECRÉE si une table du même nom
    existe déjà avec un schéma différent.

    Contexte (incident du 2026-09-12) : une table vat_rate_cache
    préexistait en production avec un schéma différent (issu d'une
    version antérieure et jamais réellement fonctionnelle du fichier —
    celle-ci plantait à l'import). `CREATE TABLE IF NOT EXISTS` ne
    modifie jamais une table existante : toutes les lectures/écritures
    échouaient silencieusement (colonne "situation_date" inexistante),
    dégradant TOUTE requête vers TEDB à chaque appel (aucun cache L2
    possible) sans jamais faire planter le run. Sans danger de recréer :
    cette table n'est qu'un cache (perte = un re-fetch TEDB, pas une perte
    de donnée fiscale)."""
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'vat_rate_cache'
            """)
            existing_columns = {row[0] for row in cur.fetchall()}
            if existing_columns and existing_columns != _EXPECTED_COLUMNS:
                logger.warning(
                    "[VAT_RATES] table vat_rate_cache existante avec un "
                    "schéma incompatible (colonnes trouvées : %s, attendues : %s) — "
                    "recréation (perte de cache uniquement, aucune donnée fiscale "
                    "source n'est stockée dans cette table).",
                    sorted(existing_columns), sorted(_EXPECTED_COLUMNS),
                )
                cur.execute("DROP TABLE IF EXISTS vat_rate_cache")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vat_rate_cache (
                    country_code   VARCHAR(2) NOT NULL,
                    rate_type      VARCHAR(20) NOT NULL,
                    situation_date DATE NOT NULL,
                    rate           NUMERIC NOT NULL,
                    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (country_code, rate_type, situation_date)
                )
            """)
    finally:
        pool.putconn(conn)


def _load_country_history(country: str, rate_type: str) -> list[tuple[date, Decimal]]:
    """Charge en UNE requête tout l'historique de milestones connu pour ce
    (pays, type de taux), trié par date croissante. Résultat mis en cache
    process (jamais invalidé pendant la vie du process, sauf clear_cache()
    ou nouvelle entrée insérée via _record_history_entry après un fetch
    TEDB) : c'est un historique fiscal, il ne "rajeunit" jamais en base.

    Remplace l'ancienne approche (une requête SQL 'situation_date <=
    target_date ORDER BY situation_date DESC LIMIT 1' PAR JOUR distinct
    rencontré) par une seule requête par (pays, type_taux) — voir
    diagnostic du 2026-09-14 (ralentissement "jour par jour" après passage
    à la granularité journalière)."""
    key = (country, rate_type)
    with _cache_lock:
        if key in _country_history_loaded:
            return _country_history_cache.get(key, [])

    pool = _get_pool()
    if pool is None:
        return []
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT situation_date, rate FROM vat_rate_cache
                 WHERE country_code = %s AND rate_type = %s
                 ORDER BY situation_date
                """,
                (country, rate_type),
            )
            # Sécurité sur les types de données : certains exports/imports SQL ou drivers
            # peuvent renvoyer des dates sous forme de chaînes de caractères.
            history = []
            for row in cur.fetchall():
                d = row[0]
                if isinstance(d, str):
                    try:
                        d = date.fromisoformat(d)
                    except ValueError:
                        # Si le format n'est pas ISO, on laisse bisect lever l'erreur
                        # plus loin pour diagnostic, mais on tente au moins l'ISO.
                        pass
                if isinstance(d, date):
                    history.append((d, Decimal(str(row[1]))))
    except Exception as exc:
        logger.warning("[VAT_RATES] chargement historique Postgres échoué pour %s/%s : %s",
                        country, rate_type, exc)
        # Pas de mise en cache "loaded" sur échec : on retentera au
        # prochain appel plutôt que de figer un historique vide.
        return []
    finally:
        pool.putconn(conn)

    with _cache_lock:
        _country_history_cache[key] = history
        _country_history_loaded.add(key)
    return history


def _record_history_entry(country: str, rate_type: str, situation_date: date, rate: Decimal) -> None:
    """Insère un nouveau milestone (issu d'un fetch TEDB) dans l'historique
    déjà chargé en mémoire, en conservant le tri — évite de forcer un
    rechargement complet depuis Postgres juste après l'avoir écrit."""
    key = (country, rate_type)
    with _cache_lock:
        if key not in _country_history_loaded:
            return  # sera chargé (avec cette entrée incluse) au prochain besoin
        history = _country_history_cache.setdefault(key, [])
        dates = [d for d, _ in history]
        idx = bisect.bisect_left(dates, situation_date)
        if idx < len(history) and history[idx][0] == situation_date:
            history[idx] = (situation_date, rate)
        else:
            history.insert(idx, (situation_date, rate))


def _db_get_rate(country: str, rate_type: str, target_date: date) -> Optional[Decimal]:
    """Résout le taux applicable par recherche dichotomique dans
    l'historique complet du pays (chargé une seule fois, voir
    _load_country_history) — équivalent fonctionnel exact de l'ancienne
    requête SQL 'situation_date <= target_date ORDER BY situation_date DESC
    LIMIT 1', mais sans aller-retour réseau par date de transaction."""
    history = _load_country_history(country, rate_type)
    if not history:
        return None
    dates = [d for d, _ in history]
    idx = bisect.bisect_right(dates, target_date) - 1
    if idx < 0:
        return None  # target_date antérieure au premier milestone connu
    return history[idx][1]


def _db_upsert_batch(entries: list[tuple[str, str, date, Decimal]]) -> None:
    """Enregistre plusieurs (pays, type de taux, date, taux) en une seule transaction.

    Un seul appel TEDB (par pays + date) renvoie potentiellement plusieurs
    catégories à la fois (STANDARD + FOOD + MEDICINES + PARKING) — on les
    persiste toutes d'un coup pour amortir le coût réseau sur les lookups
    suivants du même pays/date.
    """
    if not entries:
        return
    pool = _get_pool()
    if pool is None:
        return
    now = datetime.now(timezone.utc)
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO vat_rate_cache (country_code, rate_type, situation_date, rate, fetched_at)
                VALUES %s
                ON CONFLICT (country_code, rate_type, situation_date) DO NOTHING
                """,
                [(c, rt, d, val, now) for c, rt, d, val in entries],
            )
    except Exception as exc:
        logger.warning("[VAT_RATES] écriture Postgres échouée (%d entrées) : %s", len(entries), exc)
    finally:
        pool.putconn(conn)


# ------------------------------------------------------------------
# Requête SOAP TEDB
# ------------------------------------------------------------------

_FETCH_MAX_ATTEMPTS = 3
_FETCH_BACKOFF_BASE_SECONDS = 1.0  # 1s, puis 2s

# Mémorise, par process, les paires (pays, date) ayant déjà échoué côté
# réseau — évite de re-tenter (avec 3 essais + backoff) pour CHAQUE ligne
# d'un fichier contenant de nombreuses ventes sur le même pays/jour quand
# TEDB est injoignable. TTL court : au cas où la panne serait transitoire.
# Purement un cache de performance process — aucun impact scale-to-zero
# (dict mémoire, aucun thread/connexion persistant).
_FAILED_PAIR_TTL_SECONDS = 300
_failed_pairs: dict[tuple[str, date], float] = {}

# BUGFIX (2026-09-20, voir ecb_rates.py pour le diagnostic complet) :
# drapeau global process, complémentaire à _failed_pairs (TTL 5 min pensé
# pour un aléa ponctuel, pas pour une panne SSL systémique sur un fichier
# dont le traitement dépasse ce TTL). Dès qu'une erreur SSL de certificat
# est constatée une fois, on coupe tout appel réseau TEDB pour le reste du
# process.
_ssl_broken_lock = threading.Lock()
_ssl_permanently_broken = False


def _mark_ssl_broken() -> None:
    global _ssl_permanently_broken
    with _ssl_broken_lock:
        _already_known = _ssl_permanently_broken
        _ssl_permanently_broken = True
    if not _already_known:
        logger.warning(
            "[VAT_RATES] TEDB API : certificat SSL non vérifiable — "
            "désactivation de TOUS les appels TEDB pour le reste de ce "
            "process (corrigez le magasin CA de l'infra ; voir "
            "_SSL_CONTEXT/certifi dans ce fichier)."
        )


def _is_ssl_broken() -> bool:
    with _ssl_broken_lock:
        return _ssl_permanently_broken


def _is_permanently_failed(country: str, target_date: date) -> bool:
    ts = _failed_pairs.get((country, target_date))
    if ts is None:
        return False
    if (time.monotonic() - ts) >= _FAILED_PAIR_TTL_SECONDS:
        del _failed_pairs[(country, target_date)]
        return False
    return True


def _mark_failed(country: str, target_date: date) -> None:
    _failed_pairs[(country, target_date)] = time.monotonic()


def _is_permanent_ssl_error(exc: BaseException) -> bool:
    """Une erreur de vérification de certificat ne se résoudra pas en
    retentant quelques secondes plus tard (magasin CA local absent/périmé).
    Même logique que ecb_rates._is_permanent_ssl_error (dupliquée
    volontairement : fonction courte, éviter un couplage entre les deux
    modules pour un détail d'implémentation réseau)."""
    seen: set[int] = set()
    to_check = [exc, getattr(exc, "reason", None), exc.__cause__, exc.__context__]
    for candidate in to_check:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if isinstance(candidate, ssl.SSLCertVerificationError):
            return True
    return False


def _build_soap_request(tedb_iso: str, target_date: date) -> bytes:
    """Construit l'enveloppe SOAP RetrieveVatRates.

    Aucun filtre categories/cnCodes/cpaCodes (tous optionnels côté TEDB) :
    on récupère TOUTES les catégories disponibles pour ce pays/date en un
    seul appel, et on ne garde ensuite que celles utiles (_parse_tedb_response).
    """
    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:urn="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService" '
        'xmlns:urn1="urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService:types">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        '<urn:retrieveVatRatesReqMsg>'
        '<urn1:memberStates>'
        f'<urn1:isoCode>{tedb_iso}</urn1:isoCode>'
        '</urn1:memberStates>'
        f'<urn1:situationOn>{target_date.isoformat()}</urn1:situationOn>'
        '</urn:retrieveVatRatesReqMsg>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )
    return xml_body.encode("utf-8")


def _request_tedb(tedb_iso: str, target_date: date) -> Optional[tuple[ET.Element, bytes]]:
    description = f"{tedb_iso} au {target_date}"
    if _is_ssl_broken():
        logger.debug(
            "[VAT_RATES] TEDB API : appel ignoré (SSL déjà signalé "
            "indisponible pour ce process) : %s", description,
        )
        return None
    body = _build_soap_request(tedb_iso, target_date)
    req = urllib.request.Request(
        TEDB_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": TEDB_SOAP_ACTION,
        },
    )
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
                raw = resp.read()
            return ET.fromstring(raw), raw
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            if _is_permanent_ssl_error(exc):
                logger.warning(
                    "[VAT_RATES] TEDB API : certificat SSL non vérifiable (%s) — "
                    "aucune nouvelle tentative pour cette requête : %s",
                    description, exc,
                )
                _mark_ssl_broken()
                return None
            is_last_attempt = attempt >= _FETCH_MAX_ATTEMPTS
            if is_last_attempt:
                logger.warning("[VAT_RATES] TEDB API indisponible (%s) après %d tentative(s) : %s",
                                description, attempt, exc)
                return None
            delay = _FETCH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.debug("[VAT_RATES] TEDB API échec (%s, tentative %d/%d) : %s — retry dans %.0fs",
                         description, attempt, _FETCH_MAX_ATTEMPTS, exc, delay)
            time.sleep(delay)
        except ET.ParseError as exc:
            logger.warning("[VAT_RATES] Réponse TEDB non parsable (%s) : %s", description, exc)
            return None
    return None


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_tedb_response(root: ET.Element, *, country: str = "?", target_date: Optional[date] = None) -> dict[str, Decimal]:
    """Extrait {catégorie interne: taux} depuis une réponse retrieveVatRatesRespMsg.

    Ne garde que les catégories couvertes par _CATEGORY_TO_TEDB — le filtre
    d'éligibilité réel (pays sûr ou non pour cette catégorie) est fait en
    amont par _is_tedb_eligible(), pas ici. Jusqu'au 2026-09-16,
    l'extraction des catégories REDUCED était désactivée en bloc
    (_PARSE_REDUCED_CATEGORIES = False, restriction du 2026-09-13 au taux
    STANDARD uniquement) car un seul appel SOAP TEDB renvoie TOUTES les
    catégories d'un pays/date en une fois (~2000 lignes de XML avec tous
    les codes CN) : parser puis comparer au statique des catégories qu'on
    n'utilisait même pas gaspillait du CPU et — surtout — générait des
    warnings de plausibilité (avec dump du XML brut COMPLET, parfois
    plusieurs dizaines de Ko) pour des taux jamais consommés. Observé en
    prod (Matthieu, 2026-09-13) : c'est cette pollution de logs qui
    causait le ralentissement perçu, pas l'appel réseau lui-même. Réactivé
    le 2026-09-16 en même temps que l'éligibilité TEDB (voir
    _TEDB_CATEGORY_SAFE_COUNTRIES) — le risque de pollution de logs ne
    revient pas puisque seules les catégories effectivement mappées dans
    _CATEGORY_TO_TEDB sont retenues (le `if cat_id in tedb_to_category`
    plus bas filtre déjà tout le reste).

    N'accepte une valeur que si rate.type == "DEFAULT", "REDUCED_RATE",
    "SUPER_REDUCED_RATE" ou "EXEMPTED" (les seules valeurs documentées
    comme fiables — cf. docstring module, point 3) ; toute autre valeur de
    rate.type (NOT_APPLICABLE, OUT_OF_SCOPE, ou autre) est ignorée.
    Historique : l'ancienne version de ce code faisait l'inverse (exclusion
    de 2 valeurs au lieu d'inclusion) — corrigé le 2026-09-13. Le filtre
    corrigé n'incluait alors que "DEFAULT"/"EXEMPTED" : correct pour le
    taux STANDARD (seul consommé à l'époque), mais rejetait à tort les
    entrées catégorie REDUCED réelles dont rate.type vaut "REDUCED_RATE"
    ou "SUPER_REDUCED_RATE" côté TEDB — élargi le 2026-09-16 en même temps
    que la réactivation de l'extraction REDUCED ci-dessus.

    Cas STANDARD multiple (incident du 2026-09-12, confirmé sur donnée
    réelle ES du 2026-01-01) : TEDB peut renvoyer PLUSIEURS entrées
    type=STANDARD pour un même (pays, date) lorsqu'un territoire spécial
    a un régime distinct (ex. Canaries pour l'Espagne, hors TVA UE,
    identifiable uniquement via un texte libre non structuré dans
    <comment> — donc pas exploitable de façon fiable pour distinguer les
    cas automatiquement). Si les valeurs STANDARD distinctes trouvées
    diffèrent, le résultat est jugé AMBIGU : aucune valeur STANDARD n'est
    retournée (repli automatique sur rates.py côté appelant), et un
    warning explicite est loggé avec le détail de chaque candidat pour
    permettre une revue manuelle. Comportement volontairement conservateur
    tant qu'aucune règle de désambiguïsation par territoire n'a été
    validée avec le cabinet comptable.
    """
    tedb_to_category = {v: k for k, v in _CATEGORY_TO_TEDB.items()}
    result: dict[str, Decimal] = {}
    standard_candidates: list[tuple[Decimal, Optional[str]]] = []

    for elem in root.iter():
        if _local_tag(elem.tag) != "vatRateResults":
            continue

        vtype: Optional[str] = None
        rtype: Optional[str] = None
        rvalue: Optional[str] = None
        cat_id: Optional[str] = None
        comment: Optional[str] = None

        for child in elem:
            tag = _local_tag(child.tag)
            if tag == "type":
                vtype = (child.text or "").strip().upper()
                if not _PARSE_REDUCED_CATEGORIES and vtype != "STANDARD":
                    # Court-circuit : on ne s'interesse a rien d'autre que
                    # STANDARD pour l'instant, inutile de lire rate/category.
                    break
            elif tag == "rate":
                for rc in child:
                    rtag = _local_tag(rc.tag)
                    if rtag == "type":
                        rtype = (rc.text or "").strip().upper()
                    elif rtag == "value":
                        rvalue = rc.text
            elif tag == "category":
                for cc in child:
                    if _local_tag(cc.tag) == "identifier":
                        cat_id = (cc.text or "").strip().upper()
            elif tag == "comment":
                comment = (child.text or "").strip() or None

        if vtype != "STANDARD" and not _PARSE_REDUCED_CATEGORIES:
            continue
        if rtype not in ("DEFAULT", "REDUCED_RATE", "SUPER_REDUCED_RATE", "EXEMPTED") or rvalue is None:
            continue
        try:
            value = Decimal(str(rvalue).strip())
        except Exception:
            continue

        if vtype == "STANDARD":
            standard_candidates.append((value, comment))
        elif vtype == "REDUCED" and cat_id in tedb_to_category:
            internal_category = tedb_to_category[cat_id]
            # Ne garder que si ce (pays, catégorie) est réellement éligible
            # (safe-list) — sinon on répète exactement le problème du
            # 2026-09-13 (warnings de plausibilité + dump XML complet pour
            # des taux jamais consommés), simplement décalé des catégories
            # non mappées vers les catégories mappées mais ambiguës pour ce
            # pays (ex. MEDICINES pour FR).
            if _is_tedb_eligible(country, internal_category):
                result.setdefault(internal_category, value)

    if standard_candidates:
        distinct_values = {v for v, _ in standard_candidates}
        if len(distinct_values) == 1:
            result["STANDARD"] = standard_candidates[0][0]
        else:
            # DESAMBIGUÏSATION (2026-09-15) : cas typique ES (Continent 21% vs Canaries 7%).
            # Si l'un des candidats correspond exactement à notre référence statique,
            # on le privilégie au lieu de rejeter tout le bloc.
            reference = _STATIC_STANDARD_RATES.get(country)
            matches = [v for v, _ in standard_candidates if reference is not None and v == reference]
            if len(matches) == 1:
                result["STANDARD"] = matches[0]
                logger.debug("[VAT_RATES] TEDB : ambiguïté résolue pour %s (correspondance statique %s%%).",
                             country, matches[0])
            else:
                logger.warning(
                    "[VAT_RATES] TEDB : %d valeurs STANDARD distinctes et incompatibles reçues pour "
                    "%s au %s (probable territoire spécial, ex. régime IGIC Canaries pour "
                    "ES) — résultat jugé ambigu, repli statique. Candidats : %s",
                    len(distinct_values), country, target_date,
                    [f"{v}% ({c or 'sans commentaire'})" for v, c in standard_candidates],
                )

    return result


def _fetch_tedb_rates(country: str, target_date: date) -> Optional[tuple[dict[str, Decimal], bytes]]:
    tedb_iso = _ISO_TO_TEDB.get(country, country)
    result = _request_tedb(tedb_iso, target_date)
    if result is None:
        return None
    root, raw = result
    return _parse_tedb_response(root, country=country, target_date=target_date), raw


# ------------------------------------------------------------------
# Coupe-circuit (2026-09-12, suite incident) : un taux ES/STANDARD erroné
# (~7% au lieu de 21%) a été observé en production. Tant que la cause
# exacte n'est pas confirmée sur une réponse XML réelle, la voie dynamique
# est DÉSACTIVÉE PAR DÉFAUT (repli 100% statique rates.py, comportement
# identique à avant le 12/09). Réactivation explicite via la variable
# d'environnement/secret VAT_DYNAMIC_TEDB_ENABLED=true.
# ------------------------------------------------------------------
def _dynamic_tedb_enabled() -> bool:
    """Détermine si la TVA dynamique (TEDB) est active.

    BASCULE 2026-09-15 (demande utilisateur) : Activée par DÉFAUT pour
    pallier les problèmes de lecture de secrets en production. La sécurité
    repose désormais sur le garde-fou de plausibilité (écart max toléré)
    plutôt que sur une activation manuelle.
    """
    raw = get_secret("VAT_DYNAMIC_TEDB_ENABLED")

    # Si absent, on cherche une variante de casse
    if raw is None:
        try:
            import streamlit as st
            if st is not None:
                for k in st.secrets.keys():
                    if k.upper() == "VAT_DYNAMIC_TEDB_ENABLED":
                        raw = st.secrets.get(k)
                        break
        except Exception:
            pass

    # Si toujours absent (None), on active par défaut
    if raw is None:
        return True

    # Si présent, on ne désactive que si explicitement demandé
    if isinstance(raw, bool):
        return raw
    val = str(raw or "").strip().lower()
    return val not in ("0", "false", "no", "off")


# Écart maximal toléré (en points de %) entre un taux STANDARD renvoyé par
# TEDB et le taux statique connu (rates.py) avant de considérer la réponse
# TEDB comme suspecte et de la rejeter au profit du statique. Les taux
# standards de l'UE ne varient jamais de plus de quelques points d'une
# année sur l'autre ; un écart plus important trahit presque à coup sûr un
# bug de parsing plutôt qu'un vrai changement légal.
_PLAUSIBILITY_MAX_DEVIATION = Decimal("3")


def _is_plausible(country: str, rate_type: str, value: Decimal) -> bool:
    """Compare une valeur TEDB à la référence statique connue. Retourne True
    si aucune référence statique n'existe (rien à comparer, pas de doute
    élevé) ou si l'écart est dans la tolérance."""
    if rate_type == "STANDARD":
        reference = _STATIC_STANDARD_RATES.get(country)
    else:
        reference = _STATIC_REDUCED_RATES.get(country, {}).get(rate_type)
    if reference is None:
        return True
    return abs(value - reference) <= _PLAUSIBILITY_MAX_DEVIATION



def _is_tedb_eligible(country: str, rate_type: str) -> bool:
    """STANDARD reste éligible sur tout _TEDB_SUPPORTED, comme depuis le
    2026-09-13. Les catégories REDUCED (_CATEGORY_TO_TEDB) sont éligibles
    UNIQUEMENT sur la safe-list par catégorie (_TEDB_CATEGORY_SAFE_COUNTRIES,
    2026-09-16 — fin de la restriction STANDARD-only) : un pays absent de
    la safe-list d'une catégorie n'est jamais interrogé en dynamique pour
    cette catégorie, même s'il est dans _TEDB_SUPPORTED pour STANDARD —
    retombe sur rates.py::REDUCED_VAT_RATES (repli statique déjà en
    production), jamais de sous-déclaration."""
    if not _dynamic_tedb_enabled():
        return False
    tedb_iso = _ISO_TO_TEDB.get(country, country)
    if rate_type == "STANDARD":
        return tedb_iso in _TEDB_SUPPORTED
    safe_countries = _TEDB_CATEGORY_SAFE_COUNTRIES.get(rate_type)
    if safe_countries is None:
        return False
    return tedb_iso in safe_countries


def _process_fetch_result(
    country: str,
    situation_date: date,
    result: Optional[tuple[dict[str, Decimal], bytes]],
    requested_rate_type: str = "STANDARD",
) -> dict[str, Decimal]:
    """Traite le résultat brut d'un fetch TEDB (filtre de plausibilité,
    warning + log XML sur rejet, écriture cache L1+L2).

    requested_rate_type : permet de persister le fallback statique en base
    si TEDB a répondu mais n'a pas fourni ce taux (ex: ambiguïté rejetée),
    évitant ainsi de re-fetcher par le réseau au prochain appel (performance).
    """
    if result is None:
        _mark_failed(country, situation_date)
        return {}

    raw_fetched, raw_xml = result
    fetched: dict[str, Decimal] = {}
    for rt, val in raw_fetched.items():
        if _is_plausible(country, rt, val):
            fetched[rt] = val
        else:
            reference = (
                _STATIC_STANDARD_RATES.get(country) if rt == "STANDARD"
                else _STATIC_REDUCED_RATES.get(country, {}).get(rt)
            )
            logger.warning(
                "[VAT_RATES] TEDB : taux %s/%s au mois %s = %s%% rejeté (écart > %s points vs "
                "référence statique %s%%) — repli statique. Réponse XML brute "
                "ci-dessous pour diagnostic :\n%s",
                country, rt, situation_date, val, _PLAUSIBILITY_MAX_DEVIATION,
                reference, raw_xml.decode("utf-8", errors="replace"),
            )

    # PERSISTANCE DU FALLBACK (2026-09-15) : si le taux demandé n'a pas pu être
    # extrait (ambiguïté non résolue ou rejet), on enregistre le taux statique
    # en base pour que les appels suivants soient instantanés (L2).
    if requested_rate_type not in fetched:
        fallback = _static_vat_rate_at_date(country, situation_date, requested_rate_type)
        fetched[requested_rate_type] = fallback

    if fetched:
        entries = [(country, rt, situation_date, val) for rt, val in fetched.items()]
        _db_upsert_batch(entries)
        with _cache_lock:
            for rt, val in fetched.items():
                _vat_memory_cache[_cache_key(country, rt, situation_date)] = val
        for rt, val in fetched.items():
            _record_history_entry(country, rt, situation_date, val)
    return fetched


def get_vat_rate(country: str, rate_type: str, target_date: date) -> Decimal:
    """Retourne le taux de TVA applicable pour un pays, un type de taux et une date.

    Ordre de résolution :
      1. Cache L1 RAM (accès immédiat)
      2. Cache L2 Postgres (Supabase)
      3. API TEDB (Commission européenne) — un seul appel par (pays, MOIS),
         qui alimente le cache pour TOUTES les catégories mappées d'un coup
      4. Fallback statique local (rates.py) — utilisé aussi immédiatement,
         sans aucun appel réseau, si le (pays, catégorie) n'est pas
         couvert par TEDB (cf. _is_tedb_eligible).

    Granularité MENSUELLE côté TEDB (2026-09-13, demande Matthieu) : un
    taux de TVA standard en UE ne change jamais en cours de mois (mise en
    application légale systématiquement au 1er du mois ou au 1er janvier)
    — hypothèse fiscale à confirmer explicitement avec le cabinet
    comptable si un contre-exemple historique était identifié, mais
    l'implémentation reste sûre par construction : TOUTE date de
    transaction est normalisée au 1er du mois AVANT interrogation TEDB et
    AVANT construction de la clé de cache. La valeur mise en cache est
    donc explicitement "le taux en vigueur au 1er du mois", jamais "la
    valeur vue par hasard au premier jour interrogé". Un seul appel réseau
    par (pays, mois) au lieu d'un par (pays, jour) — gain direct sur le
    volume de requêtes ET sur le volume de logs.

    Le repli statique (`rates.py`) continue d'utiliser la date EXACTE de
    la transaction (pas la date normalisée) : son mécanisme d'historique
    par date n'a pas besoin de cette optimisation et on ne veut rien
    changer à son comportement existant.

    NOTE PERFORMANCE (2026-09-13) : dans un traitement en masse (moteur de
    calcul, voir engine.py::_run_oss_loop), appeler cette fonction ligne à
    ligne signifie que les premiers ratés de cache (un par nouveau couple
    pays/mois rencontré) bloquent la boucle sur un aller-retour réseau
    synchrone — invisible pour l'utilisateur tant que le prochain "tick" de
    progression n'est pas atteint. Voir prefetch_standard_rates() pour
    charger TOUS les couples (pays, mois) nécessaires en une seule passe
    parallélisée AVANT de lancer la boucle ligne à ligne, avec sa propre
    progression affichable.
    """
    country = country.upper()
    rate_type = rate_type.upper()
    situation_date = target_date  # granularité journalière pour supporter les changements mi-mois
    key = _cache_key(country, rate_type, situation_date)

    with _cache_lock:
        if key in _vat_memory_cache:
            rate = _vat_memory_cache[key]
            logger.debug("[VAT_RATES] source=L1_RAM %s/%s/%s -> %s%%",
                         country, rate_type, target_date, rate)
            return rate

    if not _is_tedb_eligible(country, rate_type):
        rate = _static_vat_rate_at_date(country, target_date, rate_type)
        # Diagnostic plus précis sur la raison du repli
        if not _dynamic_tedb_enabled():
            reason = "non activé via VAT_DYNAMIC_TEDB_ENABLED"
        elif rate_type != "STANDARD":
            reason = f"catégorie {rate_type} non éligible (seul STANDARD l'est)"
        else:
            reason = f"pays {country} non supporté par TEDB"

        logger.debug("[VAT_RATES] source=STATIC_FALLBACK (%s) %s/%s/%s -> %s%%",
                     reason, country, rate_type, target_date, rate)
        with _cache_lock:
            _vat_memory_cache[key] = rate
        return rate

    if _is_permanently_failed(country, situation_date):
        # Panne déjà constatée pour ce (pays, date) il y a moins de
        # _FAILED_PAIR_TTL_SECONDS : inutile de vérifier le cache L2
        # (aucune écriture n'a pu s'y produire depuis ce constat d'échec —
        # voir _process_fetch_result, _mark_failed n'est déclenché que
        # lorsque le fetch réseau échoue totalement, donc rien de nouveau
        # à lire côté Postgres). Sans ce court-circuit, chaque ligne d'un
        # gros fichier referait un aller-retour Postgres pour rien tant
        # que la panne dure (audit 2026-09-13 (6)).
        rate = _static_vat_rate_at_date(country, target_date, rate_type)
        logger.debug("[VAT_RATES] source=STATIC_FALLBACK (TEDB indisponible, nouvel essai après %ds) %s/%s/%s -> %s%%",
                     _FAILED_PAIR_TTL_SECONDS, country, rate_type, target_date, rate)
        return rate

    cached = _db_get_rate(country, rate_type, situation_date)
    if cached is not None:
        logger.debug("[VAT_RATES] source=L2_POSTGRES %s/%s/%s -> %s%%",
                      country, rate_type, target_date, cached)
        with _cache_lock:
            _vat_memory_cache[key] = cached
        return cached

    result = _fetch_tedb_rates(country, situation_date)
    fetched = _process_fetch_result(country, situation_date, result, requested_rate_type=rate_type)
    if rate_type in fetched:
        logger.debug("[VAT_RATES] source=TEDB_FETCH %s/%s/%s -> %s%%",
                     country, rate_type, target_date, fetched[rate_type])
        return fetched[rate_type]
    if result is not None:
        # Réponse TEDB obtenue mais catégorie absente/rejetée (cas
        # ambigu, plausibilité, ou pays sans ce taux réduit) : fait
        # fiscal stable pour ce (pays, mois) — sûr de mettre en cache
        # L1, aucune raison qu'un nouvel appel donne un résultat
        # différent dans la même session.
        logger.debug(
            "[VAT_RATES] TEDB : réponse reçue pour %s au mois %s mais catégorie '%s' absente ou "
            "rejetée (pays sans taux réduit de ce type, ou anomalie) — repli statique.",
            country, situation_date, rate_type,
        )
        rate = _static_vat_rate_at_date(country, target_date, rate_type)
        logger.debug("[VAT_RATES] source=STATIC_FALLBACK (TEDB répondu, catégorie rejetée) %s/%s/%s -> %s%%",
                     country, rate_type, target_date, rate)
        with _cache_lock:
            _vat_memory_cache[key] = rate
        return rate

    # result is None : l'appel réseau vient d'échouer (voir
    # _process_fetch_result -> _mark_failed). Volontairement PAS mis en
    # cache L1 : le cache L1 n'a pas de TTL, donc le mettre en cache ici
    # figerait silencieusement le taux sur le statique pour tout le reste
    # du process, même une fois TEDB de nouveau joignable — annulant de
    # facto le mécanisme de réessai après _FAILED_PAIR_TTL_SECONDS. Bug
    # identifié lors de l'audit du 2026-09-13 (6) : avant ce correctif,
    # une simple coupure réseau transitoire figeait le taux sur le
    # statique jusqu'au redémarrage du process.
    rate = _static_vat_rate_at_date(country, target_date, rate_type)
    logger.debug("[VAT_RATES] source=STATIC_FALLBACK (TEDB indisponible, nouvel essai après %ds) %s/%s/%s -> %s%%",
                 _FAILED_PAIR_TTL_SECONDS, country, rate_type, target_date, rate)
    return rate


def prefetch_standard_rates(
    pairs,
    *,
    max_workers: int = 8,
    progress_callback=None,
) -> None:
    """Précharge en une seule passe le taux STANDARD pour tous les couples
    (pays, date de transaction) fournis — pensé pour être appelé UNE FOIS
    avant une boucle de calcul en masse (voir engine.py), plutôt que de
    laisser chaque ligne déclencher son propre aller-retour réseau au fil
    de l'eau (voir note de performance dans get_vat_rate()).

    Args:
        pairs: itérable de (country: str, target_date: date). Chaque date
            est normalisée au 1er du mois (même granularité que
            get_vat_rate) — peu importe le jour exact fourni ici, seul le
            couple (pays, mois) compte. Un simple SUPERSET des couples
            réellement nécessaires est parfaitement sûr à passer ici : les
            couples non éligibles TEDB (_is_tedb_eligible) ou déjà en
            cache sont ignorés quasi gratuitement, sans appel réseau.
        max_workers: nombre de requêtes SOAP TEDB menées en parallèle.
            Threads de courte durée, aucune connexion/pool persistant —
            même contrainte scale-to-zero que
            vies_engine.validate_vat_numbers_parallel, dont ce mécanisme
            s'inspire directement (réseau UNIQUEMENT dans les threads
            workers, aucun accès Postgres depuis un thread — les écritures
            cache L1/L2 sont faites séquentiellement dans le thread
            appelant après collecte de tous les résultats réseau).
        progress_callback: optionnel, callable(done: int, total: int),
            appelé après chaque couple (pays, mois) traité (cache hit
            immédiat ou fin de fetch réseau) — total = nombre de couples
            UNIQUES et éligibles à traiter, pas le nombre de lignes de
            vente d'origine (peut donc atteindre 100% bien avant que la
            boucle de calcul elle-même n'affiche sa propre progression).
    """
    # Normalisation + déduplication (pays, mois) — potentiellement des
    # dizaines de milliers de lignes en entrée pour une poignée de couples
    # distincts en sortie (un fichier Amazon typique couvre peu de pays et
    # peu de mois à la fois).
    normalized: set[tuple[str, date]] = set()
    for country, d in pairs:
        c = (country or "").upper()
        if not c:
            continue
        normalized.add((c, d))

    total = len(normalized)
    done = 0

    def _tick() -> None:
        nonlocal done
        done += 1
        if progress_callback is not None:
            try:
                progress_callback(done, total)
            except Exception:
                # Même posture que _run_oss_loop / validate_vat_numbers_parallel :
                # un callback défaillant ne doit jamais faire échouer le calcul.
                pass

    # Signal immédiat (0/total) AVANT même de commencer la pré-vérification
    # ci-dessous : sans ça, le texte de progression affiché à l'écran reste
    # celui de l'étape précédente (ex. "VIES : 532/532 vérifiés") pendant
    # toute la durée de cette boucle — plusieurs secondes, un aller-retour
    # Postgres par pays — car aucun tick n'était émis avant sa fin complète
    # (diagnostic 2026-09-14). Un total de 0 (rien à précharger) ne déclenche
    # rien : le callable côté UI ignore déjà total<=0.
    if progress_callback is not None and total > 0:
        try:
            progress_callback(0, total)
        except Exception:
            pass

    to_fetch: list[tuple[str, date]] = []
    for country, situation_date in normalized:
        if not _is_tedb_eligible(country, "STANDARD"):
            _tick()
            continue
        key = _cache_key(country, "STANDARD", situation_date)
        with _cache_lock:
            if key in _vat_memory_cache:
                _tick()
                continue
        if _is_permanently_failed(country, situation_date):
            _tick()
            continue
        if _db_get_rate(country, "STANDARD", situation_date) is not None:
            # Déjà en L2 (ou résolu en mémoire via l'historique pays, voir
            # _load_country_history) : get_vat_rate() le retrouvera
            # directement sans repasser par le réseau — pas la peine de le
            # committer nous-mêmes ici, juste de le compter comme "traité"
            # pour la barre de progression.
            _tick()
            continue
        to_fetch.append((country, situation_date))

    if not to_fetch:
        return

    if len(to_fetch) == 1:
        country, situation_date = to_fetch[0]
        result = _fetch_tedb_rates(country, situation_date)
        _process_fetch_result(country, situation_date, result)
        _tick()
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as executor:
        futures = {
            executor.submit(_fetch_tedb_rates, country, situation_date): (country, situation_date)
            for country, situation_date in to_fetch
        }
        for future in as_completed(futures):
            country, situation_date = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.warning(
                    "[VAT_RATES] prefetch_standard_rates : échec inattendu pour %s/%s : %s",
                    country, situation_date, exc,
                )
                result = None
            _process_fetch_result(country, situation_date, result, requested_rate_type="STANDARD")
            _tick()


@lru_cache(maxsize=20_000)
def vat_rate(
    country: str,
    product_category: str = "STANDARD",
    tx_date: Optional[date] = None,
) -> Decimal:
    """Remplacement direct de rates.vat_rate() (même signature) : source
    dynamique (TEDB) quand couverte, repli statique (rates.py) sinon.

    Args:
        country: code ISO 3166-1 alpha-2 (ex: "EE", "FR").
        product_category: catégorie produit ("STANDARD", "BOOKS", "FOOD", ...).
        tx_date: date de la transaction. Si None, utilise la date du jour.

    Raises:
        KeyError: si le pays est inconnu (délégué au repli statique).
    """
    code = (country or "").strip().upper()
    cat = (product_category or "").strip().upper()
    # Normalisation FR/EN identique à rates.vat_rate_at_date (dupliquée
    # volontairement ici : get_vat_rate() a besoin de la catégorie déjà
    # normalisée AVANT de décider de l'éligibilité TEDB).
    if cat in ("LIVRES", "BOOKS"):
        cat = "BOOKS"
    elif cat in ("ALIMENTATION", "FOOD"):
        cat = "FOOD"
    elif cat in ("MEDICAMENTS", "MEDICINES"):
        cat = "MEDICINES"
    elif cat in ("VETEMENTS", "CLOTHING"):
        cat = "CLOTHING"

    d = tx_date if tx_date is not None else date.today()
    return get_vat_rate(code, cat, d)


def clear_cache(persistent: bool = True) -> None:
    """Vide le cache mémoire (L1) et, par défaut, le cache Postgres (L2)."""
    with _cache_lock:
        _vat_memory_cache.clear()
        _country_history_cache.clear()
        _country_history_loaded.clear()
    vat_rate.cache_clear()
    _failed_pairs.clear()
    if not persistent:
        return
    pool = _get_pool()
    if pool is None:
        return
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM vat_rate_cache")
    except Exception as exc:
        logger.warning("[VAT_RATES] Impossible de vider le cache Postgres : %s", exc)
    finally:
        pool.putconn(conn)


def cache_info() -> dict:
    """Infos sur l'état du cache (debug/UI)."""
    info: dict = {
        "memory_entries": len(_vat_memory_cache),
        "db_configured": not _db_unavailable,
        "db_entries": None,
    }
    pool = _get_pool()
    if pool is None:
        return info
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vat_rate_cache")
            info["db_entries"] = cur.fetchone()[0]
    except Exception as exc:
        logger.warning("[VAT_RATES] lecture des stats Postgres échouée : %s", exc)
    finally:
        pool.putconn(conn)
    return info
