"""Offline contract tests. Synthetic logs only; no fio or device access."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import calibration_suite as suite


class Contracts(unittest.TestCase):
    def setUp(self):
        self.no_process = patch('subprocess.Popen', side_effect=AssertionError('Offline tests cannot launch processes'))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def args(self, *options):
        a=suite.parser().parse_args(list(options))
        suite.validate(a)
        return a

    def test_default_counts_and_total_depth(self):
        a=self.args('--family','all')
        plan=suite.make_plan(a)
        self.assertEqual(len(plan),141)
        jobs=[c for c in plan if c['family']=='jobs']
        self.assertTrue(all(c['total_qd']==32 for c in jobs))
        self.assertEqual({c['jobs'] for c in jobs},{1,2,4,8})
        self.assertEqual({c['size_mib'] for c in jobs},{32768})
        self.assertEqual(plan,suite.make_plan(a))

    def test_short_plan_budget(self):
        a=self.args('--family','all','--sizes-kib','4','64','1024',
                    '--qds','1','32','--working-sets-mib','256','32768',
                    '--jobs','1','8','--rates','2000','10000','--repeats','2')
        plan=suite.make_plan(a)
        self.assertEqual(len(plan),46)
        self.assertEqual(sum(a.arrival_seconds if c['family']=='arrival' else a.runtime+a.ramp for c in plan),1600)

    def test_invalid_jobs_and_partial_burst(self):
        with self.assertRaises(ValueError): self.args('--jobs','3')
        with self.assertRaises(ValueError): self.args('--rates','1001','--arrival-seconds','1')

    def test_pair_addresses_and_distinct_timing(self):
        a=self.args('--family','arrival','--rates','1000','--arrival-seconds','1','--repeats','1')
        cases=suite.make_plan(a)
        with tempfile.TemporaryDirectory() as d:
            hashes=[];times={}
            for c in cases:
                path=Path(d)/c['mode']
                hashes.append(suite.arrival_trace(path,Path('/input'),c,a.burst))
                times[c['mode']]=[int(l.split()[0]) for l in path.read_text().splitlines() if ' read ' in l]
                job=suite.job_text(a,c,Path(d))
                self.assertNotIn('time_based=',job)
                self.assertIn('replay_no_stall=0',job)
            self.assertEqual(hashes[0],hashes[1])
            self.assertEqual(times['smooth'][:3],[1,1001,2001])
            self.assertEqual(times['burst'][:9],[1]*8+[8001])

    def test_arrival_audit_retains_missing_completion(self):
        a=self.args('--family','arrival','--rates','8','--arrival-seconds','1','--repeats','1')
        c=next(c for c in suite.make_plan(a) if c['mode']=='smooth')
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);out=run/'analysis';out.mkdir()
            suite.arrival_trace(run/'requests.iolog',Path('/input'),c,a.burst)
            records=[l.split() for l in (run/'requests.iolog').read_text().splitlines() if ' read ' in l]
            logs=[[0,100,0,4096,int(r[3]),0,100000+(int(r[0])-1)*1000+i*1000] for i,r in enumerate(records)]
            for kind,rows in [('slat',logs),('clat',logs[:-1])]:
                with (run/f'latency_{kind}.1.log').open('w') as f: csv.writer(f).writerows(rows)
            result=suite.audit_arrival(run,c,out)
            self.assertEqual(result['missing_completions'],1)
            self.assertEqual(result['relative_schedule_error_max_us'],7)
            with (out/f'{c["label"]}_arrivals.csv').open() as f: rows=list(csv.DictReader(f))
            self.assertEqual(rows[-1]['clat_us'],'')
            # Missing submissions must fail instead of silently hiding requests.
            with (run/'latency_slat.1.log').open('w') as f: csv.writer(f).writerows(logs[:-1])
            with self.assertRaises(ValueError): suite.audit_arrival(run,c,out)


if __name__=='__main__': unittest.main()
