"""Shared constants. Kept separate so listing a folder does not require OpenCV."""

# What counts as an image worth reading. SVG is here because a shop's own
# exports are often vector, and it is rasterised on the way in rather than
# refused — the analyser looks at pictures, not at markup.
RASTER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
VECTOR_EXTS = {".svg"}
IMAGE_EXTS = RASTER_EXTS | VECTOR_EXTS

# Archives a folder of designs usually arrives in.
ARCHIVE_EXTS = {".zip"}

# The same list, ordered the way anyone would name them rather than
# alphabetically — the panel shows this to say what it takes, and ".bmp" is a
# strange thing to lead with.
IMAGE_EXTS_ORDERED = [".jpg", ".jpeg", ".png", ".webp", ".svg",
                      ".tif", ".tiff", ".bmp"]
assert set(IMAGE_EXTS_ORDERED) == IMAGE_EXTS
