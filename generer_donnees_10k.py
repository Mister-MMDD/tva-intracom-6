import pandas as pd
import random
from datetime import datetime, timedelta

# Configuration de la reproductibilité
random.seed(2026)

# Catalogues de produits réalistes (Électronique, Cuisine, Mode)
products = [
    {"name": "Echo Dot (5th Gen) - Smart Speaker", "sku": "B09B8V1C6A", "cost": 15.00, "price_eur_base": 59.99},
    {"name": "Kindle Paperwhite (16 GB) - 6.8\" display", "sku": "B09TMN584B", "cost": 45.00, "price_eur_base": 169.99},
    {"name": "Anker PowerBank 20000mAh", "sku": "B08LG2X98F", "cost": 12.50, "price_eur_base": 39.99},
    {"name": "Sony WH-1000XM4 Noise Canceling Headphones", "sku": "B08C56KX2F", "cost": 95.00, "price_eur_base": 249.00},
    {"name": "Logitech MX Master 3S Wireless Mouse", "sku": "B09HM94V6G", "cost": 35.00, "price_eur_base": 109.00},
    {"name": "Nespresso Vertuo Next Coffee Machine", "sku": "B084GBC5Y2", "cost": 60.00, "price_eur_base": 149.00},
    {"name": "Apple AirTag (4 Pack)", "sku": "B0932Q6M4B", "cost": 48.00, "price_eur_base": 119.00},
    {"name": "SanDisk 128GB Ultra MicroSDXC", "sku": "B073JYC4XM", "cost": 5.00, "price_eur_base": 19.99}
]

# Taux de TVA par pays
vat_rates = {
    "FR": 0.20, "DE": 0.19, "IT": 0.22, "ES": 0.21, "GB": 0.20, 
    "PL": 0.23, "NL": 0.21, "BE": 0.21, "SE": 0.25, "US": 0.00
}

# Devises et taux de conversion (1 EUR = X Devise)
currencies = {
    "EUR": 1.0,
    "GBP": 0.85,
    "PLN": 4.32,
    "SEK": 11.45,
    "USD": 1.09
}

# Fonctions de génération de numéros de TVA (Valides vs Invalides / Format incorrect)
def gen_buyer_vat(country, valid=True):
    if not valid:
        # Format erroné ou flag "INVALID" incrusté pour les tests de nettoyage de données
        return f"{country}INV{random.randint(10000,99999)}X"
    
    # Formats VIES réalistes
    if country == "FR": return f"FR{random.randint(10,99)}{random.randint(100000000,999999999)}"
    elif country == "DE": return f"DE{random.randint(100000000,999999999)}"
    elif country == "IT": return f"IT{random.randint(10000000000,99999999999)}"
    elif country == "ES": return f"ESX{random.randint(10000000,99999999)}Z"
    elif country == "PL": return f"PL{random.randint(1000000000,9999999999)}"
    elif country == "GB": return f"GB{random.randint(100000000,999999999)}"
    else: return f"{country}{random.randint(10000000,99999999)}"

# Préparation de la boucle géante
data_volume = []
start_date = datetime(2026, 1, 1)

# Définition de profils de transactions pour couvrir "tous les cas possibles"
cases_pool = [
    # 1. Vente B2C Domestique Standard (Vendeur gère)
    {"type": "Sale", "b2b": False, "depart": "FR", "arrival": "FR", "currency": "EUR", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": False, "depart": "DE", "arrival": "DE", "currency": "EUR", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": False, "depart": "PL", "arrival": "PL", "currency": "PLN", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    
    # 2. Vente B2C Intra-UE via Guichet Unique Union-OSS (Vendeur déclare, devise locale ou EUR)
    {"type": "Sale", "b2b": False, "depart": "FR", "arrival": "IT", "currency": "EUR", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": False, "depart": "DE", "arrival": "ES", "currency": "EUR", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": False, "depart": "DE", "arrival": "SE", "currency": "SEK", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": False, "depart": "FR", "arrival": "PL", "currency": "PLN", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    
    # 3. Vente Marketplace Facilitator (Amazon collecte et reverse la TVA) - ex: Client UK, ou Vendeur Non-UE vers UE
    {"type": "Sale", "b2b": False, "depart": "FR", "arrival": "GB", "currency": "GBP", "scheme": "REGULAR", "model": "MarketplaceFacilitator", "resp": "Amazon"},
    {"type": "Sale", "b2b": False, "depart": "DE", "arrival": "GB", "currency": "GBP", "scheme": "REGULAR", "model": "MarketplaceFacilitator", "resp": "Amazon"},
    
    # 4. Vente B2B Exonérée Intra-UE (VIES Valide)
    {"type": "Sale", "b2b": True, "vat_valid": True, "depart": "FR", "arrival": "DE", "currency": "EUR", "scheme": "B2B_INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": True, "vat_valid": True, "depart": "DE", "arrival": "IT", "currency": "EUR", "scheme": "B2B_INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": True, "vat_valid": True, "depart": "FR", "arrival": "PL", "currency": "PLN", "scheme": "B2B_INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    
    # 5. Vente B2B Domestique (TVA applicable normale, VIES renseigné)
    {"type": "Sale", "b2b": True, "vat_valid": True, "depart": "FR", "arrival": "FR", "currency": "EUR", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": True, "vat_valid": True, "depart": "DE", "arrival": "DE", "currency": "EUR", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    
    # 6. Vente B2B avec Numéro VIES Invalide / Erreur de saisie (La TVA doit être chargée car VIES KO !)
    {"type": "Sale", "b2b": True, "vat_valid": False, "depart": "FR", "arrival": "IT", "currency": "EUR", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    {"type": "Sale", "b2b": True, "vat_valid": False, "depart": "DE", "arrival": "FR", "currency": "EUR", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    
    # 7. Remboursements (Refunds) sur divers scénarios
    {"type": "Refund", "b2b": False, "depart": "FR", "arrival": "FR", "currency": "EUR", "scheme": "REGULAR", "model": "Seller", "resp": "Seller"},
    {"type": "Refund", "b2b": False, "depart": "FR", "arrival": "IT", "currency": "EUR", "scheme": "UNION-OSS", "model": "Seller", "resp": "Seller"},
    {"type": "Refund", "b2b": False, "depart": "DE", "arrival": "GB", "currency": "GBP", "scheme": "REGULAR", "model": "MarketplaceFacilitator", "resp": "Amazon"},
    {"type": "Refund", "b2b": True, "vat_valid": True, "depart": "FR", "arrival": "DE", "currency": "EUR", "scheme": "B2B_INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    
    # 8. Transferts de Stock Intra-communautaires (FC_Transfer) - Exonéré / Autoliquidation
    {"type": "FC_Transfer", "b2b": False, "depart": "FR", "arrival": "DE", "currency": "EUR", "scheme": "INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    {"type": "FC_Transfer", "b2b": False, "depart": "DE", "arrival": "PL", "currency": "EUR", "scheme": "INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    {"type": "FC_Transfer", "b2b": False, "depart": "FR", "arrival": "ES", "currency": "EUR", "scheme": "INTRA_COMMUNITY", "model": "Seller", "resp": "Seller"},
    
    # 9. Inbound Stock (Entrée usine/fournisseur vers FBA)
    {"type": "Inbound", "b2b": False, "depart": "FR", "arrival": "FR", "currency": "EUR", "scheme": "", "model": "", "resp": ""},
    
    # 10. Exportation Hors UE (Exonéré de TVA - ex: Vente vers USA)
    {"type": "Sale", "b2b": False, "depart": "FR", "arrival": "US", "currency": "USD", "scheme": "NON-EU-EXPORT", "model": "Seller", "resp": "Seller"}
]

# Génération des 10 000 lignes
for i in range(10000):
    cfg = random.choice(cases_pool)
    
    # Horodatage sur les 5 premiers mois de 2026
    tx_date = start_date + timedelta(days=random.randint(0, 140), hours=random.randint(0, 23), minutes=random.randint(0, 59))
    tx_date_str = tx_date.strftime("%Y-%m-%d %H:%M:%S")
    
    order_id = f"{random.randint(400,499)}-{random.randint(1000000,9999999)}-{random.randint(1000000,9999999)}" if cfg["type"] in ["Sale", "Refund"] else ""
    
    prod = random.choice(products)
    quantity = random.randint(1, 4) if not cfg["b2b"] else random.randint(5, 25)
    item_name = f"{quantity}x {prod['sku']} - {prod['name']}"
    
    # Gestion des devises et taux de change
    currency = cfg["currency"]
    rate = currencies[currency]
    
    # Détermination du taux de TVA appliqué
    if cfg["scheme"] in ["B2B_INTRA_COMMUNITY", "INTRA_COMMUNITY", "NON-EU-EXPORT", ""] or cfg["type"] == "Inbound":
        vat_pct = 0.0
    else:
        vat_pct = vat_rates.get(cfg["arrival"], 0.20)
        
    # Calcul des montants de base (conversion de l'EUR de base vers la devise cible)
    base_price_in_currency = prod['price_eur_base'] * rate
    
    if vat_pct == 0.0:
        # Cas exonéré / Hors taxes : le prix de base devient le prix HT direct
        price_ht = round(base_price_in_currency * quantity, 2)
        price_vat = 0.0
        price_ttc = price_ht
    else:
        # Cas taxé normalement
        price_ttc = round(base_price_in_currency * quantity, 2)
        price_ht = round(price_ttc / (1 + vat_pct), 2)
        price_vat = round(price_ttc - price_ht, 2)
        
    # Frais de port optionnels
    if cfg["type"] in ["Sale", "Refund"] and random.random() > 0.6:
        ship_ttc = round(4.99 * rate * (1 if not cfg["b2b"] else 3), 2)
        if vat_pct == 0.0:
            ship_ht = ship_ttc
            ship_vat = 0.0
        else:
            ship_ht = round(ship_ttc / (1 + vat_pct), 2)
            ship_vat = round(ship_ttc - ship_ht, 2)
    else:
        ship_ht, ship_vat, ship_ttc = 0.0, 0.0, 0.0
        
    gift_ht, gift_vat, gift_ttc = 0.0, 0.0, 0.0
    
    # Application du signe négatif pour les remboursements
    if cfg["type"] == "Refund":
        price_ht, price_vat, price_ttc = -price_ht, -price_vat, -price_ttc
        ship_ht, ship_vat, ship_ttc = -ship_ht, -ship_vat, -ship_ttc
        
    total_ht = round(price_ht + ship_ht, 2)
    total_vat = round(price_vat + ship_vat, 2)
    total_ttc = round(price_ttc + ship_ttc, 2)
    
    # Gestion du numéro de TVA Acheteur (VIES)
    buyer_vat = ""
    if cfg["b2b"]:
        buyer_vat = gen_buyer_vat(cfg["arrival"], valid=cfg.get("vat_valid", True))
        
    row = {
        "TRANSACTION_COMPLETE_DATE": tx_date_str,
        "TRANSACTION_TYPE": cfg["type"],
        "ORDER_ID": order_id,
        "ITEM_NAME": item_name,
        "SALE_DEPART_COUNTRY": cfg["depart"],
        "SALE_ARRIVAL_COUNTRY": cfg["arrival"],
        "TAX_REPORTING_SCHEME": cfg["scheme"],
        "TAXABLE_JURISDICTION": cfg["arrival"] if cfg["type"] not in ["Inbound"] else "",
        "BUYER_COUNTRY": cfg["arrival"] if cfg["type"] in ["Sale", "Refund"] else "",
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL": total_ht,
        "TOTAL_ACTIVITY_VALUE_VAT_AMT": total_vat,
        "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL": total_ttc,
        "PRICE_ITEMS_VAT_EXCL_AMT": price_ht,
        "PRICE_ITEMS_VAT_AMT": price_vat,
        "PRICE_ITEMS_VAT_INCL_AMT": price_ttc,
        "SHIPPING_VALUE_VAT_EXCL_AMT": ship_ht,
        "SHIPPING_VALUE_VAT_AMT": ship_vat,
        "SHIPPING_VALUE_VAT_INCL_AMT": ship_ttc,
        "GIFT_WRAP_VALUE_VAT_EXCL_AMT": gift_ht,
        "GIFT_WRAP_VALUE_VAT_AMT": gift_vat,
        "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL": price_ttc,
        "TRANSACTION_CURRENCY_CODE": currency,
        "EXCHANGE_RATE": round(1 / rate, 5) if rate != 1.0 else 1.0, # Taux d'inversion pour correspondre au format Amazon (Devise locale vers EUR)
        "VAT_RATE_PERCENT": round(vat_pct * 100, 1),
        "TAX_COLLECTION_MODEL": cfg["model"],
        "TAX_COLLECTION_RESPONSIBILITY": cfg["resp"],
        "COST_PRICE_OF_ITEMS": round(prod["cost"] * quantity, 2),
        "BUYER_VAT_NUMBER": buyer_vat,
        "SHIP_FROM_COUNTRY": cfg["depart"],
        "SHIP_TO_COUNTRY": cfg["arrival"],
        "FULFILLMENT_CHANNEL": "AFN" if cfg["type"] != "Inbound" else ""
    }
    data_volume.append(row)

# Création et tri du DataFrame global
df_volume = pd.DataFrame(data_volume)
df_volume = df_volume.sort_values(by="TRANSACTION_COMPLETE_DATE").reset_index(drop=True)

# Export final au format standard Amazon .TSV
massive_filename = "amazon_vat_10k_transactions_report_2026.tsv"
df_volume.to_csv(massive_filename, sep='\t', index=False, encoding='utf-8')

print(f"Fichier volumétrique généré avec {len(df_volume)} lignes.")