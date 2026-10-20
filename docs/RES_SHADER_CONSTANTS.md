# 1080p constants in classic shaders (for 720p / 900p support)

Pattern: 1920, 1080, 120 (blocks per row), 68 (block rows), 240 (L2 stride), 135 (L2 rows), 480, 270, 960, 540, 160, 90, 30 (dispatch groups x).
Files only read, nothing changed. Line numbers are in classic/shaders/*.ll at origin/main a0a7d47.

## coarse1.ll (3 lines)
- L33: `%crow = mul i32 %cy, 160`
- L36: `%prow = mul i32 %py, 160`
- L65: `%ok = icmp ult i32 %b, 240`

## coarse2.ll (1 lines)
- L27: `%ok = icmp ult i32 %b, 240`

## dump8.ll (2 lines)
- L52: `%row = mul i32 %cy, 960`
- L62: `%gxl = mul i32 %gy, 120`

## fgs8.ll (12 lines)
- L310: `%okx = icmp ult i32 %gx, 120`
- L311: `%oky = icmp ult i32 %gy, 68`
- L330: `%row = mul i32 %gy, 120`
- L402: `%cs0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmy)`
- L403: `%cs1 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxm, i32 %gmy)`
- L404: `%cs2 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxp, i32 %gmy)`
- L405: `%cs3 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmym)`
- L406: `%cs4 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmyp)`
- L439: `%ax0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 1, i32 0)`
- L440: `%ay0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 0, i32 1)`
- L444: `%px0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 1, i32 0)`
- L445: `%py0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 0, i32 1)`

## gm8.ll (1 lines)
- L46: `%g2 = icmp ule i32 %sb, 160`

## gv8.ll (3 lines)
- L358: `%r = mul i32 %yi, 480`
- L480: `%grow = mul i32 %by, 120`
- L584: `%sad = call i32 @bsad(<4 x i32> %vimg, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %cx, i32 %cy)`

## gvc8.ll (4 lines)
- L135: `%inx = icmp ult i32 %cx, 480`
- L136: `%iny = icmp ult i32 %cy, 270`
- L296: `%row = mul i32 %cy, 480`
- L321: `%row2 = mul i32 %cy, 480`

## gvz8.ll (4 lines)
- L84: `%gapx = icmp uge i32 %px, 240`
- L85: `%gapy = icmp uge i32 %py, 135`
- L92: `%crow = mul i32 %cy, 480`
- L96: `%frow = mul i32 %fby, 120`

## luma4.ll (2 lines)
- L20: `%ok = icmp ult i32 %y4, 90`
- L46: `%orow = mul i32 %y4, 160`

## me1.ll (6 lines)
- L463: `%trow = mul i32 %tby, 120`
- L474: `%grow = mul i32 %gy, 120`
- L508: `%bs1 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)`
- L620: `%bs2 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d2x, i32 %d2y)`
- L667: `%bs3 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d3x, i32 %d3y)`
- L752: `%frow = mul i32 %gy, 120`

## me2.ll (3 lines)
- L419: `%trow = mul i32 %tby, 120`
- L436: `%sad1 = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 480, i32 270, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)`
- L519: `%s = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 480, i32 270, i32 %X, i32 %Y, i32 %dx, i32 %dy)`

## me3.ll (5 lines)
- L360: `%trow = mul i32 %tby, 120`
- L374: `%sa = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 240, i32 135, i32 %X, i32 %Y, i32 %ax, i32 %ay)`
- L422: `%syok = icmp ult i32 %sy, 135`
- L423: `%srow = mul i32 %sy, 240`
- L467: `%s = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 240, i32 135, i32 %X, i32 %Y, i32 %dx, i32 %dy)`

## mf8.ll (3 lines)
- L343: `%r = mul i32 %y, 120`
- L390: `%grow = mul i32 %by, 120`
- L430: `%sad = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %Xu, i32 %Yu, i32 %cx, i32 %cy)`

## pyr8.ll (6 lines)
- L82: `%iny1 = icmp ult i32 %y1, 540`
- L95: `%row1 = mul i32 %y1, 960`
- L137: `%iny2 = icmp ult i32 %y2l, 270`
- L142: `%row2 = mul i32 %y2l, 480`
- L183: `%iny3 = icmp ult i32 %y3, 135`
- L188: `%row3 = mul i32 %y3, 240`

## warp8.ll (11 lines)
- L45: `%pl1 = fcmp ogt float %up, 1920.0`
- L47: `%pl3 = fcmp ogt float %vp, 1080.0`
- L60: `%cl1 = fcmp ogt float %uc, 1920.0`
- L62: `%cl3 = fcmp ogt float %vc, 1080.0`
- L110: `%pl1 = fcmp ogt float %up, 1920.0`
- L112: `%pl3 = fcmp ogt float %vp, 1080.0`
- L125: `%cl1 = fcmp ogt float %uc, 1920.0`
- L127: `%cl3 = fcmp ogt float %vc, 1080.0`
- L296: `%r = mul i32 %iy, 120`
- L504: `%gbrs = mul i32 %gbys, 120`
- L564: `%gbr = mul i32 %gby, 120`

## warp8g.ll (12 lines)
- L38: `%pl1 = fcmp ogt float %up, 1920.0`
- L40: `%pl3 = fcmp ogt float %vp, 1080.0`
- L53: `%cl1 = fcmp ogt float %uc, 1920.0`
- L55: `%cl3 = fcmp ogt float %vc, 1080.0`
- L87: `%pl1 = fcmp ogt float %up, 1920.0`
- L89: `%pl3 = fcmp ogt float %vp, 1080.0`
- L102: `%cl1 = fcmp ogt float %uc, 1920.0`
- L104: `%cl3 = fcmp ogt float %vc, 1080.0`
- L275: `%r = mul i32 %iy, 120`
- L476: `%gcr = mul i32 %gcyi, 480`
- L533: `%agbr = mul i32 %agby, 120`
- L620: `%gbr = mul i32 %gby, 120`

