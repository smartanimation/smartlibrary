$out = 'P:\dev\smartlibrary\.tmp\ui_mockups\usd_variant_registration_ui.png'
Add-Type -AssemblyName System.Drawing
$w=640; $h=960
$bmp = New-Object System.Drawing.Bitmap($w,$h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
function C($hex){ return [System.Drawing.ColorTranslator]::FromHtml($hex) }
$bg=C '#2f2f2f'; $panel=C '#353535'; $panel2=C '#303030'; $border=C '#4a4a4a'; $line=C '#424242'; $field=C '#202020'; $text=C '#dddddd'; $muted=C '#9a9a9a'; $blue2=C '#2873aa'; $select=C '#4f91b7'; $green=C '#55b886'
$font=[System.Drawing.Font]::new('Segoe UI',8.5)
$fontSmall=[System.Drawing.Font]::new('Segoe UI',7.5)
$fontTitle=[System.Drawing.Font]::new('Segoe UI',10,[System.Drawing.FontStyle]::Bold)
$fontBold=[System.Drawing.Font]::new('Segoe UI',8.5,[System.Drawing.FontStyle]::Bold)
$fontTab=[System.Drawing.Font]::new('Segoe UI',8,[System.Drawing.FontStyle]::Bold)
function FillRect($x,$y,$ww,$hh,$color){ $b=[System.Drawing.SolidBrush]::new($color); $g.FillRectangle($b,$x,$y,$ww,$hh); $b.Dispose() }
function StrokeRect($x,$y,$ww,$hh,$color){ $p=[System.Drawing.Pen]::new($color); $g.DrawRectangle($p,$x,$y,$ww,$hh); $p.Dispose() }
function Txt($s,$x,$y,$f=$font,$color=$text){ $b=[System.Drawing.SolidBrush]::new($color); $g.DrawString($s,$f,$b,[single]$x,[single]$y); $b.Dispose() }
function Field($x,$y,$ww,$hh,$s,$focus=$false){ FillRect $x $y $ww $hh $field; if($focus){ StrokeRect $x $y $ww $hh (C '#3c8dbc') } else { StrokeRect $x $y $ww $hh $border }; Txt $s ($x+7) ($y+4) $font $text }
function Label($s,$x,$y){ Txt $s $x $y $fontSmall $muted }
function Button($x,$y,$ww,$hh,$s,$primary=$false){ if($primary){ FillRect $x $y $ww $hh $blue2; StrokeRect $x $y $ww $hh (C '#3a8bc5') } else { FillRect $x $y $ww $hh (C '#3e3e3e'); StrokeRect $x $y $ww $hh $border }; $sz=$g.MeasureString($s,$fontBold); if($primary){ $c=[System.Drawing.Color]::White } else { $c=$text }; Txt $s ($x+($ww-$sz.Width)/2) ($y+($hh-$sz.Height)/2-1) $fontBold $c }
$g.Clear($bg)
FillRect 8 8 624 944 (C '#252525'); StrokeRect 8 8 624 944 (C '#151515')
FillRect 9 9 622 28 (C '#eeeeee'); Txt 'Asset Assembly - STKB' 34 15 ([System.Drawing.Font]::new('Segoe UI',8.5)) (C '#111111')
FillRect 13 14 15 15 (C '#7bb7d8'); Txt 'M' 16 13 ([System.Drawing.Font]::new('Segoe UI',8,[System.Drawing.FontStyle]::Bold)) ([System.Drawing.Color]::White)
Txt '-' 565 12 ([System.Drawing.Font]::new('Segoe UI',12)) (C '#111111'); Txt '□' 594 14 ([System.Drawing.Font]::new('Segoe UI',9)) (C '#111111'); Txt '×' 615 13 ([System.Drawing.Font]::new('Segoe UI',10)) (C '#111111')
FillRect 14 39 612 27 (C '#303030')
FillRect 16 42 94 23 (C '#3d3d3d'); StrokeRect 16 42 94 23 (C '#4d4d4d'); Txt 'Extract / Publish' 25 47 $fontTab $text
FillRect 110 42 76 23 (C '#2f2f2f'); Txt 'Place Asset' 122 47 $fontTab $muted
FillRect 186 42 86 23 (C '#2f2f2f'); Txt 'USD Variants' 198 47 $fontTab $muted
FillRect 14 66 612 874 $panel; StrokeRect 14 66 612 874 $border
Txt 'Extract Candidates' 25 77 $fontTitle $text
Txt 'Extract selected Maya objects, publish model payloads, then register USD variants.' 25 99 $fontSmall $muted
FillRect 26 121 118 68 (C '#565656'); StrokeRect 26 121 118 68 (C '#454545')
$chair=[System.Drawing.Pen]::new((C '#e8c1c5'),4); $g.DrawLine($chair,82,141,108,145); $g.DrawLine($chair,105,145,102,172); $g.DrawLine($chair,82,141,78,172); $g.DrawLine($chair,73,173,112,173); $chair.Dispose()
Txt '2 selected object(s). Candidate is not published yet.' 156 146 $fontSmall $muted
$p=[System.Drawing.Pen]::new($line); $g.DrawLine($p,25,205,615,205); $p.Dispose()
Txt 'Naming Defaults' 25 218 $fontBold $text
Label 'Category' 28 247; Field 82 242 205 24 'prop'
Label 'Group' 320 247; Field 366 242 235 24 'bp'
Label 'Variant' 28 279; Field 82 274 205 24 'default'
Label 'Department' 320 279; Field 366 274 235 24 'model'
Label 'Subset' 28 311; Field 82 306 150 24 'proxy  ▾'; Txt '☑ Auto-split by top transform' 320 309 $fontSmall (C '#cfdce6')
$p=[System.Drawing.Pen]::new($line); $g.DrawLine($p,25,343,615,343); $p.Dispose()
Txt 'Candidate Details' 25 356 $fontBold $text
Label 'Target' 28 385; Field 82 380 520 24 'Chair'
Label 'Asset' 28 417; Field 82 412 520 24 'Chair' $true
Label 'Output' 28 449; Txt 'D:/Projects/STKB/assets/prop/bp/Chair/default/publish/usd/latest/Chair.usd' 82 449 $fontSmall $muted
$tableX=25; $tableY=475; $tableW=590; $tableH=174
FillRect $tableX $tableY $tableW $tableH $panel2; StrokeRect $tableX $tableY $tableW $tableH (C '#39779d')
$hdrH=25; FillRect ($tableX+1) ($tableY+1) ($tableW-2) $hdrH (C '#3a3a3a')
$cols=@(40,148,90,76,70,86,80); $headers=@('#','Target','Asset','Category','Variant','USD Mode','Locator')
$cx=$tableX
for($i=0;$i -lt $cols.Length;$i++){ Txt $headers[$i] ($cx+6) ($tableY+7) $fontSmall $text; $cx += $cols[$i]; $pen=[System.Drawing.Pen]::new($border); $g.DrawLine($pen,$cx,$tableY,$cx,$tableY+$tableH); $pen.Dispose() }
$rows=@(@('1','Chair','Chair','prop','default','reference','Chair_place_LOC'),@('2','KitchenTable','KitchenTable','prop','default','reference','KitchenTable_LOC'))
for($r=0;$r -lt $rows.Length;$r++){ $ry=$tableY+$hdrH+($r*27); if($r -eq 0){ FillRect ($tableX+1) $ry ($tableW-2) 27 $select } else { FillRect ($tableX+1) $ry ($tableW-2) 27 (C '#2b2b2b') }; $cx=$tableX; for($i=0;$i -lt $cols.Length;$i++){ if($r -eq 0){ $rc=[System.Drawing.Color]::White } else { $rc=$text }; Txt $rows[$r][$i] ($cx+6) ($ry+6) $fontSmall $rc; $cx += $cols[$i] } }
$vy=668; $vh=170
FillRect 25 $vy 590 $vh (C '#323232'); StrokeRect 25 $vy 590 $vh (C '#4d4d4d')
Txt 'USD Variant Registration' 36 ($vy+12) $fontTitle $text
Txt 'Register the selected component publish as a variant in assembly_assets.usd.' 36 ($vy+34) $fontSmall $muted
Label 'Variant Set' 38 ($vy+65); Field 112 ($vy+60) 150 24 'modelVariant  ▾'
Label 'Variant Name' 285 ($vy+65); Field 365 ($vy+60) 120 24 'default'
Txt '☑ Default' 502 ($vy+64) $fontSmall (C '#cfdce6')
Label 'Variant USD' 38 ($vy+99); Field 112 ($vy+94) 373 24 'Chair.usd'; Button 496 ($vy+94) 104 24 'Browse'
$vtX=36; $vtY=$vy+128; $vtW=568; $vtH=31
FillRect $vtX $vtY $vtW $vtH (C '#272727'); StrokeRect $vtX $vtY $vtW $vtH (C '#3c789f')
Txt 'modelVariant' ($vtX+8) ($vtY+8) $fontSmall $text; Txt 'default' ($vtX+120) ($vtY+8) $fontSmall $text; Txt 'Chair' ($vtX+210) ($vtY+8) $fontSmall $text; Txt 'Chair.usd' ($vtX+295) ($vtY+8) $fontSmall $muted; Txt 'default' ($vtX+430) ($vtY+8) $fontSmall $green; Txt 'registered' ($vtX+495) ($vtY+8) $fontSmall $green
Button 25 858 136 34 'Register Variant' $true
Button 169 858 104 34 'Set Default'
Button 281 858 94 34 'Remove'
Button 383 858 126 34 'Save Variants' $true
Button 517 858 98 34 'Extract' $true
Txt 'Selected asset: Chair    Variant Set: modelVariant/default    Preview layer: assembly_assets.usd' 18 918 $fontSmall $muted
$bmp.Save($out,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output $out
