#!/usr/bin/env python3
"""Compare every exported outline and every printed chart label with the source PDF.

This audit does not call the extractor's glyph selection or codepoint assignment
functions. It independently inventories chart labels, reference-font uses, and
their exact path data in the original page coordinate system.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path

from extract_glyphs import multiply, transform_matrix, transformed_bounds, sha256

SVG = 'http://www.w3.org/2000/svg'
XLINK = 'http://www.w3.org/1999/xlink'
IDENTITY = (1., 0., 0., 1., 0., 0.)
STYLE_KEYS = ('fill', 'fill-rule', 'fill-opacity', 'opacity', 'stroke',
              'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity')
HEX = re.compile(r'[0-9A-F]{4,6}\Z')


def outline_key(data, matrix, style):
    return (hashlib.sha256(data.encode()).hexdigest(),
            tuple(round(v, 6) for v in matrix[:4]), tuple(sorted(style.items())))


def font_is_typography(name):
    return name in ('Arial', 'ArialNarrow', 'UCSMyriadProLight', 'Arial,Bold') or name.startswith('MyriadPro')


def noncharacter(cp):
    return 0xFDD0 <= cp <= 0xFDEF or cp & 0xFFFF in (0xFFFE, 0xFFFF)


def printed_labels(page):
    spans = [span for block in page.get_text('dict')['blocks'] if 'lines' in block
             for line in block['lines'] for span in line['spans']]
    labels = [span for span in spans if HEX.fullmatch(span['text'])
              and 60 < span['origin'][1] < 735]
    grid = [span for span in labels if span['font'] == 'ArialNarrow'
            and 5.5 < span['size'] < 6.5]
    source = [span for span in labels if span['font'] == 'Arial'
              and 9.5 < span['size'] < 10.5]
    if grid:
        return 'grid', grid, spans
    if source:
        return 'sources', source, spans
    return 'annotations', [], spans


def grid_region(labels):
    centers = defaultdict(list)
    for label in labels:
        centers[int(label['text'], 16) >> 4].append((label['bbox'][0] + label['bbox'][2]) / 2)
    positions = {column: statistics.median(values) for column, values in centers.items()}
    ordered = sorted(positions)
    pitches = [(positions[b] - positions[a]) / (b - a) for a, b in zip(ordered, ordered[1:])]
    pitch = statistics.median(pitches) if pitches else 31.68
    return min(positions.values()) - pitch / 2, max(positions.values()) + pitch / 2


def convention_labels(page, labels):
    rectangles=[drawing['rect'] for drawing in page.get_drawings()
                if drawing['type']=='f' and drawing['fill']
                and all(value<.001 for value in drawing['fill'])
                and len(drawing['items'])==1 and drawing['items'][0][0]=='re']
    result={}
    for rectangle in rectangles:
        inside=[label for label in labels if noncharacter(int(label['text'],16))
                and rectangle.contains(pymupdf.Point(*label['origin']))]
        inside.sort(key=lambda label:label['origin'][1])
        for index,label in enumerate(inside):
            height=rectangle.height/len(inside)
            cell=(rectangle.x0,rectangle.y0+index*height,rectangle.x1,rectangle.y0+(index+1)*height)
            result[int(label['text'],16)]={'label':label,'background':tuple(rectangle),'cell':cell}
    return result


def reference_uses(page, layout, labels, conventions):
    trace_fonts = {}
    for span in page.get_texttrace():
        for _, _, origin, _ in span['chars']:
            trace_fonts[(round(origin[0], 2), round(origin[1], 2))] = span['font']
    root = ET.fromstring(page.get_svg_image(text_as_path=True))
    definitions = {element.get('id'): element for element in root.iter() if element.get('id')}
    instances = []

    def traverse(node, inherited_matrix=IDENTITY, inherited_style=None):
        tag = node.tag.rsplit('}', 1)[-1]
        if tag == 'defs':
            return
        matrix = multiply(inherited_matrix, transform_matrix(node.get('transform')))
        style = dict(inherited_style or {})
        style.update({key: node.attrib[key] for key in STYLE_KEYS if key in node.attrib})
        if tag == 'use':
            href = node.get(f'{{{XLINK}}}href', node.get('href', '')).lstrip('#')
            origin = (round(matrix[4], 2), round(matrix[5], 2))
            font = trace_fonts.get(origin)
            if font is None:
                nearest = min(trace_fonts, key=lambda pos: abs(pos[0] - matrix[4]) + abs(pos[1] - matrix[5]), default=None)
                if nearest is not None and abs(nearest[0] - matrix[4]) + abs(nearest[1] - matrix[5]) < .04:
                    font = trace_fonts[nearest]
            instances.append({'definition': href, 'matrix': matrix, 'style': style,
                              'font': font, 'data_text': node.get('data-text')})
        for child in node:
            traverse(child, matrix, style)

    traverse(root)
    # Resolve rare rounding collisions using another instance of the same PDF font.
    font_ids = {}
    for instance in instances:
        if instance['font']:
            font_ids[instance['definition'].rsplit('_', 1)[0]] = instance['font']
    region = grid_region(labels) if layout == 'grid' else None
    bounds = {}
    selected = []
    unknown = []
    for instance in instances:
        matrix = instance['matrix']
        font = instance['font'] or font_ids.get(instance['definition'].rsplit('_', 1)[0])
        native_cell_label=font=='ArialNarrow' and any(
            abs(matrix[5]-item['label']['origin'][1])<.03
            and item['label']['bbox'][0]-.03<=matrix[4]<=item['label']['bbox'][2]+.03
            for item in conventions.values())
        if font and font_is_typography(font) and not native_cell_label:
            continue
        if not 60 < matrix[5] < 735:
            continue
        if layout == 'sources' and math.hypot(matrix[0], matrix[1]) < 11.5:
            continue  # Six-point radicals and modern equivalents are annotations.
        definition = definitions[instance['definition']]
        if definition.tag != f'{{{SVG}}}path':
            raise ValueError(f'Non-vector reference definition {instance["definition"]}')
        data = definition.get('d', '')
        if instance['definition'] not in bounds:
            pen = BoundsPen(None)
            parse_path(data, pen)
            bounds[instance['definition']] = pen.bounds
        bbox = bounds[instance['definition']]
        if bbox is None:
            continue  # Whitespace uses have no outline in the PDF.
        world_bbox = transformed_bounds(bbox, matrix)
        if region and not region[0] <= (world_bbox[0] + world_bbox[2]) / 2 <= region[1]:
            continue  # Names-list examples next to a small grid.
        if font is None:
            unknown.append({'origin': matrix[4:], 'definition': instance['definition']})
            continue
        selected.append({'key': outline_key(data, matrix, instance['style']),
                         'origin': matrix[4:], 'font': font,
                         'data_text': instance['data_text']})
    return selected, unknown


def exported_uses(row, output, padding, conventions):
    root = ET.parse(output / row['path']).getroot()
    x0, y0, _, _ = row['pdf_bbox']
    restore = (1., 0., 0., 1., x0 - padding, y0 - padding)
    outlines = []
    background_matches=0
    for index,node in enumerate(root.findall(f'{{{SVG}}}path')):
        matrix = multiply(restore, transform_matrix(node.get('transform')))
        style = {key: node.attrib[key] for key in STYLE_KEYS if key in node.attrib}
        if row.get('specimen_kind')=='noncharacter-chart-cell' and index==0:
            cp=int(row['codepoint'],16)
            if cp not in conventions:raise ValueError(f'No printed noncharacter cell for {row["path"]}')
            expected=conventions[cp]
            for key,target in [('pdf_cell_bbox',expected['cell']),('pdf_background_bbox',expected['background'])]:
                if max(abs(a-b) for a,b in zip(row[key],target))>.00001:
                    raise ValueError(f'Noncharacter cell geometry mismatch: {row["path"]}')
            pen=RecordingPen();parse_path(node.attrib['d'],pen)
            if [operation for operation,_ in pen.value]!=['moveTo','lineTo','lineTo','lineTo','lineTo','closePath']:
                raise ValueError(f'Nonrectangular cell background: {row["path"]}')
            corners=[]
            for operation,points in pen.value:
                if operation=='closePath':continue
                x,y=points[0]
                corners.append((matrix[0]*x+matrix[2]*y+matrix[4],matrix[1]*x+matrix[3]*y+matrix[5]))
            x0,y0,x1,y1=expected['cell']
            target=[(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
            if max(abs(a-b) for actual,wanted in zip(corners,target) for a,b in zip(actual,wanted))>.00001:
                raise ValueError(f'Changed black-cell background: {row["path"]}')
            if style!={'fill':'#000000'}:raise ValueError(f'Changed black-cell paint: {row["path"]}')
            background_matches+=1
            continue
        outlines.append({'key': outline_key(node.attrib['d'], matrix, style),
                         'origin': matrix[4:], 'path': row['path']})
    if len(outlines)+background_matches != row['outline_count']:
        raise ValueError(f'Outline count differs for {row["path"]}')
    return outlines,background_matches


def audit(args):
    settings = json.loads((args.output / 'extraction.json').read_text())
    pdf_path = args.sources / 'CodeCharts.pdf'
    if sha256(pdf_path) != settings['pdf_sha256']:
        raise ValueError('PDF hash differs from extraction input')
    if sha256(args.sources / 'UnicodeData.txt') != settings['ucd_sha256']:
        raise ValueError('UCD hash differs from extraction input')
    doc = pymupdf.open(pdf_path)
    by_page = defaultdict(list)
    all_paths = set()
    all_codepoints = set()
    for line in (args.output / 'manifest.jsonl').open():
        row = json.loads(line)
        if row['path'] in all_paths:
            raise ValueError(f'Duplicate manifest path {row["path"]}')
        all_paths.add(row['path'])
        all_codepoints.add(int(row['codepoint'], 16))
        by_page[row['page']].append(row)
    selected_pages = set(map(int, args.pages.split(','))) if args.pages else set(range(1, len(doc) + 1))
    if not selected_pages or min(selected_pages) < 1 or max(selected_pages) > len(doc):
        raise ValueError('Audit pages outside source PDF')
    failures = []
    labels_all = set()
    counters = Counter()
    font_counts = Counter()
    page_reports = []
    started = time.monotonic()
    for number, page in enumerate(doc, 1):
        if number not in selected_pages:
            continue
        layout, labels, spans = printed_labels(page)
        counters[layout + '_pages'] += 1
        rows = by_page[number]
        printed = {int(label['text'], 16) for label in labels}
        exported = {int(row['codepoint'], 16) for row in rows}
        labels_all.update(printed)
        for cp in sorted(printed - exported):
            failures.append({'kind': 'missing_printed_codepoint', 'page': number, 'codepoint': f'{cp:04X}'})
        for cp in sorted(exported - printed):
            failures.append({'kind': 'unprinted_exported_codepoint', 'page': number, 'codepoint': f'{cp:04X}'})
        counters['printed_label_occurrences'] += len(labels)
        if layout == 'annotations':
            # Every represented character is accounted for by its primary chart;
            # large unmatched illustrations would indicate an unclassified layout.
            large = [span for span in spans if span['size'] >= 14 and not font_is_typography(span['font'])]
            if large:
                failures.append({'kind': 'unclassified_reference_page', 'page': number,
                                 'fonts': sorted({span['font'] for span in large})})
            continue
        conventions=convention_labels(page,labels) if layout=='grid' else {}
        reference, unknown = reference_uses(page, layout, labels, conventions)
        for item in unknown:
            failures.append({'kind': 'unresolved_pdf_reference_font', 'page': number, **item})
        if settings['remove_dotted_circle']:
            reference = [item for item in reference if not (item['font'] == 'SpecialsUC6' and item['data_text'] == '$')]
        actual = defaultdict(list)
        background_matches=0
        for row in rows:
            exported_outlines,background_count=exported_uses(row, args.output, settings['padding'], conventions)
            background_matches+=background_count
            for outline in exported_outlines:
                actual[outline['key']].append(outline)
            counters['svg_files_compared'] += 1
        matched = 0
        for item in reference:
            font_counts[item['font']] += 1
            candidates = actual.get(item['key'], [])
            if not candidates:
                failures.append({'kind': 'missing_pdf_outline', 'page': number,
                                 'font': item['font'], 'origin': item['origin']})
                continue
            nearest = min(range(len(candidates)), key=lambda i: max(abs(candidates[i]['origin'][axis] - item['origin'][axis]) for axis in (0, 1)))
            distance = max(abs(candidates[nearest]['origin'][axis] - item['origin'][axis]) for axis in (0, 1))
            if distance > .00001:
                failures.append({'kind': 'outline_coordinate_mismatch', 'page': number,
                                 'font': item['font'], 'origin': item['origin'], 'difference': distance})
                continue
            candidates.pop(nearest)
            matched += 1
        for candidates in actual.values():
            for item in candidates:
                failures.append({'kind': 'outline_absent_from_pdf_chart', 'page': number,
                                 'path': item['path'], 'origin': item['origin']})
        counters['pdf_reference_outlines'] += len(reference)+len(conventions)
        counters['exact_outline_matches'] += matched+background_matches
        counters['noncharacter_background_matches']+=background_matches
        page_reports.append({'page': number, 'layout': layout, 'printed_labels': len(labels),
                             'svg_files': len(rows), 'pdf_outlines': len(reference)+len(conventions),
                             'matched_outlines': matched+background_matches})
        if number % 100 == 0 or number == max(selected_pages):
            print(f'PDF page {number}/{len(doc)}: {counters["svg_files_compared"]:,} SVGs, '
                  f'{counters["exact_outline_matches"]:,} outlines, {len(failures)} discrepancies', flush=True)
    if not args.pages:
        actual_paths = {path.relative_to(args.output).as_posix() for path in args.output.glob('U*/*/**/*.svg')}
        for missing in sorted(all_paths - actual_paths):
            failures.append({'kind': 'missing_svg_file', 'path': missing})
        for extra in sorted(actual_paths - all_paths):
            failures.append({'kind': 'unlisted_svg_file', 'path': extra})
        if set(by_page) - set(range(1, len(doc) + 1)):
            failures.append({'kind': 'manifest_page_outside_pdf'})
    report = {'passed': not failures, 'complete_pdf': not bool(args.pages),
              'pdf_sha256': settings['pdf_sha256'], 'ucd_sha256': settings['ucd_sha256'],
              'pdf_pages': len(doc), 'pages_audited': len(selected_pages),
              'distinct_printed_chart_codepoints': len(labels_all),
              'distinct_exported_codepoints': len(all_codepoints),
              'coordinate_tolerance_pdf_points': .00001, 'counts': dict(counters),
              'reference_font_outline_counts': dict(font_counts),
              'errors': failures, 'page_comparisons': page_reports,
              'elapsed_seconds': round(time.monotonic() - started, 2)}
    destination = args.output / ('pdf-audit-partial.json' if args.pages else 'pdf-audit.json')
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('reference_font_outline_counts', 'errors', 'page_comparisons')}, indent=2))
    if failures:
        print(json.dumps(failures[:20], indent=2))
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('glyphs/18.0.0'))
    parser.add_argument('--sources', type=Path, default=Path('sources/18.0.0'))
    parser.add_argument('--pages', help='Comma-separated one-based page numbers for a partial audit')
    args = parser.parse_args()
    try:
        return audit(args)
    except (ValueError, OSError, KeyError, ET.ParseError) as error:
        print(f'PDF audit failed: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
