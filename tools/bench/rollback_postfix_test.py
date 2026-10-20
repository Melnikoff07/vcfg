"""Restore the preceding gridmix b mapping for CUSA00557; retain all artifacts."""
import ftplib
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console

with console.connect() as ftp:
    original=console.get(ftp,console.INI);lines=original.splitlines()
    section=next(idx for title,idx in console.sections(lines) if title=='CUSA00557')
    body=[i for i in section[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';',b'#'))]
    if len(body)!=1:raise RuntimeError('unexpected title mapping')
    current=lines[body[0]].strip();previous=b'/data/GoldHEN/plugins/vcfg_classic_gridmix_20261020b.prx'
    if current!=previous and not current.startswith(b'/data/GoldHEN/plugins/vcfg_classic_postfix_20261020'):
        raise RuntimeError('mapping changed outside this test; refusing')
    ftp.size(previous.decode());lines[body[0]]=previous
    console.write_ini(ftp,original,lines,'postfix_rollback_CUSA00557')
    marker='/data/bb_probe/postfix_CUSA00557'
    try:console.get(ftp,marker)
    except ftplib.error_perm as ex:
        if not str(ex).startswith('550'):raise
    else:
        target='/data/vcfg_quarantine_20261019_163934/postfix_CUSA00557_'+time.strftime('%Y%m%d_%H%M%S')
        ftp.rename(marker,target);print('MARKER_QUARANTINED',target)
    print('Restored CUSA00557 to gridmix b; no PRX or profiles changed/deleted.')
