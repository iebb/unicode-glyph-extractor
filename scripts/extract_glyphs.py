#!/usr/bin/env python3
"""Extract the displayed reference forms from Unicode PDF code charts as SVG paths.

No installed font is used. Printed chart labels, not the PDF's unreliable
ToUnicode mappings, identify code points. Each SVG contains only its specimen
outlines, a tight viewBox, provenance, and the original document notices.
"""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path

SVG = 'http://www.w3.org/2000/svg'
XLINK = 'http://www.w3.org/1999/xlink'
ET.register_namespace('', SVG)
IDENTITY = (1., 0., 0., 1., 0., 0.)
HEX = re.compile(r'^[0-9A-F]{4,6}$')
STYLE_KEYS = ('fill', 'fill-rule', 'fill-opacity', 'opacity', 'stroke',
              'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity')
ALGORITHM_VERSION = 1
LABEL_FONTS = {'Arial', 'ArialNarrow', 'UCSMyriadProLight', 'Arial,Bold'}


def is_label_font(font):
    return font in LABEL_FONTS or font.startswith('MyriadPro')


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def multiply(a, b):
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def transform_matrix(value):
    result = IDENTITY
    for name, arg in re.findall(r'(\w+)\(([^)]*)\)', value or ''):
        ns = [float(x) for x in re.split(r'[ ,]+', arg.strip()) if x]
        if name == 'matrix' and len(ns) == 6:
            m = tuple(ns)
        elif name == 'translate':
            m = (1., 0., 0., 1., ns[0], ns[1] if len(ns)>1 else 0.)
        elif name == 'scale':
            m = (ns[0], 0., 0., ns[1] if len(ns)>1 else ns[0], 0., 0.)
        else:
            raise ValueError(f'Unsupported SVG transform: {name}({arg})')
        result = multiply(result, m)
    return result


def transformed_bounds(b, m):
    points = [(m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5])
              for x in (b[0], b[2]) for y in (b[1], b[3])]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


@dataclass
class Outline:
    d: str
    matrix: tuple
    bbox: tuple
    style: dict
    font: str
    text: str

    @property
    def center(self):
        return ((self.bbox[0]+self.bbox[2])/2, (self.bbox[1]+self.bbox[3])/2)


def page_spans(page):
    return [s for b in page.get_text('dict')['blocks'] if 'lines' in b
            for line in b['lines'] for s in line['spans']]


def page_outlines(page):
    # Trace gives original font identity even when data-text is a bogus Unicode.
    fonts = {}
    for span in page.get_texttrace():
        for _, _, origin, _ in span['chars']:
            fonts[(round(origin[0], 2), round(origin[1], 2))] = span['font']
    root = ET.fromstring(page.get_svg_image(text_as_path=True))
    definitions = {e.attrib['id']: e for e in root.iter() if 'id' in e.attrib}
    bounds = {}
    result = []

    def visit(node, parent_m=IDENTITY, inherited=None, in_defs=False):
        tag = node.tag.rsplit('}', 1)[-1]
        if tag == 'defs' or in_defs:
            return
        m = multiply(parent_m, transform_matrix(node.get('transform')))
        style = dict(inherited or {})
        style.update({k: node.attrib[k] for k in STYLE_KEYS if k in node.attrib})
        if tag == 'use':
            font = fonts.get((round(m[4],2),round(m[5],2)), '')
            if not font:
                nearest = min(fonts, key=lambda k: abs(k[0]-m[4])+abs(k[1]-m[5]), default=None)
                if nearest is not None and abs(nearest[0]-m[4])+abs(nearest[1]-m[5])<.04:
                    font = fonts[nearest]
            if not font or is_label_font(font):return
            key = node.get(f'{{{XLINK}}}href', node.get('href', '')).lstrip('#')
            definition = definitions.get(key)
            if definition is None or definition.tag.rsplit('}', 1)[-1] != 'path':
                raise ValueError(f'Unsupported outline definition {key}')
            d = definition.get('d', '')
            if key not in bounds:
                pen = BoundsPen(None)
                parse_path(d, pen)
                bounds[key] = pen.bounds
            b = bounds[key]
            if b is not None:
                result.append(Outline(d,m,transformed_bounds(b,m),style,font,node.get('data-text','')))
        for child in node:
            visit(child,m,style)
    visit(root)
    return result


def noncharacter_cells(page, spans):
    """Copy printed noncharacter cells, including their native outlined labels.

    The PDF uses one black rectangle for a pair of these cells. Slice that
    rectangle at the cell boundary; do not invent a character outline.
    """
    labels=[s for s in label_spans(spans) if s['font']=='ArialNarrow'
            and 5.5<s['size']<6.5 and is_noncharacter(int(s['text'],16))]
    if not labels:return {}
    black=[drawing for drawing in page.get_drawings() if drawing['type']=='f'
           and drawing['fill'] and all(value<.001 for value in drawing['fill'])
           and len(drawing['items'])==1 and drawing['items'][0][0]=='re']
    cells={}
    for drawing in black:
        rectangle=drawing['rect']
        inside=[label for label in labels if rectangle.contains(pymupdf.Point(*label['origin']))]
        if not inside:continue
        inside.sort(key=lambda label:label['origin'][1])
        height=rectangle.height/len(inside)
        for index,label in enumerate(inside):
            box=(rectangle.x0,rectangle.y0+index*height,rectangle.x1,rectangle.y0+(index+1)*height)
            data=f'M{box[0]:.12g} {box[1]:.12g}H{box[2]:.12g}V{box[3]:.12g}H{box[0]:.12g}Z'
            background=Outline(data,IDENTITY,box,{'fill':'#000000'},'[PDF cell background]','')
            cells[int(label['text'],16)]={'label':label,'outlines':[background],
                'pdf_cell_bbox':box,'pdf_background_bbox':tuple(rectangle),
                'specimen_kind':'noncharacter-chart-cell'}
    if not cells:return {}
    root=ET.fromstring(page.get_svg_image(text_as_path=True))
    definitions={element.get('id'):element for element in root.iter() if element.get('id')}

    def visit(node,parent_matrix=IDENTITY,inherited=None):
        tag=node.tag.rsplit('}',1)[-1]
        if tag=='defs':return
        matrix=multiply(parent_matrix,transform_matrix(node.get('transform')))
        style=dict(inherited or {})
        style.update({key:node.attrib[key] for key in STYLE_KEYS if key in node.attrib})
        if tag=='use':
            for cell in cells.values():
                label=cell['label']
                if abs(matrix[5]-label['origin'][1])<.03 and label['bbox'][0]-.03<=matrix[4]<=label['bbox'][2]+.03:
                    key=node.get(f'{{{XLINK}}}href',node.get('href','')).lstrip('#')
                    data=definitions[key].get('d','')
                    pen=BoundsPen(None);parse_path(data,pen)
                    if pen.bounds:
                        cell['outlines'].append(Outline(data,matrix,transformed_bounds(pen.bounds,matrix),
                                                       style,'ArialNarrow',node.get('data-text','')))
        for child in node:visit(child,matrix,style)
    visit(root)
    for cp,cell in cells.items():
        if len(cell['outlines'])!=len(cell['label']['text'])+1:
            raise ValueError(f'Incomplete native label for noncharacter cell {cp:X}')
    return cells


def label_spans(spans):
    return [s for s in spans if HEX.fullmatch(s['text'])
            and 60 < s['origin'][1] < 735]


def grid_forms(spans, outlines):
    labels = [s for s in label_spans(spans)
              if s['font']=='ArialNarrow' and 5.5 < s['size'] < 6.5]
    if not labels:
        return None
    # Empty/unassigned cells have no printed label; derive full column geometry
    # from the cp high bits, and never assign a glyph to a neighbouring codepoint.
    columns = defaultdict(list)
    for s in labels:
        columns[int(s['text'],16)>>4].append((s['bbox'][0]+s['bbox'][2])/2)
    centers = {k: statistics.median(v) for k,v in columns.items()}
    by_cp = {int(s['text'],16):s for s in labels}
    heights = [abs(a['origin'][1]-b['origin'][1])/abs((int(a['text'],16)&15)-(int(b['text'],16)&15))
               for a,b in zip(labels,labels[1:])
               if (int(a['text'],16)>>4)==(int(b['text'],16)>>4)
               and (int(a['text'],16)&15)!=(int(b['text'],16)&15)]
    height = statistics.median(heights) if heights else 39.6
    pitch_values = [abs(centers[a]-centers[b])/abs(a-b) for a,b in zip(sorted(centers),sorted(centers)[1:])]
    pitch = statistics.median(pitch_values) if pitch_values else 31.68
    # All row label baselines are the same across the chart.
    baseline0 = statistics.median(s['origin'][1]-(int(s['text'],16)&15)*height for s in labels)
    groups = defaultdict(list)
    unassigned = []
    for outline in outlines:
        x,y = outline.center
        if not min(centers.values())-pitch/2 <= x <= max(centers.values())+pitch/2:
            # Some small charts share their page with the explanatory names list.
            continue
        col = min(centers, key=lambda k:abs(centers[k]-x))
        row = math.floor((outline.matrix[5]-baseline0+height)/height)
        cp = (col<<4)+row
        if abs(x-centers[col])>pitch*.65 or not 0<=row<=15 or cp not in by_cp:
            unassigned.append({'bbox':outline.bbox,'font':outline.font,'origin':outline.matrix[4:]})
            continue
        groups[cp].append(outline)
    forms = [{'cp':cp,'source':None,'outlines':groups.get(cp,[])} for cp in sorted(by_cp)]
    return forms, unassigned


def source_forms(spans, outlines):
    labels = [s for s in label_spans(spans) if s['font']=='Arial' and 9.5<s['size']<10.5]
    if not labels:
        return None
    sources = [dict(s) for s in spans if s['font']=='UnihanInfo' and s['text'].strip()]
    # Narrow CJK columns occasionally wrap a source identifier, e.g.
    # "GKX-" / "0078.14". Rejoin those two spans before matching the glyph.
    consumed=set()
    for i,s in enumerate(sources):
        if not s['text'].endswith(('-',':')):continue
        tails=[(j,t) for j,t in enumerate(sources) if j!=i
               and abs(t['origin'][0]-s['origin'][0])<.1
               and 0<t['origin'][1]-s['origin'][1]<10
               and re.fullmatch(r'[0-9A-Za-z.]+',t['text'])]
        if len(tails)!=1:raise ValueError(f"Ambiguous wrapped source {s['text']}")
        j,t=tails[0];s['text']+=t['text'];consumed.add(j)
        s['bbox']=(min(s['bbox'][0],t['bbox'][0]),min(s['bbox'][1],t['bbox'][1]),
                   max(s['bbox'][2],t['bbox'][2]),max(s['bbox'][3],t['bbox'][3]))
    sources=[s for i,s in enumerate(sources) if i not in consumed]
    groups = defaultdict(list)
    unassigned = []
    for outline in outlines:
        if math.hypot(outline.matrix[0],outline.matrix[1])<11.5:
            # Radical/stroke annotations and modern equivalents use six-point
            # glyphs beside the code label in source tables.
            continue
        x,y = outline.center
        origin_y = outline.matrix[5]
        possible = [s for s in labels if s['bbox'][2]+3<x and s['origin'][1]<=origin_y+5]
        if not possible:
            unassigned.append({'bbox':outline.bbox,'font':outline.font,'origin':outline.matrix[4:]})
            continue
        # A character's forms may wrap over two or more source rows. Its cell
        # continues until the next codepoint label in this same table column.
        column_x = max(s['origin'][0] for s in possible)
        same_column = [s for s in possible if abs(s['origin'][0]-column_x)<1]
        label = max(same_column,key=lambda s:s['origin'][1])
        nearby = [s for s in sources if 0<s['origin'][1]-origin_y<32
                  and abs((s['bbox'][0]+s['bbox'][2])/2-x)<20]
        if not nearby:
            unassigned.append({'bbox':outline.bbox,'font':outline.font,'origin':outline.matrix[4:],
                               'reason':'no source identifier','codepoint':label['text']})
            continue
        source = min(nearby,key=lambda s:abs((s['bbox'][0]+s['bbox'][2])/2-x))['text'].strip()
        sequence = re.fullmatch(r'([0-9A-F]{4,6})(?: ([0-9A-F]{4,6}))?', source)
        if sequence:
            # Variation tables print a full base+selector sequence underneath
            # each outline. Prefer this exact label to inferred cell geometry.
            label_cp = int(sequence[1],16)
            if label_cp not in {int(s['text'],16) for s in labels}:
                raise ValueError(f'Unexpected variation-table label {source}')
        else:
            label_cp = int(label['text'],16)
        groups[(label_cp,source)].append(outline)
    forms = [{'cp':cp,'source':source,'outlines':os_} for (cp,source),os_ in sorted(groups.items())]
    found = {cp for cp,_ in groups}
    for label in labels:
        cp = int(label['text'],16)
        if cp not in found:
            unassigned.append({'kind':'empty_source_cell','codepoint':label['text']})
    return forms,unassigned


def code_path(cp):
    h = f'{cp:06X}'
    return Path(f'U{h[:2]}') / h[2:4] / h[4:6]


def safe_variant(source):
    return re.sub(r'[^A-Za-z0-9_-]+','_',source).strip('_') or 'reference'


def is_noncharacter(cp):
    return (cp&0xFFFF)>=0xFFFE or 0xFDD0<=cp<=0xFDEF


def page_selection(value,total):
    result=set()
    for part in value.split(','):
        lo,_,hi=part.partition('-')
        result.update(range(int(lo),int(hi or lo)+1))
    if not result or min(result)<1 or max(result)>total:raise ValueError('Page range outside PDF')
    return result


def write_svg(path, outlines, metadata, notices, padding):
    bbox = (min(o.bbox[0] for o in outlines),min(o.bbox[1] for o in outlines),
            max(o.bbox[2] for o in outlines),max(o.bbox[3] for o in outlines))
    x0,y0,x1,y1 = bbox
    x0-=padding; y0-=padding; x1+=padding; y1+=padding
    width,height=x1-x0,y1-y0
    root=ET.Element(f'{{{SVG}}}svg',{'version':'1.1','width':f'{width:.6f}',
                                  'height':f'{height:.6f}','viewBox':f'0 0 {width:.6f} {height:.6f}'})
    ET.SubElement(root,f'{{{SVG}}}title').text=f"U+{metadata['codepoint']} {metadata.get('source_identifier') or 'reference'}"
    ET.SubElement(root,f'{{{SVG}}}metadata').text=json.dumps({**metadata,'pdf_bbox':bbox,
        'viewbox_pdf_origin':[x0,y0],'original_document_notices':notices},ensure_ascii=True)
    for outline in outlines:
        a,b,c,d,e,f=outline.matrix
        attrs={'d':outline.d,'transform':f'matrix({a:.9g} {b:.9g} {c:.9g} {d:.9g} {e-x0:.9g} {f-y0:.9g})',**outline.style}
        ET.SubElement(root,f'{{{SVG}}}path',attrs)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.svg.tmp')
    temp.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
    os.replace(temp,path)
    return bbox


def load_ucd(path):
    characters={}
    first=None
    for line in path.read_text().splitlines():
        fields=line.split(';')
        cp=int(fields[0],16);name=fields[1];category=fields[2]
        if name.endswith(', First>'):
            first=(cp,name,category)
        elif name.endswith(', Last>'):
            if first is None:raise ValueError('Unmatched UCD range')
            start,base,cat=first
            for n in range(start,cp+1):characters[n]=(base.replace(', First>','>'),cat)
            first=None
        else:
            characters[cp]=(name,category)
    return characters


def extract(args):
    doc=pymupdf.open(args.pdf)
    notices=doc[0].get_text()
    short_version='.'.join(args.version.split('.')[:2])
    if not re.search(r'\bVersion\s+'+re.escape(short_version)+r'\b',notices):
        raise ValueError('PDF version does not match --version')
    out=args.output
    out.mkdir(parents=True,exist_ok=True)
    checkpoint=out/'.pages';checkpoint.mkdir(exist_ok=True)
    digest=sha256(args.pdf)
    settings={'algorithm_version':ALGORITHM_VERSION,'pdf_sha256':digest,'unicode_version':args.version,
              'ucd_sha256':sha256(args.ucd) if args.ucd else None,
              'padding':args.padding,'remove_dotted_circle':args.remove_dotted_circle,
              'pages':args.pages}
    state_path=out/'extraction.json'
    if state_path.exists():
        old=json.loads(state_path.read_text())
        if old!=settings:raise ValueError('Output belongs to different input/settings; select a new --output directory.')
        if not args.resume:raise ValueError('Output already has extraction state. Use --resume or a new --output directory.')
    elif any(out.glob('U*')):
        raise ValueError('Output already contains glyphs without matching extraction state.')
    else:
        state_path.write_text(json.dumps(settings,indent=2)+'\n')
    (out/'SOURCE-NOTICES.txt').write_text(notices)
    ucd=load_ucd(args.ucd) if args.ucd else {}
    pages=set()
    if args.pages:
        pages=page_selection(args.pages,len(doc))
    else:pages=set(range(1,len(doc)+1))
    reprocess=page_selection(args.reprocess_pages,len(doc)) if args.reprocess_pages else set()
    title_by_page={page:title for _,title,page in doc.get_toc()}
    block='';records=[];issues=[];layouts=defaultdict(int);start=time.monotonic()
    for number in range(1,len(doc)+1):
        block=title_by_page.get(number,block)
        if number not in pages:continue
        cpfile=checkpoint/f'{number:05d}.json'
        if args.resume and cpfile.exists() and number not in reprocess:
            result=json.loads(cpfile.read_text())
        else:
            if cpfile.exists():
                previous=json.loads(cpfile.read_text())
                for record in previous['records']:
                    old=out/record['path']
                    if old.exists():old.unlink()
                    else:
                        singleton=(out/code_path(int(record['codepoint'],16))).with_suffix('.svg')
                        singleton.unlink(missing_ok=True)
                    if old.parent.exists() and not any(old.parent.iterdir()):old.parent.rmdir()
                cpfile.unlink()
            page=doc[number-1];spans=page_spans(page)
            labels=label_spans(spans)
            grid=any(s['font']=='ArialNarrow' and 5.5<s['size']<6.5 for s in labels)
            sources=any(s['font']=='Arial' and 9.5<s['size']<10.5 for s in labels)
            result={'page':number,'layout':'annotations','records':[],'issues':[]}
            if grid or sources:
                outlines=page_outlines(page)
                mapped=grid_forms(spans,outlines) if grid else source_forms(spans,outlines)
                forms,unassigned=mapped
                conventions=noncharacter_cells(page,spans) if any(is_noncharacter(form['cp']) for form in forms) else {}
                result['layout']='grid' if grid else 'sources'
                for issue in unassigned:result['issues'].append({'page':number,'block':block,'kind':'unassigned_outline',**issue})
                variants=set()
                for form in forms:
                    cp=form['cp'];os_=form['outlines']
                    if cp in conventions:
                        convention=conventions[cp]
                        os_=[convention['outlines'][0],*os_,*convention['outlines'][1:]]
                        form.update({key:convention[key] for key in ('specimen_kind','pdf_cell_bbox','pdf_background_bbox')})
                    if args.remove_dotted_circle:
                        os_=[o for o in os_ if not (o.font=='SpecialsUC6' and o.text=='$')]
                    if not os_:
                        result['issues'].append({'page':number,'block':block,'kind':'empty_specimen','codepoint':f'{cp:04X}'})
                        continue
                    sequence=re.fullmatch(r'([0-9A-F]{4,6})(?: ([0-9A-F]{4,6}))?',form['source'] or '')
                    variant=(sequence[2] or 'table-reference') if sequence else (safe_variant(form['source']) if form['source'] else 'reference')
                    if (cp,variant) in variants:raise ValueError(f'Duplicate variant {cp:X}/{variant} on page {number}')
                    variants.add((cp,variant))
                    # Initial directories keep all variants; singletons become .svg at finalization.
                    path=code_path(cp)/f'{variant}.svg'
                    if (out/path).exists() and not args.resume:raise ValueError(f'Duplicate specimen path {path}')
                    name,category=ucd.get(cp,('', ''))
                    meta={'codepoint':f'{cp:04X}','unicode_version':args.version,'page':number,
                          'block':block,'source_identifier':form['source'],'name':name,'category':category,
                          'source_pdf':args.pdf.name,'source_url':args.source_url,'pdf_sha256':digest,
                          'fonts':sorted({o.font for o in os_ if o.font!='[PDF cell background]'}),
                          'dotted_circle':any(o.font=='SpecialsUC6' and o.text=='$' for o in os_)}
                    if sequence and sequence[2]:meta['variation_sequence']=[sequence[1],sequence[2]]
                    for key in ('specimen_kind','pdf_cell_bbox','pdf_background_bbox'):
                        if key in form:meta[key]=form[key]
                    bbox=write_svg(out/path,os_,meta,notices,args.padding)
                    result['records'].append({**meta,'path':path.as_posix(),'pdf_bbox':bbox,
                                              'outline_count':len(os_)})
            temp=cpfile.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=True));os.replace(temp,cpfile)
        records.extend(result['records']);issues.extend(result['issues']);layouts[result['layout']]+=1
        if number%50==0 or number==max(pages):
            print(f'Page {number}/{len(doc)}: {len(records):,} forms, {len(issues)} issues, {time.monotonic()-start:.0f}s',flush=True)
    groups=defaultdict(list)
    for record in records:groups[int(record['codepoint'],16)].append(record)
    for cp,rows in groups.items():
        if len(rows)==1:
            row=rows[0];old=out/row['path'];new=(out/code_path(cp)).with_suffix('.svg')
            if old.exists():
                os.replace(old,new)
                old.parent.rmdir()
            elif not new.exists():raise ValueError(f'Missing resumed specimen {new}')
            row['path']=new.relative_to(out).as_posix()
        else:
            for row in rows:
                target=out/row['path']
                singleton=(out/code_path(cp)).with_suffix('.svg')
                if not target.exists() and singleton.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(singleton,target)
                if not target.exists():raise ValueError(f'Missing resumed specimen {target}')
    records.sort(key=lambda r:(int(r['codepoint'],16),r['path']))
    temp_manifest=out/'manifest.jsonl.tmp'
    with temp_manifest.open('w') as f:
        for record in records:f.write(json.dumps(record,ensure_ascii=True)+'\n')
    os.replace(temp_manifest,out/'manifest.jsonl')
    covered=set(groups)
    # Private-use and surrogate ranges have no distinct assigned reference forms.
    expected={cp for cp,(_,cat) in ucd.items() if cat not in ('Co','Cs')}
    missing=sorted(expected-covered) if not args.pages else []
    unexpected=sorted(cp for cp in covered-set(ucd) if not is_noncharacter(cp)) if ucd else []
    (out/'issues.json').write_text(json.dumps(issues,indent=2)+'\n')
    (out/'missing.json').write_text(json.dumps([{'codepoint':f'{cp:04X}','name':ucd[cp][0],'category':ucd[cp][1]} for cp in missing],indent=2)+'\n')
    summary={'unicode_version':args.version,'source_url':args.source_url,'pdf_sha256':digest,
             'pdf_pages':len(doc),'processed_pages':len(pages),'layouts':dict(layouts),
             'codepoints':len(covered),'svg_files':len(records),'multiple_form_codepoints':sum(len(v)>1 for v in groups.values()),
             'noncharacter_reference_codepoints':sum(is_noncharacter(cp) for cp in covered),
             'noncharacter_chart_cells':sum(row.get('specimen_kind')=='noncharacter-chart-cell' for row in records),
             'expected_codepoints':len(expected),'missing_codepoints':len(missing),
             'unexpected_codepoints':[f'{cp:04X}' for cp in unexpected],
             'issues':len(issues),'complete_pdf':not bool(args.pages),'elapsed_seconds':round(time.monotonic()-start,2)}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)
    return 1 if missing or unexpected or issues else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdf',type=Path,help='Default: sources/<version>/CodeCharts.pdf')
    p.add_argument('--ucd',type=Path,help='Default: sources/<version>/UnicodeData.txt')
    p.add_argument('--version',default='18.0.0')
    p.add_argument('--source-url',help='Default: the versioned official consolidated chart URL')
    p.add_argument('--output',type=Path,help='Default: glyphs/<version>')
    p.add_argument('--pages',help='One-based page selection, e.g. 2,34,558-559 (partial validation only)')
    p.add_argument('--padding',type=float,default=1.0,help='Transparent padding in PDF points')
    p.add_argument('--remove-dotted-circle',action='store_true',help='Remove the chart dotted-circle helper from combining glyphs')
    p.add_argument('--resume',action='store_true',help='Reuse completed page checkpoints for identical settings')
    p.add_argument('--reprocess-pages',help='Rebuild selected page checkpoints during --resume, e.g. 2,558-559')
    args=p.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+',args.version):p.error('--version must look like 18.0.0')
    args.pdf=args.pdf or Path('sources')/args.version/'CodeCharts.pdf'
    args.ucd=args.ucd or Path('sources')/args.version/'UnicodeData.txt'
    args.output=args.output or Path('glyphs')/args.version
    args.source_url=args.source_url or f'https://www.unicode.org/Public/{args.version}/charts/CodeCharts.pdf'
    if not math.isfinite(args.padding) or args.padding<0:p.error('--padding must be finite and nonnegative')
    if args.reprocess_pages and not args.resume:p.error('--reprocess-pages requires --resume')
    try:
        return extract(args)
    except (ValueError,OSError) as e:
        print(f'Extraction failed: {e}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
