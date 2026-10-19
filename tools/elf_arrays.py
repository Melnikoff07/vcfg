"""Check that the GPU machine arrays loaded by the runtime appear byte-exactly once in the linked ELF.
Usage: python -I tools/elf_arrays.py <plugin.elf> <plugin/source> [name,name,...]
Why: a global text replace once changed hex words inside shader arrays (A15-A19 regression). The arrays in the headers
are the reference; regenerate them only from IR with build_shader.py, never edit them by hand.
"""
import pathlib, re, struct, sys

DEFAULT = 'pyr8,me3,me2,gm8,fgs8,me1,mf8,phase8,warp720,sum_previous,osd8,post_cb'

def arrays(src):
    out = {}
    for h in sorted(pathlib.Path(src).glob('*.h')):
        for m in re.finditer(r'static const uint32_t (\w+)_code\[(\d+)\] = \{(.*?)\};', h.read_text(), re.S):
            w = [int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{8})', m[3])]
            out[m[1]] = struct.pack('<%dI' % len(w), *w)
    return out

def main():
    elf = pathlib.Path(sys.argv[1]).read_bytes()
    arr = arrays(sys.argv[2])
    names = (sys.argv[3] if len(sys.argv) > 3 else DEFAULT).split(',')
    bad = 0
    for n in names:
        c = elf.count(arr[n]) if n in arr else -1
        print(f'{n:13s} {"missing header" if c < 0 else ("ok" if c == 1 else "FOUND %d TIMES" % c)}')
        bad += c != 1
    print(f'arrays_exact={len(names) - bad}/{len(names)}')
    sys.exit(1 if bad else 0)

if __name__ == '__main__':
    main()
