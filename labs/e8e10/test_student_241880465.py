"""验证仅头文件间接包含变化时，历史依赖图能够正确失效。"""
import tempfile
import unittest
from pathlib import Path

from lab import commit, fixture, scan


class Student241880465Tests(unittest.TestCase):
    """使用独立预期检查间接依赖的新增、稳定复用及移除。"""

    def test_indirect_include_round_trip(self):
        """只改 config.h，确认 extra.h 的新增与消除不会被旧图掩盖。"""
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            fixture(project)
            baseline = scan(project)
            original_header = (project / 'config.h').read_text()
            original_source = (project / 'main.c').read_bytes()
            original_makefile = (project / 'Makefile').read_bytes()
            expected_base = {
                ('MISSING', 'main.o', 'config.h'),
                ('REDUNDANT', 'main.o', 'unused.h'),
            }
            self.assertEqual(baseline['actual'], {
                'main.o': ['config.h', 'main.c'], 'helper.o': ['helper.c'],
            })
            (project / 'config.h').write_text(
                '#include "extra.h"\n' + original_header, encoding='utf-8')
            commit(project, '新增头文件内部的间接包含')
            added = scan(project, baseline)
            self.assertNotEqual(added['key'], baseline['key'])
            self.assertEqual(added['reused'], [])
            self.assertEqual(set(added['refreshed']), {'main.o', 'helper.o'})
            self.assertEqual(added['actual'], {
                'main.o': ['config.h', 'extra.h', 'main.c'], 'helper.o': ['helper.c'],
            })
            self.assertEqual(
                {(f['type'], f['target'], f['dependency']) for f in added['findings']},
                expected_base | {('MISSING', 'main.o', 'extra.h')})
            self.assertEqual(added['added'], [['MISSING', 'main.o', 'extra.h']])
            self.assertEqual(added['resolved'], [])
            full = scan(project)
            for field in ('actual', 'declared', 'findings'):
                self.assertEqual(added[field], full[field])
            unchanged = scan(project, added)
            self.assertEqual(unchanged['reused'], ['main.o', 'helper.o'])
            self.assertEqual(unchanged['refreshed'], [])
            self.assertEqual(unchanged['actual'], added['actual'])
            self.assertEqual(unchanged['added'], [])
            self.assertEqual(unchanged['resolved'], [])
            (project / 'config.h').write_text(original_header, encoding='utf-8')
            commit(project, '移除头文件内部的间接包含')
            removed = scan(project, added)
            self.assertEqual(removed['reused'], [])
            self.assertEqual(set(removed['refreshed']), {'main.o', 'helper.o'})
            self.assertEqual(removed['actual'], baseline['actual'])
            self.assertEqual(
                {(f['type'], f['target'], f['dependency']) for f in removed['findings']},
                expected_base)
            self.assertEqual(removed['added'], [])
            self.assertEqual(removed['resolved'], [['MISSING', 'main.o', 'extra.h']])
            full_removed = scan(project)
            for field in ('actual', 'declared', 'findings'):
                self.assertEqual(removed[field], full_removed[field])
            self.assertEqual((project / 'main.c').read_bytes(), original_source)
            self.assertEqual((project / 'Makefile').read_bytes(), original_makefile)
