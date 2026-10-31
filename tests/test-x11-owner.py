#!/usr/bin/env python3
"""Focused owner-aware X11 restacking tests; no live display is opened."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dock_x11_owner_test", ROOT / "dock-x11.py")
x11 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(x11)


class OwnerRestackTests(unittest.TestCase):
    def test_only_current_owner_mapped_exact_dock_windows_are_raised(self):
        inventory = [
            {"xid": 0x11, "title": "Eww - dock", "mapped": True, "pid": 101},
            {"xid": 0x12, "title": "Eww - dock_open_area", "mapped": True, "pid": 202},
            {"xid": 0x13, "title": "Eww - dock_open_area", "mapped": True, "pid": 101},
            {"xid": 0x14, "title": "Eww - dock", "mapped": False, "pid": 101},
        ]
        raised = []
        with patch.object(x11, "verified_owner", return_value={"pid": 101}), \
             patch.object(x11, "inventory", return_value=inventory), \
             patch.object(x11, "_raise_xid", side_effect=lambda xid: raised.append(xid) or True):
            self.assertEqual(x11.raise_docks(), 2)
        self.assertEqual(raised, [0x11, 0x13])

    def test_missing_or_invalid_owner_never_raises(self):
        for owner in (None, {}, {"pid": "101"}, {"pid": 0}):
            with self.subTest(owner=owner), \
                 patch.object(x11, "verified_owner", return_value=owner), \
                 patch.object(x11, "inventory", side_effect=AssertionError("unknown owner scanned")), \
                 patch.object(x11, "_raise_xid", side_effect=AssertionError("window raised")):
                self.assertEqual(x11.raise_docks(), 0)

    def test_pid_property_reads_native_unsigned_long_and_rejects_bad_format(self):
        import ctypes as C
        from types import SimpleNamespace
        from unittest.mock import Mock

        value = C.c_ulong(4242)

        def get_property(_display, _xid, _atom, _offset, _length, _delete, _requested,
                         actual_type, actual_format, count, remaining, data):
            actual_type._obj.value = 6
            actual_format._obj.value = 32
            count._obj.value = 1
            remaining._obj.value = 0
            C.cast(data, C.POINTER(C.POINTER(C.c_ubyte)))[0] = C.cast(
                C.pointer(value), C.POINTER(C.c_ubyte))
            return 0

        library = SimpleNamespace(XGetWindowProperty=Mock(side_effect=get_property), XFree=Mock())
        self.assertEqual(x11._property_pid(library, 1, 2, 3), 4242)
        library.XFree.assert_called_once()

        def bad_format(*args):
            args[8]._obj.value = 16
            args[9]._obj.value = 1
            args[11]._obj.value = None
            return 0

        library = SimpleNamespace(XGetWindowProperty=Mock(side_effect=bad_format), XFree=Mock())
        self.assertIsNone(x11._property_pid(library, 1, 2, 3))
        library.XFree.assert_not_called()


if __name__ == "__main__":
    unittest.main()
