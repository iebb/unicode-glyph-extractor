#!/usr/bin/env python3
"""Extract, verify and stage the current release/draft into a glyph repository.

No network write is performed here. CI commits and pushes only after this script
and the collection's catalog builder succeed. A changed PDF layout fails closed.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from discover_unicode import discover

SCRIPTS=Path(__file__).resolve().parent


def source_signature(records):
    if not records:return None
    return (records['unicode_version'],tuple(sorted(
        (row['file'],row['url'],row['bytes'],row['sha256']) for row in records['files'])))


def run(name,*args):
    subprocess.run([sys.executable,str(SCRIPTS/name),*map(str,args)],check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--discovery',type=Path,help='Use a previously saved official discovery result')
    p.add_argument('--force',action='store_true',help='Reextract even when sources and scripts are unchanged')
    args=p.parse_args()
    repo=args.repo.resolve(); work=args.work.resolve()
    if repo==work or repo in work.parents or work in repo.parents:
        p.error('--work must be outside the glyph repository')
    work.mkdir(parents=True,exist_ok=True)
    discovered=json.loads(args.discovery.read_text()) if args.discovery else discover()
    (work/'discovery.json').write_text(json.dumps(discovered,indent=2)+'\n')
    outputs=[]
    previous=json.loads((repo/'catalog-state.json').read_text()) if (repo/'catalog-state.json').exists() else {}
    extractor_hash=hashlib.sha256(b''.join(path.name.encode()+path.read_bytes() for path in sorted(SCRIPTS.glob('*.py')))).hexdigest()
    fingerprints={'extractor_sha256':extractor_hash}
    for channel in ('release','draft'):
        spec=discovered[channel]
        if spec is None:continue
        version=spec['version']; source=work/channel/'sources'/version
        run('download_unicode.py','--version',version,'--base-url',spec['base_url'],
            '--sources',work/channel/'sources','--refresh')
        records=json.loads((source/'sources.json').read_text())
        fingerprints[channel]=records
        if not args.force and source_signature(previous.get(channel))==source_signature(records) and previous.get('extractor_sha256')==extractor_hash:
            print(f'{channel} sources unchanged',flush=True)
            continue
        output=work/channel/'output'
        run('extract_glyphs.py','--pdf',source/'CodeCharts.pdf','--ucd',source/'UnicodeData.txt',
            '--version',version,'--source-url',spec['base_url']+'charts/CodeCharts.pdf','--output',output)
        run('verify_glyphs.py','--output',output,'--sources',source)
        run('audit_pdf_coverage.py','--output',output,'--sources',source)
        outputs.append(channel)
        target=repo if channel=='release' else repo/'draft'
        target.mkdir(parents=True,exist_ok=True)
        for directory in target.glob('U[0-9A-F][0-9A-F]'):
            if directory.is_dir():shutil.rmtree(directory)
        run('compress_glyphs.py','--source',output,'--destination',target)
        reports=repo/'reports'/channel
        reports.mkdir(parents=True,exist_ok=True)
        for name in ('summary.json','validation.json','pdf-audit.json','SOURCE-NOTICES.txt','extraction.json'):
            shutil.copyfile(output/name,reports/name)
        shutil.copyfile(work/channel/'sources'/discovered[channel]['version']/'sources.json',reports/'sources.json')
        shutil.move(str(target/'compression.json'),reports/'compression.json')
        with (target/'compressed-manifest.jsonl').open('rb') as src:
            with (reports/'compressed-manifest.jsonl.gz').open('wb') as dest:
                with gzip.GzipFile(fileobj=dest,mode='wb',mtime=0,filename='') as z:
                    shutil.copyfileobj(src,z)
        (target/'compressed-manifest.jsonl').unlink()
        # Only one uncompressed extraction is kept at a time on a CI runner.
        shutil.rmtree(output)
    changed=bool(outputs) or bool(previous.get('draft'))!=bool(discovered['draft'])
    if not changed:
        print('No source changes; nothing to publish.');return
    if not discovered['draft']:
        if (repo/'draft').exists():shutil.rmtree(repo/'draft')
        if (repo/'reports/draft').exists():shutil.rmtree(repo/'reports/draft')
    subprocess.run([sys.executable,str(repo/'tools/build_catalog.py'),'--repo',str(repo),
                    '--discovery',str(work/'discovery.json')],check=True)
    (repo/'catalog-state.json').write_text(json.dumps(fingerprints,indent=2)+'\n')


if __name__=='__main__':main()
