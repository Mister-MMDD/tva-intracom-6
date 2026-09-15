#!/usr/bin/env python3
"""Script d'audit de performance du système TEDB.

Analyse les goulots d'étranglement et les performances :
- Cache L1 vs L2 vs TEDB
- Préchargement en lot
- Locks threading
- Scalabilité
"""

import sys
import logging
import time
from datetime import date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from tva_intracom import vat_rates_db
    from tva_intracom.config import get_secret
except ImportError as e:
    logger.error(f"Erreur d'import : {e}")
    sys.exit(1)


def benchmark_cache_levels():
    """Benchmark des différents niveaux de cache."""
    logger.info("=" * 70)
    logger.info("BENCHMARK 1 : Niveaux de cache")
    logger.info("=" * 70)
    
    # Vider le cache pour le test
    vat_rates_db.clear_cache(persistent=False)
    
    test_date = date(2026, 1, 15)
    iterations = 100
    
    # Test 1 : Cache froid (premier appel - L2 Postgres)
    start = time.time()
    for _ in range(iterations):
        vat_rates_db.get_vat_rate("FR", "STANDARD", test_date)
    elapsed_cold = time.time() - start
    logger.info(f"Cache froid (L2 Postgres) : {elapsed_cold:.3f}s pour {iterations} appels")
    logger.info(f"  -> {elapsed_cold/iterations*1000:.2f}ms par appel")
    
    # Test 2 : Cache chaud (L1 mémoire)
    start = time.time()
    for _ in range(iterations):
        vat_rates_db.get_vat_rate("FR", "STANDARD", test_date)
    elapsed_hot = time.time() - start
    logger.info(f"Cache chaud (L1 mémoire) : {elapsed_hot:.3f}s pour {iterations} appels")
    logger.info(f"  -> {elapsed_hot/iterations*1000:.2f}ms par appel")
    
    speedup = elapsed_cold / elapsed_hot
    logger.info(f"Speedup cache L1 vs L2 : {speedup:.1f}x")


def benchmark_prefetch():
    """Benchmark du préchargement en lot."""
    logger.info("=" * 70)
    logger.info("BENCHMARK 2 : Préchargement en lot")
    logger.info("=" * 70)
    
    # Vider le cache pour le test
    vat_rates_db.clear_cache(persistent=False)
    
    # Simuler un fichier avec plusieurs pays et dates
    pairs = [
        ("FR", date(2026, 1, 15)),
        ("DE", date(2026, 1, 15)),
        ("ES", date(2026, 1, 15)),
        ("IT", date(2026, 1, 15)),
        ("FR", date(2026, 2, 15)),
        ("DE", date(2026, 2, 15)),
        ("ES", date(2026, 2, 15)),
        ("IT", date(2026, 2, 15)),
    ]
    
    # Test sans préchargement (appel ligne par ligne)
    vat_rates_db.clear_cache(persistent=False)
    start = time.time()
    for country, d in pairs:
        vat_rates_db.get_vat_rate(country, "STANDARD", d)
    elapsed_no_prefetch = time.time() - start
    logger.info(f"Sans préchargement : {elapsed_no_prefetch:.3f}s pour {len(pairs)} couples")
    
    # Test avec préchargement
    vat_rates_db.clear_cache(persistent=False)
    start = time.time()
    vat_rates_db.prefetch_standard_rates(pairs)
    elapsed_prefetch = time.time() - start
    logger.info(f"Avec préchargement : {elapsed_prefetch:.3f}s pour {len(pairs)} couples")
    
    # Vérifier que les appels suivants viennent du cache
    start = time.time()
    for country, d in pairs:
        vat_rates_db.get_vat_rate(country, "STANDARD", d)
    elapsed_after_prefetch = time.time() - start
    logger.info(f"Après préchargement : {elapsed_after_prefetch:.3f}s pour {len(pairs)} appels")
    
    speedup = elapsed_no_prefetch / elapsed_prefetch
    logger.info(f"Speedup préchargement : {speedup:.1f}x")


def benchmark_concurrent_access():
    """Benchmark des accès concurrents."""
    logger.info("=" * 70)
    logger.info("BENCHMARK 3 : Accès concurrents")
    logger.info("=" * 70)
    
    # Vider le cache pour le test
    vat_rates_db.clear_cache(persistent=False)
    
    def fetch_rate(country, test_date):
        return vat_rates_db.get_vat_rate(country, "STANDARD", test_date)
    
    # Test avec accès séquentiels
    start = time.time()
    for i in range(50):
        fetch_rate("FR", date(2026, 1, 1))
    elapsed_sequential = time.time() - start
    logger.info(f"Accès séquentiels : {elapsed_sequential:.3f}s pour 50 appels")
    
    # Test avec accès parallèles
    vat_rates_db.clear_cache(persistent=False)
    start = time.time()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_rate, "FR", date(2026, 1, 1)) for _ in range(50)]
        results = [f.result() for f in futures]
    elapsed_parallel = time.time() - start
    logger.info(f"Accès parallèles (8 workers) : {elapsed_parallel:.3f}s pour 50 appels")
    
    speedup = elapsed_sequential / elapsed_parallel
    logger.info(f"Speedup parallèle : {speedup:.1f}x")


def benchmark_cache_hit_rate():
    """Analyser le taux de hit du cache."""
    logger.info("=" * 70)
    logger.info("BENCHMARK 4 : Taux de hit du cache")
    logger.info("=" * 70)
    
    # Vider le cache pour le test
    vat_rates_db.clear_cache(persistent=False)
    
    # Simuler un pattern d'accès réaliste
    # - 70% des accès sur des pays/dates répétés (cache hit)
    # - 30% sur des nouveaux couples (cache miss)
    
    repeated_data = [("FR", date(2026, 1, 15))] * 70
    new_data = [
        ("DE", date(2026, 1, 15)),
        ("ES", date(2026, 1, 15)),
        ("IT", date(2026, 1, 15)),
    ] * 10  # 30 appels
    
    start = time.time()
    for country, d in repeated_data + new_data:
        vat_rates_db.get_vat_rate(country, "STANDARD", d)
    elapsed = time.time() - start
    
    total_calls = len(repeated_data) + len(new_data)
    logger.info(f"{total_calls} appels (70% répétés, 30% nouveaux) : {elapsed:.3f}s")
    logger.info(f"  -> {elapsed/total_calls*1000:.2f}ms par appel moyen")
    
    # Vérifier les stats de cache
    cache_info = vat_rates_db.cache_info()
    logger.info(f"Stats cache : {cache_info}")


def benchmark_memory_usage():
    """Estimer l'utilisation mémoire du cache."""
    logger.info("=" * 70)
    logger.info("BENCHMARK 5 : Utilisation mémoire")
    logger.info("=" * 70)
    
    # Vider le cache pour le test
    vat_rates_db.clear_cache(persistent=False)
    
    # Charger un grand nombre de couples
    countries = ["FR", "DE", "ES", "IT", "NL", "BE", "AT", "PL"]
    dates = [date(2026, month, 1) for month in range(1, 13)]
    
    pairs = [(c, d) for c in countries for d in dates]
    logger.info(f"Chargement de {len(pairs)} couples (pays x mois)...")
    
    start = time.time()
    vat_rates_db.prefetch_standard_rates(pairs)
    elapsed = time.time() - start
    logger.info(f"Préchargement terminé en {elapsed:.3f}s")
    
    # Vérifier les stats
    cache_info = vat_rates_db.cache_info()
    logger.info(f"Stats après chargement : {cache_info}")
    
    # Estimation approximative de la mémoire
    # Chaque entrée : clé (~30 chars) + valeur (Decimal) + overhead dict
    estimated_memory = cache_info['memory_entries'] * 200  # ~200 bytes par entrée
    logger.info(f"Estimation mémoire cache L1 : ~{estimated_memory/1024:.1f} KB")


def main():
    """Fonction principale."""
    logger.info("DÉBUT DE L'AUDIT DE PERFORMANCE TEDB")
    logger.info("")
    
    try:
        benchmark_cache_levels()
        logger.info("")
        
        benchmark_prefetch()
        logger.info("")
        
        benchmark_concurrent_access()
        logger.info("")
        
        benchmark_cache_hit_rate()
        logger.info("")
        
        benchmark_memory_usage()
        logger.info("")
        
        logger.info("=" * 70)
        logger.info("✅ TOUS LES BENCHMARKS DE PERFORMANCE TERMINÉS")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"❌ BENCHMARK FAILED : {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()