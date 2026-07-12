import csv
import random
from datetime import datetime, timedelta
from decimal import Decimal

HEADERS = [
    "UNIQUE_ACCOUNT_IDENTIFIER", "ACTIVITY_PERIOD", "SALES_CHANNEL", "MARKETPLACE",
    "PROGRAM_TYPE", "TRANSACTION_TYPE", "TRANSACTION_EVENT_ID", "ACTIVITY_TRANSACTION_ID",
    "TAX_CALCULATION_DATE", "TRANSACTION_DEPART_DATE", "TRANSACTION_ARRIVAL_DATE", "TRANSACTION_COMPLETE_DATE",
    "SELLER_SKU", "ASIN", "ITEM_DESCRIPTION", "ITEM_MANUFACTURE_COUNTRY", "QTY",
    "ITEM_WEIGHT", "TOTAL_ACTIVITY_WEIGHT", "COST_PRICE_OF_ITEMS",
    "PRICE_OF_ITEMS_AMT_VAT_EXCL", "PROMO_PRICE_OF_ITEMS_AMT_VAT_EXCL", "TOTAL_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "SHIP_CHARGE_AMT_VAT_EXCL", "PROMO_SHIP_CHARGE_AMT_VAT_EXCL", "TOTAL_SHIP_CHARGE_AMT_VAT_EXCL",
    "GIFT_WRAP_AMT_VAT_EXCL", "PROMO_GIFT_WRAP_AMT_VAT_EXCL", "TOTAL_GIFT_WRAP_AMT_VAT_EXCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL",
    "PRICE_OF_ITEMS_VAT_RATE_PERCENT", "PRICE_OF_ITEMS_VAT_AMT", "PROMO_PRICE_OF_ITEMS_VAT_AMT", "TOTAL_PRICE_OF_ITEMS_VAT_AMT",
    "SHIP_CHARGE_VAT_RATE_PERCENT", "SHIP_CHARGE_VAT_AMT", "PROMO_SHIP_CHARGE_VAT_AMT", "TOTAL_SHIP_CHARGE_VAT_AMT",
    "GIFT_WRAP_VAT_RATE_PERCENT", "GIFT_WRAP_VAT_AMT", "PROMO_GIFT_WRAP_VAT_AMT", "TOTAL_GIFT_WRAP_VAT_AMT",
    "TOTAL_ACTIVITY_VALUE_VAT_AMT",
    "PRICE_OF_ITEMS_AMT_VAT_INCL", "PROMO_PRICE_OF_ITEMS_AMT_VAT_INCL", "TOTAL_PRICE_OF_ITEMS_AMT_VAT_INCL",
    "SHIP_CHARGE_AMT_VAT_INCL", "PROMO_SHIP_CHARGE_AMT_VAT_INCL", "TOTAL_SHIP_CHARGE_AMT_VAT_INCL",
    "GIFT_WRAP_AMT_VAT_INCL", "PROMO_GIFT_WRAP_AMT_VAT_INCL", "TOTAL_GIFT_WRAP_AMT_VAT_INCL",
    "TOTAL_ACTIVITY_VALUE_AMT_VAT_INCL",
    "CURRENCY",
    "EXPORT_DETAILED_STATUS", "EXCHANGE_RATE", "EXCHANGE_RATE_DATE", "DEFLATED_PRICE_OF_ITEMS_AMT_VAT_EXCL",
    "DEFLATED_PRICE_OF_ITEMS_VAT_AMT", "DEFLATED_TOTAL_ACTIVITY_VALUE_AMT_VAT_EXCL", "DEFLATED_TOTAL_ACTIVITY_VALUE_VAT_AMT",
    "TAX_COLLECTION_RESPONSIBILITY", "EXCLUSION_REASON_CODE",
    "INVOICE_NUMBER", "INVOICE_DATE", "INVOICE_URL",
    "BUYER_TAX_REGISTRATION_ID", "BUYER_TAX_REGISTRATION_TYPE", "BUYER_TAX_REGISTRATION_JURISDICTION",
    "SELLER_TAX_REGISTRATION_ID", "SELLER_TAX_REGISTRATION_TYPE", "SELLER_TAX_REGISTRATION_JURISDICTION",
    "FISCAL_CODE", "IS_TAX_INVOICE_REQUIRED", "TAX_REPORTING_SCHEME", "TAX_VALUATION_ASPECT",
    "TAX_LOCATION_CODE", "TAX_RATE_MODEL", "TAX_POINT_DATE",
    "SHIP_FROM_ADDRESS_1", "SHIP_FROM_ADDRESS_2", "SHIP_FROM_CITY", "SHIP_FROM_STATE", "SHIP_FROM_POSTAL_CODE", "SHIP_FROM_COUNTRY",
    "SHIP_TO_ADDRESS_1", "SHIP_TO_ADDRESS_2", "SHIP_TO_CITY", "SHIP_TO_STATE", "SHIP_TO_POSTAL_CODE", "SHIP_TO_COUNTRY",
    "BILL_TO_ADDRESS_1", "BILL_TO_ADDRESS_2", "BILL_TO_CITY", "BILL_TO_STATE", "BILL_TO_POSTAL_CODE", "BILL_TO_COUNTRY",
    "DELIVERY_INCOTERMS", "ODR_TAX_CALCULATION_DATE", "ODR_TAX_POINT_DATE",
    "ORDER_DATE", "MERCHANT_ORDER_ID",
    "sale_depart_country", "sale_arrival_country"
]

EU_COUNTRIES = ["FR", "DE", "IT", "ES", "NL", "PL", "BE", "AT", "IE", "SE"]
NON_EU_COUNTRIES = ["US", "GB", "CA", "CH", "JP", "CN", "AU"]
SPECIAL_TERRITORIES = [
    ("ES", "Canary Islands", "35000"),
    ("FR", "Guadeloupe", "97100"),
    ("DE", "Heligoland", "27498"),
    ("IT", "Livigno", "23030")
]

def generate_valid_vat(country_code):
    """Génère un format de numéro TVA conforme aux masques VIES de l'UE."""
    if country_code == "FR":
        return f"FR{random.randint(10,99)}{random.randint(100000000,999999999)}"
    elif country_code == "DE":
        return f"DE{random.randint(100000000,999999999)}"
    elif country_code == "IT":
        return f"IT{random.randint(10000000000,99999999999)}"
    elif country_code == "ES":
        return f"ESX{random.randint(10000000,99999999)}X"
    else:
        return f"{country_code}{random.randint(10000000,99999999)}"

CASES = [
    "DOMESTIC_B2C", "DOMESTIC_B2B",
    "OSS_B2C", "B2B_REVERSE_CHARGE",
    "DEEMED_SUPPLIER_OUTSIDE_EU", "DEEMED_SUPPLIER_IOSS",
    "EXPORT", "IMPORT_STANDARD",
    "NON_EU_PURE_TRANSACTION", # Nouveau cas pur Chine/Japon pour tester tes bypass de validation TVA
    "SPECIAL_TERRITORY_ORIGIN", "SPECIAL_TERRITORY_DEST",
    "REFUND_DOMESTIC", "REFUND_OSS"
]

def generate_avsr_file(filename="vente_amazon_complet.csv", total_rows=100000):
    print(f"Génération de {total_rows} lignes au format correct...")
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2025, 12, 31)
    delta_days = (end_date - start_date).days
    
    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quoting=csv.QUOTE_ALL)
        writer.writerow(HEADERS)
        
        for i in range(total_rows):
            case = CASES[i % len(CASES)]
            
            days_offset = random.randint(0, delta_days)
            order_dt = start_date + timedelta(days=days_offset, hours=random.randint(0,23), minutes=random.randint(0,59))
            
            period_str = order_dt.strftime("%Y-%b").upper()
            date_iso = order_dt.strftime("%Y-%m-%d")
            datetime_iso = order_dt.strftime("%Y-%m-%dT%H:%M:%S")
            
            tx_type = "SALE"
            qty = "1"
            src_country = "FR"
            src_state = ""
            src_zip = "75018"
            dest_country = "FR"
            dest_state = ""
            dest_zip = "75001"
            
            buyer_vat = ""
            buyer_type = "Country"
            seller_vat = "FR54498123629"
            tax_scheme = "UNION-OSS"
            tax_responsibility = "SELLER"
            
            tax_rate = Decimal("0.20")
            price_incl = Decimal(random.randint(20, 150))
            currency = "EUR"
            
            if case == "DOMESTIC_B2C":
                src_country = random.choice(EU_COUNTRIES)
                dest_country = src_country
                tax_rate = Decimal("0.20") if src_country == "FR" else Decimal("0.19")
                tax_scheme = "REGULAR"
                
            elif case == "DOMESTIC_B2B":
                src_country = random.choice(EU_COUNTRIES)
                dest_country = src_country
                buyer_vat = generate_valid_vat(src_country)
                buyer_type = "Business"
                tax_scheme = "REGULAR"
                
            elif case == "OSS_B2C":
                src_country = random.choice(EU_COUNTRIES)
                dest_country = random.choice([c for c in EU_COUNTRIES if c != src_country])
                tax_rate = Decimal("0.21") if dest_country == "ES" else Decimal("0.22") if dest_country == "IT" else Decimal("0.19")
                tax_scheme = "UNION-OSS"
                
            elif case == "B2B_REVERSE_CHARGE":
                src_country = "FR"
                dest_country = random.choice([c for c in EU_COUNTRIES if c != "FR"])
                buyer_vat = generate_valid_vat(dest_country)
                buyer_type = "Business"
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                
            elif case == "DEEMED_SUPPLIER_OUTSIDE_EU":
                src_country = random.choice(EU_COUNTRIES)
                dest_country = random.choice(EU_COUNTRIES)
                tax_responsibility = "MARKETPLACE"
                tax_scheme = "MARKETPLACE-FACILITATED"
                
            elif case == "DEEMED_SUPPLIER_IOSS":
                # Vente depuis la Chine/Japon vers l'UE sous le seuil IOSS (TVA collectée par Amazon)
                src_country = random.choice(NON_EU_COUNTRIES)
                dest_country = "FR"
                price_incl = Decimal(random.randint(15, 140)) # Seuil <= 150 EUR
                tax_responsibility = "MARKETPLACE"
                tax_scheme = "IOSS"
                tax_rate = Decimal("0.20")
                
            elif case == "EXPORT":
                src_country = "FR"
                dest_country = random.choice(NON_EU_COUNTRIES)
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                currency = "GBP" if dest_country == "GB" else "USD"
                
            elif case == "IMPORT_STANDARD":
                src_country = random.choice(NON_EU_COUNTRIES)
                dest_country = "FR"
                price_incl = Decimal(random.randint(165, 500))
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                
            elif case == "NON_EU_PURE_TRANSACTION":
                # Cas typique Chine -> Japon (Hors UE complet) : Aucune TVA intracommunautaire requise
                src_country = "CN"
                dest_country = "JP"
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                currency = "JPY"
                buyer_type = "Country"
                
            elif case == "SPECIAL_TERRITORY_ORIGIN":
                country, state, zip_code = random.choice(SPECIAL_TERRITORIES)
                src_country = country
                src_state = state
                src_zip = zip_code
                dest_country = "FR"
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                
            elif case == "SPECIAL_TERRITORY_DEST":
                src_country = "FR"
                country, state, zip_code = random.choice(SPECIAL_TERRITORIES)
                dest_country = country
                dest_state = state
                dest_zip = zip_code
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                
            elif case == "REFUND_DOMESTIC":
                tx_type = "REFUND"
                qty = "-1"
                src_country = "FR"
                dest_country = "FR"
                price_incl = Decimal(random.randint(20, 100))
                tax_scheme = "REGULAR"
                
            elif case == "REFUND_OSS":
                tx_type = "REFUND"
                qty = "-1"
                src_country = "FR"
                dest_country = "DE"
                tax_rate = Decimal("0.19")
                price_incl = Decimal(random.randint(20, 100))
                tax_scheme = "UNION-OSS"

            price_excl = (price_incl / (Decimal("1.00") + tax_rate)).quantize(Decimal("0.01"))
            tax_amt = (price_incl - price_excl).quantize(Decimal("0.01"))
            
            if tx_type == "REFUND":
                price_incl = -price_incl
                price_excl = -price_excl
                tax_amt = -tax_amt

            order_id = f"40{random.randint(10,99)}-{random.randint(1000000,9999999)}-{random.randint(1000000,9999999)}"
            tx_event_id = f"tx_ev_{i:08d}"
            
            row = [
                "A21IQVJAS2C4XO", period_str, "amazon.fr", "amazon.fr",
                "AFN", tx_type, tx_event_id, tx_event_id,
                date_iso, date_iso, date_iso, date_iso,
                f"SKU-{random.randint(100,999)}-PROD", f"B00{random.randint(100000,999999)}", "Product Mock Description", "FR", qty,
                "0.2", "0.2", "",
                str(price_excl), "0.0", str(price_excl),
                "0.0", "0.0", "0.0",
                "0.0", "0.0", "0.0",
                str(price_excl),
                str(tax_rate), str(tax_amt), "0.0", str(tax_amt),
                "0.0", "0.0", "0.0", "0.0",
                "0.0", "0.0", "0.0", "0.0",
                str(tax_amt),
                str(price_incl), "0.0", str(price_incl),
                "0.0", "0.0", "0.0",
                "0.0", "0.0", "0.0",
                str(price_incl),
                currency,
                "", "", "", "", "", "", "",
                tax_responsibility, "",
                f"INV-{i:07d}", date_iso, "https://sellercentral.amazon.fr/mock-invoice",
                buyer_vat, buyer_type, dest_country,
                seller_vat, "RESELLER", src_country,
                "", "NO", tax_scheme, "SELLER",
                "", "Standard", datetime_iso,
                "Street 1", "", "Paris", src_state, src_zip, src_country,
                "Route 2", "", "CityDest", dest_state, dest_zip, dest_country,
                "Route 2", "", "CityDest", dest_state, dest_zip, dest_country,
                "DAP", date_iso, date_iso,
                datetime_iso, order_id,
                src_country, dest_country
            ]
            writer.writerow(row)
            
    print("Fichier de test généré.")

if __name__ == "__main__":
    generate_avsr_file()