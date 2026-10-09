"""Cap the size of the composite images pgsrip sends to tesseract (memory grows with their area)."""
import pgsrip.engines.tesseract as t
p = t.__file__
s = open(p).read()
for a, b in [("MIN_WIDTH = 10 * 1024\n", "MIN_WIDTH = 1024\nCOMPOSITE_MAX = 4096\n"),
             ("default=MAX_TESS_DIMENSION,", "default=COMPOSITE_MAX,"),
             ("Composite.from_items(items, gap, max_width, MAX_TESS_DIMENSION, self.workers)",
              "Composite.from_items(items, gap, max_width, COMPOSITE_MAX, self.workers)")]:
    assert s.count(a) == 1, "pgsrip changed, patch needs review: " + a
    s = s.replace(a, b)
open(p, "w").write(s)
print("pgsrip patched: composites capped at 4096 px")
