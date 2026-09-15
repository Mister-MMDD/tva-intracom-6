#!/usr/bin/env python3
"""Script de diagnostic détaillé pour comprendre la différence de comportement.

But : comprendre pourquoi la table se crée correctement dans votre test
mais pas dans le système normal, alors que le code est le même.
"""

import sys
import logging
from datetime import date

# Configuration du logging pour voir TOUT
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from tva_intracom.config import get_secret
    from tva_intracom.database import get_shared_pool
    from tva_intracom import vat_rates_db
except ImportError as e:
    logger.error(f"Erreur d'import : {e}")
    sys.exit(1)


def check_initial_state():
    """Vérifier l'état AVANT toute opération."""
    logger.info("=" * 70)
    logger.info("ÉTAT INITIAL (avant toute opération)")
    logger.info("=" * 70)
    
    # Flags internes
    logger.info(f"_schema_ready = {vat_rates_db._schema_ready}")
    logger.info(f"_db_unavailable = {vat_rates_db._db_unavailable}")
    
    # Secrets
    vat_dynamic = get_secret("VAT_DYNAMIC_TEDB_ENABLED")
    logger.info(f"VAT_DYNAMIC_TEDB_ENABLED = {vat_dynamic!r}")
    
    supabase_url = get_secret("SUPABASE_DB_URL")
    logger.info(f"SUPABASE_DB_URL configuré : {supabase_url is not None}")
    
    # Table
    supabase_url = get_secret("SUPABASE_DB_URL")
    if supabase_url:
        try:
            pool = get_shared_pool(supabase_url)
            conn = pool.getconn()
            with conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'vat_rate_cache'
                    )
                """)
                table_exists = cur.fetchone()[0]
                logger.info(f"Table vat_rate_cache existe : {table_exists}")
                
                if table_exists:
                    cur.execute("SELECT COUNT(*) FROM vat_rate_cache")
                    count = cur.fetchone()[0]
                    logger.info(f"Nombre de lignes : {count}")
            
            pool.putconn(conn)
        except Exception as e:
            logger.error(f"Erreur vérification table : {e}", exc_info=True)


def test_deletion_and_recreation():
    """Tester explicitement la suppression et recréation."""
    logger.info("=" * 70)
    logger.info("TEST : Suppression et recréation de table")
    logger.info("=" * 70)
    
    supabase_url = get_secret("SUPABASE_DB_URL")
    if not supabase_url:
        logger.error("SUPABASE_DB_URL non configuré - test impossible")
        return
    
    try:
        pool = get_shared_pool(supabase_url)
        conn = pool.getconn()
        
        # Étape 1 : Supprimer la table si elle existe
        logger.info("ÉTAPE 1 : Suppression de la table...")
        with conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS vat_rate_cache")
            logger.info("✅ Table supprimée")
        
        # Étape 2 : Vérifier qu'elle n'existe plus
        logger.info("ÉTAPE 2 : Vérification suppression...")
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'vat_rate_cache'
                )
            """)
            table_exists = cur.fetchone()[0]
            logger.info(f"Table existe après suppression : {table_exists}")
        
        # Étape 3 : Forcer la réinitialisation des flags
        logger.info("ÉTAPE 3 : Réinitialisation des flags internes...")
        vat_rates_db._schema_ready = False
        vat_rates_db._db_unavailable = False
        logger.info(f"_schema_ready = {vat_rates_db._schema_ready}")
        logger.info(f"_db_unavailable = {vat_rates_db._db_unavailable}")
        
        # Étape 4 : Appeler _init_schema explicitement
        logger.info("ÉTAPE 4 : Appel explicite de _init_schema()...")
        vat_rates_db._init_schema(pool)
        logger.info("✅ _init_schema() terminé")
        
        # Étape 5 : Vérifier que la table existe maintenant
        logger.info("ÉTAPE 5 : Vérification création...")
        with conn, conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'vat_rate_cache'
                )
            """)
            table_exists = cur.fetchone()[0]
            logger.info(f"Table existe après création : {table_exists}")
            
            if table_exists:
                cur.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = 'vat_rate_cache'
                    ORDER BY ordinal_position
                """)
                columns = cur.fetchall()
                logger.info(f"Colonnes ({len(columns)}) :")
                for col_name, data_type in columns:
                    logger.info(f"  - {col_name} : {data_type}")
        
        pool.putconn(conn)
        
        # Étape 6 : Tester un appel get_vat_rate
        logger.info("ÉTAPE 6 : Test d'appel get_vat_rate()...")
        rate = vat_rates_db.get_vat_rate("FR", "STANDARD", date.today())
        logger.info(f"Taux obtenu : {rate}%")
        
        # Étape 7 : Vérifier les flags après tout
        logger.info("ÉTAPE 7 : Vérification flags finaux...")
        logger.info(f"_schema_ready = {vat_rates_db._schema_ready}")
        logger.info(f"_db_unavailable = {vat_rates_db._db_unavailable}")
        
    except Exception as e:
        logger.error(f"Erreur lors du test : {e}", exc_info=True)


def test_normal_flow_without_deletion():
    """Tester le flux normal SANS suppression de table."""
    logger.info("=" * 70)
    logger.info("TEST : Flux normal (sans suppression)")
    logger.info("=" * 70)
    
    # Réinitialiser les flags pour simuler un démarrage frais
    vat_rates_db._schema_ready = False
    vat_rates_db._db_unavailable = False
    vat_rates_db.clear_cache(persistent=False)
    
    logger.info("Flags réinitialisés pour simuler démarrage frais")
    logger.info(f"_schema_ready = {vat_rates_db._schema_ready}")
    logger.info(f"_db_unavailable = {vat_rates_db._db_unavailable}")
    
    # Appeler get_vat_rate normalement
    logger.info("Appel get_vat_rate() sans suppression préalable...")
    rate = vat_rates_db.get_vat_rate("FR", "STANDARD", date.today())
    logger.info(f"Taux obtenu : {rate}%")
    
    # Vérifier les flags après
    logger.info(f"_schema_ready après appel : {vat_rates_db._schema_ready}")
    logger.info(f"_db_unavailable après appel : {vat_rates_db._db_unavailable}")


def check_tedb_network_call():
    """Vérifier si l'appel TEDB réseau est tenté."""
    logger.info("=" * 70)
    logger.info("TEST : Vérification appel réseau TEDB")
    logger.info("=" * 70)
    
    # Vider le cache pour forcer un appel
    vat_rates_db.clear_cache(persistent=False)
    
    # Essayer un appel pour un pays qui n'est probablement pas dans le cache
    logger.info("Appel get_vat_rate() pour un pays probablement pas en cache...")
    
    # On ne peut pas facilement mock pour voir si l'appel réseau est fait
    # Mais on peut observer les logs pour voir la source
    
    rate = vat_rates_db.get_vat_rate("AT", "STANDARD", date(2026, 1, 1))
    logger.info(f"Taux obtenu : {rate}%")
    
    # Vérifier cache_info
    cache_info = vat_rates_db.cache_info()
    logger.info(f"Cache info : {cache_info}")


def main():
    """Fonction principale."""
    logger.info("DÉBUT DU DIAGNOSTIC DÉTAILLÉ")
    logger.info("")
    
    try:
        check_initial_state()
        logger.info("")
        
        test_normal_flow_without_deletion()
        logger.info("")
        
        test_deletion_and_recreation()
        logger.info("")
        
        check_tedb_network_call()
        logger.info("")
        
        logger.info("=" * 70)
        logger.info("FIN DU DIAGNOSTIC DÉTAILLÉ")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"❌ DIAGNOSTIC FAILED : {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()