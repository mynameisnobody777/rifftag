"""The one list of file types RiffTag claims, in every form a desktop wants.

Three different places need to agree about what RiffTag opens: the Linux
``.desktop`` entries (MIME types), the Windows registry script (extensions),
and the app itself (``tagio.EXT_FAMILY``). They had drifted, and the symptom
was the bug this module exists to stop coming back: **RiffTag did not appear
in "Open with" for a .wav file**, because the entry claimed ``audio/x-wav``
and shared-mime-info on Mint calls that file ``audio/vnd.wave``.

So the types live here, are generated into the desktop entries from here, and
a test asserts the shipped files still match.

Two things learned the hard way:

* **Aliases are not enough.** shared-mime-info records ``audio/x-wav`` as an
  alias of ``audio/vnd.wave``, and the desktop's "Open with" list did *not*
  resolve it. Every spelling a distribution might use has to be listed.
* **Both spellings, always.** Which one is canonical differs between
  distributions and between releases of shared-mime-info, so claiming both
  costs nothing and is the only thing that is portable. Find out what the
  machine in front of you actually thinks with::

      printf xxxxxxxxxxxxxxxx > /tmp/probe.wav
      xdg-mime query filetype /tmp/probe.wav
"""

from __future__ import annotations

from .tagio import AUDIO_EXTENSIONS

#: extension -> every MIME type a desktop might hand us for it.
#:
#: Checked against ``xdg-mime query filetype`` on Mint 22 (shared-mime-info
#: 2.4); the first entry of each is what that version reports.
MIME_BY_EXTENSION: dict[str, tuple[str, ...]] = {
    ".mp3":  ("audio/mpeg", "audio/mp3", "audio/x-mp3", "audio/mpeg3"),
    ".flac": ("audio/flac", "audio/x-flac"),
    ".ogg":  ("audio/ogg", "audio/x-vorbis+ogg", "audio/vorbis"),
    ".oga":  ("audio/ogg", "audio/x-vorbis+ogg"),
    ".opus": ("audio/ogg", "audio/opus", "audio/x-opus+ogg"),
    ".spx":  ("audio/ogg", "audio/x-speex+ogg", "audio/speex"),
    ".m4a":  ("audio/mp4", "audio/x-m4a", "audio/m4a"),
    ".m4b":  ("audio/x-m4b",),
    ".m4r":  ("audio/mp4",),
    ".m4p":  ("audio/mp4",),
    ".wma":  ("audio/x-ms-wma",),
    # No video/x-ms-asf, for the same reason the video containers below are
    # left out: .asf is usually video, and this is a tag editor.
    ".asf":  ("application/vnd.ms-asf", "audio/x-ms-asf"),
    ".wav":  ("audio/vnd.wave", "audio/x-wav", "audio/wav", "audio/wave"),
    ".aif":  ("audio/x-aiff", "audio/aiff"),
    ".aiff": ("audio/x-aiff", "audio/aiff"),
    ".aifc": ("audio/x-aifc", "audio/x-aiff"),
    ".dsf":  ("audio/x-dsf", "audio/x-dsd"),
    ".dsd":  ("audio/x-dsd",),
    ".ape":  ("audio/x-ape", "audio/x-monkeys-audio"),
    ".wv":   ("audio/x-wavpack",),
    ".mpc":  ("audio/x-musepack", "audio/x-mpc"),
    ".mp+":  ("audio/x-musepack",),
    ".tta":  ("audio/x-tta",),
    ".ofr":  ("audio/x-optimfrog",),
    ".ofs":  ("audio/x-optimfrog",),
    # Video containers RiffTag can tag but deliberately does NOT claim: see
    # VIDEO_EXTENSIONS below.
}

#: Tagged happily, but never claimed in a desktop entry. Putting a tag editor
#: in the "Open with" menu of every .mp4 and .wmv on the machine is noise for
#: a case nobody asked for; passing one on the command line still works.
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mpg", ".wmv")


def extensions() -> tuple[str, ...]:
    """Every extension to claim, sorted. The Windows registry list."""
    return tuple(sorted(e for e in AUDIO_EXTENSIONS
                        if e in MIME_BY_EXTENSION
                        and e not in VIDEO_EXTENSIONS))


def mime_types() -> tuple[str, ...]:
    """Every MIME type to claim, de-duplicated, first-seen order kept.

    Order is stable rather than sorted so a regenerated desktop entry does
    not churn in diffs, and so the type a desktop is most likely to use comes
    first.
    """
    seen: dict[str, None] = {}
    for ext in extensions():
        for mime in MIME_BY_EXTENSION[ext]:
            seen.setdefault(mime, None)
    return tuple(seen)


def mime_line() -> str:
    """The ``MimeType=`` line, trailing semicolon included as the spec wants."""
    return "MimeType=" + ";".join(mime_types()) + ";"


def desktop_entry_text(exec_line: str, icon: str = "rifftag") -> str:
    """A complete .desktop body.

    ``exec_line`` is everything after ``Exec=``; it differs between an
    installed copy (``rifftag %F``) and a source checkout, so the caller
    decides. ``%F`` is what carries the clicked file through to
    ``rifftag/cli.py`` — an entry without it opens the app with no argument
    and looks like the feature is broken.

    ``icon`` is a parameter for the Flatpak's sake: Flathub requires the icon
    name to be the application id, so that build passes
    ``com.nobody777.RiffTag`` while everything else uses ``rifftag``.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RiffTag\n"
        "GenericName=Music Tag Editor\n"
        "Comment=Edit. Rename. Organize.\n"
        f"Exec={exec_line}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Music;\n"
        f"{mime_line()}\n"
        "Keywords=tag;id3;mp3;flac;rename;music;metadata;\n"
    )
