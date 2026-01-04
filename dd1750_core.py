"""DD1750 core - Full featured version with advanced parsing."""

import io
import math
import re
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any
from collections import defaultdict

import pdfplumber
import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# Register fonts for better rendering
try:
    pdfmetrics.registerFont(TTFont('Arial', 'arial.ttf'))
    DEFAULT_FONT = 'Arial'
except:
    DEFAULT_FONT = 'Helvetica'


# Constants
ROWS_PER_PAGE = 18
PAGE_W, PAGE_H = 612.0, 792.0

# Column positions
X_BOX_L, X_BOX_R = 44.0, 88.0
X_CONTENT_L, X_CONTENT_R = 88.0, 365.0
X_UOI_L, X_UOI_R = 365.0, 408.5
X_INIT_L, X_INIT_R = 408.5, 453.5
X_SPARES_L, X_SPARES_R = 453.5, 514.5
X_TOTAL_L, X_TOTAL_R = 514.5, 566.0

Y_TABLE_TOP_LINE = 616.0
Y_TABLE_BOTTOM_LINE = 89.5
ROW_H = (Y_TABLE_TOP_LINE - Y_TABLE_BOTTOM_LINE) / ROWS_PER_PAGE
PAD_X = 3.0


@dataclass
class BomItem:
    line_no: int
    description: str
    nsn: str
    qty: int
    confidence: float = 1.0  # How confident we are in this extraction


class AdvancedBomParser:
    """Advanced parser with multiple strategies."""
    
    def __init__(self):
        self.strategies = [
            self._parse_with_tables,
            self._parse_with_text_alignment,
            self._parse_with_regex,
            self._parse_with_heuristics
        ]
    
    def parse(self, pdf_path: str, start_page: int = 0) -> List[BomItem]:
        """Try multiple parsing strategies."""
        all_items = []
        
        for strategy in self.strategies:
            try:
                items = strategy(pdf_path, start_page)
                if items and len(items) > len(all_items):
                    all_items = items
                    print(f"Strategy {strategy.__name__} found {len(items)} items")
            except Exception as e:
                print(f"Strategy {strategy.__name__} failed: {e}")
                continue
        
        return all_items
    
    def _parse_with_tables(self, pdf_path: str, start_page: int) -> List[BomItem]:
        """Parse using table structure detection."""
        items = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start_page:]:
                tables = page.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "explicit_vertical_lines": [],
                    "explicit_horizontal_lines": [],
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                    "edge_min_length": 3,
                    "min_words_vertical": 3,
                    "min_words_horizontal": 1,
                    "keep_blank_chars": False,
                    "text_tolerance": 3,
                    "text_x_tolerance": 3,
                    "text_y_tolerance": 3,
                    "intersection_tolerance": 3,
                    "intersection_x_tolerance": 3,
                    "intersection_y_tolerance": 3,
                })
                
                if tables:
                    for table in tables:
                        items.extend(self._extract_from_table(table))
        
        return items
    
    def _extract_from_table(self, table: List[List]) -> List[BomItem]:
        """Extract items from a table."""
        items = []
        
        if len(table) < 2:
            return items
        
        # Find column indices
        header = table[0]
        col_indices = self._identify_columns(header)
        
        # Process each row
        for row in table[1:]:
            item = self._extract_row_item(row, col_indices)
            if item:
                items.append(item)
        
        return items
    
    def _identify_columns(self, header: List) -> Dict[str, int]:
        """Identify column positions based on header content."""
        indices = {}
        
        for idx, cell in enumerate(header):
            if not cell:
                continue
            
            cell_text = str(cell).strip().upper()
            
            # LV/Level column
            if any(keyword in cell_text for keyword in ['LV', 'LEVEL', 'L/V']):
                indices['lv'] = idx
            
            # Description column
            elif any(keyword in cell_text for keyword in ['DESCRIPTION', 'DESC', 'ITEM', 'NOMENCLATURE']):
                indices['desc'] = idx
            
            # Material/NSN column
            elif any(keyword in cell_text for keyword in ['MATERIAL', 'NSN', 'STOCK', 'PART']):
                indices['material'] = idx
            
            # Quantity column
            elif any(keyword in cell_text for keyword in ['QTY', 'QUANTITY', 'AUTH QTY', 'QTY.']):
                indices['qty'] = idx
        
        return indices
    
    def _extract_row_item(self, row: List, col_indices: Dict) -> BomItem:
        """Extract a single item from a row."""
        # Check if LV = 'B'
        if 'lv' not in col_indices or len(row) <= col_indices['lv']:
            return None
        
        lv_cell = row[col_indices['lv']]
        if not lv_cell or str(lv_cell).strip().upper() != 'B':
            return None
        
        # Extract description
        description = ""
        if 'desc' in col_indices and len(row) > col_indices['desc']:
            desc_cell = row[col_indices['desc']]
            if desc_cell:
                description = self._extract_middle_text(str(desc_cell))
        
        if not description:
            return None
        
        # Extract NSN
        nsn = ""
        if 'material' in col_indices and len(row) > col_indices['material']:
            material_cell = row[col_indices['material']]
            if material_cell:
                nsn = self._extract_nsn(str(material_cell))
        
        # Extract quantity
        qty = 1
        if 'qty' in col_indices and len(row) > col_indices['qty']:
            qty_cell = row[col_indices['qty']]
            if qty_cell:
                qty = self._extract_quantity(str(qty_cell))
        
        return BomItem(
            line_no=0,  # Will be set later
            description=description[:100],
            nsn=nsn,
            qty=qty,
            confidence=0.9
        )
    
    def _extract_middle_text(self, text: str) -> str:
        """Extract the middle/primary text from description."""
        # Split by newlines
        lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
        
        if not lines:
            return ""
        
        # Strategy 1: Take the line that's most likely the middle text
        # Middle text usually: not in parentheses, not all caps, contains letters
        candidates = []
        for line in lines:
            # Skip parenthetical lines
            if line.startswith('(') and line.endswith(')'):
                continue
            
            # Skip lines that are just codes
            if re.match(r'^[A-Z0-9_\-\s]+$', line) and len(line.split()) <= 3:
                continue
            
            # Skip lines with column headers
            if any(code in line.upper() for code in ['WTY', 'ARC', 'CIIC', 'UI', 'SCMC']):
                continue
            
            candidates.append(line)
        
        # If we have candidates, take the first one
        if candidates:
            return candidates[0][:100]
        
        # Strategy 2: Take the line with measurements
        for line in lines:
            if re.search(r'\d+\s*(FT|IN|MM|CM|M|LB|KG)\b', line, re.IGNORECASE):
                return line[:100]
        
        # Strategy 3: Take the first non-empty line
        return lines[0][:100]
    
    def _extract_nsn(self, text: str) -> str:
        """Extract NSN from text."""
        nsn_match = re.search(r'\b(\d{4}[\-\s]?\d{2}[\-\s]?\d{3}[\-\s]?\d{4}|\d{9,13})\b', text)
        if nsn_match:
            # Clean up the NSN
            nsn = re.sub(r'[^\d]', '', nsn_match.group(1))
            if len(nsn) >= 9:
                return nsn[:13]
        return ""
    
    def _extract_quantity(self, text: str) -> int:
        """Extract quantity from text."""
        try:
            # Look for numbers
            numbers = re.findall(r'\b\d+\b', text)
            if numbers:
                return int(numbers[0])
        except:
            pass
        return 1
    
    def _parse_with_text_alignment(self, pdf_path: str, start_page: int) -> List[BomItem]:
        """Parse using text alignment detection."""
        items = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start_page:]:
                # Get words with positions
                words = page.extract_words(
                    extra_attrs=["fontname", "size"],
                    keep_blank_chars=False,
                    use_text_flow=True,
                    horizontal_ltr=True,
                    vertical_ttb=True,
                )
                
                if not words:
                    continue
                
                # Group words by approximate y-position
                y_tolerance = 2.0
                rows = defaultdict(list)
                
                for word in words:
                    y_key = round(word['top'] / y_tolerance) * y_tolerance
                    rows[y_key].append(word)
                
                # Process each row
                for y, row_words in sorted(rows.items()):
                    row_text = ' '.join([w['text'] for w in sorted(row_words, key=lambda x: x['x0'])])
                    
                    # Check if this row has LV = 'B'
                    if re.search(r'\bB\b', row_text) and ',' in row_text:
                        item = self._extract_from_text_row(row_text, row_words)
                        if item:
                            items.append(item)
        
        return items
    
    def _extract_from_text_row(self, row_text: str, row_words: List[Dict]) -> BomItem:
        """Extract item from a text row."""
        # Extract NSN if present
        nsn = self._extract_nsn(row_text)
        
        # Extract description (text after "B" and before codes)
        parts = row_text.split()
        if 'B' in parts:
            b_index = parts.index('B')
            desc_parts = []
            
            for i in range(b_index + 1, len(parts)):
                part = parts[i]
                # Skip if it's a code
                if (len(part) <= 3 and part.isupper()) or \
                   part in ['WTY', 'ARC', 'CIIC', 'UI', 'SCMC', 'EA', 'AY', '9K', '9G']:
                    continue
                desc_parts.append(part)
            
            description = ' '.join(desc_parts)
            description = self._extract_middle_text(description)
            
            if description:
                return BomItem(
                    line_no=0,
                    description=description[:100],
                    nsn=nsn,
                    qty=1,
                    confidence=0.7
                )
        
        return None
    
    def _parse_with_regex(self, pdf_path: str, start_page: int) -> List[BomItem]:
        """Parse using regex patterns."""
        items = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start_page:]:
                text = page.extract_text() or ""
                
                # Pattern for BOM items
                patterns = [
                    # Pattern 1: NSN B Description
                    r'(\d{9})\s+B\s+([^\n\(]+?)(?:\s+\d+)?\s*$',
                    # Pattern 2: B Description (NSN on next line)
                    r'B\s+([^\n\(]+?)(?:\s+\d+)?\s*\n\s*(\d{9})',
                    # Pattern 3: Description with NSN nearby
                    r'([A-Z][^\(\n]{10,}?)(?:\s+\d+)?\s*(?:\n\s*(\d{9}))?',
                ]
                
                for pattern in patterns:
                    matches = re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE)
                    for match in matches:
                        description = match.group(1).strip()
                        nsn = match.group(2) if match.group(2) else ""
                        
                        # Clean description
                        description = self._extract_middle_text(description)
                        
                        if description:
                            items.append(BomItem(
                                line_no=0,
                                description=description[:100],
                                nsn=nsn,
                                qty=1,
                                confidence=0.6
                            ))
        
        return items
    
    def _parse_with_heuristics(self, pdf_path: str, start_page: int) -> List[BomItem]:
        """Parse using heuristic rules."""
        items = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[start_page:]:
                text = page.extract_text() or ""
                lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
                
                i = 0
                while i < len(lines):
                    line = lines[i]
                    
                    # Heuristic: Line contains comma and looks like description
                    if ',' in line and len(line) > 10 and not line.startswith(('COEI-', 'BII-')):
                        # Check if previous line has NSN
                        nsn = ""
                        if i > 0 and re.match(r'^\d{9}$', lines[i-1]):
                            nsn = lines[i-1]
                        
                        # Check if line starts with B
                        description = line
                        if description.startswith('B '):
                            description = description[2:]
                        
                        # Extract middle text
                        description = self._extract_middle_text(description)
                        
                        if description:
                            items.append(BomItem(
                                line_no=0,
                                description=description[:100],
                                nsn=nsn,
                                qty=1,
                                confidence=0.5
                            ))
                    
                    i += 1
        
        return items


def extract_items_from_pdf(pdf_path: str, start_page: int = 0) -> List[BomItem]:
    """Main extraction function using advanced parser."""
    parser = AdvancedBomParser()
    items = parser.parse(pdf_path, start_page)
    
    # Assign line numbers
    for idx, item in enumerate(items, 1):
        item.line_no = idx
    
    print(f"Extracted {len(items)} items with advanced parser")
    return items


def generate_dd1750_from_pdf(bom_path, template_path, output_path, start_page=0):
    """Generate DD1750 with advanced features."""
    try:
        items = extract_items_from_pdf(bom_path
