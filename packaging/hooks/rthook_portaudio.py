"""Runtime hook, Linux only: point sounddevice at the staged libportaudio.

sounddevice looks for PortAudio by `ctypes.util.find_library("portaudio")`,
which consults the linker cache and never the bundle. The build stages
`libportaudio.so.2` beside the executable (spec §8.5), so this answers that
one lookup with the bundled path and leaves every other lookup — alsa-lib,
the Vulkan loader — to the host, where they belong.
"""

import ctypes.util
import glob
import os
import sys

_bundle = getattr(sys, "_MEIPASS", None)
if _bundle:
    _staged = sorted(glob.glob(os.path.join(_bundle, "libportaudio.so.2*")))
    if _staged:
        _find_library = ctypes.util.find_library

        def find_library(name):
            if name == "portaudio":
                return _staged[0]
            return _find_library(name)

        ctypes.util.find_library = find_library
