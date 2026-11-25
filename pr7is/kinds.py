"""What kind of file each extension is (no Qt): PR7-DU's table, plus the search filter's categories.

WizTree colours the map by extension. The family allows only its own hues (pink 318 deg -> red -> orange 42 deg,
pearl and onyx greys), so each kind gets a step of that range, spaced so neighbours on the map stay apart."""

from __future__ import annotations

KINDS = {   # kind: (colour, extensions)
    "video": ("#DE0639", "mp4 mkv avi mov webm wmv m4v mpg mpeg ts mts m2ts vob flv 3gp"),
    "program": ("#F07A2A", "exe dll sys msi msix appx cab drv ocx efi mui pyd so dylib"),
    "image": ("#D6457E", "jpg jpeg png gif webp bmp tif tiff heic heif raw cr2 cr3 nef arw dng psd svg ico avif"),
    "archive": ("#B84A12", "zip 7z rar tar gz tgz xz bz2 zst iso img vhd vhdx wim esd"),
    "audio": ("#FFAA5A", "mp3 flac m4a ogg opus wav wma aac aiff ape"),
    "document": ("#E8B4C8", "pdf doc docx odt xls xlsx ods ppt pptx odp rtf epub txt md"),
    "code": ("#9A2E4A", "py js ts c cpp h cs java go rs json xml yml yaml toml html css sql ini cfg log"),
    "data": ("#7A3A28", "db sqlite mdb pak dat bin pst ost vmdk qcow2 edb"),
    "other": ("#6B6569", ""),
}
FOLDER = "#3A3438"          # a folder too small on the map to open up
BY_EXT = {ext: kind for kind, (_, exts) in KINDS.items() for ext in exts.split()}


def ext_of(name: str) -> str:
    """ "Movie.Final.MKV" -> "mkv"; no dot (or a dot file) -> ""."""
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem else ""


def kind_of(name: str) -> str:
    return BY_EXT.get(ext_of(name), "other")


def colour_of(name: str) -> str:
    return KINDS[kind_of(name)][0]


# The filter box's categories, as Everything's: each is a set of extensions ("folder" is handled by the index).
CATEGORIES = {
    "audio": set(KINDS["audio"][1].split()),
    "compressed": set(KINDS["archive"][1].split()) - {"img", "vhd", "vhdx", "wim", "esd"},
    "document": set(KINDS["document"][1].split()) | {"csv", "md", "log"},
    "executable": {"exe", "msi", "bat", "cmd", "com", "ps1", "scr", "msix", "appx"},
    "picture": set(KINDS["image"][1].split()),
    "video": set(KINDS["video"][1].split()),
    "folder": None,
}
