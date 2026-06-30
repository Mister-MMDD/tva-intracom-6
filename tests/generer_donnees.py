import csv
import random
from datetime import date, timedelta

# Liste des colonnes exactes d'Amazon basées sur ton fichier d'exemple
HEADERS = [
    "transaction_type", "transaction_event_id", "activity_transaction_id", 
    "tax_calculation_date", "departure_country", "arrival_country", 
    "seller_depart_vat_number_country", "buyer_vat_number", "buyer_vat_number_country", 
    "total_activity_value_amt_vat_excl", "price_of_items_amt_vat_excl", 
    "total_ship_charge_amt_vat_excl", "total_gift_wrap_amt_vat_excl", 
    "qty", "marketplace", "seller_sku", "asin", "item_description", 
    "price_of_items_vat_rate_percent", "total_price_of_items_vat_amt"
]

# Données de simulation
COUNTRIES = ["FR", "DE", "ES", "IT", "PL"]
CURRENCIES = {
    "Amazon.fr": ("FR", "EUR"),
    "Amazon.de": ("DE", "EUR"),
    "Amazon.es": ("ES", "EUR"),
    "Amazon.it": ("IT", "EUR"),
    "Amazon.co.uk": ("GB", "GBP"),
    "Amazon.pl": ("PL", "PLN"),
    "Amazon.com": ("US", "USD")
}

# Quelques vrais formats de TVA (vrais et faux pour le VIES)
# Note : Pour les besoins du test, ton moteur passera par l'API VIES. 
# Les numéros finissant par 'B01' ou 'VALID' simuleront des bons ou mauvais numéros selon la configuration de ton mock/VIES.
VAT_NUMBERS = {
    "FR": ["FR123456789", "FR987654321", "FRINVALID99"],
    "DE": ["DE123456789", "DE999999999", "DE000000000"],
    "ES": ["ESA1234567B", "ESB98765432", "ESINVALID1"],
    "IT": ["IT12345678901", "IT09876543210", "IT99999999999"]
}

start_date = date(2024, 3, 1)

with open("amazon_sample_100.tsv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter="\t")
    writer.writerow(HEADERS)
    
    for i in range(1, 101):
        # 1. Sélection de la Marketplace et Devise
        marketplace = random.choice(list(CURRENCIES.keys()))
        market_country, currency = CURRENCIES[marketplace]
        
        # 2. Logique de flux (Départ / Arrivée)
        # On simule du stock principalement en France ou Allemagne (FBA)
        departure = random.choice(["FR", "DE"])
        arrival = random.choice(COUNTRIES + ["GB"]) # Parfois expédié hors UE (UK)
        
        # Date incrémentale
        tx_date = (start_date + timedelta(days=i // 4)).strftime("%Y-%m-%d")
        
        # 3. Type d'acheteur (B2C vs B2B)
        buyer_type = random.choice(["B2C", "B2C", "B2C", "B2B"]) # 25% de B2B
        buyer_vat = ""
        buyer_vat_country = ""
        
        if buyer_type == "B2B" and arrival in VAT_NUMBERS:
            buyer_vat_country = arrival
            buyer_vat = random.choice(VAT_NUMBERS[arrival])
            
        # 4. Montants financiers
        qty = random.randint(1, 3)
        price_ht = round(random.uniform(15.0, 180.0), 2)
        ship_ht = round(random.uniform(0.0, 12.0), 2)
        total_ht = round(price_ht + ship_ht, 2)
        
        # Détermination théorique du taux de TVA pour le fichier d'origine d'Amazon
        # (Amazon applique la TVA du pays de destination en B2C)
        vat_rate = 20
        if arrival == "DE": vat_rate = 19
        elif arrival == "ES": vat_rate = 21
        elif arrival == "IT": vat_rate = 22
        elif arrival == "PL": vat_rate = 23
        elif arrival == "GB": vat_rate = 20
        
        # Si B2B avec numéro valide supposé, Amazon met souvent 0 (Exonéré)
        if buyer_type == "B2B" and "INVALID" not in buyer_vat:
            vat_rate = 0
            
        vat_amt = round(total_ht * (vat_rate / 100), 2)
        
        # Construction de la ligne
        row = [
            "SALE",
            f"EVT-{1000+i}",
            f"ORD-2024-{1000+i}",
            tx_date,
            departure,
            arrival,
            departure, # Identifiant TVA vendeur (pays départ)
            buyer_vat,
            buyer_vat_country,
            f"{total_ht:.2f}",
            f"{price_ht:.2f}",
            f"{ship_ht:.2f}",
            "0.00", # Gift wrap
            str(qty),
            marketplace,
            f"SKU-{random.randint(100,999)}",
            f"B00{random.randint(100000,999999)}",
            f"Produit High-Tech Eco v{i}",
            str(vat_rate),
            f"{vat_amt:.2f}"
        ]
        
        writer.writerow(row)

print("Fichier 'amazon_sample_100.tsv' généré avec succès (100 lignes) !")