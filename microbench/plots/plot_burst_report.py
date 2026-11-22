#!/usr/bin/env python3
"""Plot existing burst_analysis CSVs only. Does not execute fio."""
import argparse
import csv
import os
from pathlib import Path
import statistics
import tempfile
os.environ.setdefault('MPLCONFIGDIR', tempfile.mkdtemp(prefix='mqsim-mpl-'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    analysis=a.archive/'burst_analysis'
    with (analysis/'per_run.csv').open() as f: rows=list(csv.DictReader(f))
    with (analysis/'paired.csv').open() as f: pairs=list(csv.DictReader(f))
    rates=sorted({int(r['target_iops']) for r in rows})
    repeats=sorted({int(r['repeat']) for r in rows})
    for rate in rates:
        for mode in ('smooth','burst'):
            assert sorted(int(r['repeat']) for r in rows if int(r['target_iops'])==rate and r['mode']==mode)==repeats
    out=a.out or analysis/'figures'
    out.mkdir(parents=True,exist_ok=True)
    colors={'smooth':'#2563a6','burst':'#d46618'}
    markers={'smooth':'o','burst':'^'}
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':180})
    def save(fig,name):
        for ext in ('png','svg'):
            path=out/f'{name}.{ext}'
            if path.exists(): raise FileExistsError(path)
            fig.savefig(path,bbox_inches='tight')
        plt.close(fig)
    def setup(ax,ylabel):
        ax.set_xticks(range(len(rates)),[f'{r/1000:g}' for r in rates])
        ax.set_xlabel('Target offered rate (kIOPS; categorical spacing)')
        ax.set_ylabel(ylabel);ax.grid(axis='y',alpha=.22)
    def scatter(ax,key,scale=1):
        for mode in ('smooth','burst'):
            med=[]
            for i,rate in enumerate(rates):
                rr=[r for r in rows if int(r['target_iops'])==rate and r['mode']==mode]
                values=[float(r[key])*scale for r in rr];med.append(statistics.median(values))
                center=i+(-.09 if mode=='smooth' else .09)
                for r,v in zip(rr,values):
                    jitter=(repeats.index(int(r['repeat']))-(len(repeats)-1)/2)*.035
                    ax.scatter(center+jitter,v,c=colors[mode],marker=markers[mode],s=27,alpha=.7,zorder=3)
                ax.plot([center-.09,center+.09],[med[-1]]*2,color=colors[mode],lw=2.5,zorder=4)
            ax.plot([i+(-.09 if mode=='smooth' else .09) for i in range(len(rates))],med,color=colors[mode],alpha=.45,lw=1)
        ax.legend(handles=[Line2D([],[],color=colors[m],marker=markers[m],label=m.capitalize(),lw=1) for m in ('smooth','burst')],loc='best')
    fig,axes=plt.subplots(1,2,figsize=(12,4.4),layout='constrained')
    setup(axes[0],'Achieved fio submission rate (kIOPS)');scatter(axes[0],'submission_rate_iops',.001)
    axes[0].plot(range(len(rates)),[r/1000 for r in rates],'--',color='#666666',lw=1,label='Target')
    axes[0].text(.03,.94,'Dashed: target rate',transform=axes[0].transAxes,va='top',fontsize=9)
    setup(axes[1],'First-to-last fio submission span (s)');scatter(axes[1],'issue_span_s')
    axes[1].axhline(5,color='#666666',ls='--',lw=1)
    axes[1].text(.03,.94,'Dashed: nominal 5 s offering window',transform=axes[1].transAxes,va='top',fontsize=9)
    axes[0].set_ylim(bottom=0);axes[1].set_ylim(bottom=0)
    fig.suptitle('Offered load vs achieved submission timing\nEach point = one run; short bars / connecting lines = medians, not confidence intervals',fontsize=12)
    save(fig,'load_and_timing')
    fig,axes=plt.subplots(1,2,figsize=(12,4.4),layout='constrained')
    for ax,key,title in zip(axes,['lat_mean_us','lat_p99_us'],['Mean latency','Per-run p99 latency']):
        setup(ax,f'{title} (ms; log scale)');scatter(ax,key,.001);ax.set_yscale('log')
    fig.suptitle('Latency across target loads (all runs retained)\nHost-visible fio latency; loads may not match between modes; p99 is not pooled',fontsize=12)
    save(fig,'latency_by_load')
    fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
    flags={'rate_checks_only_passed':('#187b61','Rate checks only passed'),
           'rate_mismatch':('#bb3547','Actual rates differ >2%'),
           'target_not_reached':('#ad7518','Target not reached (>2%)')}
    for ax,key,title in zip(axes,['burst_over_smooth_mean_latency','burst_over_smooth_p99'],['Mean latency ratio','Per-run p99 ratio']):
        setup(ax,f'{title}: burst / smooth (log scale)');ax.set_yscale('log');ax.axhline(1,color='#555555',ls='--',lw=1)
        for r in pairs:
            x=rates.index(int(r['target_iops']))+(repeats.index(int(r['repeat']))-(len(repeats)-1)/2)*.14
            y=float(r[key]);color=flags[r['review_flag']][0]
            ax.scatter(x,y,color=color,s=38,zorder=3)
            ax.annotate(r['repeat'],(x,y),xytext=(0,6),textcoords='offset points',ha='center',fontsize=8,color=color)
    fig.suptitle('Paired latency ratios: diagnostics, not causal burst effects\nNumbers = repeat IDs; above 1 means burst latency is higher',fontsize=12)
    fig.legend(handles=[Line2D([],[],marker='o',color=c,lw=0,label=label) for c,label in flags.values()],loc='outside lower center',ncol=3,fontsize=9)
    save(fig,'paired_ratios')
    print(f'Wrote 3 PNG + 3 SVG charts to {out}')


if __name__=='__main__':main()
