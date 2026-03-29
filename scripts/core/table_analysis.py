"""Table layout detection and multi-layout row extraction."""

from __future__ import annotations

from core.horizontal_compact import extract_compact_horizontal_rows
from core.models import NormalizedRow
from core.parsing import clean_alias, clean_pack, looks_like_alias, parse_price
from core.text_utils import split_cell_lines


def extract_horizontal_table_rows(matrix: list[list[str]], page_number: int) -> list[NormalizedRow]:
    """Parse horizontally-oriented tables where configs are columns and role labels are rows.
    
    Handles multiple configuration blocks separated by empty/label rows.
    Example structure (Modular Plates):
      RowLabel        1M              2M              3M
      Reference No.   5TG31000AA      5TG31100AA      5TG31200AA
      Unit MRP        139.-           139.-           148.-
      Std. Pkg.       10              10              10
      [blank/label row]
      RowLabel        8MH             8MV             12M      (next block)
      ...
    """
    if len(matrix) < 4 or len(matrix[0]) < 2:
        return []

    # Detect horizontal table: first column contains role-like labels
    first_col = [row[0].strip() for row in matrix]
    role_pattern = ["reference", "mrp", "unit", "price", "pack", "std pkg", "qty"]
    role_indicators = sum(1 for label in first_col if any(pat in label.lower() for pat in role_pattern))

    # Need strong evidence it's horizontal: at least 2 role labels in first column
    if role_indicators < 2:
        return extract_compact_horizontal_rows(matrix, page_number=page_number)

    rows: list[NormalizedRow] = []
    current_block_roles: dict[str, int] = {}
    
    for ri, label in enumerate(first_col):
        label_lower = label.lower()
        
        # Check if this is a role label
        is_role_label = any(pat in label_lower for pat in ["reference", "mrp", "unit", "price", "pack", "std pkg"])
        
        if is_role_label:
            # Determine which role this is
            if any(pat in label_lower for pat in ["reference", "ref no", "cat.no", "item code"]):
                role_key = "reference"
            elif any(pat in label_lower for pat in ["mrp", "unit mrp", "price", "unit price"]):
                role_key = "purchase"
            elif any(pat in label_lower for pat in ["pack", "std pkg", "qty", "nos"]):
                role_key = "pack"
            else:
                continue
            
            # When we see reference again after full block, it's a new block
            if role_key == "reference" and len(current_block_roles) >= 2 and "reference" in current_block_roles:
                # Process the current block
                if "reference" in current_block_roles and "purchase" in current_block_roles:
                    ref_row = current_block_roles["reference"]
                    purch_row = current_block_roles["purchase"]
                    pack_row = current_block_roles.get("pack")
                    
                    for col_idx in range(1, len(matrix[0])):
                        # Get config header from the first row
                        config_header_idx = None
                        for search_ri in range(ref_row):
                            if "plate" in first_col[search_ri].lower() or (ref_row - search_ri == 1 and matrix[search_ri][col_idx].strip()):
                                config_header_idx = search_ri
                                break
                        
                        config_header = matrix[config_header_idx][col_idx].strip() if config_header_idx is not None and col_idx < len(matrix[config_header_idx]) else ""
                        
                        alias_raw = matrix[ref_row][col_idx].strip() if col_idx < len(matrix[ref_row]) else ""
                        purchase_raw = matrix[purch_row][col_idx].strip() if col_idx < len(matrix[purch_row]) else ""
                        
                        alias = clean_alias(alias_raw)
                        purchase = parse_price(purchase_raw)
                        
                        if not looks_like_alias(alias) or purchase is None:
                            continue
                        
                        pack = ""
                        if pack_row is not None:
                            pack_raw = matrix[pack_row][col_idx].strip() if col_idx < len(matrix[pack_row]) else ""
                            pack = clean_pack(pack_raw)
                        
                        rows.append(
                            NormalizedRow(
                                particulars=config_header,
                                alias=alias,
                                purchase=round(purchase, 2),
                                pack=pack,
                                source_page=page_number,
                            )
                        )
                
                # Reset for new block
                current_block_roles = {}
            
            # Record this role in current block
            if role_key not in current_block_roles:
                current_block_roles[role_key] = ri
    
    # Process the final block
    if "reference" in current_block_roles and "purchase" in current_block_roles:
        ref_row = current_block_roles["reference"]
        purch_row = current_block_roles["purchase"]
        pack_row = current_block_roles.get("pack")
        
        for col_idx in range(1, len(matrix[0])):
            # Find config header
            config_header_idx = None
            for search_ri in range(ref_row - 1, -1, -1):
                if matrix[search_ri][col_idx].strip() and "reference" not in first_col[search_ri].lower():
                    config_header_idx = search_ri
                    break
            
            config_header = matrix[config_header_idx][col_idx].strip() if config_header_idx is not None and col_idx < len(matrix[config_header_idx]) else ""
            
            alias_raw = matrix[ref_row][col_idx].strip() if col_idx < len(matrix[ref_row]) else ""
            purchase_raw = matrix[purch_row][col_idx].strip() if col_idx < len(matrix[purch_row]) else ""
            
            alias = clean_alias(alias_raw)
            purchase = parse_price(purchase_raw)
            
            if not looks_like_alias(alias) or purchase is None:
                continue
            
            pack = ""
            if pack_row is not None:
                pack_raw = matrix[pack_row][col_idx].strip() if col_idx < len(matrix[pack_row]) else ""
                pack = clean_pack(pack_raw)
            
            rows.append(
                NormalizedRow(
                    particulars=config_header,
                    alias=alias,
                    purchase=round(purchase, 2),
                    pack=pack,
                    source_page=page_number,
                )
            )
    
    if rows:
        return rows

    # Fallback for compact/collapsed horizontal matrices.
    return extract_compact_horizontal_rows(matrix, page_number=page_number)
