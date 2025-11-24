# Source: https://gist.github.com/medicalwei/c9fdcd9ec19b0c363ec1

import argparse
from enum import Enum
from typing import Any
import freetype
import os
import re
import struct
import sys
import itertools
import json
from math import ceil

from utils.io import LinedFileReader

sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
# import generate_c_byte_array

MIN_CODEPOINT = 0x20
MAX_2_BYTES_CODEPOINT = 0xffff
MAX_EXTENDED_CODEPOINT = 0x10ffff
FONT_VERSION_1 = 1
FONT_VERSION_2 = 2
WILDCARD_CODEPOINT = 0x25AF  # White vertical rectangle ▯
ELLIPSIS_CODEPOINT = 0x2026  # …

HASH_TABLE_SIZE = 255
OFFSET_TABLE_MAX_SIZE = 128
MAX_GLYPHS_EXTENDED = HASH_TABLE_SIZE * OFFSET_TABLE_MAX_SIZE
MAX_GLYPHS = 256
OFFSET_SIZE_BYTES = 4


def grouper(n, iterable, fillvalue=None):
    """grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx"""
    args = [iter(iterable)] * n
    return itertools.zip_longest(*args, fillvalue=fillvalue)


def hasher(codepoint, num_glyphs):
    return (codepoint % num_glyphs)


def bits(x):
    data = []
    for i in range(8):
        data.insert(0, int((x & 1) == 1))
        x = x >> 1
    return data

def load_pbff_file(path: str) -> dict[int, dict[str, Any]]:
    """
    Source: https://github.com/pebble-dev/renaissance/blob/master/lib/pbff.py
    
    Copyright (c) 2017 jneubrand, MIT License
    """

    glyphs = {}
    with open(path, 'r') as fh:
        f1 = LinedFileReader(fh)
        glyphs = {}
        while not f1.empty():
            line = f1.next()

            r = re.match(r'^\s*$/', line)
            if r:
                continue

            r = re.match(r'^line-height (\d+)$', line)
            if r:
                # self.line_height = int(r.group(1))
                continue

            r = re.match(r'^version (\d+)$', line)
            if r:
                # self.version = int(r.group(1))
                continue

            r = re.match(r'^fallback (\d+)$', line)
            if r:
                # self.fallback_codepoint = int(r.group(1))
                continue

            r = re.match(r'^glyph (\d+)', line)
            if r:
                glyph_codepoint = int(r.group(1))
                data = []
                # 3rd capture group should accept negative numbers, such as -1
                r = re.match(r'^(\s*)(-+|\.)\s*(-?\d+)$', f1.next())
                if r:
                    negativeLeft = len(r.group(1))
                    advance = 0 if r.group(2) == '.' else len(r.group(2))
                    top = int(r.group(3))
                    r = re.match(r'^([ #]*)$', f1.peek())
                    while r:
                        f1.next()
                        data += [[True if x == '#' else False
                                for x in r.group(1)]]
                        r = re.match(r'^([ #]*)$', f1.peek())
                    first_enabled = None
                    last_enabled = 0
                    for bmpline in data:
                        idx = 0
                        for char in bmpline:
                            if char and (first_enabled is None or idx < first_enabled):
                                first_enabled = idx
                            if char and idx > last_enabled:
                                last_enabled = idx
                            idx += 1
                    for bmpline in data:
                        while len(bmpline) <= last_enabled:
                            bmpline += [False]
                    data = [x[first_enabled:] for x in data]
                    if first_enabled is None:
                        left = 0
                        width = 0
                        height = 0
                    else:
                        left = first_enabled - negativeLeft
                        width = last_enabled - first_enabled + 1
                        height = len(data)
                    glyphs[glyph_codepoint] = {
                        'top': top,
                        'data': data,
                        'left': left,
                        'width': width,
                        'height': height,
                        'advance': advance
                    }
                else:
                    print(f'glyph_codepoint {glyph_codepoint}')
                    print(f'path {path}')
                    raise Exception('Invalid data')
        return glyphs


class FontType(Enum):
    TTF = 1
    PBFF = 2
    MERGED = 3


class Font:
    def __init__(self,
                 font_type: FontType,
                 ttf_path: str,
                 pbff_path: str,
                 height: int,
                 max_glyphs: int,
                 legacy=False):
        self.version = FONT_VERSION_2
        self.type = font_type
        self.ttf_path = ttf_path
        self.pbff_path = pbff_path
        self.max_height = int(height)
        self.legacy = legacy
        if self.ttf_path != '':
            self.face = freetype.Face(self.ttf_path)
            self.face.set_pixel_sizes(0, self.max_height)
            self.name = self.face.family_name + b'_' + self.face.style_name
        if self.pbff_path != '':
            self.pbff_glyphs: dict[int, dict[str, Any]] = load_pbff_file(pbff_path)
            self.pbff_glyphs_list = list(self.pbff_glyphs.items())
            self.pbff_glyphs_list_cursor_index = 0
        self.wildcard_codepoint = WILDCARD_CODEPOINT
        self.number_of_glyphs = 0
        self.table_size = HASH_TABLE_SIZE
        self.tracking_adjust = 0
        self.regex = None
        self.codepoints = range(MIN_CODEPOINT, MAX_EXTENDED_CODEPOINT)
        self.codepoint_bytes = 2
        self.max_glyphs = max_glyphs
        self.glyph_table = []
        self.hash_table = [0] * self.table_size
        self.offset_tables = [[] for _ in range(self.table_size)]
        self.heightoffset = 0
        self.fauxbold = False

    def set_tracking_adjust(self, adjust):
        self.tracking_adjust = adjust

    def set_heightoffset(self, offset):
        self.heightoffset = offset

    def set_fauxbold(self, fauxbold):
        self.fauxbold = fauxbold

    def set_regex_filter(self, regex_string):
        if regex_string != ".*":
            try:
                self.regex = re.compile(str(regex_string), re.UNICODE)
            except Exception:
                raise Exception("Supplied filter argument was not a valid regular expression.")
        else:
            self.regex = None

    def set_codepoint_list(self, list_path):
        with open(list_path, "r", encoding="utf-8") as codepoints_file:
            codepoints_json = json.load(codepoints_file)
            self.codepoints = [int(cp) for cp in codepoints_json["codepoints"]]

    def is_supported_glyph(self, codepoint):
        return (self.face.get_char_index(codepoint) > 0 or (codepoint == self.wildcard_codepoint))
    
    def get_first_char(self) -> tuple[int, int]:
        if self.type == FontType.TTF:
            (codepoint, gindex) = self.face.get_first_char()
            return int(codepoint), gindex
        else:
            self.pbff_glyphs_list_cursor_index = 1
            codepoint = self.pbff_glyphs_list[self.pbff_glyphs_list_cursor_index][0]
            gindex = 1
            return codepoint, gindex
        
    def get_next_char(self, codepoint, gindex) -> tuple[int, int]:
        if self.type == FontType.TTF:
            (codepoint, gindex) = self.face.get_next_char(codepoint, gindex)
            return int(codepoint), gindex
        else:
            self.pbff_glyphs_list_cursor_index += 1
            try:
                codepoint = self.pbff_glyphs_list[self.pbff_glyphs_list_cursor_index][0]
            except IndexError:
                self.pbff_glyphs_list_cursor_index = 0
                return 0, 0
            gindex = self.pbff_glyphs_list_cursor_index
            return codepoint, gindex
    
    def glyph_bits_pbff(self, codepoint) -> bytes:
        def get_bytes(bits):
            while len(bits):
                item = 0
                for x in range(8):
                    item <<= 1
                    item += bits.pop(7 - x)
                yield item

        glyph = self.pbff_glyphs[codepoint]
        glyph_header = struct.pack('<BBbbb',
                                   glyph['width'],
                                   glyph['height'],
                                   glyph['left'],
                                   glyph['top'],
                                   glyph['advance'])
        bits = sum(glyph['data'], [])
        assert len(bits) == glyph['width'] * glyph['height']
        while (len(bits) % 32):
            bits = bits + [False]
        glyph_packed = []
        for byte in get_bytes(bits):
            glyph_packed.append(struct.pack('<B', byte))

        return glyph_header + b''.join(glyph_packed)

    def glyph_bits_ttf(self, gindex):
        flags = (freetype.FT_LOAD_RENDER if self.legacy else
                 freetype.FT_LOAD_RENDER | freetype.FT_LOAD_MONOCHROME | freetype.FT_LOAD_TARGET_MONO)
        self.face.load_glyph(gindex, flags)
        bitmap = self.face.glyph.bitmap
        advance = self.face.glyph.advance.x / 64  # Convert 26.6 fixed float format to px
        advance += self.tracking_adjust
        width = bitmap.width
        if self.fauxbold:
            width += 1
        fauxbold_additional_byte = (bitmap.width % 8 == 0)
        height = bitmap.rows
        left = self.face.glyph.bitmap_left
        bottom = self.max_height - self.face.glyph.bitmap_top + self.heightoffset
        pixel_mode = self.face.glyph.bitmap.pixel_mode

        glyph_structure = ''.join((
            '<',  # little_endian
            'B',  # bitmap_width
            'B',  # bitmap_height
            'b',  # offset_left
            'b',  # offset_top
            'b'   # horizontal_advance
        ))
        glyph_header = struct.pack(glyph_structure, width, height, left, bottom, int(advance))

        glyph_bitmap = []

        if pixel_mode == 1 and self.fauxbold:  # faux bold monochrome font, 1 bit per pixel
            for i in range(bitmap.rows):
                row = []
                previousbyte = 0
                for j in range(bitmap.pitch):
                    byte = bitmap.buffer[i * bitmap.pitch + j] | previousbyte
                    fauxboldbyte = byte | byte >> 1
                    row.extend(bits(fauxboldbyte))
                    previousbyte = byte << 8  # shift 8 bits for next
                if fauxbold_additional_byte:
                    byte = previousbyte
                    fauxboldbyte = byte | byte >> 1
                    row.extend(bits(fauxboldbyte))
                glyph_bitmap.extend(row[:width])
        elif pixel_mode == 1:  # monochrome font, 1 bit per pixel
            for i in range(bitmap.rows):
                row = []
                for j in range(bitmap.pitch):
                    row.extend(bits(bitmap.buffer[i * bitmap.pitch + j]))
                glyph_bitmap.extend(row[:bitmap.width])
        elif pixel_mode == 2:  # grey font, 255 bits per pixel
            for val in bitmap.buffer:
                glyph_bitmap.extend([1 if val > 127 else 0])
        else:
            raise Exception("Unsupported pixel mode: {}".format(pixel_mode))

        glyph_packed = []
        for word in grouper(32, glyph_bitmap, 0):
            w = 0
            for index, bit in enumerate(word):
                w |= bit << index
            glyph_packed.append(struct.pack('<I', w))

        return glyph_header + b''.join(glyph_packed)

    def fontinfo_bits(self):
        return struct.pack('<BBHHBB',
                           self.version,
                           self.max_height,
                           self.number_of_glyphs,
                           self.wildcard_codepoint,
                           self.table_size,
                           self.codepoint_bytes)

    def bitstring(self):
        btstr = self.fontinfo_bits()
        btstr += b''.join(self.hash_table)
        for table in self.offset_tables:
            btstr += b''.join(table)
        btstr += b''.join(self.glyph_table)
        return btstr
