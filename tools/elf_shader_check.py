import sys,re,struct,pathlib
elf=pathlib.Path(sys.argv[1]).read_bytes()
names=sys.argv[4].split(',')
for h in sys.argv[2:4]:
    t=pathlib.Path(h).read_text()
    for m in re.finditer(r'static const uint32_t (\w+)_code\[(\d+)\] = \{(.*?)\};',t,re.S):
        if m[1] not in names: continue
        w=[int(x,16) for x in re.findall(r'0x([0-9A-Fa-f]{8})',m[3])]
        b=struct.pack('<%dI'%len(w),*w)
        print(f'{m[1]:13s} words={len(w):5d} found_in_elf={elf.count(b)}')
