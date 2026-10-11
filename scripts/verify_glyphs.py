#!/usr/bin/env python3
"""Audit every exported SVG, codepoint coverage, source identities, and PDF fidelity."""
import argparse
from collections import defaultdict, Counter
import json
import math
from pathlib import Path
import random
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import pymupdf
from extract_glyphs import SVG, code_path, load_ucd, sha256, is_noncharacter


def source_identities(directory,version='18.0.0'):
    allowed=defaultdict(set)
    with zipfile.ZipFile(directory/'Unihan.zip') as z:
        lines=z.read('Unihan_IRGSources.txt').decode().splitlines()
    for line in lines:
        if not line.startswith('U+'):continue
        cp,prop,values=line.split('\t')
        if re.fullmatch(r'kIRG_\w+Source',prop):
            allowed[int(cp[2:],16)].update(values.split())
    names=['TangutSources','NushuSources']
    if int(version.split('.')[0])>=18:names+=['SealSources','JurchenSources']
    for name in names:
        for line in (directory/(name+'.txt')).read_text().splitlines():
            if not line.startswith('U+'):continue
            cp,prop,values=line.split('\t')
            if prop.endswith('Src'):
                allowed[int(cp[2:],16)].update(values.split())
    variants=set()
    for line in (directory/'StandardizedVariants.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            variants.add(line.split(';')[0].strip())
    return allowed,variants


def raster_compare(row,root,pdf,padding,scale=4):
    # Restore the original page coordinates before rendering. This keeps pixel
    # alignment identical, rather than comparing two independently cropped grids.
    x0,y0,x1,y1=row['pdf_bbox']
    page=pdf[row['page']-1]
    wrapper=ET.Element(f'{{{SVG}}}svg',{'width':str(page.rect.width),'height':str(page.rect.height),
                                     'viewBox':f'0 0 {page.rect.width} {page.rect.height}'})
    group=ET.SubElement(wrapper,f'{{{SVG}}}g',{'transform':f'translate({x0-padding} {y0-padding})'})
    for element in root.findall(f'{{{SVG}}}path'):group.append(element)
    svg_doc=pymupdf.open(stream=ET.tostring(wrapper),filetype='svg')
    rect=(pymupdf.Rect(x0,y0,x1,y1) if row.get('specimen_kind')=='noncharacter-chart-cell'
          else pymupdf.Rect(x0-padding,y0-padding,x1+padding,y1+padding))
    matrix=pymupdf.Matrix(scale,scale)
    a=page.get_pixmap(matrix=matrix,clip=rect,colorspace=pymupdf.csGRAY,alpha=False)
    b=svg_doc[0].get_pixmap(matrix=matrix,clip=rect,colorspace=pymupdf.csGRAY,alpha=False)
    if (a.width,a.height)!=(b.width,b.height):
        return {'error':'raster dimension mismatch','pdf':[a.width,a.height],'svg':[b.width,b.height]}
    a_bytes=bytearray(a.samples);b_bytes=bytearray(b.samples)
    ignored=set()
    # Tight specimen bounds can intersect a chart rule when the glyph nearly
    # fills its cell. Rules are intentionally absent from the exported specimen.
    for drawing in page.get_drawings():
        if drawing['type']!='s' or drawing['width']>2:continue
        for item in drawing['items']:
            if item[0]!='l':continue
            start,end=item[1:3]
            if abs(start.x-end.x)>.01 and abs(start.y-end.y)>.01:continue
            if abs(start.x-end.x)+abs(start.y-end.y)<30:continue
            half=drawing['width']/2+.25
            bx0=min(start.x,end.x)-half;bx1=max(start.x,end.x)+half
            by0=min(start.y,end.y)-half;by1=max(start.y,end.y)+half
            ix0=max(0,math.floor(bx0*scale)-a.x);ix1=min(a.width,math.ceil(bx1*scale)-a.x)
            iy0=max(0,math.floor(by0*scale)-a.y);iy1=min(a.height,math.ceil(by1*scale)-a.y)
            for yy in range(iy0,iy1):
                for xx in range(ix0,ix1):
                    index=yy*a.width+xx;ignored.add(index);a_bytes[index]=b_bytes[index]=255
    raw_error=sum(abs(x-y) for x,y in zip(a_bytes,b_bytes))
    ink=sum(255-y for y in b_bytes)
    # PDF font hinting may move an edge by one raster pixel relative to the
    # unhinted SVG outline. Compare ink with this explicit 0.25-point tolerance.
    radius=max(1,int(scale*.25))
    def unmatched(source,target):
        error=0;w=a.width;h=a.height
        for y in range(h):
            for x in range(w):
                i=y*w+x
                if source[i]==255:continue
                nearest=min(target[yy*w+xx] for yy in range(max(0,y-radius),min(h,y+radius+1))
                            for xx in range(max(0,x-radius),min(w,x+radius+1)))
                error+=max(0,nearest-source[i])
        return error
    error=unmatched(a_bytes,b_bytes)+unmatched(b_bytes,a_bytes)
    return {'normalized_ink_error':round(error/max(ink,1),6),
            'raw_ink_error':round(raw_error/max(ink,1),6),'pixels':len(a_bytes),
            'edge_tolerance_pdf_points':.25,'raster_scale':scale,'ignored_chart_rule_pixels':len(ignored)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('glyphs/18.0.0'))
    p.add_argument('--sources',type=Path,default=Path('sources/18.0.0'))
    p.add_argument('--samples',type=int,default=256)
    p.add_argument('--seed',type=int,default=1800)
    p.add_argument('--max-ink-error',type=float,default=.08)
    p.add_argument('--report-name',default='validation.json',help='JSON report filename inside --output')
    args=p.parse_args()
    if Path(args.report_name).name!=args.report_name or not args.report_name.endswith('.json'):
        p.error('--report-name must be a plain .json filename')
    settings=json.loads((args.output/'extraction.json').read_text())
    summary=json.loads((args.output/'summary.json').read_text())
    pdf_path=args.sources/'CodeCharts.pdf'
    errors=[]
    if sha256(pdf_path)!=settings['pdf_sha256']:errors.append({'kind':'pdf_hash_mismatch'})
    if settings.get('ucd_sha256') and sha256(args.sources/'UnicodeData.txt')!=settings['ucd_sha256']:
        errors.append({'kind':'ucd_hash_mismatch'})
    allowed,variants=source_identities(args.sources,settings['unicode_version'])
    ucd=load_ucd(args.sources/'UnicodeData.txt')
    expected={cp for cp,(_,category) in ucd.items() if category not in ('Co','Cs')}
    rows=[json.loads(line) for line in (args.output/'manifest.jsonl').open()]
    by_cp=defaultdict(list)
    for row in rows:by_cp[int(row['codepoint'],16)].append(row)
    if summary['complete_pdf']:
        for cp in sorted(expected-set(by_cp)):errors.append({'kind':'missing_codepoint','codepoint':f'{cp:04X}'})
    for cp in sorted(set(by_cp)-set(ucd)):
        if not is_noncharacter(cp):errors.append({'kind':'unexpected_codepoint','codepoint':f'{cp:04X}'})
    counts=Counter(row['path'] for row in rows)
    for name,count in counts.items():
        if count!=1:errors.append({'kind':'duplicate_path','path':name,'count':count})
    randomizer=random.Random(args.seed)
    # Include every displayed chart block and additional random variants.
    by_block=defaultdict(list)
    for i,row in enumerate(rows):by_block[row['block']].append(i)
    selected={randomizer.choice(indices) for indices in by_block.values()} if args.samples else set()
    if args.samples:
        selected.update(randomizer.sample(range(len(rows)),min(args.samples,len(rows))))
        selected.update(i for i,row in enumerate(rows) if row.get('specimen_kind')=='noncharacter-chart-cell')
    pdf=pymupdf.open(pdf_path)
    raster=[];source_checks=0;sequence_checks=0;empty_fonts=0
    for i,row in enumerate(rows):
        cp=int(row['codepoint'],16);path=args.output/row['path']
        base=code_path(cp).as_posix()
        correct=(row['path']==base+'.svg') if len(by_cp[cp])==1 else (path.parent.relative_to(args.output).as_posix()==base)
        if not correct:errors.append({'kind':'wrong_path','path':row['path']})
        try:
            root=ET.parse(path).getroot()
            if root.tag!=f'{{{SVG}}}svg':raise ValueError('Missing SVG root')
            box=[float(v) for v in root.attrib['viewBox'].split()]
            if len(box)!=4 or not all(math.isfinite(v) for v in box) or box[2]<=0 or box[3]<=0:raise ValueError('Invalid viewBox')
            paths=root.findall(f'{{{SVG}}}path')
            if not paths or len(paths)!=row['outline_count']:raise ValueError('Missing specimen outlines')
            for element in root.iter():
                if element.tag.rsplit('}',1)[-1] not in ('svg','path','title','metadata'):raise ValueError('Unexpected element or external font dependency')
                if any('href' in attr for attr in element.attrib):raise ValueError('External reference')
            meta=json.loads(root.find(f'{{{SVG}}}metadata').text)
            for key in ['codepoint','page','source_identifier','pdf_sha256']:
                if meta[key]!=row[key]:raise ValueError(f'Metadata mismatch for {key}')
            if not meta.get('original_document_notices'):raise ValueError('Missing source notices')
            if '' in row['fonts']:empty_fonts+=1
            source=row['source_identifier']
            if row.get('variation_sequence'):
                sequence_checks+=1
                if ' '.join(row['variation_sequence']) not in variants:errors.append({'kind':'unknown_variation_sequence','path':row['path'],'source':source})
            elif source and cp in allowed:
                source_checks+=1
                if source not in allowed[cp]:errors.append({'kind':'source_identity_mismatch','path':row['path'],'source':source,'allowed':sorted(allowed[cp])})
            if i in selected:
                comparison=raster_compare(row,root,pdf,settings['padding'])
                if comparison.get('normalized_ink_error',0)>args.max_ink_error:
                    comparison=raster_compare(row,root,pdf,settings['padding'],scale=8)
                raster.append({'path':row['path'],'page':row['page'],**comparison})
                if comparison.get('error') or comparison.get('normalized_ink_error',0)>args.max_ink_error:
                    errors.append({'kind':'raster_difference',**raster[-1]})
        except (OSError,ValueError,ET.ParseError,KeyError) as e:
            errors.append({'kind':'invalid_svg','path':row['path'],'error':str(e)})
        if (i+1)%50000==0:print(f'Validated {i+1:,}/{len(rows):,} SVGs',flush=True)
    actual={p.relative_to(args.output).as_posix() for p in args.output.glob('U*/*/**/*.svg')}
    for extra in sorted(actual-set(counts)):errors.append({'kind':'orphan_svg','path':extra})
    if summary['issues']:errors.append({'kind':'extraction_issues','count':summary['issues']})
    result={'passed':not errors,'svg_files_checked':len(rows),'codepoints_checked':len(by_cp),
            'source_identity_checks':source_checks,'variation_sequence_checks':sequence_checks,
            'raster_samples':len(raster),'max_normalized_ink_error':max((r.get('normalized_ink_error',0) for r in raster),default=0),
            'missing_font_identities':empty_fonts,'errors':errors,'raster_comparisons':raster}
    (args.output/args.report_name).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('errors','raster_comparisons')},indent=2))
    if errors:print(json.dumps(errors[:20],indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    sys.exit(main())
