#!/usr/bin/env python3
"""Discover the official final release and optional public alpha/beta draft."""
import argparse
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = 'https://www.unicode.org/Public/'


def read_text(url, optional=False):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'UnicodeSpecimenExtractor/1.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read(1_000_000).decode('utf-8-sig')
    except urllib.error.HTTPError as e:
        if optional and e.code in (404, 410):return ''
        raise


def version(text):
    # An empty draft directory can return HTML with status 200, even for ReadMe.txt.
    if '<html' in text.lower() or 'currently empty' in text.lower():return None
    match = re.search(r'(?:version|Unicode(?: Standard)?)[\s-]+(\d+\.\d+\.\d+)', text, re.I)
    return match.group(1) if match else None


def discover():
    final = version(read_text(ROOT + 'UCD/latest/ReadMe.txt'))
    if not final:raise ValueError('Official latest ReadMe does not identify a final release')
    draft = None
    for relative in ('draft/ReadMe.txt', 'draft/ucd/ReadMe.txt', 'draft/charts/ReadMe.txt'):
        candidate = version(read_text(ROOT + relative, optional=True))
        if candidate:
            if tuple(map(int,candidate.split('.'))) > tuple(map(int,final.split('.'))):
                draft = {'version':candidate,'base_url':ROOT+'draft/','status':'draft'}
            break
    return {'release':{'version':final,'base_url':ROOT+final+'/','status':'release'},
            'draft':draft}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    result=discover()
    encoded=json.dumps(result,indent=2)+'\n'
    if args.output:args.output.write_text(encoded)
    print(encoded,end='')


if __name__=='__main__':main()
