"""Tests de performance pour les gros fichiers.

Couvre les tests de performance :
- Parsing de fichiers volumineux (10k, 50k, 100k lignes)
- Calcul TVA sur gros volumes
- Mesure de la consommation mémoire
- Tests de seuils de performance

Approche : Tests isolés sans dépendances externes
- Utilise les générateurs de données existants
- Pas de base de données requise (tests en mémoire)
"""

from __future__ import annotations

import os
import sys
import time
from decimal import Decimal

# Permet d'importer le package tva_intracom
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from tva_intracom.engine import compute_vat, compute_all_with_vies
from tva_intracom.models import (
    BuyerType,
    Channel,
    Collector,
    Sale,
    Scenario,
)


# ---------------------------------------------------------------------------
# Helpers pour générer des données de test
# ---------------------------------------------------------------------------

def generate_sales(count: int) -> list[Sale]:
    """Génère une liste de ventes de test."""
    sales = []
    countries = ["DE", "IT", "ES", "NL", "BE"]
    
    for i in range(count):
        sale = Sale(
            sale_id=f"TEST-{i:06d}",
            amount_ht=Decimal(str(100 + (i % 50))),
            buyer_type=BuyerType.B2C if i % 3 != 0 else BuyerType.B2B,
            stock_country="FR",
            buyer_country=countries[i % len(countries)],
            seller_country="FR",
            buyer_vat_valid=(i % 3 == 0),
            buyer_vat_number=f"{countries[i % len(countries)]}123456789" if i % 3 == 0 else "",
            transaction_date="2026-01-15",
            product_category="STANDARD",
            quantity=1 + (i % 5),
        )
        sales.append(sale)
    
    return sales


def generate_amazon_csv(count: int, format_version: int = 3) -> str:
    """Génère un fichier CSV Amazon de test."""
    if format_version == 3:
        header = "transaction_type\torder_id\tsale_depart_country\tsale_arrival_country\tbuyer_vat_number\ttotal_activity_value_amt_vat_excl\ttransaction_currency_code\ttransaction_complete_date\ttax_collection_model\tasin"
        lines = [header]
        
        for i in range(count):
            line = f"sale\tORD-{i:06d}\tFR\tDE\t\t{100 + (i % 50):.2f}\tEUR\t2026-01-15\tfacilitator\tB{i:04d}"
            lines.append(line)
        
        return "\n".join(lines)
    
    elif format_version == 5:
        header = "transaction_id\torder_date\ttransaction_type\tour_price_tax_exclusive_selling_price\tshipping_tax_exclusive_selling_price\tgiftwrap_tax_exclusive_selling_price\tship_from_country\tship_to_country\ttax_collection_responsibility\tjurisdiction_level\tcurrency\tinvoice_level_exchange_rate"
        lines = [header]
        
        for i in range(count):
            line = f"TXN-{i:06d}\t2026-01-15\tsale\t{100 + (i % 50):.2f}\t5.00\t0.00\tFR\tDE\tseller\tcountry\tEUR\t1.0"
            lines.append(line)
        
        return "\n".join(lines)
    
    else:
        raise ValueError(f"Format {format_version} non supporté")


# ---------------------------------------------------------------------------
# Tests de performance - Parsing
# ---------------------------------------------------------------------------

class TestParsingPerformance:
    """Test de performance du parsing de fichiers volumineux."""

    def test_parse_10k_lines_format3(self):
        """Test du parsing de 10k lignes Format 3."""
        csv_content = generate_amazon_csv(10000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps de parsing
            start = time.time()
            results = load_amazon_report(temp_file)
            parsing_time = time.time() - start
            
            # Vérifier le parsing
            assert len(results.sales) == 10000
            
            # Vérifier la performance (< 30 secondes pour 10k lignes)
            assert parsing_time < 30.0, f"Parsing trop lent: {parsing_time:.2f}s"
            
        finally:
            os.unlink(temp_file)

    def test_parse_50k_lines_format3(self):
        """Test du parsing de 50k lignes Format 3."""
        csv_content = generate_amazon_csv(50000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps de parsing
            start = time.time()
            results = load_amazon_report(temp_file)
            parsing_time = time.time() - start
            
            # Vérifier le parsing
            assert len(results.sales) == 50000
            
            # Vérifier la performance (< 120 secondes pour 50k lignes)
            assert parsing_time < 120.0, f"Parsing trop lent: {parsing_time:.2f}s"
            
        finally:
            os.unlink(temp_file)

    def test_parse_100k_lines_format3(self):
        """Test du parsing de 100k lignes Format 3."""
        csv_content = generate_amazon_csv(100000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps de parsing
            start = time.time()
            results = load_amazon_report(temp_file)
            parsing_time = time.time() - start
            
            # Vérifier le parsing
            assert len(results.sales) == 100000
            
            # Vérifier la performance (< 240 secondes pour 100k lignes)
            assert parsing_time < 240.0, f"Parsing trop lent: {parsing_time:.2f}s"
            
        finally:
            os.unlink(temp_file)


# ---------------------------------------------------------------------------
# Tests de performance - Calcul TVA
# ---------------------------------------------------------------------------

class TestCalcPerformance:
    """Test de performance du calcul TVA sur gros volumes."""

    def test_calc_10k_sales(self):
        """Test du calcul TVA sur 10k ventes."""
        sales = generate_sales(10000)
        
        # Mesurer le temps de calcul
        start = time.time()
        results = [compute_vat(sale) for sale in sales]
        calc_time = time.time() - start
        
        # Vérifier le calcul
        assert len(results) == 10000
        
        # Vérifier la performance (< 10 secondes pour 10k ventes)
        assert calc_time < 10.0, f"Calcul trop lent: {calc_time:.2f}s"

    def test_calc_50k_sales(self):
        """Test du calcul TVA sur 50k ventes."""
        sales = generate_sales(50000)
        
        # Mesurer le temps de calcul
        start = time.time()
        results = [compute_vat(sale) for sale in sales]
        calc_time = time.time() - start
        
        # Vérifier le calcul
        assert len(results) == 50000
        
        # Vérifier la performance (< 45 secondes pour 50k ventes)
        assert calc_time < 45.0, f"Calcul trop lent: {calc_time:.2f}s"

    def test_calc_100k_sales(self):
        """Test du calcul TVA sur 100k ventes."""
        sales = generate_sales(100000)
        
        # Mesurer le temps de calcul
        start = time.time()
        results = [compute_vat(sale) for sale in sales]
        calc_time = time.time() - start
        
        # Vérifier le calcul
        assert len(results) == 100000
        
        # Vérifier la performance (< 90 secondes pour 100k ventes)
        assert calc_time < 90.0, f"Calcul trop lent: {calc_time:.2f}s"


# ---------------------------------------------------------------------------
# Tests de performance - Mémoire
# ---------------------------------------------------------------------------

class TestMemoryPerformance:
    """Test de la consommation mémoire."""

    def test_memory_10k_sales(self):
        """Test de la consommation mémoire pour 10k ventes."""
        try:
            import psutil
            import gc
            
            # Forcer le garbage collection avant le test
            gc.collect()
            
            # Mesurer la mémoire avant
            process = psutil.Process()
            mem_before = process.memory_info().rss / 1024 / 1024  # MB
            
            # Générer et calculer
            sales = generate_sales(10000)
            results = [compute_vat(sale) for sale in sales]
            
            # Forcer le garbage collection après
            gc.collect()
            
            # Mesurer la mémoire après
            mem_after = process.memory_info().rss / 1024 / 1024  # MB
            mem_increase = mem_after - mem_before
            
            # Vérifier la consommation mémoire (< 100 MB pour 10k ventes)
            assert mem_increase < 100, f"Consommation mémoire trop élevée: {mem_increase:.2f} MB"
            
        except ImportError:
            # psutil non disponible, skip le test
            pytest.skip("psutil non disponible")

    def test_memory_50k_sales(self):
        """Test de la consommation mémoire pour 50k ventes."""
        try:
            import psutil
            import gc
            
            # Forcer le garbage collection avant le test
            gc.collect()
            
            # Mesurer la mémoire avant
            process = psutil.Process()
            mem_before = process.memory_info().rss / 1024 / 1024  # MB
            
            # Générer et calculer
            sales = generate_sales(50000)
            results = [compute_vat(sale) for sale in sales]
            
            # Forcer le garbage collection après
            gc.collect()
            
            # Mesurer la mémoire après
            mem_after = process.memory_info().rss / 1024 / 1024  # MB
            mem_increase = mem_after - mem_before
            
            # Vérifier la consommation mémoire (< 300 MB pour 50k ventes)
            assert mem_increase < 300, f"Consommation mémoire trop élevée: {mem_increase:.2f} MB"
            
        except ImportError:
            # psutil non disponible, skip le test
            pytest.skip("psutil non disponible")


# ---------------------------------------------------------------------------
# Tests de performance - Flux complet
# ---------------------------------------------------------------------------

class TestEndToEndPerformance:
    """Test de performance du flux complet (parsing + calcul)."""

    def test_end_to_end_10k_lines(self):
        """Test du flux complet pour 10k lignes."""
        csv_content = generate_amazon_csv(10000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps total
            start = time.time()
            
            # Parsing
            sales = load_amazon_report(temp_file).sales
            
            # Calcul
            results = [compute_vat(sale) for sale in sales]
            
            total_time = time.time() - start
            
            # Vérifier les résultats
            assert len(results) == 10000
            
            # Vérifier la performance (< 40 secondes pour 10k lignes)
            assert total_time < 40.0, f"Flux complet trop lent: {total_time:.2f}s"
            
        finally:
            os.unlink(temp_file)

    def test_end_to_end_50k_lines(self):
        """Test du flux complet pour 50k lignes."""
        csv_content = generate_amazon_csv(50000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps total
            start = time.time()
            
            # Parsing
            sales = load_amazon_report(temp_file).sales
            
            # Calcul
            results = [compute_vat(sale) for sale in sales]
            
            total_time = time.time() - start
            
            # Vérifier les résultats
            assert len(results) == 50000
            
            # Vérifier la performance (< 160 secondes pour 50k lignes)
            assert total_time < 160.0, f"Flux complet trop lent: {total_time:.2f}s"
            
        finally:
            os.unlink(temp_file)


# ---------------------------------------------------------------------------
# Tests de régression de performance
# ---------------------------------------------------------------------------

class TestPerformanceRegression:
    """Test de régression de performance."""

    def test_performance_baseline_10k(self):
        """Test que les performances ne se dégradent pas (baseline 10k)."""
        csv_content = generate_amazon_csv(10000, format_version=3)
        
        # Écrire le fichier temporaire
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_content)
            temp_file = f.name
        
        try:
            from tva_intracom.parsers.amazon.loader import load_amazon_report
            
            # Mesurer le temps de parsing
            start = time.time()
            sales = load_amazon_report(temp_file).sales
            parsing_time = time.time() - start
            
            # Mesurer le temps de calcul
            start = time.time()
            results = [compute_vat(sale) for sale in sales]
            calc_time = time.time() - start
            
            # Baseline : parsing < 30s, calcul < 10s
            assert parsing_time < 30.0, f"Parsing en dessous de la baseline: {parsing_time:.2f}s"
            assert calc_time < 10.0, f"Calcul en dessous de la baseline: {calc_time:.2f}s"
            
        finally:
            os.unlink(temp_file)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
