"""Geometry checks run with base Miniforge: python tests/test_cnrfc_hu18_geometry.py."""
import importlib.util
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec('geopandas') and importlib.util.find_spec('osgeo'),
                     'Requires base Miniforge geospatial dependencies')
class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root/'bin'))
        sys.path.insert(0, str(root/'src'))
        import build_cnrfc_hu18_domain
        cls.tool = build_cnrfc_hu18_domain

    def test_exact_two_way_union_and_single_buffer(self):
        from shapely.geometry import box
        official, hu = box(0, 0, 10000, 10000), box(5000, -10000, 15000, 10000)
        union, buffered = self.tool.build_geometries(official, hu)
        self.assertTrue(union.equals(official.union(hu)))
        self.assertEqual(buffered.bounds, (-20000., -30000., 35000., 30000.))
        self.assertTrue(buffered.covers(union))

    def test_zip_traversal_rejected(self):
        import tempfile
        import zipfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'bad.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('../outside.txt', 'unsafe')
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.tool.extract_archive(archive, root/'extract')
            self.assertFalse((root/'outside.txt').exists())


if __name__ == '__main__':
    unittest.main()
