from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import update_ember_showcase as s


class EmberShowcaseTests(unittest.TestCase):
    def test_only_recent_original_repositories(self):
        def item(name, pushed, fork=False, archived=False):
            return {'name':name, 'full_name':'OKAN-SOFTWARE/'+name,
                    'owner':{'login':'OKAN-SOFTWARE'},'pushed_at':pushed,
                    'fork':fork,'private':False,'archived':archived}
        batch=[item('older','2026-10-20T12:00:00Z'),
               item('newer','2026-10-29T12:00:00Z'),
               item('fork','2026-10-30T12:00:00Z',fork=True),
               item('OKAN-SOFTWARE','2026-10-31T12:00:00Z'),
               item('archived','2026-10-30T12:00:00Z',archived=True)]
        with patch.object(s,'api',return_value=batch):
            got=s.recent_original_repos('OKAN-SOFTWARE','token')
        self.assertEqual(['newer','older'],[x['name'] for x in got])

    def test_languages_are_real_code_and_max_three(self):
        with patch.object(s,'api',return_value={'Markdown':50000,'Java':10000,'JavaScript':9000,'HTML':8000,'CSS':7000}):
            langs=s.source_languages({'full_name':'OKAN-SOFTWARE/Doc2FAQ'},'token')
        self.assertEqual(['Java','JavaScript','HTML'],langs)

    def test_commits_filter_profile_and_merge(self):
        def item(repo,sha,msg):
            return {'repository':{'full_name':repo},'sha':sha*40,'commit':{'message':msg}}
        data={'items':[item('OKAN-SOFTWARE/OKAN-SOFTWARE','a','profile refresh'),
                       item('pylint-dev/astroid','b','Merge pull request #4'),
                       item('pylint-dev/astroid','c','fix(builder): close stream')]}
        with patch.object(s,'api',return_value=data):
            commits=s.latest_commits('OKAN-SOFTWARE','token')
        self.assertEqual(['fix(builder): close stream'],[c['message'] for c in commits])

    def test_svg_escapes_input_and_respects_reduced_motion(self):
        svg=s.ticker_svg([{'repo':'OKAN-SOFTWARE/Doc2FAQ','sha':'abcdef0','message':'fix <unsafe> & symbols'}])
        self.assertIn('&lt;unsafe&gt;',svg)
        self.assertNotIn('<unsafe>',svg)
        self.assertIn('prefers-reduced-motion',svg)
        self.assertIn('LATEST COMMITS',svg)
        card=s.project_svg({'name':'Doc2FAQ','pushed_at':'2026-10-29T19:12:48Z','description':'Build FAQs <securely>'},['Java','JavaScript','HTML'])
        self.assertIn('&lt;securely&gt;',card)
        self.assertIn('JavaScript',card)
        self.assertNotIn('Documents',card)

    def test_stats_use_existing_generated_counts(self):
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        svg=s.stats_svg(readme)
        import re
        activity = re.search(r'\*\*(\d+) opened · (\d+) merged · \d+ open now · (\d+) upstream repositories\*\*', readme)
        journey = re.search(r'\*\*\d+ authored commits · (\d+) public original repositories\*\*', readme)
        self.assertIsNotNone(activity)
        self.assertIsNotNone(journey)
        for number in [*activity.groups(), journey.group(1)]:
            self.assertIn('>'+number+'</text>', svg)

    def test_markers_assets_and_timestamp_free_activity(self):
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        for name in ['ACTIVITY','RECENT','JOURNEY','EMBER_PROJECTS']:
            self.assertEqual(1,readme.count(f'<!-- {name}:START -->'))
            self.assertEqual(1,readme.count(f'<!-- {name}:END -->'))
        for path in ['assets/phoenix-hero-animated.gif','assets/contribution-snake-dark.svg',
                     'assets/phoenix-footer-animated.gif','assets/ember/latest-commits.svg']:
            self.assertIn(path,readme)
        self.assertNotIn('| Time (Eastern) |',readme)
        self.assertNotIn('Phoenix system engineering mode',readme)
        with self.assertRaises(ValueError):
            s.replace_section('missing markers','content')


if __name__=='__main__':
    unittest.main()
