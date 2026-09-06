import csv
import random
from datetime import datetime, timedelta
from decimal import Decimal

# Le moteur de TVA attend des clés en minuscules (normalisation interne du loader)
HEADERS = [
    "unique_account_identifier", "activity_period", "sales_channel", "marketplace",
    "program_type", "transaction_type", "transaction_event_id", "activity_transaction_id",
    "tax_calculation_date", "transaction_depart_date", "transaction_arrival_date", "transaction_complete_date",
    "seller_sku", "asin", "item_description", "item_manufacture_country", "qty",
    "item_weight", "total_activity_weight", "cost_price_of_items",
    "price_of_items_amt_vat_excl", "promo_price_of_items_amt_vat_excl", "total_price_of_items_amt_vat_excl",
    "ship_charge_amt_vat_excl", "promo_ship_charge_amt_vat_excl", "total_ship_charge_amt_vat_excl",
    "gift_wrap_amt_vat_excl", "promo_gift_wrap_amt_vat_excl", "total_gift_wrap_amt_vat_excl",
    "total_activity_value_amt_vat_excl",
    "price_of_items_vat_rate_percent", "price_of_items_vat_amt", "promo_price_of_items_vat_amt", "total_price_of_items_vat_amt",
    "ship_charge_vat_rate_percent", "ship_charge_vat_amt", "promo_ship_charge_vat_amt", "total_ship_charge_vat_amt",
    "gift_wrap_vat_rate_percent", "gift_wrap_vat_amt", "promo_gift_wrap_vat_amt", "total_gift_wrap_vat_amt",
    "total_activity_value_vat_amt",
    "price_of_items_amt_vat_incl", "promo_price_of_items_amt_vat_incl", "total_price_of_items_amt_vat_incl",
    "ship_charge_amt_vat_incl", "promo_ship_charge_amt_vat_incl", "total_ship_charge_amt_vat_incl",
    "gift_wrap_amt_vat_incl", "promo_gift_wrap_amt_vat_incl", "total_gift_wrap_amt_vat_incl",
    "total_activity_value_amt_vat_incl",
    "transaction_currency_code",
    "export_detailed_status", "exchange_rate", "exchange_rate_date", "deflated_price_of_items_amt_vat_excl",
    "deflated_price_of_items_vat_amt", "deflated_total_activity_value_amt_vat_excl", "deflated_total_activity_value_vat_amt",
    "tax_collection_responsibility", "exclusion_reason_code",
    "invoice_number", "invoice_date", "invoice_url",
    "buyer_tax_registration_id", "buyer_tax_registration_type", "buyer_tax_registration_jurisdiction",
    "seller_tax_registration_id", "seller_tax_registration_type", "seller_tax_registration_jurisdiction",
    "fiscal_code", "is_tax_invoice_required", "tax_reporting_scheme", "tax_valuation_aspect",
    "tax_location_code", "tax_rate_model", "tax_point_date",
    "ship_from_address_1", "ship_from_address_2", "ship_from_city", "ship_from_state", "ship_from_postal_code", "ship_from_country",
    "ship_to_address_1", "ship_to_address_2", "ship_to_city", "ship_to_state", "ship_to_postal_code", "ship_to_country",
    "bill_to_address_1", "bill_to_address_2", "bill_to_city", "bill_to_state", "bill_to_postal_code", "bill_to_country",
    "delivery_incoterms", "odr_tax_calculation_date", "odr_tax_point_date",
    "order_date", "merchant_order_id",
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
    "IMPORT_SELLER_AS_IMPORTER",
    "IOSS_DIRECT",
    "NON_EU_PURE_TRANSACTION",
    "SPECIAL_TERRITORY_ORIGIN", "SPECIAL_TERRITORY_DEST",
    "REFUND_DOMESTIC", "REFUND_OSS", "REFUND_B2B", "REFUND_EXPORT", "REFUND_DEEMED_SUPPLIER",
    "TRANSFER_INTRA_EU", "TRANSFER_DOMESTIC",
    "B2B_NATIONAL_ES", "B2B_NATIONAL_IT",
    "DOMESTIC_REVERSE_CHARGE_IT",
    "B2B_OSS_INCORRECT_VIES"
]

def generate_avsr_file(filename="data/vente_amazon_complet3.csv", total_rows=100000):
    print(f"Génération de {total_rows} lignes dans {filename}...")
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2025, 12, 31)
    delta_days = (end_date - start_date).days
    
    categories = ["STANDARD", "BOOKS", "FOOD", "MEDICINES", "CLOTHING"]
    
    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=',', quoting=csv.QUOTE_MINIMAL)
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
            product_cat = random.choice(categories)
            
            tax_rate = Decimal("0.20")
            price_incl = Decimal(random.randint(20, 150))
            TRANSACTION_CURRENCY_CODE = "EUR"
            
            # --- Logic par cas ---
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
                src_country = random.choice(NON_EU_COUNTRIES)
                dest_country = "FR"
                price_incl = Decimal(random.randint(15, 140))
                tax_responsibility = "MARKETPLACE"
                tax_scheme = "IOSS"
                tax_rate = Decimal("0.20")

            elif case == "IOSS_DIRECT":
                src_country = "CN"
                dest_country = "FR"
                price_incl = Decimal(random.randint(15, 140))
                tax_responsibility = "SELLER"
                tax_scheme = "IOSS"
                tax_rate = Decimal("0.20")
                
            elif case == "EXPORT":
                src_country = "FR"
                dest_country = random.choice(NON_EU_COUNTRIES)
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                TRANSACTION_CURRENCY_CODE = "GBP" if dest_country == "GB" else "USD"
                
            elif case == "IMPORT_STANDARD":
                src_country = random.choice(NON_EU_COUNTRIES)
                dest_country = "FR"
                price_incl = Decimal(random.randint(165, 500))
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"

            elif case == "IMPORT_SELLER_AS_IMPORTER":
                src_country = "US"
                dest_country = "FR"
                price_incl = Decimal(random.randint(200, 1000))
                tax_rate = Decimal("0.20")
                tax_scheme = "REGULAR"
                tax_responsibility = "SELLER"
                
            elif case == "NON_EU_PURE_TRANSACTION":
                src_country = "CN"
                dest_country = "JP"
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"
                TRANSACTION_CURRENCY_CODE = "JPY"
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

            elif case == "REFUND_B2B":
                tx_type = "REFUND"
                qty = "-1"
                src_country = "FR"
                dest_country = "IT"
                buyer_vat = generate_valid_vat("IT")
                buyer_type = "Business"
                tax_rate = Decimal("0.00")
                price_incl = Decimal(random.randint(50, 200))
                tax_scheme = "REGULAR"

            elif case == "REFUND_EXPORT":
                tx_type = "REFUND"
                qty = "-1"
                src_country = "FR"
                dest_country = "US"
                tax_rate = Decimal("0.00")
                price_incl = Decimal(random.randint(50, 200))
                tax_scheme = "REGULAR"

            elif case == "REFUND_DEEMED_SUPPLIER":
                tx_type = "REFUND"
                qty = "-1"
                src_country = "FR"
                dest_country = "IT"
                tax_responsibility = "MARKETPLACE"
                tax_scheme = "MARKETPLACE-FACILITATED"
                tax_rate = Decimal("0.22")
                price_incl = Decimal(random.randint(20, 100))

            elif case == "TRANSFER_INTRA_EU":
                tx_type = "FC_TRANSFER"
                qty = "1"
                src_country = "FR"
                dest_country = "DE"
                tax_rate = Decimal("0.00")
                price_incl = Decimal("0.00")
                tax_scheme = "REGULAR"

            elif case == "TRANSFER_DOMESTIC":
                tx_type = "FC_TRANSFER"
                qty = "1"
                src_country = "FR"
                dest_country = "FR"
                tax_rate = Decimal("0.00")
                price_incl = Decimal("0.00")
                tax_scheme = "REGULAR"

            elif case == "B2B_NATIONAL_ES":
                src_country = "ES"
                dest_country = "ES"
                buyer_vat = f"{random.randint(10000000, 99999999)}A"
                buyer_type = "Business"
                tax_scheme = "REGULAR"
                tax_rate = Decimal("0.21")

            elif case == "B2B_NATIONAL_IT":
                src_country = "IT"
                dest_country = "IT"
                buyer_vat = f"IT{random.randint(10000000000, 99999999999)}"
                buyer_type = "Business"
                tax_scheme = "REGULAR"
                tax_rate = Decimal("0.22")

            elif case == "DOMESTIC_REVERSE_CHARGE_IT":
                src_country = "IT"
                dest_country = "IT"
                buyer_vat = generate_valid_vat("IT")
                buyer_type = "Business"
                tax_rate = Decimal("0.00")
                tax_scheme = "REGULAR"

            elif case == "B2B_OSS_INCORRECT_VIES":
                src_country = "FR"
                dest_country = "DE"
                buyer_vat = "DE123" 
                buyer_type = "Business"
                tax_scheme = "UNION-OSS"
                tax_rate = Decimal("0.19")

            # --- Calcul final ---
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
                f"SKU-{random.randint(100,999)}-PROD", f"B00{random.randint(100000,999999)}", f"Product Mock {product_cat}", "FR", qty,
                "0.2", "0.2", "0.0",
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
                TRANSACTION_CURRENCY_CODE,
                "", "1.0", date_iso, "0.0", "0.0", "0.0", "0.0",
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
            
    print(f"Fichier de test généré dans {filename}.")

if __name__ == "__main__":
    generate_avsr_file()
