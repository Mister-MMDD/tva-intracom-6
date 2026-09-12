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

import psycopg2
import psycopg2.extras

from .config import get_secret
from .database import NonPoolingConnectionPool, get_shared_pool, close_idle_connections as _database_close_idle
from .rates import vat_rate_at_date as _static_vat_rate_at_date

logger = logging.getLogger(__name__)

TEDB_ENDPOINT = "https://ec.europa.eu/taxation_customs/tedb/ws/VatRetrievalService"
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
#     CLOTHING_REPAIR = réparation existe, hors sujet).
#   - SUPER_REDUCED : regroupement interne à ce projet (palier de taux),
#     pas une catégorie TEDB (qui catégorise par nature de bien/service).
_CATEGORY_TO_TEDB: dict[str, str] = {
    "FOOD": "FOODSTUFFS",
    "MEDICINES": "PHARMACEUTICAL_PRODUCTS",  # à valider cabinet, voir docstring module
    "PARKING": "PARKING",
}

# ------------------------------------------------------------------
# Cache L1 (mémoire/process) + Verrou de thread
# ------------------------------------------------------------------
# Clé : "COUNTRY|RATE_TYPE|YYYY-MM-DD" -> Decimal(rate)
_vat_memory_cache: dict[str, Decimal] = {}
_cache_lock = threading.Lock()

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
        logger.debug("SUPABASE_DB_URL non défini — TVA dynamique en cache mémoire / statique uniquement.")
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
        logger.warning("Cache TVA dynamique : Postgres indisponible (%s) — repli statique.", exc)
        _db_unavailable = True
        return None
    return pool


def close_idle_connections() -> None:
    """Libère les connexions inactives au début de chaque run (appelé par app.py)."""
    _database_close_idle()


def _init_schema(pool: NonPoolingConnectionPool) -> None:
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
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


def _db_get_rate(country: str, rate_type: str, target_date: date) -> Optional[Decimal]:
    pool = _get_pool()
    if pool is None:
        return None
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT rate FROM vat_rate_cache
                 WHERE country_code = %s AND rate_type = %s AND situation_date = %s
                """,
                (country, rate_type, target_date),
            )
            row = cur.fetchone()
            return Decimal(str(row[0])) if row else None
    except Exception as exc:
        logger.warning("Cache TVA dynamique : lecture Postgres échouée pour %s/%s/%s : %s",
                        country, rate_type, target_date, exc)
        return None
    finally:
        pool.putconn(conn)


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
        logger.warning("Cache TVA dynamique : écriture Postgres échouée (%d entrées) : %s", len(entries), exc)
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


def _request_tedb(tedb_iso: str, target_date: date) -> Optional[ET.Element]:
    description = f"{tedb_iso} au {target_date}"
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            return ET.fromstring(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            if _is_permanent_ssl_error(exc):
                logger.warning(
                    "TEDB API : certificat SSL non vérifiable (%s) — "
                    "aucune nouvelle tentative pour cette requête : %s",
                    description, exc,
                )
                return None
            is_last_attempt = attempt >= _FETCH_MAX_ATTEMPTS
            if is_last_attempt:
                logger.warning("TEDB API indisponible (%s) après %d tentative(s) : %s",
                                description, attempt, exc)
                return None
            delay = _FETCH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.debug("TEDB API échec (%s, tentative %d/%d) : %s — retry dans %.0fs",
                         description, attempt, _FETCH_MAX_ATTEMPTS, exc, delay)
            time.sleep(delay)
        except ET.ParseError as exc:
            logger.warning("Réponse TEDB non parsable (%s) : %s", description, exc)
            return None
    return None


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_tedb_response(root: ET.Element) -> dict[str, Decimal]:
    """Extrait {catégorie interne: taux} depuis une réponse retrieveVatRatesRespMsg.

    Ne garde que :
      - le taux STANDARD (type == "STANDARD"),
      - les taux REDUCED dont la catégorie TEDB est dans _CATEGORY_TO_TEDB.
    Ignore les entrées rate.type == NOT_APPLICABLE/OUT_OF_SCOPE ou sans
    valeur (cf. docstring module, point 3).
    """
    tedb_to_category = {v: k for k, v in _CATEGORY_TO_TEDB.items()}
    result: dict[str, Decimal] = {}

    for elem in root.iter():
        if _local_tag(elem.tag) != "vatRateResults":
            continue

        vtype: Optional[str] = None
        rtype: Optional[str] = None
        rvalue: Optional[str] = None
        cat_id: Optional[str] = None

        for child in elem:
            tag = _local_tag(child.tag)
            if tag == "type":
                vtype = (child.text or "").strip().upper()
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

        if rtype in ("NOT_APPLICABLE", "OUT_OF_SCOPE") or rvalue is None:
            continue
        try:
            value = Decimal(str(rvalue).strip())
        except Exception:
            continue

        if vtype == "STANDARD":
            result.setdefault("STANDARD", value)
        elif vtype == "REDUCED" and cat_id in tedb_to_category:
            result.setdefault(tedb_to_category[cat_id], value)

    return result


def _fetch_tedb_rates(country: str, target_date: date) -> Optional[dict[str, Decimal]]:
    tedb_iso = _ISO_TO_TEDB.get(country, country)
    root = _request_tedb(tedb_iso, target_date)
    if root is None:
        return None
    return _parse_tedb_response(root)


def _is_tedb_eligible(country: str, rate_type: str) -> bool:
    tedb_iso = _ISO_TO_TEDB.get(country, country)
    if tedb_iso not in _TEDB_SUPPORTED:
        return False
    if rate_type == "STANDARD":
        return True
    return rate_type in _CATEGORY_TO_TEDB


def get_vat_rate(country: str, rate_type: str, target_date: date) -> Decimal:
    """Retourne le taux de TVA applicable pour un pays, un type de taux et une date.

    Ordre de résolution :
      1. Cache L1 RAM (accès immédiat)
      2. Cache L2 Postgres (Supabase)
      3. API TEDB (Commission européenne) — un seul appel par (pays, date),
         qui alimente le cache pour TOUTES les catégories mappées d'un coup
      4. Fallback statique local (rates.py) — utilisé aussi immédiatement,
         sans aucun appel réseau, si le (pays, catégorie) n'est pas
         couvert par TEDB (cf. _is_tedb_eligible).
    """
    country = country.upper()
    rate_type = rate_type.upper()
    key = _cache_key(country, rate_type, target_date)

    with _cache_lock:
        if key in _vat_memory_cache:
            return _vat_memory_cache[key]

    if not _is_tedb_eligible(country, rate_type):
        rate = _static_vat_rate_at_date(country, target_date, rate_type)
        with _cache_lock:
            _vat_memory_cache[key] = rate
        return rate

    cached = _db_get_rate(country, rate_type, target_date)
    if cached is not None:
        with _cache_lock:
            _vat_memory_cache[key] = cached
        return cached

    if not _is_permanently_failed(country, target_date):
        fetched = _fetch_tedb_rates(country, target_date)
        if fetched:
            entries = [(country, rt, target_date, val) for rt, val in fetched.items()]
            _db_upsert_batch(entries)
            with _cache_lock:
                for rt, val in fetched.items():
                    _vat_memory_cache[_cache_key(country, rt, target_date)] = val
            if rate_type in fetched:
                return fetched[rate_type]
            logger.debug(
                "TEDB : réponse reçue pour %s au %s mais catégorie '%s' absente "
                "(pays sans taux réduit de ce type) — repli statique.",
                country, target_date, rate_type,
            )
        else:
            _mark_failed(country, target_date)

    rate = _static_vat_rate_at_date(country, target_date, rate_type)
    with _cache_lock:
        _vat_memory_cache[key] = rate
    return rate


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
    code = country.upper()
    cat = product_category.strip().upper()
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
        logger.warning("Impossible de vider le cache TVA dynamique Postgres : %s", exc)
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
        logger.warning("Cache TVA dynamique : lecture des stats Postgres échouée : %s", exc)
    finally:
        pool.putconn(conn)
    return info
