import os

# List of files to clean
files_to_clean = ['de.toml', 'es.toml', 'pl.toml', 'pt.toml', 'it.toml']

for filename in files_to_clean:
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Find the line after viz_data_vat_refund (should be line 1416 in de.toml)
    # The duplicate starts with "title = \"IOSS\"" or similar and continues until before "# Donation"
    start_remove = None
    end_remove = None
    
    for i, line in enumerate(lines):
        # Find the line after viz_data_vat_refund
        if 'viz_data_vat_refund' in line:
            # The duplicate starts on the next line
            if i+1 < len(lines):
                start_remove = i + 1
    
    # Find the line before "# Donation"
    for i, line in enumerate(lines):
        if '# Donation' in line:
            end_remove = i
            break
    
    if start_remove and end_remove and start_remove < end_remove:
        # Remove the duplicate nested sections
        new_lines = lines[:start_remove] + lines[end_remove:]
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print(f'Cleaned {filename}: removed lines {start_remove+1} to {end_remove}')
    else:
        print(f'Could not find duplicate sections in {filename}')
        print(f'start_remove: {start_remove}, end_remove: {end_remove}')
