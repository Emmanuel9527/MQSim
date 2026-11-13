#!/usr/bin/env python3
"""Figures from existing calibration measurements; never launches workloads."""
import os
import tempfile
from pathlib import Path
from statistics import median


def plot_trends(rows, real, out):
    os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir())/'mqsim-calibration-mpl'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    artifacts=[]
    for index,variant in enumerate(sorted({r['variant'] for r in rows})):
        sim=[r for r in rows if r['variant']==variant]
        common=sorted({int(r['qd']) for r in sim}&{int(r['requested_qd']) for r in real})
        for base in (1,32):
            qds=[q for q in common if q>=base]
            if base not in qds or len(qds)<2:continue
            fig,axes=plt.subplots(1,2,figsize=(11,4.5))
            for ax,simkey,realkey,ylabel in zip(axes,['iops','end_to_end_mean_us'],['iops','lat_mean_us'],['IOPS / baseline IOPS','Mean latency / baseline latency']):
                for data,idkey,qdkey,key,color,label in [(sim,'seed','qd',simkey,'#2166ac','MQSim seeds'),(real,'repeat','requested_qd',realkey,'#d95f02','SSD repeats')]:
                    series=[]
                    for ident in sorted({int(r[idkey]) for r in data}):
                        points={int(r[qdkey]):float(r[key]) for r in data if int(r[idkey])==ident}
                        if not all(q in points for q in qds):raise ValueError('Incomplete plot series')
                        values=[points[q]/points[base] for q in qds];series.append(values)
                        ax.plot(qds,values,color=color,alpha=.3,lw=1,marker='.',markersize=4)
                    columns=list(zip(*series))
                    ax.fill_between(qds,[min(v) for v in columns],[max(v) for v in columns],color=color,alpha=.10)
                    ax.plot(qds,[median(v) for v in columns],color=color,marker='o',lw=2,label=label+' median')
                ax.set_xscale('log',base=2);ax.set_xticks(qds,[str(q) for q in qds]);ax.set_xlabel('Requested queue depth (QD)')
                ax.set_ylabel(ylabel);ax.grid(alpha=.2);ax.legend(fontsize=9)
            fig.suptitle(f'{variant}: normalized to QD{base} within each seed / repeat')
            fig.text(.5,.015,'Thin lines: individual runs. Shading: observed min-max, not confidence intervals.\nDifferent QDs were measured at different times; connecting lines do not establish causality.',ha='center',fontsize=9)
            fig.tight_layout(rect=(0,.10,1,.94))
            name=f'trend_v{index}_qd{base}'
            for ext in ('png','svg'):fig.savefig(out/f'{name}.{ext}',dpi=180,bbox_inches='tight')
            plt.close(fig);artifacts.append((variant,base,out/f'{name}.png'))
    return artifacts
