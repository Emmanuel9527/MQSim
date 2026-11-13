# SSD microbenchmark：真機量測與 MQSim 校準

這份文件是 microbenchmark 的統一入口，實驗腳本位於 `microbench/`。
真機原始結果與分析保存於專案根目錄下的 `microbench/results/`。

目標是在明確工作負載與裝置狀態下，比較真實 SSD 與 MQSim 的延遲、吞吐及競爭趨勢。
**目前新增的是 fio 真機實驗套件，尚未實作 MQSim 對應工作負載轉換或完成校準。**
外部曲線接近，不代表已證明真機的 channel/die/FTL 內部行為。
任何模型修改都必須記錄硬體機制、證據、參數範圍及未參與調參的驗證結果。

## MQSim 校準入口

校準以吞吐增益、延遲成長與轉折趨勢為主。計畫、測試紀錄及結果分析統一見 [mqsim_calibration.md](mqsim_calibration.md)。
使用 `bash microbench/run_mqsim_calibration.sh --prepare` 只產生輸入；模擬仍由使用者明確執行 `--run`。

## 執行原則

所有實驗由使用者在主機終端執行 bash 腳本，包含短測。代理程式只準備程式、靜態／離線檢查及分析既有結果。
不帶 `--run` 僅列出計畫，不啟動 fio、不讀取資料集。`--analyze` 只分析既有歸檔。

預設資料：

```
/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_block_shuffle_disk.index
```

預設工作範圍是這個已填入資料檔案的前 32 GiB，目標裝置是 `nvme0n1`。
使用 libaio、direct I/O、`--readonly`、`allow_file_create=0`；不格式化、不 trim、不寫入測試資料、不清 cache、不改電源或 HMB。
只接受存在且非明顯 sparse 的一般檔案，並檢查它直接位於指定裝置。
這是系統碟，其他程序仍可能干擾量測；避免同時跑其他實驗。

## NAND transaction 分批研究方向

研究假設、分層量測與建議研究順序記錄於 [NAND_TRANSACTION_BATCHING.md](NAND_TRANSACTION_BATCHING.md)。

## 批次與 pipeline 需求探索

針對 batch16／64／256 與持續補入比較的新計畫，請見 [PIPELINE_SWEEP.md](PIPELINE_SWEEP.md)。
先由使用者執行 `bash microbench/run_pipeline_sweep.sh --pilot`；確認時序後，完整探索使用 `--run`，名目26分鐘加額外開銷。
這個計畫尚未修正 fio replay drift，不可把目標速率直接當裝置實際收到的速率。

## Burst／smooth 負載掃描

這個預設用來探索提高負載後是否出現差異。**既有 fio timestamped replay 的累積延遲尚未修正**；
它不是保證等實際負載的因果實驗。報告會把目標速率、實際 fio submission 速率、完成速率分開，
若實際速率不匹配，必須保留此限制，不能將 latency 差異全歸因於 SSD。

```bash
cd /home/emmanuel/projects/MQSim
# 只看計畫，不啟動 fio
bash microbench/run_burst_sweep.sh
# 使用者執行正式掃描
bash microbench/run_burst_sweep.sh --run
```

- 目標速率：2,000 / 8,000 / 16,000 / 24,000 / 32,000 / 48,000 / 64,000 IOPS。
  先前約 24–25k random-read 平台只是選點參考，並非已證明的硬體最大吞吐。
- 固定 4 KiB random direct read、32 GiB 工作範圍、1 libaio job、outstanding cap 128。
- 每個負載比較 smooth 與 burst8；每次名目提供區間 5 秒，N = 速率 × 5，全數完成才停止。
  同一配對的地址序列及請求數完全相同；不同負載的請求數不同。
- 每點四次重複，共 56 次，名目 280 秒（4 分 40 秒），另加啟動、落後、排空、歸檔及分析。
  不保證固定完成時間，個別 fio 預設 timeout 600 秒。
- 每次重複隨機打亂負載順序，配對相鄰；奇數 repeat 為 smooth → burst，偶數反過來。
  這能平衡先後順序，但不是 reset cache，也不能消除 SSD 快慢狀態變動。
- 未增加 burst 大小或 QD，以免同時改變多個條件。可另用 `--burst 32` 探索大 burst，
  但須視為獨立實驗，不能直接混入 burst8 的結果。
- 純讀、無 warmup，保留 startup/drain；不在記錄途中寫入持久化結果。
- 同時保留全部 trace 和逐筆 log，需要約 5.4 GiB tmpfs 預算及額外 RAM；執行前會檢查。

完成後自動產生一般 `analysis/` 及專用 `burst_analysis/`：

- `per_run.csv`：實際 submission 速率、完成 IOPS、mean/p99、slat/clat、送入間隔與累積時間偏差。
- `paired.csv`：每個 repeat／負載的 burst/smooth 延遲比例、速率差與 review flag。
- `report.md`：依負載排序的比較表。實際執行順序仍保留在 metadata。

預設超過 2% 的實際速率差標記 `rate_mismatch`；若配對接近但未達目標則標記 `target_not_reached`。
這是診斷門檻，不是統計顯著性、通過校準或兩條流量等價的證明。
低於門檻仍要檢查群聚形狀、背景流量與重複結果。generator 跟不上、QD cap 回壓與 SSD 變慢可能同時發生，
不能把累積 schedule error 當 SSD service time。

也可只分析既有結果，不執行 fio：

```bash
python3 -B microbench/analyze_burst_sweep.py microbench/results/實際目錄名稱
```

## 五類實驗

所有預設計畫均每個條件重複三次，各次重複內以固定 seed 打亂順序。
一般測試每次讀取暖機 10 秒、量測 30 秒。此暖機不是寫入 preconditioning，也不保證裝置達穩態。

| `--family` | 預設條件 | 名目時間 | 用途與限制 |
|---|---|---:|---|
| `size` | QD1，4/16/64/256/1024 KiB，random read | 10 分鐘 | 基礎延遲與傳輸成本；fio latency 不是 NAND latency |
| `size-qd` | 上述五種大小 × QD1/8/32/128 | 40 分鐘 | 吞吐平台、排隊與平行度；曲線無法唯一推導 channel 數 |
| `locality` | sequential/random × 256 MiB/4 GiB/32 GiB × QD1/32，4 KiB | 24 分鐘 | 位址模式、工作範圍影響；轉折不能單獨證明某種 cache |
| `jobs` | 總 QD32，1/2/4/8 jobs，各 job QD32/16/8/4，4 KiB random | 8 分鐘 | 多 flow 競爭，job 數不是實際 NVMe queue 數 |
| `arrival` | 2k/10k/20k IOPS 目標速率 × smooth/burst8，4 KiB | 約 3 分鐘 + drain | 固定地址序列、數量與預定平均到達率；預設保留作驗證 |

時間未含 fio 啟動、最後排空、歸檔及分析。`all` 約 85 分鐘加額外開銷，**不是 30 分鐘套餐**。
實際到達速率不足或裝置變慢時，arrival 執行時間可能更長；每次 fio 預設 timeout 600 秒，超時標記不完整，不刪除已取得資料。

```bash
cd /home/emmanuel/projects/MQSim

# 列出計畫：沒有 I/O 實驗
bash microbench/run_calibration.sh --family size

# 正式執行：擇一，不要在多個終端同時執行
bash microbench/run_calibration.sh --family size --run
bash microbench/run_calibration.sh --family size-qd --run
bash microbench/run_calibration.sh --family locality --run
bash microbench/run_calibration.sh --family jobs --run
bash microbench/run_calibration.sh --family arrival --run
```

若只想先跑約 30 分鐘、涵蓋五類的探索版：

```bash
bash microbench/run_calibration.sh --family all \
  --sizes-kib 4 64 1024 --qds 1 32 \
  --working-sets-mib 256 32768 --jobs 1 8 --rates 2000 10000 \
  --runtime 30 --ramp 10 --repeats 2 --run
```

這是 46 次測試，名目 1600 秒（26 分 40 秒）加啟動／歸檔／分析，非保證 30 分鐘內完成。
只有兩次重複，適合探索，不足以宣稱長期平均收斂。

## 先由使用者執行功能短測

新增套件尚未由代理在真機執行 fio。建議先跑下列短測確認當地 fio 的選項與輸出格式；**短測不是校準資料**。

```bash
bash microbench/run_calibration.sh --family all \
  --sizes-kib 4 --qds 1 --working-sets-mib 256 --jobs 1 2 \
  --rates 1000 --arrival-seconds 1 --runtime 2 --ramp 0 --repeats 1 --run
```

`locality` 固定包含 QD1/32，`jobs` 固定總 QD（可用 `--total-qd` 改），不受 `--qds` 控制。
更換路徑／裝置使用 `--file`／`--device`。可用 `--cpu` 固定邏輯 CPU，但多 job 全綁同一 CPU 可能造成主機瓶頸，預設不綁。

## burst／平滑的精確定義

對同一 repeat、同一速率，產生完全相同的有限 random 4 KiB 地址序列及請求數：

- smooth：第 i 筆目標時間為 `i / rate`。
- burst：第 i 筆目標時間為 `floor(i/B) * B / rate`，B 預設 8。
- 每次 N = rate × arrival-seconds，N 必須整除 B。兩者以相同 N/rate 名目提供區間定義平均速率；最後一筆到達時刻不同。
- fio v3 iolog 用微秒時間戳，第一筆設在 1 µs。依 fio replay 行為，分析使用第一筆 submission 對齊，不把啟動耗時算成排程誤差。
- 預設 outstanding cap 為 128。無 warmup、無 time_based 截斷；必須完成完整 N 筆，包括 drain。
- `replay_no_stall=0` 要求 fio 嘗試遵守時間；這是**有限 QD 的 timestamped replay**，不是保證開環的硬體請求產生器。送不出去時會延後，不能假稱裝置真的收到原定節奏。
- 同一目標時間的請求仍由 fio 逐筆提交，不保證一個 io_submit 批次或硬體同時到達。
- `requests.iolog` 是目標時間與地址，`address_sequence.sha256` 可確認配對地址一致。slat log 檢查實際記錄的地址順序與數量；clat log 用 issue time + offset 對應。
- `arrival_audit.csv` 與逐筆 `*_arrivals.csv` 報告相對第一筆的時間偏差，並保留負值。它不是絕對啟動延遲，也不是 NVMe 命令實際進入裝置的時間。
- 既有 fio 3.41 固定筆數測試發現 clat log 可能漏掉最後 QD−1 筆 drain。分析明列缺失數，不補造資料。JSON+ 的完成數與整體延遲統計另行保存。
- 詳細 logging 有成本。先確認送入節奏，之後若要歸因 burst 效果，仍需評估 observer overhead、實際 block issue/completion 和背景流量。

此項預設是 held-out validation，不能用它調參後又把同一份資料稱為獨立驗證。
這也不是 DiskANN 的「等整批完成才發下一批」barrier 模式；後者是另一種 completion-dependent workload。

## 多 job 與工作範圍

jobs 實驗所有 jobs 共享同一 32 GiB 位址範圍，不切成較小的 per-job working set。
保留 fio 的 per-job JSON 和深度分布，沒有開 group_reporting。
相同總 QD 不代表相同完成數、完全相同聚合地址序列或硬體 queue 分配。
報告 `sum_job_iops` 是各 job 量測窗的 IOPS 相加，不是精確共同時間窗的總完成數／時間；啟動與結束邊界需保留。
平均 latency 依完成請求數加權，p99 保留每 job 的值，不平均後當成 pooled p99。

## 輸出與重跑分析

量測期間所有輸出放在經 findmnt 確認的 tmpfs：`/dev/shm/mqsim_suite_*`。
會估算詳細 log 容量並檢查 tmpfs／可用 RAM；tmpfs 在記憶體壓力下仍可能 swap。
整套量測與監控停止後，才寫入 `microbench/results/mqsim_suite_*/`。
SHA-256 驗證複製完整性，並保留 tmpfs 原件；不覆寫既有結果。

- `metadata.json`：完整計畫、seed、條件、工具版本、硬體／主機資訊。
- 程式與 `ssdconfig.xml` snapshot：追溯用，不代表本實驗執行過 MQSim。
- 每次的 `job.fio`、`command.json`、`fio.json`（JSON+ histogram）、stdout/stderr、latency logs。
- `system_samples.jsonl`：每秒主機／裝置計數器，包含其他程序流量。
- `events.jsonl`：fio process 起訖兩種時鐘，包含啟動與暖機，不能當精確量測邊界。
- `COMPLETE` 或 `INCOMPLETE`；完整量測不代表 arrival 時序已符合目標。
- `analysis/report.md`、`per_run.csv`、`per_job.csv`，arrival 額外包含時序稽核。

```bash
# 使用實驗最後印出的實際目錄；只分析、不啟動 fio
bash microbench/run_calibration.sh --analyze microbench/results/mqsim_suite_實際名稱
```

完成執行會自動分析。已有 `analysis/` 時拒絕覆寫；失敗分析留在 `.analysis-*` 供診斷。
不同 suite 或 family 的結果不能直接交給舊版 `analyze_read_sweep.py`，以免把不同 request size／job 數混成同一條 QD 曲線。

## MQSim 比較規則

1. 先對齊大小、地址範圍、併發／到達方式、讀寫比例與量測邊界。fio latency 包含主機效應，不能直接填入 NAND read latency。
2. 一組固定的 SSD 參數必須解釋多種工作負載，不能每個 QD 各調一組。
3. `size`／`size-qd`／`locality`／`jobs` 是候選校準資料；先指定其中哪些條件保留驗證。arrival 預設全保留驗證。
4. 報告絕對誤差與趨勢、重複測試分布；保存快慢時段，不因不吻合模型而刪掉。
5. 若多組合理參數都能吻合，保留不確定性並做敏感度分析。不能由單一外部曲線宣布某個 NAND channel 已塞滿。

## 既有工具

- [原始 QD sweep 與量測語意](#legacy-sweep)
- [長時間 QD 基線](LONG_BASELINE.md)
- [地址／cache probe](CACHE_PROBE.md)
- [原始 sweep 分析](ANALYSIS.md)

參考：[fio 官方文件](https://fio.readthedocs.io/en/latest/fio_doc.html)、[fio iolog 實作](https://github.com/axboe/fio/blob/master/iolog.c)、[MQSim 原論文附錄 A](https://www.usenix.org/system/files/conference/fast18/fast18-tavakkol.pdf)。

<a id="legacy-sweep"></a>

## 原始 QD sweep 詳細說明

以下保留既有 `read_sweep.py` 的參數、輸出與量測語意；五類新實驗請使用上方的 `run_calibration.sh`。

### Goal and scientific constraints

Target observed on this machine: `/dev/nvme0n1`, Predator SSD GM7 2TB,
firmware GT11cb0c. This is also the system drive. No claim is made about its
channel count, NAND geometry, FTL policy, or internal cache implementation.
`ssdconfig.xml` remains unchanged. This tool measures the real system first.

The objective is matching response trends, not forcing absolute numbers to
agree. Do not add QD-dependent delays, per-workload correction factors, or
manually alter measurements to improve a fit. Every eventual MQSim change must
identify the hardware mechanism, source/evidence, affected code, parameter
range, calibration tests, and independent validation tests. A discrepancy can
remain unexplained; it must not be silently fitted away. More than one internal
configuration may explain the external measurements.

### Run

From `/home/emmanuel/projects/MQSim` in the ordinary host terminal:

```bash
# Read-only metadata checks and randomized schedule, no benchmark:
python3 microbench/read_sweep.py

# Short functional test, not calibration data:
python3 microbench/read_sweep.py --qds 1 8 --repeats 1 --runtime 3 --ramp 1 --run

# Baseline: 8 QDs x 3 repeats x (10 s warmup + 30 s measurement), about 16 minutes:
python3 microbench/read_sweep.py --run
```

The default input is the existing 39 GiB SIFT100M block-shuffled disk index,
using its first 32 GiB. It is NOT loaded into RAM. fio uses direct random reads,
4 KiB alignment, libaio, one job, one-I/O submissions and completion reaping,
and tries to refill to the requested depth. `--readonly` and
`allow_file_create=0` protect against accidental fio writes/file creation.
The script never formats, trims, fills, drops caches, or changes SSD/OS settings.
It rejects raw devices and apparently sparse files. Allocation checks do not
prove physical NAND layout or reveal SSD caches. The existing populated index
is the intended input; don't substitute an unwritten/preallocated file.

Outputs go to a unique `/dev/shm/mqsim_read_sweep_*` directory. An explicit
`--out` must also have a tmpfs parent. Startup checks require 1 GiB free tmpfs
and 2 GiB available RAM. No automatic disk fallback. After the sweep (or a caught failure/interruption), the runner automatically
copies results to `microbench/results/<run-directory>/` in this repository.
Monitoring and fio stop before any archival write starts. A SHA-256 manifest
verifies the copy; a temporary directory is renamed only after verification.
Existing archives are never overwritten. The tmpfs original remains available.
If archiving fails, the error and original path are printed. `--archive-root`
can override the persistent parent. Forced termination/power loss cannot be
automatically archived. Failed runs are marked `INCOMPLETE`, never `COMPLETE`. Tmpfs can be swapped under memory pressure.
No elevated privileges are required on the normal host for readable files.
Sandboxed terminals may expose `/dev/shm` read-only.

Options include `--size-gib`, `--block-kib`, `--qds`, `--repeats`, `--runtime`,
`--ramp`, `--seed`, and optional `--cpu N` for consistent CPU affinity. Record
and retain any choice; don't change affinity mid-comparison. QD order is
randomized reproducibly within each repeat. The random address seed is shared
across QDs within a repeat; timed runs perform different numbers of requests,
so they share a sequence prefix, NOT identical full traces/counts.

### Outputs and measurement boundaries

- `metadata.json`: exact options/schedule, fio version, device/mount identity,
  firmware, scheduler, CPU affinity, source revision/status and config hash.
- `ssdconfig.snapshot.xml`: provenance only; this benchmark does not use MQSim.
- `events.jsonl`: paired epoch and monotonic clocks around each fio process.
  These include setup/warmup/teardown; do not treat them as exact measured-phase
  boundaries. fio's runtime/epoch logs identify the measured workload.
- `system_samples.jsonl`: raw block counters, CPU/memory counters, accessible
  NVMe hwmon temperatures, sampled about once per second. All other processes'
  traffic is included; it cannot be attributed solely to fio.
- Per-run `job.fio`, `command.json`, stdout/stderr, original JSON+ `fio.json` (including latency histogram bins), and
  per-second average latency/IOPS logs. No per-request tracing is enabled by
  default to keep observer overhead manageable.
- `summary.csv`: one row per successfully completed run, no outlier removal.
- `COMPLETE`: only created after every run succeeds.

fio slat is its submission latency; clat is submission-to-observed-completion
latency, including host effects; lat is their total as defined by fio. None is
pure NAND service time. Raw JSON retains completion/total percentiles and the
depth distribution. Mean slat/clat/lat can be compared, but percentiles cannot
be added or subtracted. Per-second average logs are not per-second p99 logs.
`iodepth_level` is fio's sampled depth histogram, NOT the time-weighted block
device QD. The upper bucket also cannot distinguish 64 from 128 requests.

Block `/stat` fields can supply read IOPS, average read completion time, and
time-weighted outstanding requests from interval deltas. They include background
traffic and use different boundaries from fio. Queue depth and block busy time
do not expose NAND die/channel occupancy or prove saturation.

References: https://fio.readthedocs.io/en/latest/fio_doc.html
and https://github.com/CMU-SAFARI/MQSim

### What to compare

1. Repeatability before model fitting: inspect all repetitions and time series,
   temperatures, background traffic and input configuration. Preserve fast/slow
   regimes separately if they recur; don't average them into a fictitious SSD.
2. Plot IOPS vs requested/observed depth, mean/p50/p95/p99 latency vs depth,
   and throughput normalized to QD=1. Look for slope changes, plateaus, latency
   growth, and whether these are larger than real-device run-to-run variation.
3. Fit only a declared subset (e.g. QD 1/4/16/64); reserve 2/8/32/128 as holdout.
   Decide this before fitting. A similar trend on training tests alone is not
   validation. Absolute discrepancies remain reported even if trends match.
4. Later run block-size and working-set sweeps with the same engine/protocol.
   Shared size/geometry/caching parameters must explain all tested conditions;
   do not retune them independently for each workload.

Warmup here is a repeatable READ warmup, not steady-state write preconditioning.
This suite characterizes the current populated SSD state only. Direct I/O
bypasses the OS data page cache, not the device's data/mapping caches. Working-set
changes cannot uniquely identify a cache type. Do not tune simulated NAND latency
to absorb host submission/scheduling delays. Keep the real and simulated timing
boundaries explicit.

### Uniform versus burst arrivals (separate suite)

Timestamped smooth/burst replay is now available in `calibration_suite.py`; see
[the suite guide](#五類實驗). Its intended-versus-recorded
arrival audit must be checked before interpreting results.

This original QD sweep is CLOSED LOOP: completions release slots for new reads. It is
neither fixed-rate uniform traffic nor an open-loop burst generator.

After finding the baseline operating range, compare the same finite address
sequence and request count at fixed mean offered rate lambda:
uniform arrivals every 1/lambda, versus B arrivals every B/lambda. Preserve the
actual issue times, finite outstanding limit, backlog, and intended-vs-actual
arrival delay. Don't silently drop overdue requests or wait for batch completion
and call that fixed-rate traffic. Keep the generator CPU/queue limits observable.
Validate burst/drain response on hardware before using MQSim internals to explain
it. Completion-dependent DiskANN barrier traffic is a separate experiment.

### Model-change ledger (empty until evidence exists)

For each proposed change record: observation; physical mechanism; source;
candidate parameters/range; alternatives; calibration workloads; held-out
workloads; residual errors; and whether the conclusion holds across plausible
parameter sets. No calibration is claimed by merely creating this benchmark.

The benchmark engine is fio; Python only plans runs, collects host metadata,
invokes fio and archives its unmodified outputs. JSON+ histogram bins allow
later distribution analysis without fabricating individual request samples.
The maintained launcher remains `microbench/read_sweep.py`; persistent measured
results are under `microbench/results/`, ignored by Git.
