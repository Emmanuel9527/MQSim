#!/usr/bin/env python3
"""Plot archived full closed-loop results; no simulator execution."""
import argparse,json,os
from pathlib import Path
import numpy as np
os.environ.setdefault('MPLCONFIGDIR','/tmp/mqsim-calibration-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
 p=argparse.ArgumentParser();p.add_argument('trace',type=Path);p.add_argument('run',type=Path);args=p.parse_args();trace=args.trace.resolve();run=args.run.resolve()
 s=json.loads((run/'comparison.json').read_text());out=run/'report_figures';out.mkdir(exist_ok=True)
 real=np.genfromtxt(trace/'closed_loop/real_rounds.csv',delimiter=',',names=True,dtype=np.int64)
 sim=np.genfromtxt(run/'mqsim_rounds.txt',names=True,dtype=np.int64)
 assert len(real)==len(sim)==s['rounds_real']==s['rounds_sim']
 assert int(real['n'].sum())==s['reads_in_rounds']==s['reads_sim']
 fig,axes=plt.subplots(1,3,figsize=(13,4.3));qs=['mean','p50','p90','p99','p99.9']
 for ax,key,title in zip(axes,['request_latency_us','round_time_us','query_io_span_us'],['Request latency','Round read-call time','Query I/O span']):
  for side,label in [('real','T500'),('sim','MQSim')]:ax.plot(qs,[s[key][side][q] for q in qs],'o-',label=label)
  ax.set_title(title);ax.set_ylabel('us (log scale)');ax.set_yscale('log');ax.grid(alpha=.25);ax.legend()
 fig.suptitle('Full closed-loop replay: means match better than tails');fig.tight_layout()
 for ext in ['png','svg']:fig.savefig(out/f'latency_quantiles.{ext}',dpi=180)
 plt.close(fig)
 fig,axes=plt.subplots(1,2,figsize=(9,3.8))
 for ax,key,title in zip(axes,['query_rate_per_s','read_iops'],['Queries / second (I/O-phase window)','Reads / second']):
  vals=[s[key][x] for x in ['real','sim']];ax.bar(['T500','MQSim'],vals,color=['#d95f02','#2166ac']);ax.set_title(title);ax.set_ylim(0,max(vals)*1.18)
  for i,v in enumerate(vals):ax.text(i,v,f'{v:,.2f}',ha='center',va='bottom')
 fig.tight_layout()
 for ext in ['png','svg']:fig.savefig(out/f'throughput.{ext}',dpi=180)
 plt.close(fig)
 fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=True)
 for arr,b,e,label in [(real,'read_begin_ns','read_end_ns','T500'),(sim,'start_ns','end_ns','MQSim')]:
  origin=int(real['read_begin_ns'].min()) if label=='T500' else 0
  time=(arr[b]-origin)/1e9;lat=(arr[e]-arr[b])/1000;bins=(time/.05).astype(int);xs=[];means=[];tails=[]
  for k in np.unique(bins):
   values=lat[bins==k];xs.append((k+.5)*.05);means.append(values.mean());tails.append(np.percentile(values,99))
  axes[0].plot(xs,means,label=label);axes[1].plot(xs,tails,label=label)
 for ax,y in zip(axes,['Round mean (us)','Round p99 (us)']):ax.set_ylabel(y);ax.grid(alpha=.25);ax.legend()
 axes[1].set_xlabel('Elapsed time within each run (s); 50 ms bins by round start')
 fig.suptitle('Independent closed-loop timelines; bins are not matched query cohorts');fig.tight_layout()
 for ext in ['png','svg']:fig.savefig(out/f'round_timeline.{ext}',dpi=180)
 plt.close(fig)
 print(out)
if __name__=='__main__':main()
