import unittest
from midimap_model import MapModel, NrpnAssembler, scale_to_field


class MapModelTest(unittest.TestCase):
    def test_bind_query_unbind(self):
        m = MapModel()
        m.bind("lxp1", "p0", {"type": "cc", "ch": 1, "num": 74})
        m.bind("matrix1000", "n21", {"type": "cc", "ch": 1, "num": 74})  # fan-out
        self.assertEqual(len(m.targets_for("cc", 1, 74)), 2)
        self.assertEqual(m.describe("lxp1", "p0"), "ch1 cc74")
        m.bind("lxp1", "p0", {"type": "nrpn", "ch": 2, "num": 300})     # rebind replaces
        self.assertEqual(len(m.targets_for("cc", 1, 74)), 1)
        self.assertEqual(m.describe("lxp1", "p0"), "ch2 nrpn300")
        self.assertTrue(m.unbind("lxp1", "p0"))
        self.assertFalse(m.unbind("lxp1", "p0"))
        self.assertEqual(MapModel(m.to_config()["mappings"]).describe("matrix1000", "n21"), "ch1 cc74")

    def test_scale(self):
        self.assertEqual(scale_to_field(0, 127, 0, 15), 0)
        self.assertEqual(scale_to_field(127, 127, 0, 15), 15)
        self.assertEqual(scale_to_field(64, 127, -63, 63), 0)
        self.assertEqual(scale_to_field(16383, 16383, 0, 4095), 4095)

    def test_nrpn_assembler(self):
        a = NrpnAssembler()
        self.assertIsNone(a.feed(1, 99, 2))
        self.assertIsNone(a.feed(1, 98, 44))
        self.assertEqual(a.feed(1, 6, 100), ((2 << 7) | 44, 100, 7))
        self.assertEqual(a.feed(1, 38, 5), ((2 << 7) | 44, (100 << 7) | 5, 14))
        self.assertIsNone(a.feed(3, 6, 1))   # no param selected on ch3


if __name__ == "__main__":
    unittest.main()
