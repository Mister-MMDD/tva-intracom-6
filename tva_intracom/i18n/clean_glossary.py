import os

# List of files to clean
files_to_clean = ['de.toml', 'es.toml', 'pl.toml', 'pt.toml', 'it.toml']

for filename in files_to_clean:
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Find the start of the duplicate nested sections (after viz_data_vat_refund)
    # and end before # Donation
    start_remove = None
    end_remove = None
    
    for i, line in enumerate(lines):
        if i > 0 and 'viz_data_vat_refund' in lines[i-1] and line.strip() == '':
            # Check if next line is 'title = "IOSS"' or starts a glossary section
            if i+1 < len(lines) and ('title = "IOSS"' in lines[i+1] or '[glossary_terms.' in lines[i+1]):
                start_remove = i
        if start_remove and '# Donation' in line:
            end_remove = i
            break
    
    if start_remove and end_remove:
        # Remove the duplicate nested sections
        new_lines = lines[:start_remove] + lines[end_remove:]
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f'Cleaned {filename}: removed lines {start_remove+1} to {end_remove}')
    else:
        print(f'Could not find duplicate sections in {filename}')
