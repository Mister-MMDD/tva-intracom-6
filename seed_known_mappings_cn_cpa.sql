-- Seed product_tax_code_category — premier lot "sûr" du mapping PTC->TEDB
-- (reprise chantier CN/CPA, 2026-09-16). Source : Mapping_Amazon_TEDB.xlsx
-- fourni par Matthieu (premier jet), filtré des codes fiscalement ambigus
-- (cf. mémoire tedb-amazon-ptc-reference.md section 3bis) — CES CODES-LÀ
-- RESTENT VOLONTAIREMENT ABSENTS DE CE SCRIPT, en attente de validation
-- cabinet comptable :
--   A_GEN_NOTAX (hors champ TVA — projet séparé, pas une catégorie)
--   A_BOOK_ADULT, A_BOOK_ATLAS, A_BOOK_AUDIOBOOK, A_BOOK_GLOBE, A_BOOK_MAP
--   A_CLTH_PROTECTIVE (médical vs professionnel — ambigu)
--   A_FOOD_CEREALCHOCBARS, A_FOOD_CHOCEREAL, A_FOOD_CNDY, A_FOOD_SODAJUICE
--   A_FOOD_ANIMALFOOD, A_FOOD_PETFOOD, A_FOOD_ANIMALMED, A_FOOD_ANIMALVITAMINS
--   A_HLTH_NUTRITIONBAR, A_HLTH_NUTRITIONDRINK, A_HLTH_VITAMINS, A_HPC_DIETARYSUPPL
--   A_HPC_CONTRACEPTIVE, A_HPC_SANITARYPRODUCTS, A_HPC_SANITARYPRODUCTSWASHABLE
--   A_HPC_PPESANITISER (catégorie TEDB "CLEANING_PRODUCT" absente de notre
--                       dump de référence, à réconcilier avant mapping)
--
-- Idempotent : ON CONFLICT DO UPDATE (permet de relancer ce script tel
-- quel si un futur lot vient corriger une ligne existante).
-- À exécuter directement sur Supabase (psql / SQL editor) — ce module
-- (product_tax_code_category.py) ne fait QUE lire cette table, jamais
-- l'écrire pour des mappings connus (cf. docstring module).

INSERT INTO product_tax_code_category (product_tax_code, category, source) VALUES
    ('A_GEN_STANDARD', 'STANDARD', 'known_mapping'),

    -- Livres / périodiques
    ('A_BOOKS_GEN', 'BOOKS', 'known_mapping'),
    ('A_BOOK_MAGAZINE', 'PERIODICALS', 'known_mapping'),

    -- Vêtements bébé/enfant
    ('A_CLTH_BABY', 'CHILD_WEAR', 'known_mapping'),
    ('A_CLTH_CHILD', 'CHILD_WEAR', 'known_mapping'),
    ('A_BABY_BIBCLOTH', 'CHILD_WEAR', 'known_mapping'),
    ('A_BABY_NAPPIES', 'CHILD_WEAR', 'known_mapping'),
    ('A_BABY_CARSEAT', 'CHILDREN_CAR_SEATS', 'known_mapping'),

    -- Alimentation générale (hors confiserie/boissons sucrées/animalier — ambigus)
    ('A_FOOD_GEN', 'FOOD', 'known_mapping'),
    ('A_FOOD_CAKEDECOR', 'FOOD', 'known_mapping'),
    ('A_FOOD_CANFRUIT', 'FOOD', 'known_mapping'),
    ('A_FOOD_CEREALBARS', 'FOOD', 'known_mapping'),
    ('A_FOOD_COFFEE', 'FOOD', 'known_mapping'),
    ('A_FOOD_DAIRY', 'FOOD', 'known_mapping'),
    ('A_FOOD_DESSERT', 'FOOD', 'known_mapping'),
    ('A_FOOD_DRIEDFRUIT', 'FOOD', 'known_mapping'),
    ('A_FOOD_FLOUR', 'FOOD', 'known_mapping'),
    ('A_FOOD_MEATCHICKEN', 'FOOD', 'known_mapping'),
    ('A_FOOD_MISCBEVERAGE', 'FOOD', 'known_mapping'),
    ('A_FOOD_NAAN', 'FOOD', 'known_mapping'),
    ('A_FOOD_NCARBWTR', 'FOOD', 'known_mapping'),
    ('A_FOOD_OIL', 'FOOD', 'known_mapping'),
    ('A_FOOD_OLIVEOIL', 'FOOD', 'known_mapping'),
    ('A_FOOD_PASTANOODLE', 'FOOD', 'known_mapping'),
    ('A_FOOD_PASTRYCASE', 'FOOD', 'known_mapping'),
    ('A_FOOD_PLAINBISCUIT', 'FOOD', 'known_mapping'),
    ('A_FOOD_PLAINCRACKER', 'FOOD', 'known_mapping'),
    ('A_FOOD_PLAINNUT', 'FOOD', 'known_mapping'),
    ('A_FOOD_RICE', 'FOOD', 'known_mapping'),
    ('A_FOOD_SEASONINGS', 'FOOD', 'known_mapping'),
    ('A_FOOD_SNACK', 'FOOD', 'known_mapping'),
    ('A_FOOD_SODAJUICEUNSWEET', 'FOOD', 'known_mapping'),
    ('A_FOOD_SPREAD', 'FOOD', 'known_mapping'),
    ('A_FOOD_SWEEETENER', 'FOOD', 'known_mapping'),
    ('A_FOOD_TEA', 'FOOD', 'known_mapping'),
    ('A_FOOD_VEGETABLE', 'FOOD', 'known_mapping'),

    -- Médicaments à usage humain (hors contraception — ambigu)
    ('A_HLTH_PILLCAPSULETABLET', 'MEDICINES', 'known_mapping'),
    ('A_HPC_MEDICINE', 'MEDICINES', 'known_mapping'),
    ('A_HLTH_SMOKINGCESSATION', 'MEDICINES', 'known_mapping'),
    ('A_HLTH_SMOKINGGUM', 'MEDICINES', 'known_mapping'),

    -- Équipement médical
    ('A_HPC_CONTACTLENSES', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_CORRECTIVEGLASSES', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_THERMOMETER', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_WALKINGSTICK', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_WHEELCHAIR', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_INCONTINENCE', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_PPECLOTHING', 'MEDICAL_EQUIPMENT', 'known_mapping'),
    ('A_HPC_PPEMASKS', 'MEDICAL_EQUIPMENT', 'known_mapping'),

    -- Extérieur / agricole
    ('A_OUTDOOR_SOLARPANEL300', 'SOLAR_PANELS', 'known_mapping'),
    ('A_OUTDOOR_PLANTS', 'PLANT', 'known_mapping'),
    ('A_OUTDOOR_SEEDS', 'PLANT', 'known_mapping'),
    ('A_OUTDOOR_FUEL', 'FOSSIL_FUEL', 'known_mapping'),
    ('A_OUTDOOR_FERTILIZER', 'CHEMICAL_FERTILISERS', 'known_mapping'),
    ('A_OUTDOOR_LAWNCONTROL', 'CHEMICAL_PESTICIDES_ENVIRONMENT', 'known_mapping'),
    ('A_OUTDOOR_PLANTFOOD', 'CERTAIN_AGRICULTURAL_INPUT', 'known_mapping')

ON CONFLICT (product_tax_code) DO UPDATE
    SET category = EXCLUDED.category,
        source   = EXCLUDED.source,
        resolved_at = now()
    WHERE product_tax_code_category.source != 'manual_override';
-- Le WHERE protège une correction manuelle ponctuelle (source =
-- 'manual_override', ex. un vendeur signale une erreur) : ce script ne
-- l'écrase jamais, contrairement à une ligne 'unresolved_default' ou
-- 'known_mapping' déjà en base.
