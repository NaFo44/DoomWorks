#!/usr/bin/env python3

import argparse
import heapq
import os
import struct
import sys
from dataclasses import dataclass

FRACBITS = 16
NO_INDEX = 0xFFFF

ML_LABEL = 0
ML_THINGS = 1
ML_LINEDEFS = 2
ML_SIDEDEFS = 3
ML_VERTEXES = 4
ML_SEGS = 5
ML_SSECTORS = 6
ML_NODES = 7
ML_SECTORS = 8
ML_REJECT = 9
ML_BLOCKMAP = 10

ML_TWOSIDED = 4

ST_HORIZONTAL = 0
ST_VERTICAL = 1
ST_POSITIVE = 2
ST_NEGATIVE = 3

HUFF_LUMP_MAGIC = b"HUF0"
HUFF_COMPRESSIBLE_MAP_LUMP_OFFSETS = (
    ML_THINGS,
    ML_SIDEDEFS,
    ML_SSECTORS,
    ML_SECTORS,
    ML_REJECT,
)
HUFF_MIN_SAVINGS = 1
HUFF_MAX_CODE_LEN = 24
FLAT_LUMP_SIZE = 64 * 64
LOSSY_PATCH_COLUMN_GROUP = 7
LOSSY_FLAT_BLOCK_SIZE = 2
NODE_COMPACT_MAGIC = b"NDC0"
NODE_COMPACT_HEADER_SIZE = 16
NODE_COMPACT_ENTRY_SIZE = 16
SPRITE_START_MARKERS = {"S_START", "SS_START", "S1_START", "S2_START"}
SPRITE_END_MARKERS = {"S_END", "SS_END", "S1_END", "S2_END"}
FLAT_START_MARKERS = {"F_START", "FF_START", "F1_START", "F2_START"}
FLAT_END_MARKERS = {"F_END", "FF_END", "F1_END", "F2_END"}
DEMO_LUMP_NAMES = {"DEMO1", "DEMO2", "DEMO3", "DEMO4"}


def align4(value):
    return (value + 3) & ~3


def to_u16(value):
    return value & 0xFFFF


def to_s16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def to_u32(value):
    return value & 0xFFFFFFFF


def to_s32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def trunc_div(num, den):
    if den == 0:
        raise ZeroDivisionError
    q = abs(num) // abs(den)
    return -q if (num < 0) ^ (den < 0) else q


def fixed_div(a, b):
    abs_a = abs(to_s32(a))
    abs_b = abs(to_s32(b))

    if (abs_a >> 14) >= abs_b:
        sign = 0xFFFFFFFF if (to_u32(a) ^ to_u32(b)) & 0x80000000 else 0x00000000
        return to_s32(sign ^ 0x7FFFFFFF)

    return to_s32(trunc_div(to_s32(a) << FRACBITS, to_s32(b)))


def _build_huffman_code_lengths(data):
    freq = [0] * 256
    for value in data:
        freq[value] += 1

    heap = []
    order = 0
    for symbol, count in enumerate(freq):
        if count:
            heapq.heappush(heap, (count, order, symbol))
            order += 1

    if not heap:
        return {}

    if len(heap) == 1:
        return {heap[0][2]: 1}

    while len(heap) > 1:
        count_a, _, node_a = heapq.heappop(heap)
        count_b, _, node_b = heapq.heappop(heap)
        heapq.heappush(heap, (count_a + count_b, order, (node_a, node_b)))
        order += 1

    lengths = {}
    stack = [(heap[0][2], 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, int):
            lengths[node] = depth if depth > 0 else 1
            continue
        left, right = node
        stack.append((right, depth + 1))
        stack.append((left, depth + 1))

    return lengths


def _build_canonical_codes(lengths):
    entries = sorted((length, symbol) for symbol, length in lengths.items())
    if not entries:
        return {}

    codes = {}
    code = 0
    prev_len = entries[0][0]

    for length, symbol in entries:
        code <<= (length - prev_len)
        codes[symbol] = (code, length)
        code += 1
        prev_len = length

    return codes


def _pack_huffman_payload(data, codes):
    out = bytearray()
    current = 0
    bits_used = 0

    for value in data:
        code, bit_len = codes[value]
        for bit_index in range(bit_len - 1, -1, -1):
            current = (current << 1) | ((code >> bit_index) & 1)
            bits_used += 1
            if bits_used == 8:
                out.append(current)
                current = 0
                bits_used = 0

    if bits_used:
        out.append(current << (8 - bits_used))

    return bytes(out)


def huffman_compress_block(data):
    if not data:
        return None

    code_lengths = _build_huffman_code_lengths(data)
    if not code_lengths:
        return None

    max_code_len = max(code_lengths.values())
    if max_code_len > HUFF_MAX_CODE_LEN:
        return None

    symbol_count = len(code_lengths)
    if symbol_count == 0 or symbol_count > 256:
        return None

    canonical_codes = _build_canonical_codes(code_lengths)
    payload = _pack_huffman_payload(data, canonical_codes)

    table = bytearray(symbol_count * 2)
    write_ofs = 0
    for symbol, length in sorted(code_lengths.items()):
        table[write_ofs] = symbol
        table[write_ofs + 1] = length
        write_ofs += 2

    header = struct.pack(
        "<4sIIHBB",
        HUFF_LUMP_MAGIC,
        len(data),
        len(payload),
        symbol_count,
        max_code_len,
        0,
    )
    return header + table + payload


def parse_patch_lump(data):
    if len(data) < 8:
        return None

    width, height, leftoffset, topoffset = struct.unpack_from("<hhhh", data, 0)
    if width <= 0 or width > 2048 or height <= 0 or height > 2048:
        return None

    table_ofs = 8
    table_size = width * 4
    if table_ofs + table_size > len(data):
        return None

    col_offsets = struct.unpack_from("<" + ("I" * width), data, table_ofs)
    columns = []

    for col_ofs in col_offsets:
        if col_ofs < table_ofs + table_size or col_ofs >= len(data):
            return None

        pos = col_ofs
        post_count = 0

        while True:
            if pos >= len(data):
                return None

            topdelta = data[pos]
            pos += 1

            if topdelta == 0xFF:
                break

            if pos + 2 > len(data):
                return None

            length = data[pos]
            pos += 2  # length + unused byte

            if pos + length + 1 > len(data):
                return None

            pos += length + 1  # pixel payload + trailing unused byte
            post_count += 1

            if post_count > 65535:
                return None

        columns.append(data[col_ofs:pos])

    return width, height, leftoffset, topoffset, columns


def rebuild_patch_lump(width, height, leftoffset, topoffset, columns):
    if len(columns) != width:
        return None

    out = bytearray(8 + (width * 4))
    struct.pack_into("<hhhh", out, 0, width, height, leftoffset, topoffset)

    column_cache = {}

    for col_index, column_bytes in enumerate(columns):
        shared_ofs = column_cache.get(column_bytes)
        if shared_ofs is None:
            shared_ofs = len(out)
            out.extend(column_bytes)
            column_cache[column_bytes] = shared_ofs

        struct.pack_into("<I", out, 8 + (col_index * 4), shared_ofs)

    return bytes(out)


def is_sprite_lump_name(name):
    if len(name) not in (6, 8):
        return False

    if not name[:4].isalnum():
        return False
    if not name[4].isalpha() or not name[5].isdigit():
        return False

    if len(name) == 8 and (not name[6].isalpha() or not name[7].isdigit()):
        return False

    return True


def parse_sprite_parts(name):
    if not is_sprite_lump_name(name):
        return None

    frames = [name[4]]
    rotations = [ord(name[5]) - ord("0")]

    if len(name) == 8:
        frames.append(name[6])
        rotations.append(ord(name[7]) - ord("0"))

    return tuple(frames), tuple(rotations)


def sprite_rotation_distance(a, b):
    diff = abs(a - b)
    return min(diff, 8 - diff)


def is_ui_font_lump_name(name):
    return (
        name.startswith("STCFN")
        or name.startswith("STTNUM")
        or name.startswith("STYSNUM")
        or name.startswith("WINUM")
        or name.startswith("AMMNUM")
        or name.startswith("CWILV")
        or name.startswith("WILV")
        or name.startswith("STGNUM")
        or name.startswith("STTPRCNT")
        or name.startswith("STKEYS")
        or name.startswith("STARMS")
        or name.startswith("STBAR")
        or name.startswith("STDISK")
        or name.startswith("STCDROM")
        or name.startswith("STFB")
        or name.startswith("STPB")
        or name.startswith("STF")
        or name.startswith("STTMINUS")
        or name.startswith("FONTA")
        or name.startswith("FONTB")
        or name.startswith("M_")
        or name.startswith("WI")
        or name in ("TITLEPIC", "CREDIT", "HELP", "INTERPIC")
    )


def decode_lump_name(raw):
    return raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore")


def encode_lump_name(name):
    return name.upper().encode("latin-1", errors="ignore")[:8].ljust(8, b"\x00")


@dataclass
class Lump:
    name: str
    data: bytes

    @property
    def length(self):
        return len(self.data)


class WadFile:
    def __init__(self, file_path):
        self.wad_path = file_path
        self.lumps = []

    def load_wad_file(self):
        try:
            with open(self.wad_path, "rb") as f:
                wad_data = f.read()
        except OSError:
            return False

        if len(wad_data) < 12:
            return False

        ident, num_lumps, info_table_ofs = struct.unpack_from("<4sii", wad_data, 0)
        ident_text = ident.decode("latin-1", errors="ignore")

        if ident_text not in ("IWAD", "PWAD"):
            return False

        if num_lumps < 0:
            return False

        if info_table_ofs < 0 or info_table_ofs + (num_lumps * 16) > len(wad_data):
            return False

        self.lumps = []

        for i in range(num_lumps):
            file_pos, size, name_raw = struct.unpack_from("<ii8s", wad_data, info_table_ofs + (i * 16))
            name = decode_lump_name(name_raw)

            if size <= 0:
                data = b""
            else:
                if file_pos < 0 or file_pos + size > len(wad_data):
                    return False
                data = wad_data[file_pos:file_pos + size]

            self.lumps.append(Lump(name=name, data=data))

        return True

    def to_bytes(self):
        out = bytearray()
        out.extend(struct.pack("<4sii", b"IWAD", len(self.lumps), 12))
        out.extend(b"\x00" * (len(self.lumps) * 16))

        while len(out) % 4:
            out.append(0)

        dir_entries = []
        shared_data_ofs = {}

        for lump in self.lumps:
            size = lump.length
            if size > 0:
                file_pos = shared_data_ofs.get(lump.data)
                if file_pos is None:
                    while len(out) % 4:
                        out.append(0)
                    file_pos = len(out)
                    out.extend(lump.data)
                    shared_data_ofs[lump.data] = file_pos
            else:
                file_pos = 0
            dir_entries.append((file_pos, size, encode_lump_name(lump.name)))

        for i, (file_pos, size, name_raw) in enumerate(dir_entries):
            struct.pack_into("<ii8s", out, 12 + (i * 16), file_pos, size, name_raw)

        return bytes(out)

    def save_wad_file(self, file_path):
        try:
            with open(file_path, "wb") as f:
                f.write(self.to_bytes())
        except OSError:
            return False
        return True

    def get_lump_by_name(self, name):
        name_upper = name.upper()
        for i in range(len(self.lumps) - 1, -1, -1):
            if self.lumps[i].name.upper() == name_upper:
                return i, self.lumps[i]
        return -1, None

    def get_lump_by_num(self, lump_num):
        if lump_num < 0 or lump_num >= len(self.lumps):
            return None
        return self.lumps[lump_num]

    def replace_lump(self, lump_num, new_lump):
        if lump_num < 0 or lump_num >= len(self.lumps):
            return False
        self.lumps[lump_num] = new_lump
        return True

    def remove_lump(self, lump_num):
        if lump_num < 0 or lump_num >= len(self.lumps):
            return False
        del self.lumps[lump_num]
        return True

    def lump_count(self):
        return len(self.lumps)

    def merge_wad_file(self, wad_file):
        self.lumps.extend(wad_file.lumps)
        return True


class WadProcessor:
    AUDIO_START_MARKERS = {
        "D_START",
        "DS_START",
        "DP_START",
        "DSTART",
        "DSSTART",
        "DPSTART",
        "MUSSTART",
        "SFXSTART",
    }

    AUDIO_END_MARKERS = {
        "D_END",
        "DS_END",
        "DP_END",
        "DEND",
        "DSEND",
        "DPEND",
        "MUSEND",
        "SFXEND",
    }

    GRAPHICS_MARKER_NAMES = (
        "S_START",
        "S_END",
        "SS_START",
        "SS_END",
        "S1_START",
        "S1_END",
        "S2_START",
        "S2_END",
        "P_START",
        "P_END",
        "PP_START",
        "PP_END",
        "P1_START",
        "P1_END",
        "P2_START",
        "P2_END",
    )

    def __init__(self, wad_file):
        self.wad_file = wad_file
        self.texture_map = None
        self.flat_map = None

    def process_wad(self):
        self.remove_unused_lumps()
        self.optimize_graphics_lumps()
        self.compress_flat_lumps()

        map_lump_num, _ = self.wad_file.get_lump_by_name("MAP01")
        if map_lump_num != -1:
            return self.process_d2_levels()

        map_lump_num, _ = self.wad_file.get_lump_by_name("E1M1")
        if map_lump_num == -1:
            return False

        return self.process_d1_levels()

    def process_d2_levels(self):
        for m in range(1, 33):
            map_name = f"MAP{m:02d}"
            lump_num, _ = self.wad_file.get_lump_by_name(map_name)
            if lump_num != -1:
                self.process_level(lump_num)
        return True

    def process_d1_levels(self):
        for e in range(1, 5):
            for m in range(1, 10):
                map_name = f"E{e}M{m}"
                lump_num, _ = self.wad_file.get_lump_by_name(map_name)
                if lump_num != -1:
                    self.process_level(lump_num)
        return True

    def collect_ranges(self, start_markers, end_markers):
        ranges = []
        range_start = None

        for idx, lump in enumerate(self.wad_file.lumps):
            name_upper = lump.name.upper()

            if name_upper in start_markers:
                range_start = idx + 1
                continue

            if name_upper in end_markers and range_start is not None:
                if range_start < idx:
                    ranges.append((range_start, idx))
                range_start = None

        if range_start is not None and range_start < len(self.wad_file.lumps):
            ranges.append((range_start, len(self.wad_file.lumps)))

        return ranges

    def collect_indices_from_ranges(self, ranges):
        indices = set()
        for start, end in ranges:
            for idx in range(start, end):
                indices.add(idx)
        return indices

    def build_texture_patch_name_set(self):
        names = set()
        lump_num, pnames_lump = self.wad_file.get_lump_by_name("PNAMES")

        if lump_num == -1 or pnames_lump is None or pnames_lump.length < 4:
            return names

        (count,) = struct.unpack_from("<I", pnames_lump.data, 0)
        if count <= 0:
            return names

        max_entries = (pnames_lump.length - 4) // 8
        if count > max_entries:
            count = max_entries

        for i in range(count):
            start = 4 + (i * 8)
            raw_name = pnames_lump.data[start:start + 8]
            name = decode_lump_name(raw_name).upper()
            if name:
                names.add(name)

        return names

    def collapse_sprite_diagonals(self, sprite_indices):
        if not sprite_indices:
            return 0

        sprite_entries = []
        original_data = {}

        for idx in sorted(sprite_indices):
            lump = self.wad_file.get_lump_by_num(idx)
            if lump is None:
                continue
            name_upper = lump.name.upper()
            parsed = parse_sprite_parts(name_upper)
            if parsed is None:
                continue
            frames, rotations = parsed
            sprite_entries.append((idx, name_upper, frames, rotations))
            original_data[idx] = lump.data

        replaced = 0

        for target_idx, target_name, target_frames, target_rotations in sprite_entries:
            if not any(rot in (2, 4, 6, 8) for rot in target_rotations):
                continue

            best = None
            best_score = None

            for source_idx, source_name, source_frames, source_rotations in sprite_entries:
                if source_idx == target_idx:
                    continue
                if source_name[:4] != target_name[:4]:
                    continue
                if source_frames != target_frames:
                    continue
                if len(source_rotations) != len(target_rotations):
                    continue
                if any(rot in (2, 4, 6, 8) for rot in source_rotations):
                    continue

                score = 0
                valid = True
                for tr, sr in zip(target_rotations, source_rotations):
                    if tr == 0 or sr == 0:
                        valid = False
                        break
                    score += sprite_rotation_distance(tr, sr)

                if not valid:
                    continue

                if best_score is None or score < best_score:
                    best_score = score
                    best = source_idx

            if best is None:
                continue

            source_data = original_data.get(best)
            lump = self.wad_file.get_lump_by_num(target_idx)
            if source_data is None or lump is None:
                continue

            if lump.data != source_data and self.wad_file.replace_lump(target_idx, Lump(lump.name, source_data)):
                replaced += 1

        return replaced

    def compress_graphics_lumps(self, sprite_indices, texture_patch_names):
        rewritten = 0
        saved_bytes = 0

        for idx, lump in enumerate(self.wad_file.lumps):
            name_upper = lump.name.upper()
            if name_upper in self.GRAPHICS_MARKER_NAMES:
                continue

            is_sprite = idx in sprite_indices

            if not is_sprite and name_upper not in texture_patch_names:
                continue

            patch_info = parse_patch_lump(lump.data)
            if patch_info is None:
                continue

            width, height, leftoffset, topoffset, columns = patch_info
            is_ui_font = is_ui_font_lump_name(name_upper)

            if is_ui_font:
                continue

            if width > 1:
                for col_idx in range(1, width):
                    src_col = (col_idx // LOSSY_PATCH_COLUMN_GROUP) * LOSSY_PATCH_COLUMN_GROUP
                    if src_col >= width:
                        src_col = width - 1
                    columns[col_idx] = columns[src_col]

            rebuilt = rebuild_patch_lump(width, height, leftoffset, topoffset, columns)
            if rebuilt is None:
                continue

            if len(rebuilt) >= len(lump.data):
                continue

            if self.wad_file.replace_lump(idx, Lump(lump.name, rebuilt)):
                rewritten += 1
                saved_bytes += len(lump.data) - len(rebuilt)

        return rewritten, saved_bytes

    def optimize_graphics_lumps(self):
        sprite_ranges = self.collect_ranges(SPRITE_START_MARKERS, SPRITE_END_MARKERS)
        sprite_indices = self.collect_indices_from_ranges(sprite_ranges)
        texture_patch_names = self.build_texture_patch_name_set()

        self.collapse_sprite_diagonals(sprite_indices)
        self.compress_graphics_lumps(sprite_indices, texture_patch_names)

    def lossify_flat_lump(self, data):
        if LOSSY_FLAT_BLOCK_SIZE <= 1 or len(data) != FLAT_LUMP_SIZE:
            return data

        step = LOSSY_FLAT_BLOCK_SIZE
        out = bytearray(data)
        side = 64

        for y in range(0, side, step):
            row = y * side
            for x in range(0, side, step):
                base = out[row + x]
                ymax = min(y + step, side)
                xmax = min(x + step, side)
                for yy in range(y, ymax):
                    dst = yy * side
                    for xx in range(x, xmax):
                        out[dst + xx] = base

        return bytes(out)

    def compress_flat_lumps(self):
        flat_ranges = self.collect_ranges(FLAT_START_MARKERS, FLAT_END_MARKERS)
        flat_indices = self.collect_indices_from_ranges(flat_ranges)

        rewritten = 0
        saved_bytes = 0

        for idx in sorted(flat_indices):
            lump = self.wad_file.get_lump_by_num(idx)
            if lump is None:
                continue
            if lump.length != FLAT_LUMP_SIZE:
                continue
            if lump.data.startswith(HUFF_LUMP_MAGIC):
                continue

            flat_data = self.lossify_flat_lump(lump.data)
            compressed = huffman_compress_block(flat_data)
            if compressed is None:
                continue

            if len(compressed) + HUFF_MIN_SAVINGS > lump.length:
                continue

            if self.wad_file.replace_lump(idx, Lump(lump.name, compressed)):
                rewritten += 1
                saved_bytes += lump.length - len(compressed)

        return rewritten, saved_bytes

    def process_level(self, lump_num):
        self.process_vertexes(lump_num)
        self.process_lines(lump_num)
        self.process_segs(lump_num)
        self.process_sides(lump_num)
        self.process_sectors(lump_num)
        self.process_nodes(lump_num)
        self.process_blockmap(lump_num)
        self.compress_map_runtime_lumps(lump_num)
        self.process_pnames()
        return True

    def compress_map_runtime_lumps(self, lump_num):
        for offset in HUFF_COMPRESSIBLE_MAP_LUMP_OFFSETS:
            self.compress_lump_huffman(lump_num + offset)

    def compress_lump_huffman(self, lump_num):
        lump = self.wad_file.get_lump_by_num(lump_num)
        if lump is None or lump.length == 0:
            return False

        if lump.length <= 16:
            return False

        if lump.data.startswith(HUFF_LUMP_MAGIC):
            return False

        compressed = huffman_compress_block(lump.data)
        if compressed is None:
            return False

        if len(compressed) + HUFF_MIN_SAVINGS > lump.length:
            return False

        return self.wad_file.replace_lump(lump_num, Lump(lump.name, compressed))

    def process_vertexes(self, lump_num):
        vtx_lump_num = lump_num + ML_VERTEXES
        vxl = self.wad_file.get_lump_by_num(vtx_lump_num)

        if vxl is None or vxl.length == 0:
            return False

        vtx_count = vxl.length // 4
        out = bytearray(vtx_count * 8)

        for i in range(vtx_count):
            x, y = struct.unpack_from("<hh", vxl.data, i * 4)
            struct.pack_into("<ii", out, i * 8, to_s32(x << 16), to_s32(y << 16))

        return self.wad_file.replace_lump(vtx_lump_num, Lump(vxl.name, bytes(out)))

    def process_lines(self, lump_num):
        line_lump_num = lump_num + ML_LINEDEFS
        lines = self.wad_file.get_lump_by_num(line_lump_num)
        if lines is None or lines.length == 0:
            return False

        vtx_lump_num = lump_num + ML_VERTEXES
        vxl = self.wad_file.get_lump_by_num(vtx_lump_num)
        if vxl is None or vxl.length == 0:
            return False

        line_count = lines.length // 14
        vtx_count = vxl.length // 8
        out = bytearray(line_count * 56)

        def get_vertex(index):
            if index < 0 or index >= vtx_count:
                return 0, 0
            return struct.unpack_from("<ii", vxl.data, index * 8)

        for i in range(line_count):
            v1, v2, flags, special, tag, side0, side1 = struct.unpack_from("<HHHhhHH", lines.data, i * 14)

            v1x, v1y = get_vertex(v1)
            v2x, v2y = get_vertex(v2)

            dx = to_s32(v2x - v1x)
            dy = to_s32(v2y - v1y)

            if dx == 0:
                slopetype = ST_VERTICAL
            elif dy == 0:
                slopetype = ST_HORIZONTAL
            else:
                slopetype = ST_POSITIVE if fixed_div(dy, dx) > 0 else ST_NEGATIVE

            bbox_left = v1x if v1x < v2x else v2x
            bbox_right = v2x if v1x < v2x else v1x
            bbox_top = v2y if v1y < v2y else v1y
            bbox_bottom = v1y if v1y < v2y else v2y

            struct.pack_into(
                "<iiiiIiiHHiiiiHhhH",
                out,
                i * 56,
                to_s32(v1x),
                to_s32(v1y),
                to_s32(v2x),
                to_s32(v2y),
                to_u32(i),
                dx,
                dy,
                to_u16(side0),
                to_u16(side1),
                to_s32(bbox_top),
                to_s32(bbox_bottom),
                to_s32(bbox_left),
                to_s32(bbox_right),
                to_u16(flags),
                to_s16(special),
                to_s16(tag),
                to_u16(slopetype),
            )

        return self.wad_file.replace_lump(line_lump_num, Lump(lines.name, bytes(out)))

    def process_segs(self, lump_num):
        segs_lump_num = lump_num + ML_SEGS
        segs = self.wad_file.get_lump_by_num(segs_lump_num)
        if segs is None or segs.length == 0:
            return False

        vtx_lump_num = lump_num + ML_VERTEXES
        vxl = self.wad_file.get_lump_by_num(vtx_lump_num)
        if vxl is None or vxl.length == 0:
            return False

        lines_lump_num = lump_num + ML_LINEDEFS
        lxl = self.wad_file.get_lump_by_num(lines_lump_num)
        if lxl is None or lxl.length == 0:
            return False

        sides_lump_num = lump_num + ML_SIDEDEFS
        sxl = self.wad_file.get_lump_by_num(sides_lump_num)
        if sxl is None or sxl.length == 0:
            return False

        seg_count = segs.length // 12
        vtx_count = vxl.length // 8
        line_count = lxl.length // 56
        side_count = sxl.length // 30

        out = bytearray(seg_count * 32)

        def get_vertex(index):
            if index < 0 or index >= vtx_count:
                return 0, 0
            return struct.unpack_from("<ii", vxl.data, index * 8)

        def get_line(index):
            if index < 0 or index >= line_count:
                return None
            return struct.unpack_from("<iiiiIiiHHiiiiHhhH", lxl.data, index * 56)

        def get_side_sector(index):
            if index < 0 or index >= side_count:
                return NO_INDEX
            _, _, _, _, _, sector = struct.unpack_from("<hh8s8s8sh", sxl.data, index * 30)
            return to_u16(sector)

        for i in range(seg_count):
            v1, v2, angle, linedef, side, offset = struct.unpack_from("<HHhHhh", segs.data, i * 12)

            v1x, v1y = get_vertex(v1)
            v2x, v2y = get_vertex(v2)

            line = get_line(linedef)
            if line is None:
                sidenum = NO_INDEX
                frontsectornum = NO_INDEX
                backsectornum = NO_INDEX
                line_flags = 0
                side0 = NO_INDEX
                side1 = NO_INDEX
            else:
                side0 = line[7]
                side1 = line[8]
                line_flags = line[13]
                if side in (0, 1):
                    sidenum = side0 if side == 0 else side1
                else:
                    sidenum = NO_INDEX

                if sidenum != NO_INDEX:
                    frontsectornum = get_side_sector(sidenum)
                else:
                    frontsectornum = NO_INDEX

                backsectornum = NO_INDEX
                if line_flags & ML_TWOSIDED:
                    if side in (0, 1):
                        other = side ^ 1
                        other_side = side0 if other == 0 else side1
                        if other_side != NO_INDEX:
                            backsectornum = get_side_sector(other_side)

            struct.pack_into(
                "<iiiiiIHHHH",
                out,
                i * 32,
                to_s32(v1x),
                to_s32(v1y),
                to_s32(v2x),
                to_s32(v2y),
                to_s32(offset << 16),
                to_u32(to_s32(angle << 16)),
                to_u16(sidenum),
                to_u16(linedef),
                to_u16(frontsectornum),
                to_u16(backsectornum),
            )

        return self.wad_file.replace_lump(segs_lump_num, Lump(segs.name, bytes(out)))

    def process_sides(self, lump_num):
        sides_lump_num = lump_num + ML_SIDEDEFS
        sides = self.wad_file.get_lump_by_num(sides_lump_num)

        if sides is None or sides.length == 0:
            return False
        if sides.length % 30 != 0:
            return False

        side_count = sides.length // 30
        out = bytearray(side_count * 12)

        for i in range(side_count):
            textureoffset, rowoffset, toptexture, bottomtexture, midtexture, sector = struct.unpack_from(
                "<hh8s8s8sh", sides.data, i * 30
            )

            top_num = self.get_texture_num_for_name(toptexture)
            bottom_num = self.get_texture_num_for_name(bottomtexture)
            mid_num = self.get_texture_num_for_name(midtexture)

            struct.pack_into(
                "<hhhhhh",
                out,
                i * 12,
                to_s16(textureoffset),
                to_s16(rowoffset),
                to_s16(top_num),
                to_s16(bottom_num),
                to_s16(mid_num),
                to_s16(sector),
            )

        return self.wad_file.replace_lump(sides_lump_num, Lump(sides.name, bytes(out)))

    def process_sectors(self, lump_num):
        sectors_lump_num = lump_num + ML_SECTORS
        sectors = self.wad_file.get_lump_by_num(sectors_lump_num)

        if sectors is None or sectors.length == 0:
            return False
        if sectors.length % 26 != 0:
            return False

        sector_count = sectors.length // 26
        out = bytearray(sector_count * 13)

        for i in range(sector_count):
            floorheight, ceilingheight, floorpic, ceilingpic, lightlevel, special, tag = struct.unpack_from(
                "<hh8s8shhh", sectors.data, i * 26
            )

            floor_num = self.get_flat_num_for_name(floorpic)
            ceiling_num = self.get_flat_num_for_name(ceilingpic)

            if lightlevel < 0:
                light_byte = 0
            elif lightlevel > 255:
                light_byte = 255
            else:
                light_byte = lightlevel

            struct.pack_into(
                "<hhhhBhh",
                out,
                i * 13,
                to_s16(floorheight),
                to_s16(ceilingheight),
                to_s16(floor_num),
                to_s16(ceiling_num),
                light_byte,
                to_s16(special),
                to_s16(tag),
            )

        return self.wad_file.replace_lump(sectors_lump_num, Lump(sectors.name, bytes(out)))

    def process_nodes(self, lump_num):
        nodes_lump_num = lump_num + ML_NODES
        nodes = self.wad_file.get_lump_by_num(nodes_lump_num)

        if nodes is None or nodes.length == 0:
            return False
        if nodes.data.startswith(NODE_COMPACT_MAGIC):
            return False
        if (nodes.length % 28) != 0:
            return False

        node_count = nodes.length // 28
        if node_count <= 0:
            return False

        parsed_nodes = []
        for i in range(node_count):
            values = struct.unpack_from("<hhhhhhhhhhhhHH", nodes.data, i * 28)
            parsed_nodes.append({
                "x": values[0],
                "y": values[1],
                "dx": values[2],
                "dy": values[3],
                "bbox": [
                    [values[4], values[5], values[6], values[7]],
                    [values[8], values[9], values[10], values[11]],
                ],
                "children": [to_u16(values[12]), to_u16(values[13])],
            })

        root_idx = node_count - 1
        root = parsed_nodes[root_idx]
        root_bbox = [
            max(root["bbox"][0][0], root["bbox"][1][0]),    # top
            min(root["bbox"][0][1], root["bbox"][1][1]),    # bottom
            min(root["bbox"][0][2], root["bbox"][1][2]),    # left
            max(root["bbox"][0][3], root["bbox"][1][3]),    # right
        ]

        encoded_nodes = [None] * node_count
        encoded_seen = [False] * node_count

        def clamp_q(value):
            if value < 0:
                return 0
            if value > 15:
                return 15
            return value

        def quantize_pair(parent_min, parent_max, child_min, child_max):
            if parent_max <= parent_min:
                return 0, 15, parent_min, parent_max

            span = parent_max - parent_min
            clamped_min = max(parent_min, min(parent_max, child_min))
            clamped_max = max(parent_min, min(parent_max, child_max))

            q_min = ((clamped_min - parent_min) * 15) // span
            q_max = ((clamped_max - parent_min) * 15 + span - 1) // span

            q_min = clamp_q(q_min)
            q_max = clamp_q(q_max)

            decoded_min = parent_min + ((q_min * span) // 15)
            decoded_max = parent_min + ((q_max * span + 14) // 15)

            return q_min, q_max, decoded_min, decoded_max

        def quantize_child_bbox(parent_bbox, child_bbox):
            q_bottom, q_top, dec_bottom, dec_top = quantize_pair(
                parent_bbox[1], parent_bbox[0], child_bbox[1], child_bbox[0]
            )
            q_left, q_right, dec_left, dec_right = quantize_pair(
                parent_bbox[2], parent_bbox[3], child_bbox[2], child_bbox[3]
            )

            qvals = [q_top, q_bottom, q_left, q_right]
            decoded = [dec_top, dec_bottom, dec_left, dec_right]
            return qvals, decoded

        def encode_node(idx, parent_bbox):
            if idx < 0 or idx >= node_count:
                return
            if encoded_seen[idx]:
                return

            node = parsed_nodes[idx]
            q0, child_bbox0 = quantize_child_bbox(parent_bbox, node["bbox"][0])
            q1, child_bbox1 = quantize_child_bbox(parent_bbox, node["bbox"][1])

            out = bytearray(NODE_COMPACT_ENTRY_SIZE)
            struct.pack_into(
                "<hhhhHH",
                out,
                0,
                to_s16(node["x"]),
                to_s16(node["y"]),
                to_s16(node["dx"]),
                to_s16(node["dy"]),
                to_u16(node["children"][0]),
                to_u16(node["children"][1]),
            )
            out[12] = ((q0[0] & 0xF) << 4) | (q0[1] & 0xF)
            out[13] = ((q0[2] & 0xF) << 4) | (q0[3] & 0xF)
            out[14] = ((q1[0] & 0xF) << 4) | (q1[1] & 0xF)
            out[15] = ((q1[2] & 0xF) << 4) | (q1[3] & 0xF)

            encoded_nodes[idx] = bytes(out)
            encoded_seen[idx] = True

            child0 = node["children"][0]
            child1 = node["children"][1]
            if (child0 & 0x8000) == 0:
                encode_node(child0, child_bbox0)
            if (child1 & 0x8000) == 0:
                encode_node(child1, child_bbox1)

        encode_node(root_idx, root_bbox)

        for idx in range(node_count):
            if not encoded_seen[idx]:
                encode_node(idx, root_bbox)

        if any(entry is None for entry in encoded_nodes):
            return False

        out = bytearray(NODE_COMPACT_HEADER_SIZE + (node_count * NODE_COMPACT_ENTRY_SIZE))
        struct.pack_into(
            "<4sHhhhhH",
            out,
            0,
            NODE_COMPACT_MAGIC,
            to_u16(node_count),
            to_s16(root_bbox[0]),
            to_s16(root_bbox[1]),
            to_s16(root_bbox[2]),
            to_s16(root_bbox[3]),
            0,
        )

        for i, entry in enumerate(encoded_nodes):
            start = NODE_COMPACT_HEADER_SIZE + (i * NODE_COMPACT_ENTRY_SIZE)
            out[start:start + NODE_COMPACT_ENTRY_SIZE] = entry

        if len(out) >= nodes.length:
            return False

        return self.wad_file.replace_lump(nodes_lump_num, Lump(nodes.name, bytes(out)))

    def process_blockmap(self, lump_num):
        blockmap_lump_num = lump_num + ML_BLOCKMAP
        blockmap = self.wad_file.get_lump_by_num(blockmap_lump_num)

        if blockmap is None or blockmap.length < 8 or (blockmap.length % 2) != 0:
            return False

        short_count = blockmap.length // 2
        shorts = list(struct.unpack_from("<" + ("h" * short_count), blockmap.data, 0))
        width = to_u16(shorts[2])
        height = to_u16(shorts[3])
        block_count = width * height

        if block_count <= 0 or (4 + block_count) > short_count:
            return False

        offsets = [to_u16(shorts[4 + i]) for i in range(block_count)]
        decoded_lists = []

        for offset in offsets:
            if offset >= short_count:
                decoded_lists.append((-1,))
                continue

            idx = offset
            if shorts[idx] == 0:
                idx += 1

            entries = []
            while idx < short_count:
                value = to_s16(shorts[idx])
                entries.append(value)
                idx += 1
                if value == -1:
                    break
                if len(entries) > 8192:
                    break

            if not entries or entries[-1] != -1:
                entries = [-1]

            decoded_lists.append(tuple(entries))

        out_shorts = [shorts[0], shorts[1], shorts[2], shorts[3]]
        out_shorts.extend([0] * block_count)
        list_offsets = {}
        next_offset = 4 + block_count

        for i, entries in enumerate(decoded_lists):
            offset = list_offsets.get(entries)
            if offset is None:
                if next_offset > 0x7FFF:
                    return False
                offset = next_offset
                list_offsets[entries] = offset
                out_shorts.extend(entries)
                next_offset += len(entries)

            out_shorts[4 + i] = to_s16(offset)

        rebuilt = struct.pack("<" + ("h" * len(out_shorts)), *out_shorts)
        if len(rebuilt) >= blockmap.length:
            return False

        return self.wad_file.replace_lump(blockmap_lump_num, Lump(blockmap.name, rebuilt))

    def get_texture_num_for_name(self, tex_name):
        if self.texture_map is None:
            self.texture_map = self.build_texture_lookup()

        tex_name_upper = decode_lump_name(tex_name).upper()
        return self.texture_map.get(tex_name_upper, 0)

    def get_flat_num_for_name(self, flat_name):
        if self.flat_map is None:
            self.flat_map = self.build_flat_lookup()

        flat_name_upper = decode_lump_name(flat_name).upper()
        return self.flat_map.get(flat_name_upper, 0)

    def build_texture_lookup(self):
        texture_map = {}
        next_index = 0

        lump_num, tex1 = self.wad_file.get_lump_by_name("TEXTURE1")
        if lump_num != -1 and tex1 is not None:
            next_index = self.append_texture_lump(texture_map, tex1.data, next_index)

        lump_num, tex2 = self.wad_file.get_lump_by_name("TEXTURE2")
        if lump_num != -1 and tex2 is not None:
            self.append_texture_lump(texture_map, tex2.data, next_index)

        return texture_map

    def append_texture_lump(self, texture_map, data, base_index):
        if len(data) < 4:
            return base_index

        (count,) = struct.unpack_from("<i", data, 0)
        if count <= 0:
            return base_index

        for i in range(count):
            ofs_pos = 4 + (i * 4)
            if ofs_pos + 4 > len(data):
                break

            (entry_ofs,) = struct.unpack_from("<i", data, ofs_pos)
            if entry_ofs < 0 or entry_ofs + 8 > len(data):
                continue

            tex_name = decode_lump_name(data[entry_ofs:entry_ofs + 8]).upper()
            tex_index = base_index + i

            if tex_name and tex_name not in texture_map:
                texture_map[tex_name] = tex_index

        return base_index + count

    def build_flat_lookup(self):
        flat_map = {}
        firstflat, _ = self.wad_file.get_lump_by_name("F_START")
        lastflat, _ = self.wad_file.get_lump_by_name("F_END")

        if firstflat == -1 or lastflat == -1 or lastflat <= firstflat:
            return flat_map

        for idx in range(firstflat + 1, lastflat):
            name_upper = self.wad_file.lumps[idx].name.upper()
            if name_upper:
                flat_map[name_upper] = idx - (firstflat + 1)

        return flat_map

    def process_pnames(self):
        lump_num, pnames_lump = self.wad_file.get_lump_by_name("PNAMES")

        if lump_num == -1 or pnames_lump is None or pnames_lump.length < 4:
            return False

        (count,) = struct.unpack_from("<I", pnames_lump.data, 0)

        out = bytearray(4 + (count * 8))
        struct.pack_into("<I", out, 0, count)

        for i in range(count):
            start = 4 + (i * 8)
            raw_name = pnames_lump.data[start:start + 8]
            raw_name = raw_name.ljust(8, b"\x00")
            name_upper = decode_lump_name(raw_name).upper().encode("latin-1", errors="ignore")[:8]
            out[start:start + 8] = name_upper.ljust(8, b"\x00")

        return self.wad_file.replace_lump(lump_num, Lump("PNAMES", bytes(out)))

    def is_audio_lump(self, lump):
        name_upper = lump.name.upper()

        if name_upper in ("GENMIDI", "DMXGUS"):
            return True

        if name_upper.startswith("D_") or name_upper.startswith("DS") or name_upper.startswith("DP") or name_upper.startswith("MUS_"):
            return True

        return lump.data.startswith(b"MUS\x1A")

    def remove_unused_lumps(self):
        filtered = []
        in_audio_block = False

        for lump in self.wad_file.lumps:
            name_upper = lump.name.upper()

            if name_upper in self.AUDIO_START_MARKERS:
                in_audio_block = True
                continue

            if name_upper in self.AUDIO_END_MARKERS:
                in_audio_block = False
                continue

            if name_upper in DEMO_LUMP_NAMES:
                continue

            if in_audio_block or self.is_audio_lump(lump):
                continue

            filtered.append(lump)

        self.wad_file.lumps = filtered
        return True


def save_bytes_as_c_file(data, file_path):
    with open(file_path, "w", encoding="ascii") as f:
        f.write(f"const unsigned char doom_iwad[{len(data)}UL] = {{\n")

        for i, byte in enumerate(data):
            f.write(f"0x{byte:02x},")
            if ((i + 1) % 40) == 0:
                f.write("\n")

        f.write("\n};")


def main():
    parser = argparse.ArgumentParser(description="Process Doom WAD files for GBADoom.")
    parser.add_argument("-in", dest="in_file", required=True, help="Input IWAD/PWAD file.")
    parser.add_argument("-out", dest="out_file", help="Optional output WAD path.")
    parser.add_argument("-cfile", dest="c_file", help="Optional output C source path.")
    parser.add_argument("-pwad", dest="pwads", nargs="*", default=[], help="Optional PWAD files to merge before processing.")

    args = parser.parse_args()

    wf = WadFile(args.in_file)
    if not wf.load_wad_file():
        print(f"error: failed to load WAD: {args.in_file}", file=sys.stderr)
        return 1

    pwads = list(args.pwads)
    bundled_wad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gbadoom.wad")
    if os.path.isfile(bundled_wad):
        pwads.append(bundled_wad)

    for pwad_path in pwads:
        pf = WadFile(pwad_path)
        if not pf.load_wad_file():
            print(f"warning: failed to load PWAD: {pwad_path}", file=sys.stderr)
            continue
        wf.merge_wad_file(pf)

    processor = WadProcessor(wf)
    if not processor.process_wad():
        print("error: could not find Doom map lumps (MAP01 or E1M1)", file=sys.stderr)
        return 1

    wad_bytes = wf.to_bytes()

    if args.out_file:
        if not wf.save_wad_file(args.out_file):
            print(f"error: failed to write output WAD: {args.out_file}", file=sys.stderr)
            return 1

    if args.c_file:
        try:
            save_bytes_as_c_file(wad_bytes, args.c_file)
        except OSError:
            print(f"error: failed to write C file: {args.c_file}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
