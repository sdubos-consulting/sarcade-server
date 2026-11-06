# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""A small HDF5 reader, enough for the radar mosaics of Météo-France
(ODIM-H5): groups, attributes and one 2D dataset.

Why not h5py: the server runs on Python 3.15 and h5py has no wheel for it
yet (6 Nov 2026); building HDF5 in the image is heavier than reading the
few structures the radar files use. Written from the HDF5 file format
specification (version 3.0), no code copied.

Supported: superblock 0 to 3; object headers 1 and 2 with continuations;
groups by symbol table (B-tree 1, local heap) or compact links; attributes
1 to 3 (integers, floats, fixed and variable-length strings); datasets
compact, contiguous or chunked (B-tree 1, single chunk, fixed array) with
the deflate, shuffle and Fletcher-32 filters. Anything else raises
``Unsupported`` so that the caller can say the file was not read.
"""
from __future__ import annotations

import zlib

import numpy as np


class Unsupported(ValueError):
    pass


_SIG = b"\x89HDF\r\n\x1a\n"


class Node:
    """A group or a dataset: ``attrs`` and, for a group, ``children``."""

    def __init__(self, f: File, addr: int, name: str):
        self.f, self.addr, self.name = f, addr, name
        self.attrs: dict = {}
        self.children: dict[str, Node] = {}
        self._msgs = f._messages(addr)
        self.is_dataset = any(t == 0x0008 for t, _ in self._msgs)
        for t, body in self._msgs:
            if t == 0x000C:
                k, v = f._attribute(body)
                self.attrs[k] = v
        if not self.is_dataset:
            for child_name, child_addr in f._links(self._msgs):
                self.children[child_name] = Node(f, child_addr, child_name)

    def __getitem__(self, path: str) -> Node:
        node = self
        for part in [p for p in path.split("/") if p]:
            node = node.children[part]
        return node

    def __contains__(self, path: str) -> bool:
        try:
            self[path]
            return True
        except KeyError:
            return False

    def walk(self, prefix: str = ""):
        for k, v in self.children.items():
            p = f"{prefix}/{k}"
            yield p, v
            yield from v.walk(p)

    def read(self) -> np.ndarray:
        if not self.is_dataset:
            raise Unsupported("not a dataset")
        return self.f._read_dataset(self._msgs)


class File:
    def __init__(self, data: bytes):
        self.d = data
        base = self._find_superblock()
        self._superblock(base)
        self.root = Node(self, self.root_addr, "")

    # --- low level -------------------------------------------------------------

    def _u(self, pos: int, size: int) -> int:
        return int.from_bytes(self.d[pos:pos + size], "little")

    def _find_superblock(self) -> int:
        pos = 0
        while pos < len(self.d):
            if self.d[pos:pos + 8] == _SIG:
                return pos
            pos = 512 if pos == 0 else pos * 2
        raise Unsupported("no HDF5 signature")

    def _superblock(self, b: int) -> None:
        version = self.d[b + 8]
        if version in (0, 1):
            self.so, self.sl = self.d[b + 13], self.d[b + 14]
            p = b + 24 + (4 if version == 1 else 0)
            self.base = self._u(p, self.so)
            p += 4 * self.so  # base, free space, end of file, driver info
            # Root group symbol table entry: name offset, object header.
            self.root_addr = self._u(p + self.so, self.so)
        elif version in (2, 3):
            self.so, self.sl = self.d[b + 9], self.d[b + 10]
            p = b + 12
            self.base = self._u(p, self.so)
            self.root_addr = self._u(p + 3 * self.so, self.so)
        else:
            raise Unsupported(f"superblock {version}")
        if self.base not in (0, b):
            self.base = b

    def _addr(self, a: int) -> int:
        return a + self.base

    def _undef(self, a: int) -> bool:
        return a == (1 << (8 * self.so)) - 1

    # --- object headers --------------------------------------------------------

    def _messages(self, addr: int) -> list[tuple[int, bytes]]:
        p = self._addr(addr)
        out: list[tuple[int, bytes]] = []
        if self.d[p:p + 4] == b"OHDR":
            flags = self.d[p + 5]
            q = p + 6
            if flags & 0x20:
                q += 16
            if flags & 0x10:
                q += 4
            n = 1 << (flags & 3)
            size = self._u(q, n)
            q += n
            blocks = [(q, q + size)]
            creation = bool(flags & 0x04)
            while blocks:
                start, end = blocks.pop(0)
                i = start
                while i + 4 <= end:
                    t = self.d[i]
                    msize = self._u(i + 1, 2)
                    i += 4 + (2 if creation else 0)
                    body = self.d[i:i + msize]
                    i += msize
                    if t == 0x10:
                        ca = self._u_from(body, 0, self.so)
                        cl = self._u_from(body, self.so, self.sl)
                        cp = self._addr(ca)
                        if self.d[cp:cp + 4] != b"OCHK":
                            raise Unsupported("continuation block")
                        blocks.append((cp + 4, cp + cl - 4))
                    elif t != 0:
                        out.append((t, body))
            return out
        if self.d[p] != 1:
            raise Unsupported(f"object header {self.d[p]}")
        count = self._u(p + 2, 2)
        size = self._u(p + 8, 4)
        blocks = [(p + 16, p + 16 + size)]
        while blocks and len(out) < count + 64:
            start, end = blocks.pop(0)
            i = start
            while i + 8 <= end:
                t = self._u(i, 2)
                msize = self._u(i + 2, 2)
                body = self.d[i + 8:i + 8 + msize]
                i += 8 + msize
                if t == 0x10:
                    ca = self._u_from(body, 0, self.so)
                    cl = self._u_from(body, self.so, self.sl)
                    blocks.append((self._addr(ca), self._addr(ca) + cl))
                elif t != 0:
                    out.append((t, body))
        return out

    @staticmethod
    def _u_from(b: bytes, pos: int, size: int) -> int:
        return int.from_bytes(b[pos:pos + size], "little")

    # --- groups ----------------------------------------------------------------

    def _links(self, msgs) -> list[tuple[str, int]]:
        out = []
        for t, body in msgs:
            if t == 0x0011:  # symbol table
                btree = self._u_from(body, 0, self.so)
                heap = self._u_from(body, self.so, self.so)
                names = self._local_heap(heap)
                out += [(names(o), a) for o, a in self._group_btree(btree)]
            elif t == 0x0006:  # link
                name, addr = self._link(body)
                if addr is not None:
                    out.append((name, addr))
            elif t == 0x0002:  # link info: dense storage
                heap = self._u_from(body, 2 + (8 if body[1] & 1 else 0), self.so)
                if not self._undef(heap):
                    raise Unsupported("dense link storage")
        return out

    def _local_heap(self, addr: int):
        p = self._addr(addr)
        if self.d[p:p + 4] != b"HEAP":
            raise Unsupported("local heap")
        data = self._addr(self._u(p + 8 + 2 * self.sl, self.so))

        def name(offset: int) -> str:
            s = data + offset
            e = self.d.index(b"\0", s)
            return self.d[s:e].decode("utf-8", "replace")
        return name

    def _group_btree(self, addr: int):
        p = self._addr(addr)
        if self.d[p:p + 4] != b"TREE":
            raise Unsupported("group B-tree")
        level = self.d[p + 5]
        used = self._u(p + 6, 2)
        q = p + 8 + 2 * self.so
        children = []
        for _ in range(used):
            q += self.sl  # key
            children.append(self._u(q, self.so))
            q += self.so
        for c in children:
            if level > 0:
                yield from self._group_btree(c)
            else:
                yield from self._symbol_node(c)

    def _symbol_node(self, addr: int):
        p = self._addr(addr)
        if self.d[p:p + 4] != b"SNOD":
            raise Unsupported("symbol node")
        n = self._u(p + 6, 2)
        q = p + 8
        for _ in range(n):
            yield self._u(q, self.so), self._u(q + self.so, self.so)
            q += 2 * self.so + 24

    def _link(self, b: bytes) -> tuple[str, int | None]:
        flags = b[1]
        i = 2
        ltype = 0
        if flags & 0x08:
            ltype = b[i]
            i += 1
        if flags & 0x04:
            i += 8
        if flags & 0x10:
            i += 1
        n = 1 << (flags & 3)
        length = self._u_from(b, i, n)
        i += n
        name = b[i:i + length].decode("utf-8", "replace")
        i += length
        if ltype != 0:
            return name, None
        return name, self._u_from(b, i, self.so)

    # --- datatypes and dataspaces ----------------------------------------------

    def _datatype(self, b: bytes):
        cls = b[0] & 0x0F
        bits = b[1] | (b[2] << 8) | (b[3] << 16)
        size = self._u_from(b, 4, 4)
        order = ">" if bits & 1 else "<"
        if cls == 0:
            signed = bool(bits & 0x08)
            return ("num", np.dtype(f"{order}{'i' if signed else 'u'}{size}"))
        if cls == 1:
            return ("num", np.dtype(f"{order}f{size}"))
        if cls == 3:
            return ("str", size)
        if cls == 9:
            if (bits & 0x0F) == 1:
                return ("vlstr", size)
            raise Unsupported("variable-length sequence")
        raise Unsupported(f"datatype class {cls}")

    def _dataspace(self, b: bytes) -> tuple[int, ...]:
        version, ndim = b[0], b[1]
        if version == 1:
            p = 8
        elif version == 2:
            if b[3] == 2:  # null
                return (0,)
            p = 4
        else:
            raise Unsupported(f"dataspace {version}")
        return tuple(self._u_from(b, p + k * self.sl, self.sl) for k in range(ndim))

    # --- attributes -------------------------------------------------------------

    def _attribute(self, b: bytes):
        version = b[0]
        nlen, tlen, slen = self._u_from(b, 2, 2), self._u_from(b, 4, 2), self._u_from(b, 6, 2)

        def pad(n):
            return (n + 7) & ~7 if version == 1 else n
        i = 8 + (1 if version == 3 else 0)
        name = b[i:i + nlen].split(b"\0")[0].decode("utf-8", "replace")
        i += pad(nlen)
        dtype = self._datatype(b[i:i + tlen])
        i += pad(tlen)
        shape = self._dataspace(b[i:i + slen])
        i += pad(slen)
        count = int(np.prod(shape)) if shape else 1
        raw = b[i:]
        kind, t = dtype
        if kind == "num":
            v = np.frombuffer(raw[:count * t.itemsize], dtype=t)
            return name, (v[0].item() if count == 1 else v.copy())
        if kind == "str":
            vals = [raw[k * t:(k + 1) * t].split(b"\0")[0].decode("utf-8", "replace") for k in range(count)]
            return name, vals[0] if count == 1 else vals
        vals = []
        step = 4 + self.so + 4
        for k in range(count):
            q = k * step
            heap = self._u_from(raw, q + 4, self.so)
            index = self._u_from(raw, q + 4 + self.so, 4)
            vals.append(self._global_heap(heap, index).split(b"\0")[0].decode("utf-8", "replace"))
        return name, vals[0] if count == 1 else vals

    def _global_heap(self, addr: int, index: int) -> bytes:
        p = self._addr(addr)
        if self.d[p:p + 4] != b"GCOL":
            raise Unsupported("global heap")
        size = self._u(p + 8, self.sl)
        q, end = p + 8 + self.sl, p + size
        while q + 8 + self.sl <= end:
            i = self._u(q, 2)
            osize = self._u(q + 8, self.sl)
            if i == 0:
                break
            if i == index:
                return self.d[q + 8 + self.sl:q + 8 + self.sl + osize]
            q += 8 + self.sl + ((osize + 7) & ~7)
        raise Unsupported("global heap object")

    # --- datasets --------------------------------------------------------------

    def _filters(self, b: bytes) -> list[tuple[int, list[int]]]:
        version, n = b[0], b[1]
        i = 8 if version == 1 else 2
        out = []
        for _ in range(n):
            fid = self._u_from(b, i, 2)
            i += 2
            nlen = 0
            if version == 1 or fid >= 256:
                nlen = self._u_from(b, i, 2)
                i += 2
            i += 2  # flags
            nvals = self._u_from(b, i, 2)
            i += 2
            i += ((nlen + 7) & ~7) if version == 1 else nlen
            vals = [self._u_from(b, i + 4 * k, 4) for k in range(nvals)]
            i += 4 * nvals
            if version == 1 and nvals % 2:
                i += 4
            out.append((fid, vals))
        return out

    @staticmethod
    def _unfilter(raw: bytes, filters, itemsize: int, mask: int = 0) -> bytes:
        for k in range(len(filters) - 1, -1, -1):
            if mask & (1 << k):
                continue
            fid, _vals = filters[k]
            if fid == 1:
                raw = zlib.decompress(raw)
            elif fid == 2:
                a = np.frombuffer(raw, dtype=np.uint8)
                n = len(a) // itemsize
                raw = a[:n * itemsize].reshape(itemsize, n).T.tobytes() + a[n * itemsize:].tobytes()
            elif fid == 3:
                raw = raw[:-4]
            else:
                raise Unsupported(f"filter {fid}")
        return raw

    def _read_dataset(self, msgs) -> np.ndarray:
        dtype = shape = layout = None
        filters: list = []
        for t, body in msgs:
            if t == 0x0003:
                dtype = self._datatype(body)
            elif t == 0x0001:
                shape = self._dataspace(body)
            elif t == 0x0008:
                layout = body
            elif t == 0x000B:
                filters = self._filters(body)
        if dtype is None or dtype[0] != "num" or shape is None or layout is None:
            raise Unsupported("dataset without numeric type")
        dt = dtype[1]
        version, cls = layout[0], layout[1]
        if version not in (3, 4, 5):
            raise Unsupported(f"layout {version}")
        if cls == 0:
            size = self._u_from(layout, 2, 2)
            return np.frombuffer(layout[4:4 + size], dtype=dt).reshape(shape).copy()
        if cls == 1:
            a = self._u_from(layout, 2, self.so)
            p = self._addr(a)
            n = int(np.prod(shape)) * dt.itemsize
            return np.frombuffer(self.d[p:p + n], dtype=dt).reshape(shape).copy()
        if cls != 2:
            raise Unsupported(f"layout class {cls}")
        out = np.zeros(shape, dtype=dt)
        if version == 3:
            ndim = layout[2]
            a = self._u_from(layout, 3, self.so)
            p = 3 + self.so
            chunk = tuple(self._u_from(layout, p + 4 * k, 4) for k in range(ndim - 1))
            chunks = self._chunk_btree(a, ndim)
        else:
            flags, ndim, enc = layout[2], layout[3], layout[4]
            p = 5
            dims = [self._u_from(layout, p + enc * k, enc) for k in range(ndim)]
            p += enc * ndim
            chunk = tuple(dims[:-1])
            index = layout[p]
            p += 1
            if index == 1:
                fsize, fmask = None, 0
                if flags & 0x02:
                    fsize = self._u_from(layout, p, self.sl)
                    fmask = self._u_from(layout, p + self.sl, 4)
                    p += self.sl + 4
                a = self._u_from(layout, p, self.so)
                if fsize is None:
                    fsize = int(np.prod(chunk)) * dt.itemsize
                chunks = [((0,) * len(chunk), self._addr(a), fsize, fmask)]
            elif index == 3:
                p += 1  # page bits
                a = self._u_from(layout, p, self.so)
                chunks = self._fixed_array(a, shape, chunk, dt.itemsize)
            else:
                raise Unsupported(f"chunk index {index}")
        for offset, pos, size, mask in chunks:
            raw = self._unfilter(self.d[pos:pos + size], filters, dt.itemsize, mask)
            block = np.frombuffer(raw[:int(np.prod(chunk)) * dt.itemsize], dtype=dt).reshape(chunk)
            sl = tuple(slice(o, min(o + c, s)) for o, c, s in zip(offset, chunk, shape))
            out[sl] = block[tuple(slice(0, x.stop - x.start) for x in sl)]
        return out

    def _chunk_btree(self, addr: int, ndim: int) -> list:
        p = self._addr(addr)
        if self.d[p:p + 4] != b"TREE" or self.d[p + 4] != 1:
            raise Unsupported("chunk B-tree")
        level = self.d[p + 5]
        used = self._u(p + 6, 2)
        q = p + 8 + 2 * self.so
        ksize = 8 + 8 * ndim
        out = []
        for _ in range(used):
            size = self._u(q, 4)
            mask = self._u(q + 4, 4)
            offset = tuple(self._u(q + 8 + 8 * k, 8) for k in range(ndim - 1))
            child = self._u(q + ksize, self.so)
            q += ksize + self.so
            if level > 0:
                out += self._chunk_btree(child, ndim)
            else:
                out.append((offset, self._addr(child), size, mask))
        return out

    def _fixed_array(self, addr: int, shape, chunk, itemsize: int) -> list:
        p = self._addr(addr)
        if self.d[p:p + 4] != b"FAHD":
            raise Unsupported("fixed array")
        client, esize = self.d[p + 5], self.d[p + 6]
        n = self._u(p + 8, self.sl)
        data = self._addr(self._u(p + 8 + self.sl, self.so))
        if self.d[data:data + 4] != b"FADB":
            raise Unsupported("fixed array data block")
        if n > (1 << self.d[p + 7]):
            raise Unsupported("paged fixed array")
        q = data + 6 + self.so
        per_row = [-(-s // c) for s, c in zip(shape, chunk)]
        out = []
        for k in range(n):
            e = q + k * esize
            a = self._u(e, self.so)
            if client == 0:
                size, mask = int(np.prod(chunk)) * itemsize, 0
            else:
                ls = esize - self.so - 4
                size = self._u(e + self.so, ls)
                mask = self._u(e + self.so + ls, 4)
            if self._undef(a):
                continue
            idx, rem = [], k
            for m in reversed(per_row):
                idx.append(rem % m)
                rem //= m
            idx.reverse()
            out.append((tuple(i * c for i, c in zip(idx, chunk)), self._addr(a), size, mask))
        return out


def read(data: bytes) -> File:
    return File(data)
