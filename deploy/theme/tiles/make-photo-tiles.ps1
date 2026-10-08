Add-Type -AssemblyName System.Drawing
$base = Split-Path -Parent $MyInvocation.MyCommand.Path   # folder that contains photos (see CREDITS in docs/12-theme.md)
$ph  = Join-Path $base "photos"
$out = Join-Path $base "tiles3"
New-Item -ItemType Directory -Force $out | Out-Null
$W = 960; $H = 540

function HexColor($h, $a = 255) { $c = [System.Drawing.ColorTranslator]::FromHtml($h); [System.Drawing.Color]::FromArgb($a, $c.R, $c.G, $c.B) }
function New-Canvas { $b = New-Object System.Drawing.Bitmap $W, $H; $g = [System.Drawing.Graphics]::FromImage($b); $g.SmoothingMode = 'AntiAlias'; $g.InterpolationMode = 'HighQualityBicubic'; $g.TextRenderingHint = 'AntiAliasGridFit'; return @($b, $g) }
function Save-Tile($bmp, $name) {
  $enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg'
  $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
  $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, 92L)
  $bmp.Save((Join-Path $out $name), $enc, $ep)
}
function Draw-Label($g, $text, $size) {
  $font = New-Object System.Drawing.Font('Segoe UI Black', $size, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
  $sf = New-Object System.Drawing.StringFormat; $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'; $sf.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
  $sh = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(160, 0, 0, 0))
  $g.DrawString($text, $font, $sh, (New-Object System.Drawing.RectangleF 5, 6, $W, $H), $sf)
  $g.DrawString($text, $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, 0, $W, $H), $sf)
}
function Draw-Cover($g, $img, $dest, $fy) {       # cover-fit; $fy = vertical focus 0 (top) .. 1 (bottom)
  $sr = $img.Width / $img.Height; $dr = $dest.Width / $dest.Height
  if ($sr -gt $dr) { $sh = $img.Height; $sw = [int]($sh * $dr); $sx = [int](($img.Width - $sw) / 2); $sy = 0 }
  else { $sw = $img.Width; $sh = [int]($sw / $dr); $sx = 0; $sy = [int](($img.Height - $sh) * $fy) }
  $g.DrawImage($img, $dest, (New-Object System.Drawing.Rectangle $sx, $sy, $sw, $sh), [System.Drawing.GraphicsUnit]::Pixel)
}
function Photo-Tile($files, $focus, $tintA, $tintB, $tintAlpha, $label, $size, $outName) {
  $r = New-Canvas; $bmp = $r[0]; $g = $r[1]
  $g.Clear((HexColor '#0a0e27'))
  $imgs = $files | ForEach-Object { [System.Drawing.Image]::FromFile((Join-Path $ph $_)) }
  $n = $imgs.Count
  if ($n -eq 1) { Draw-Cover $g $imgs[0] (New-Object System.Drawing.Rectangle 0, 0, $W, $H) $focus[0] }
  else {
    $slant = 70; $sw = ($W + $slant * 2) / $n
    for ($i = 0; $i -lt $n; $i++) {
      $x0 = $i * $sw - $slant
      $pts = @((New-Object System.Drawing.PointF ($x0 + $slant), 0), (New-Object System.Drawing.PointF ($x0 + $sw + $slant), 0), (New-Object System.Drawing.PointF ($x0 + $sw), $H), (New-Object System.Drawing.PointF $x0, $H))
      $gp = New-Object System.Drawing.Drawing2D.GraphicsPath; $gp.AddPolygon($pts)
      $st = $g.Save(); $g.SetClip($gp)
      Draw-Cover $g $imgs[$i] (New-Object System.Drawing.Rectangle ([int]($x0 - 10)), 0, ([int]($sw + $slant + 20)), $H) $focus[$i]
      $g.Restore($st)
      $g.DrawPolygon((New-Object System.Drawing.Pen ((HexColor '#0a0e27'), 6)), $pts)
    }
  }
  $rect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
  $g.FillRectangle((New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, (HexColor $tintA $tintAlpha), (HexColor $tintB $tintAlpha), 25), $rect)
  $dark = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::FromArgb(40, 5, 8, 25)), ([System.Drawing.Color]::FromArgb(205, 5, 8, 25)), 90
  $g.FillRectangle($dark, $rect)
  Draw-Label $g $label $size
  Save-Tile $bmp $outName; $g.Dispose(); $bmp.Dispose(); $imgs | ForEach-Object { $_.Dispose() }
}

Photo-Tile @('movies_a_cinema.jpg', 'movies_b_sailors.jpg') @(0.5, 0.45) '#ff3d5a' '#ff9a3d' 55 'MOVIES' 118 'Movies.jpg'
Photo-Tile @('tv_a_blanket.jpg', 'tv_b_remote.jpg') @(0.35, 0.45) '#1d4ed8' '#22d3ee' 70 'TV SHOWS' 112 'TV_Shows.jpg'
Photo-Tile @('music_a_dj.jpg', 'music_b_dj.jpg') @(0.4, 0.4) '#7c3aed' '#ec4899' 70 'MUSIC' 118 'Music.jpg'
Photo-Tile @('books_a_headphones.jpg') @(0.35) '#059669' '#0ea5e9' 62 'AUDIOBOOKS' 100 'Audiobooks.jpg'
Get-ChildItem $out | ForEach-Object { "{0} {1} KB" -f $_.Name, [math]::Round($_.Length / 1KB) }
