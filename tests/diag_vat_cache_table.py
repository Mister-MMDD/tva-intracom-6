#!/usr/bin/env python3
"""Script de diagnostic pour le problème de création de table vat_rate_cache.

But : identifier pourquoi la table n'est pas créée malgré VAT_DYNAMIC_TEDB_ENABLED=true
- Vérifier la configuration des secrets
- Tester la connexion à Supabase
- Tenter la création de table en isolation
- Logger toutes les erreurs en détail
"""

import sys
import logging
from datetime import date

# Configuration du logging pour voir tous les détails
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


def check_secrets():
    """Vérifier la configuration des secrets."""
    logger.info("=" * 70)
    logger.info("ÉTAPE 1 : Vérification des secrets")
    logger.info("=" * 70)
    
    vat_dynamic = get_secret("VAT_DYNAMIC_TEDB_ENABLED")
    logger.info(f"VAT_DYNAMIC_TEDB_ENABLED = {vat_dynamic!r}")
    
    supabase_url = get_secret("SUPABASE_DB_URL")
    if supabase_url:
        # Masquer le mot de passe dans les logs
        masked_url = supabase_url.split('@')[-1] if '@' in supabase_url else "***masked***"
        logger.info(f"SUPABASE_DB_URL configuré (hôte: {masked_url})")
    else:
        logger.warning("SUPABASE_DB_URL NON configuré - c'est probablement le problème !")
    
    return vat_dynamic, supabase_url


def test_connection(supabase_url):
    """Tester la connexion à Supabase."""
    logger.info("=" * 70)
    logger.info("ÉTAPE 2 : Test de connexion Supabase")
    logger.info("=" * 70)
    
    if not supabase_url:
        logger.error("Impossible de tester la connexion : SUPABASE_DB_URL absent")
        return None
    
    try:
        pool = get_shared_pool(supabase_url)
        logger.info("Pool de connexion créé avec succès")
        
        conn = pool.getconn()
        logger.info("Connexion obtenue avec succès")
        
        with conn, conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()
            logger.info(f"Version PostgreSQL : {version[0]}")
            
            cur.execute("SELECT current_database()")
            db_name = cur.fetchone()
            logger.info(f"Base de données : {db_name[0]}")
            
            cur.execute("SELECT current_schema()")
            schema = cur.fetchone()
            logger.info(f"Schéma courant : {schema[0]}")
        
        pool.putconn(conn)
        logger.info("Connexion testée avec succès")
        return pool
        
    except Exception as e:
        logger.error(f"Erreur de connexion : {e}", exc_info=True)
        return None


def check_existing_table(pool):
    """Vérifier si la table existe déjà et son schéma."""
    logger.info("=" * 70)
    logger.info("ÉTAPE 3 : Vérification table existante")
    logger.info("=" * 70)
    
    if not pool:
        logger.error("Pas de pool disponible pour vérifier la table")
        return
    
    try:
        conn = pool.getconn()
        with conn, conn.cursor() as cur:
            # Vérifier si la table existe
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'vat_rate_cache'
                )
            """)
            table_exists = cur.fetchone()[0]
            logger.info(f"Table 'vat_rate_cache' existe : {table_exists}")
            
            if table_exists:
                # Vérifier les colonnes
                cur.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'vat_rate_cache'
                    ORDER BY ordinal_position
                """)
                columns = cur.fetchall()
                logger.info(f"Colonnes trouvées ({len(columns)}) :")
                for col_name, data_type, is_nullable in columns:
                    logger.info(f"  - {col_name} : {data_type} (nullable: {is_nullable})")
                
                # Vérifier le nombre de lignes
                cur.execute("SELECT COUNT(*) FROM vat_rate_cache")
                count = cur.fetchone()[0]
                logger.info(f"Nombre de lignes dans la table : {count}")
        
        pool.putconn(conn)
        
    except Exception as e:
        logger.error(f"Erreur lors de la vérification de la table : {e}", exc_info=True)


def test_schema_creation(pool):
    """Tenter la création de schéma en isolation."""
    logger.info("=" * 70)
    logger.info("ÉTAPE 4 : Test de création de schéma")
    logger.info("=" * 70)
    
    if not pool:
        logger.error("Pas de pool disponible pour créer le schéma")
        return
    
    try:
        # Forcer la réinitialisation du flag pour tester
        vat_rates_db._schema_ready = False
        vat_rates_db._db_unavailable = False
        
        logger.info("Appel de _init_schema()...")
        vat_rates_db._init_schema(pool)
        logger.info("_init_schema() terminé avec succès")
        
        # Vérifier que la table existe maintenant
        check_existing_table(pool)
        
    except Exception as e:
        logger.error(f"Erreur lors de _init_schema() : {e}", exc_info=True)


def test_vat_rate_call():
    """Tenter un appel à get_vat_rate pour voir le chemin complet."""
    logger.info("=" * 70)
    logger.info("ÉTAPE 5 : Test d'appel get_vat_rate()")
    logger.info("=" * 70)
    
    try:
        # Test avec un cas simple
        rate = vat_rates_db.get_vat_rate("FR", "STANDARD", date.today())
        logger.info(f"Taux obtenu pour FR/STANDARD : {rate}%")
        
        # Vérifier les infos de cache
        cache_info = vat_rates_db.cache_info()
        logger.info(f"Infos cache : {cache_info}")
        
    except Exception as e:
        logger.error(f"Erreur lors de get_vat_rate() : {e}", exc_info=True)


def main():
    """Fonction principale."""
    logger.info("DÉBUT DU DIAGNOSTIC vat_rate_cache")
    logger.info("")
    
    # Étape 1 : Vérifier les secrets
    vat_dynamic, supabase_url = check_secrets()
    logger.info("")
    
    # Étape 2 : Tester la connexion
    pool = test_connection(supabase_url)
    logger.info("")
    
    # Étape 3 : Vérifier la table existante
    check_existing_table(pool)
    logger.info("")
    
    # Étape 4 : Tester la création de schéma
    test_schema_creation(pool)
    logger.info("")
    
    # Étape 5 : Tester un appel complet
    test_vat_rate_call()
    logger.info("")
    
    logger.info("=" * 70)
    logger.info("FIN DU DIAGNOSTIC")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()