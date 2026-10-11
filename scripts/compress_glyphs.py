#!/usr/bin/env python3
"""Losslessly convert an extracted collection to deterministic SVGZ files."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def compress(source, destination):
    destination.mkdir(parents=True,exist_ok=True)
    plain=packed=files=0
    inventory=destination/'compressed-manifest.jsonl'
    with inventory.open('w') as log:
        for line in (source/'manifest.jsonl').open():
            row=json.loads(line)
            data=(source/row['path']).read_bytes()
            encoded=gzip.compress(data,compresslevel=9,mtime=0)
            # A round trip must preserve every byte, including notices and metadata.
            if gzip.decompress(encoded)!=data:raise ValueError('Compression round trip failed')
            path=Path(row['path']).with_suffix('.svgz')
            dest=destination/path;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(encoded)
            row['svg_path']=row['path'];row['path']=path.as_posix()
            row['svg_sha256']=hashlib.sha256(data).hexdigest()
            row['svgz_sha256']=hashlib.sha256(encoded).hexdigest()
            row['svg_bytes']=len(data);row['svgz_bytes']=len(encoded)
            log.write(json.dumps(row,separators=(',',':'))+'\n')
            plain+=len(data);packed+=len(encoded);files+=1
            if files%50000==0:print(f'Compressed {files:,} SVGs',flush=True)
    result={'format':'svgz','files':files,'svg_bytes':plain,'svgz_bytes':packed,
            'round_trip_verified':True,'saved_percent':round((1-packed/plain)*100,2)}
    (destination/'compression.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    if a.source.resolve()==a.destination.resolve():p.error('Use a separate destination')
    compress(a.source,a.destination)


if __name__=='__main__':main()
