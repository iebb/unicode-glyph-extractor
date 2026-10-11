#!/usr/bin/env python3
"""Regression checks for official version discovery and lossless publication."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import discover_unicode
from compress_glyphs import compress


class UpdateTests(unittest.TestCase):
    def test_empty_draft_with_success_status_is_not_a_version(self):
        def read(url,optional=False):
            return 'final data files and charts for version 18.0.0' if 'latest' in url else '<html>This directory is currently empty.</html>'
        with patch.object(discover_unicode,'read_text',side_effect=read):
            found=discover_unicode.discover()
        self.assertEqual(found['release']['version'],'18.0.0')
        self.assertIsNone(found['draft'])

    def test_new_draft_is_discovered_from_ucd_readme(self):
        def read(url,optional=False):
            if 'latest' in url:return 'final data files for version 18.0.0'
            if 'ucd/ReadMe' in url:return 'Unicode Character Database version 19.0.0'
            return ''
        with patch.object(discover_unicode,'read_text',side_effect=read):
            found=discover_unicode.discover()
        self.assertEqual(found['draft']['version'],'19.0.0')
        self.assertEqual(found['draft']['status'],'draft')

    def test_old_beta_does_not_become_current_draft(self):
        with patch.object(discover_unicode,'read_text',return_value='version 18.0.0'):
            self.assertIsNone(discover_unicode.discover()['draft'])

    def test_missing_final_version_fails_closed(self):
        with patch.object(discover_unicode,'read_text',return_value='<html>Maintenance</html>'):
            with self.assertRaises(ValueError):discover_unicode.discover()

    def test_svgz_preserves_exact_bytes_and_hierarchy(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';dest=Path(temp)/'destination'
            svg=source/'U00/4E/00/G0-523B.svg';svg.parent.mkdir(parents=True)
            data=b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>Original source notices</metadata><path d="M0 0L1 1"/></svg>'
            svg.write_bytes(data)
            (source/'manifest.jsonl').write_text(json.dumps({'path':'U00/4E/00/G0-523B.svg'})+'\n')
            compress(source,dest)
            packed=(dest/'U00/4E/00/G0-523B.svgz').read_bytes()
            self.assertEqual(gzip.decompress(packed),data)
            self.assertEqual(packed[4:8],b'\x00'*4)
            row=json.loads((dest/'compressed-manifest.jsonl').read_text())
            self.assertEqual(row['svg_path'],'U00/4E/00/G0-523B.svg')
            self.assertEqual(row['path'],'U00/4E/00/G0-523B.svgz')


if __name__=='__main__':unittest.main()
