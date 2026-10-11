#!/usr/bin/env python3
"""Download a versioned, reproducible set of official Unicode specimens."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(url, path):
    if path.exists() and path.stat().st_size:
        print(f'Using {path}', flush=True)
        return
    part = path.with_suffix(path.suffix + '.part')
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'UnicodeSpecimenExtractor/1.0'})
            with urllib.request.urlopen(req, timeout=120) as response, part.open('wb') as f:
                while block := response.read(1024 * 1024):
                    f.write(block)
            os.replace(part, path)
            print(f'Downloaded {path}', flush=True)
            return
        except Exception:
            part.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', default='18.0.0')
    p.add_argument('--sources', type=Path, default=Path('sources'))
    p.add_argument('--base-url', help='Official release or draft root, e.g. https://www.unicode.org/Public/draft/')
    p.add_argument('--refresh', action='store_true', help='Redownload mutable draft sources')
    args = p.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        p.error('--version must look like 18.0.0')
    out = args.sources / args.version
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url or f'https://www.unicode.org/Public/{args.version}/'
    if not re.fullmatch(r'https://(?:www\.)?unicode\.org/Public/[A-Za-z0-9./_-]+/',base):
        p.error('--base-url must be an official Unicode Public HTTPS directory')
    files = {'CodeCharts.pdf': base + 'charts/CodeCharts.pdf'}
    names=['UnicodeData.txt','Unihan.zip','TangutSources.txt','NushuSources.txt','StandardizedVariants.txt']
    if int(args.version.split('.')[0])>=18:names+=['SealSources.txt','JurchenSources.txt']
    for name in names:
        files[name]=base+'ucd/'+name
    records = []
    for name, url in files.items():
        dest = out / name
        if args.refresh:dest.unlink(missing_ok=True)
        download(url, dest)
        records.append({'file': name, 'url': url, 'bytes': dest.stat().st_size,
                        'sha256': sha256(dest)})
    (out / 'sources.json').write_text(json.dumps({'unicode_version': args.version,
                                                'files': records}, indent=2) + '\n')


if __name__ == '__main__':
    main()
