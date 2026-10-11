#!/usr/bin/env python3
"""Regression checks against the downloaded Unicode 18 PDF's real specimens."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import pymupdf
from extract_glyphs import code_path, page_spans, page_outlines, grid_forms, source_forms


class SpecimenRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path('sources/18.0.0/CodeCharts.pdf')
        if not path.exists():raise unittest.SkipTest('Run scripts/download_unicode.py first')
        cls.pdf=pymupdf.open(path)

    @classmethod
    def tearDownClass(cls):
        cls.pdf.close()

    def forms(self,page_number,grid=False):
        page=self.pdf[page_number-1]
        forms,issues=(grid_forms if grid else source_forms)(page_spans(page),page_outlines(page))
        self.assertEqual(issues,[])
        return forms

    def test_requested_hexadecimal_hierarchy(self):
        self.assertEqual(code_path(0x11111).as_posix(),'U01/11/11')
        self.assertEqual(code_path(0x10FFFF).as_posix(),'U10/FF/FF')

    def test_wrapped_cjk_source_rows(self):
        sources={f['source'] for f in self.forms(377) if f['cp']==0x3401}
        self.assertEqual(sources,{'G5-3024','T4-2224','JMJ-000005','K3-2121'})

    def test_wrapped_source_identifier(self):
        sources={f['source'] for f in self.forms(559) if f['cp']==0x4E26}
        self.assertIn('GKX-0078.14',sources)
        self.assertNotIn('GKX-',sources)

    def test_standardized_variation_sequences(self):
        sources={f['source'] for f in self.forms(235) if f['cp']==0x210B}
        self.assertEqual(sources,{'210B','210B FE00','210B FE01'})

    def test_small_cuneiform_reference_font(self):
        forms=self.forms(1436,grid=True)
        self.assertEqual(len(forms),196)
        self.assertTrue(all(f['outlines'] for f in forms))

    def test_small_undersized_cuneiform_numerals(self):
        forms=self.forms(1439,grid=True)
        self.assertTrue(all(f['outlines'] for f in forms))
        self.assertIn(0x12558,{f['cp'] for f in forms})

    def test_small_siyaq_reference_font(self):
        forms=self.forms(1824,grid=True)
        self.assertEqual(len(forms),68)
        self.assertTrue(all(f['outlines'] for f in forms))

    def test_annotations_do_not_leak_into_chart(self):
        forms=self.forms(1375,grid=True)
        self.assertEqual(len(forms),13)
        self.assertEqual({f['cp'] for f in forms},set(range(0x11660,0x1166D)))

    def test_new_unicode_18_small_seal_variants(self):
        sources={f['source'] for f in self.forms(3150) if f['cp']==0x3D028}
        self.assertEqual(sources,{'TH-00038','C-00038','K-00038','D-00038'})

    def test_pdf_audit_detects_changed_outline(self):
        output=Path('glyphs/18.0.0')
        if not (output/'manifest.jsonl').exists():
            self.skipTest('Generate the full output to test the independent PDF audit')
        rows=[]
        with (output/'manifest.jsonl').open() as manifest:
            for line in manifest:
                row=json.loads(line)
                if row['page']==2:rows.append(row)
                elif rows:break
        with tempfile.TemporaryDirectory() as directory:
            temporary=Path(directory)
            shutil.copy2(output/'extraction.json',temporary/'extraction.json')
            (temporary/'manifest.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            for row in rows:
                destination=temporary/row['path']
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(output/row['path'],destination)
            corrupted=temporary/'U00/00/41.svg'
            root=ET.parse(corrupted)
            root.getroot().find('{http://www.w3.org/2000/svg}path').set('d','M0 0L1 0L0 1Z')
            root.write(corrupted,encoding='utf-8',xml_declaration=True)
            result=subprocess.run([sys.executable,'scripts/audit_pdf_coverage.py',
                                   '--output',str(temporary),'--pages','2'],
                                  capture_output=True,text=True)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            report=json.loads((temporary/'pdf-audit-partial.json').read_text())
            self.assertFalse(report['passed'])
            self.assertIn('missing_pdf_outline',{error['kind'] for error in report['errors']})
            self.assertIn('outline_absent_from_pdf_chart',{error['kind'] for error in report['errors']})


if __name__=='__main__':
    unittest.main(verbosity=2)
