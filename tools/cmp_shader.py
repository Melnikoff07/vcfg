import sys,re,subprocess,pathlib,importlib.util
spec=importlib.util.spec_from_file_location('bsh',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
llc=sys.argv[2];hdr=pathlib.Path(sys.argv[3]).read_text();name=sys.argv[4]
m=re.search(r'static const uint32_t %s_code\[(\d+)\] = \{(.*?)\};'%name,hdr,re.S)
ref=[int(x,16) for x in re.findall(r'0x([0-9A-Fa-f]{8})',m[2])]
for ll in sys.argv[5:]:
    try:
        obj=subprocess.run([llc,'-mcpu=gfx700','-O2','-filetype=obj',ll,'-o','-'],capture_output=True,check=True).stdout
        w=b.text_words(obj);w+=[0xBF810000]*((-len(w))%64+16)
        diff=sum(1 for a,c in zip(w,ref) if a!=c)+abs(len(w)-len(ref))
        print(f'{name} ref={len(ref)} got={len(w)} diffwords={diff} {ll}')
    except Exception as e: print('ERR',ll,e)
