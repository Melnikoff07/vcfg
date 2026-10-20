"""Build the dated research report and local provenance manifest from saved results."""
import hashlib
import json
import pathlib

ROOT=pathlib.Path(__file__).resolve().parents[2]


def main():
    def read(p):return json.loads((ROOT/p).read_text())
    lines=['# Edge stability research, 2026-10-20','',
        '## Subsequent validity correction','',
        'The owner supplied stairs_report.md after this round. Code inspection',
        'confirmed that runtime rcas8 modifies the real surface in place and the',
        'next warp reads it as P; these experiments supplied raw A/raw B. Their',
        'numbers describe an offline chain and must not be treated as a faithful',
        'measurement of deployed behavior. The in-place post shader also leaves',
        'a cross-workgroup border-read/write hazard despite its LDS barrier.',
        'Retained raw motion pyramids and post-processed color history must be',
        'modeled separately. Previous candidate acceptance decisions require',
        'revalidation under that corrected model. Exact external-report effect',
        'sizes are not independently reproduced here.','',
        '## Original round scope','',
        'The owner reports flickering/crawling stair steps and traveling defects on',
        'aliased contours during camera turns. Build gridmix b is slightly better,',
        'but still visually unacceptable. DOF is not established as the cause.',
        'This round changes PC experiments only; no new console build is deployed.','',
        'No new candidate passed the complete round. In particular, history 0.5',
        'improves PSNR/SSIM and the temporal proxy on all three short sequences,',
        'but worsens LPIPS by 6.585% on the third, fixed-parameter check. It is',
        'rejected for hardware promotion. The visual acceptance goal remains open.','',
        '## Primary-source findings','',
        '- [Frame interpolation](https://gpuopen.com/manuals/fidelityfx_sdk/techniques/frame-interpolation/)',
        '  uses separate visibility/disocclusion decisions when combining reprojections.',
        '  Its full integration also uses engine depth and motion resources that our',
        '  optical-only experiment does not have. Confidence mixing here is an',
        '  approximation, not an implementation of that complete algorithm.',
        '- [Optical flow](https://gpuopen.com/manuals/fidelityfx_sdk/techniques/optical-flow/)',
        '  documents 8x8 block matching, 3x3 vector outlier filtering and finer-scale',
        '  candidate selection. Copying an isolated filtering step into our different',
        '  midpoint pipeline must be measured; it can erase motion boundaries.',
        '- [Temporal AA survey](https://research.nvidia.com/labs/rtr/publication/yang2020survey/)',
        '  separates sample accumulation from history validation. We test motion-',
        '  compensated generated-frame history with rejection/clamping, without',
        '  engine jitter/depth/vectors. This is not a full TAA implementation.','',
        '## CUSA00557 screening','',
        'Reference is the deployed gridmix b PC algorithm, Quality / 100 / AA off.',
        'Training uses 6 even samples of the existing 12 sparse 1920x1080 triples.',
        'These scenes were explored before this round; the odd half is not unseen',
        'independent scene validation. GT enters scoring only. Lower LPIPS/TCE is',
        'better. TCE is the existing triple-step error proxy, not measured shimmer.','',
        '| Experiment | delta PSNR dB | delta SSIM | delta LPIPS | delta TCE |',
        '|---|---:|---:|---:|---:|']
    sources=[]
    specs=[('edge_stability/train_results.json','edge ',None),
           ('grid_confidence/train_results.json','confidence ',None),
           ('fine_filter/train_results.json','fine filter ',None),
           ('symmetric_refine/train_results.json','fine fit ',None),
           ('post_fusion/train_results.json','one shared post','single'),
           ('dense_edges/train_results.json','dense correction','single')]
    for rel,prefix,single in specs:
        p='bench/'+rel;sources.append(p);summary=read(p)['summary']
        variants={prefix:summary} if single else {prefix+k:v for k,v in summary.items()}
        for label,s in variants.items():
            d=s['delta'];lp=d['LPIPS'];fmt='unmeasured' if lp!=lp else f'{lp:+.6f}'
            lines.append(f"| {label} | {d['PSNR']:+.5f} | {d['SSIM']:+.6f} | {fmt} | {d['TCE']:+.6f} |")
    lines+=['',
        'Edge tangent50 was subsequently scored with LPIPS: delta +0.00009184',
        '(worse), despite +0.02073 dB PSNR. Additional smoothing was not accepted.',
        'Uniform medoid filtering, symmetric/subpixel fine fitting, dense correction',
        'and shared nonlinear post have material regressions; no hardware promotion.',
        'Shared post might reduce GPU passes, but no timing gain was measured.','',
        'Confidence soft12 was checked on all 12 samples with LPIPS:']
    train=read('bench/grid_confidence/train_soft12_lpips.json')['summary']
    val=read('bench/grid_confidence/validation_results.json')['summary']['soft12']
    sources+=['bench/grid_confidence/train_soft12_lpips.json','bench/grid_confidence/validation_results.json']
    for split,s in [('even',train),('odd',val)]:
        d=s['delta'];pct=d['LPIPS']/s['baseline']['LPIPS']*100
        lines.append(f"- {split}: PSNR {d['PSNR']:+.5f} dB, SSIM {d['SSIM']:+.6f}, LPIPS {pct:+.3f}%, TCE {d['TCE']:+.6f}.")
    lines+=['',
        'The visually inspected fixed fence crop still has severely broken/wavy',
        'geometry. Small aggregate gains do not resolve it. Confidence mixing also',
        'slightly worsens the aligned temporal error on both initial video sequences.','',
        '## Consecutive sequence checks','',
        'Sequences are local full-resolution 50 Hz video, evaluated as 25->50.',
        'They are not game footage or evidence of console 30->60 quality. Six',
        'consecutive triples per sequence are verified by current A == preceding B.',
        'The first pair is excluded for motion warmup; five mids and four successive',
        'error transitions are scored. The sequence is short, so history is not fully',
        'converged. Cache state is trusted local data; no pickle is accepted remotely.','',
        'The added temporal proxy reprojects the previous reconstruction residual',
        '(mid-GT) with the same current input motion field for every method. Samples',
        'outside the screen or with GT reprojection mismatch >=0.08 are excluded.',
        'Its coverage is reported; it is an approximate motion-aligned residual',
        'change, not a perceptual shimmer detector or a ground-truth flow metric.','',
        'History strength 0.25/0.5/0.75 was screened on sequences tree and park.',
        'Strength 0.5 was fixed before checking the third sequence ducks. History',
        'is reprojected, clipped into the current 3x3 color range and rejected by',
        'color disagreement/out-of-screen/static tests. Only moving edges accumulate.',
        'No explicit image-wide blur or additional frame of lookahead is introduced;',
        'GPU time, retained history surface safety and actual latency remain untested.','',
        '| Sequence, history 0.5 | delta PSNR dB | delta SSIM | LPIPS change | aligned residual change | HF/GT before -> after |',
        '|---|---:|---:|---:|---:|---:|']
    for seq in ('tree','park','ducks'):
        rel=f'bench/temporal_{seq}/history_edges/lpips_results.json'
        if not (ROOT/rel).exists():raise RuntimeError('incomplete sequence LPIPS scoring: '+rel)
        sources.append(rel);s=read(rel)['summary']['0.5'];d=s['delta'];t=s['temporal'];di=s['diagnostics']
        pct=d['LPIPS']/s['baseline']['LPIPS']*100
        tpct=(t['aligned_residual_delta']/t['baseline_aligned_residual_delta']-1)*100
        lines.append(f"| {seq} | {d['PSNR']:+.5f} | {d['SSIM']:+.6f} | {pct:+.3f}% | {tpct:+.3f}% | {di['baseline_hf']:.4f} -> {di['candidate_hf']:.4f} |")
    lines+=['',
        'HF is mean absolute 4-neighbor luma Laplacian divided by GT. Lower is not',
        'automatically better: park already loses detail relative to GT, and history',
        'reduces it further. Inspect moving edges and ghosting in the game before',
        'accepting any future replacement; this history candidate already fails',
        'the PC gate and is not sent to hardware. No HUD masks exist; HUD PSNR is unavailable and the existing',
        'HUD_flicker label is only the static-region fallback. Static output is',
        'preserved by design, which does not establish subtitle/minimap quality.','',
        'A separate experiment regularizes fine vectors with advected fine history,',
        'with camera change compensation and penalties 1/4. It avoids color history',
        'but the two screened sequences disagree; this is not promoted.','',
        '## Reproduction and verification','',
        'Use the Python and LPIPS paths in QUALITY_ROUND_20261020.md. New scripts:',
        '`experiment_edge_stability.py`, `experiment_grid_confidence.py`,',
        '`experiment_fine_filter.py`, `experiment_symmetric_refine.py`,',
        '`experiment_dense_edges.py`, `experiment_post_fusion.py`,',
        '`experiment_temporal_sequence.py`, `experiment_history_edges.py`,',
        '`experiment_fine_history.py` (all under tools/bench). The sequence script',
        'caches states/results under its --out directory. Run it before the history',
        'scripts. Use a fresh output directory after changing input/motion algorithms;',
        'cached intermediates do not automatically invalidate themselves.','',
        'Example:',
        '```powershell',
        'python tools/bench/experiment_temporal_sequence.py --clip <consecutive-triples> --out bench/temporal_new --count 6',
        'python tools/bench/experiment_history_edges.py --sequence bench/temporal_new --strengths .5 --lpips --result-name lpips_results.json',
        '```','',
        'Default refine behavior remains identical to the isolated GPU IR on all',
        '388800 blocks (check_gridrefine_host.py); seven existing benchmark tests',
        'pass. Actual GPU cost/safety of every new PC method is untested. The last',
        'hardware measurements remain those of gridmix b: about 7.8-8.6 ms for our',
        'passes, stable 30+30 cadence, and about 58.9 ms real handover-to-screen',
        'proxy (not input-to-photon latency). A sanity pass checked 60 saved history',
        'outputs from the first two sequences for geometry, finiteness, range and',
        'exact preservation of the input-defined static region; all passed.',
        'No new release/package/push was made.','',
        '## Remaining work','',
        'The fixed CUSA00557 fence comparison shows geometric deformation, not',
        'merely excess high-frequency energy. DOF has not been isolated. More',
        'blur/sharpen tuning is not supported by this round. The next architectural',
        'hypothesis is bidirectional motion/visibility validation near boundaries,',
        'with explicit protection against ambiguous repeating thin structures.',
        'That hypothesis is not implemented or validated in this report. The owner',
        'has been asked which scene/object best reproduces the current artifact.',
        'A continuous capture of that motion is still needed to connect these',
        'short external-video diagnostics to the actual in-game complaint.','']
    manifest={}
    for p in sorted(set(sources+[str(x.relative_to(ROOT)).replace('\\','/') for x in (ROOT/'tools/bench').glob('experiment_*.py')]+[
        'tools/bench/vcfg_motion.py','tools/bench/vcfg_ref.py','tools/bench/vcfg_warp.py',
        'tools/bench/write_edge_research_report.py','bench/edge_history_sanity.json','bench/state_full1_warm.pkl'])):
        manifest[p]=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
    for p in sorted((ROOT/'bench/clips/CUSA00557_full1').glob('*.png')):
        manifest[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    for seq in ('tree','park','ducks'):
        m=read(f'bench/temporal_{seq}/results.json');manifest.update(m['source_hashes'])
        for p in (ROOT/f'bench/temporal_{seq}').glob('*_state.pkl'):
            manifest[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (ROOT/'bench/edge_research_manifest.json').write_text(json.dumps(manifest,indent=2))
    (ROOT/'docs/EDGE_STABILITY_RESEARCH_20261020.md').write_text('\n'.join(lines))
    print('Wrote report and',len(manifest),'SHA-256 provenance entries')


if __name__=='__main__':main()
