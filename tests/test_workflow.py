import re
import unittest
from pathlib import Path


class CacheWorkflowTests(unittest.TestCase):
    def test_reruns_save_new_checkpoint_and_restore_previous_progress(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/update.yml').read_text()
        keys = re.findall(r'^          key: (.+)$', workflow, re.MULTILINE)
        expected = 'gis-property-v1-${{ github.run_id }}-${{ github.run_attempt }}'
        self.assertEqual(keys, [expected, expected])
        self.assertIn('restore-keys: gis-property-v1-', workflow)
        save_step = workflow.split('- uses: actions/cache/save@v4', 1)[1].split('      - ', 1)[0]
        self.assertIn('if: always()', save_step)
        self.assertIn('path: .source-cache', save_step)


if __name__ == '__main__':
    unittest.main()
