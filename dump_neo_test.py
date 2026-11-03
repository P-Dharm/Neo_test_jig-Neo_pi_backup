import zipfile
import xml.etree.ElementTree as ET

zf = zipfile.ZipFile('NEO_test.xlsx')
shared_strings = []
if 'xl/sharedStrings.xml' in zf.namelist():
    tree = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    for si in tree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si'):
        t = ''.join([elem.text for elem in si.iter() if elem.text])
        shared_strings.append(t)

tree = ET.fromstring(zf.read('xl/worksheets/sheet1.xml'))
rows = tree.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
for row in rows[:30]:
    row_vals = []
    for c in row.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
        val = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
        val_text = val.text if val is not None else ''
        if c.attrib.get('t') == 's' and val_text:
            val_text = shared_strings[int(val_text)]
        cell_ref = c.attrib.get('r')
        row_vals.append(f'{cell_ref}: {val_text}')
    print(' | '.join(row_vals))
