Add-Type -AssemblyName System.Drawing
$base = Split-Path -Parent $MyInvocation.MyCommand.Path   # folder containing art (film backdrops named movie_*.jpg)
$art  = Join-Path $base "art"
$out  = Join-Path $base "tiles2"
New-Item -ItemType Directory -Force $out | Out-Null
$W = 960; $H = 540

function HexColor($h, $a = 255) { $c = [System.Drawing.ColorTranslator]::FromHtml($h); [System.Drawing.Color]::FromArgb($a, $c.R, $c.G, $c.B) }
function New-Canvas { $b = New-Object System.Drawing.Bitmap $W, $H; $g = [System.Drawing.Graphics]::FromImage($b); $g.SmoothingMode = 'AntiAlias'; $g.InterpolationMode = 'HighQualityBicubic'; $g.TextRenderingHint = 'AntiAliasGridFit'; return @($b, $g) }
function Save-Tile($bmp, $name) {
  $enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg'
  $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
  $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, 93L)
  $bmp.Save((Join-Path $out $name), $enc, $ep)
}
function Draw-Label($g, $text, $x, $y, $w, $h, $size, $align = 'Center') {
  $x = [double]$x; $y = [double]$y; $w = [double]$w; $h = [double]$h
  $font = New-Object System.Drawing.Font('Segoe UI Black', $size, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
  $sf = New-Object System.Drawing.StringFormat; $sf.Alignment = $align; $sf.LineAlignment = 'Center'; $sf.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
  $sh = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(150, 0, 0, 0))
  $g.DrawString($text, $font, $sh, (New-Object System.Drawing.RectangleF ($x + 5), ($y + 6), $w, $h), $sf)
  $g.DrawString($text, $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF $x, $y, $w, $h), $sf)
}
function Draw-Cover($g, $img, $dest) {      # draw image to cover the destination rectangle (crop to fill)
  $sr = $img.Width / $img.Height; $dr = $dest.Width / $dest.Height
  if ($sr -gt $dr) { $sh = $img.Height; $sw = [int]($sh * $dr); $sx = [int](($img.Width - $sw) / 2); $sy = 0 }
  else { $sw = $img.Width; $sh = [int]($sw / $dr); $sx = 0; $sy = [int](($img.Height - $sh) / 2) }
  $g.DrawImage($img, $dest, (New-Object System.Drawing.Rectangle $sx, $sy, $sw, $sh), [System.Drawing.GraphicsUnit]::Pixel)
}
function Collage($files, $tintA, $tintB, $label, $outName, $tintAlpha) {
  $r = New-Canvas; $bmp = $r[0]; $g = $r[1]
  $g.Clear((HexColor '#0a0e27'))
  $imgs = $files | ForEach-Object { [System.Drawing.Image]::FromFile($_) }
  $n = $imgs.Count; $slant = 70; $sw = ($W + $slant * 2) / $n
  for ($i = 0; $i -lt $n; $i++) {
    $x0 = $i * $sw - $slant
    $pts = @((New-Object System.Drawing.PointF ($x0 + $slant), 0), (New-Object System.Drawing.PointF ($x0 + $sw + $slant), 0), (New-Object System.Drawing.PointF ($x0 + $sw), $H), (New-Object System.Drawing.PointF $x0, $H))
    $gp = New-Object System.Drawing.Drawing2D.GraphicsPath; $gp.AddPolygon($pts)
    $state = $g.Save(); $g.SetClip($gp)
    Draw-Cover $g $imgs[$i] (New-Object System.Drawing.Rectangle ([int]($x0 - 10)), 0, ([int]($sw + $slant + 20)), $H)
    $g.Restore($state)
    $pen = New-Object System.Drawing.Pen ((HexColor '#0a0e27' 255), 6); $g.DrawPolygon($pen, $pts)
  }
  $rect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
  $tint = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (HexColor $tintA $tintAlpha), (HexColor $tintB $tintAlpha), 25
  $g.FillRectangle($tint, $rect)
  $dark = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::FromArgb(0, 5, 8, 25)), ([System.Drawing.Color]::FromArgb(200, 5, 8, 25)), 90
  $g.FillRectangle($dark, $rect)
  Draw-Label $g $label 0 0 $W $H 118
  Save-Tile $bmp $outName; $g.Dispose(); $bmp.Dispose(); $imgs | ForEach-Object { $_.Dispose() }
}
function Vinyl-Tile {
  $r = New-Canvas; $bmp = $r[0]; $g = $r[1]; $rect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
  $bg = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (HexColor '#7c3aed'), (HexColor '#ec4899'), 35; $g.FillRectangle($bg, $rect)
  $glow = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(50, 255, 255, 255)); $g.FillEllipse($glow, 420, -200, 700, 700)
  $cx = 700; $cy = 280; $R = 235
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(90, 0, 0, 0))), ($cx - $R + 14), ($cy - $R + 18), (2 * $R), (2 * $R))
  $g.FillEllipse((New-Object System.Drawing.SolidBrush (HexColor '#0b0b14')), ($cx - $R), ($cy - $R), (2 * $R), (2 * $R))
  for ($k = 40; $k -lt $R; $k += 11) { $g.DrawEllipse((New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(38, 255, 255, 255)), 1.2), ($cx - $k), ($cy - $k), (2 * $k), (2 * $k)) }
  $sheen = New-Object System.Drawing.Drawing2D.LinearGradientBrush ((New-Object System.Drawing.Rectangle ($cx - $R), ($cy - $R), (2 * $R), (2 * $R))), ([System.Drawing.Color]::FromArgb(0, 255, 255, 255)), ([System.Drawing.Color]::FromArgb(70, 255, 255, 255)), 45
  $g.FillPie($sheen, ($cx - $R), ($cy - $R), (2 * $R), (2 * $R), 200, 70)
  $lbl = New-Object System.Drawing.Drawing2D.LinearGradientBrush ((New-Object System.Drawing.Rectangle ($cx - 80), ($cy - 80), 160, 160)), (HexColor '#fbbf24'), (HexColor '#f97316'), 45
  $g.FillEllipse($lbl, ($cx - 80), ($cy - 80), 160, 160)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush (HexColor '#0b0b14')), ($cx - 12), ($cy - 12), 24, 24)
  $arm = New-Object System.Drawing.Pen ((HexColor '#e5e7eb'), 12); $arm.StartCap = 'Round'; $arm.EndCap = 'Round'
  $g.DrawLine($arm, 900, 40, 800, 210); $g.FillEllipse((New-Object System.Drawing.SolidBrush (HexColor '#e5e7eb')), 878, 20, 44, 44)
  $g.DrawLine((New-Object System.Drawing.Pen ((HexColor '#9ca3af'), 16)), 800, 210, 776, 252)
  $bar = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(110, 255, 255, 255)); $hs = @(40, 90, 60, 120, 75, 105, 50)
  for ($i = 0; $i -lt 7; $i++) { $g.FillRectangle($bar, (60 + $i * 26), (470 - $hs[$i]), 14, $hs[$i]) }
  Draw-Label $g 'MUSIC' 20 0 450 540 104 'Center'
  Save-Tile $bmp 'Music.jpg'; $g.Dispose(); $bmp.Dispose()
}
function Headphones-Tile {
  $r = New-Canvas; $bmp = $r[0]; $g = $r[1]; $rect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
  $bg = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (HexColor '#059669'), (HexColor '#0ea5e9'), 35; $g.FillRectangle($bg, $rect)
  $glow = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(45, 255, 255, 255)); $g.FillEllipse($glow, 430, -180, 720, 720)
  $cx = 720
  for ($i = 0; $i -lt 3; $i++) { $g.DrawArc((New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb((60 - $i * 16), 255, 255, 255)), 4), ($cx - 250 - $i * 34), (70 - $i * 34), (500 + $i * 68), (420 + $i * 68), 195, 150) }
  $band = New-Object System.Drawing.Pen ((HexColor '#f8fafc'), 40); $band.StartCap = 'Round'; $band.EndCap = 'Round'
  $g.DrawArc($band, ($cx - 190), 110, 380, 360, 185, 170)
  foreach ($sx in @(($cx - 232), ($cx + 112))) {
    $gp = New-Object System.Drawing.Drawing2D.GraphicsPath; $rr = 36
    $gp.AddArc($sx, 255, $rr, $rr, 180, 90); $gp.AddArc(($sx + 120 - $rr), 255, $rr, $rr, 270, 90); $gp.AddArc(($sx + 120 - $rr), (255 + 190 - $rr), $rr, $rr, 0, 90); $gp.AddArc($sx, (255 + 190 - $rr), $rr, $rr, 90, 90); $gp.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush (HexColor '#0f172a')), $gp)
    $g.FillEllipse((New-Object System.Drawing.SolidBrush (HexColor '#22d3ee')), ($sx + 36), 318, 48, 48)
    $g.FillEllipse((New-Object System.Drawing.SolidBrush (HexColor '#0f172a')), ($sx + 48), 330, 24, 24)
  }
  $bar = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(120, 255, 255, 255)); $hs = @(30, 70, 110, 80, 130, 60, 95, 45, 75)
  for ($i = 0; $i -lt 9; $i++) { $g.FillRectangle($bar, (40 + $i * 24), (470 - $hs[$i]), 13, $hs[$i]) }
  Draw-Label $g 'AUDIOBOOKS' 0 0 500 540 57 'Center'
  Save-Tile $bmp 'Audiobooks.jpg'; $g.Dispose(); $bmp.Dispose()
}

$f = Get-ChildItem $art -Filter 'movie_*.jpg' | Sort-Object Name | ForEach-Object FullName
Collage $f '#ff3d5a' '#ff9a3d' 'MOVIES' 'Movies.jpg' 70
$f2 = @($f[3], $f[1], $f[0], $f[2])
Collage $f2 '#1d4ed8' '#22d3ee' 'TV SHOWS' 'TV_Shows.jpg' 105
Vinyl-Tile
Headphones-Tile
Get-ChildItem $out | ForEach-Object { "{0} {1} KB" -f $_.Name, [math]::Round($_.Length / 1KB) }
