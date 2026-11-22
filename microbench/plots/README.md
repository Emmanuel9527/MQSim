# 畫圖腳本

`microbench/` 的所有畫圖腳本都集中在這裡（2026-11-22 從 `microbench/` 根目錄搬過來）。

- **執行方式**：工作目錄維持在 `microbench/`，例如 `python3 plots/plot_ssd_pq.py results/autonomous_20261122 reports/figures`。腳本的輸入、輸出路徑都由參數給定，相對路徑以執行時的工作目錄為準，跟搬家前一樣。
- `plot_ladder.py`、`plot_cache_sweep.py` 會從上一層（`microbench/`）import `ladder_table`；`../mqsim_calibrate.py` 會從這裡 import `plot_mqsim_calibration`。
- 有畫圖功能、但以分析為主的腳本留在 `microbench/`：`server_sweeps/analyze_prefetch_prediction.py`、`realssd/analyze_read_sweep.py`、`trace_replay/analyze_ssd_slack.py`、`trace_replay/compare_closed_loop.py`、`trace_replay/compare_trace_replay.py`、`calibration/mqsim_window_probe.py`。

| 腳本 | 畫什麼 |
|---|---|
| `plot_architecture_pipelines.py` | 各候選架構的理想化 pipeline（不含 plane 碰撞），同一條時間軸 |
| `plot_barrier_vs_async.py` | SSD 內 barrier 與非同步展開的比較；控制器層級與 in-plane 運算的每跳延遲 |
| `plot_beamwidth_tradeoff.py` | beam width 取捨：相依跳數與每 query I/O，延遲下限與 plane 容量 QPS |
| `plot_burst_report.py` | 既有 burst_analysis CSV 的圖（不跑 fio） |
| `plot_cache_sweep.py` | 真機 node cache 掃描（`server_sweeps/diskann_cache_sweep.sh`）對照階梯模擬器的預測 |
| `plot_calibration_overview.py` | 校準後的 T500 MQSim 模型和真機 T500 的接近程度 |
| `plot_closed_loop_record.py` | 已歸檔的完整 closed-loop 結果（不跑模擬器） |
| `plot_ladder.py` | pipeline 階梯研究的圖（2026-11-16 報告） |
| `plot_load_scaling.py` | 真機 T500 負載掃描：IOPS／QPS／平均延遲（無 node cache） |
| `plot_mqsim_calibration.py` | 既有校準量測的趨勢圖（由 `calibration/mqsim_calibrate.py` 呼叫） |
| `plot_nominate_busy.py` | N1 簡報圖：W4 與 W8「有讀到忙碌單元的輪」 |
| `plot_nominate_choose.py` | 「主機提名、SSD 挑選」與原始 DiskANN 在一個 plane 忙碌時的理想化時間線 |
| `plot_nominate_sweep.py` | N1：`server_sweeps/diskann_nominate_sweep.sh` 的 summary.csv |
| `plot_pipeline_diagram.py` | 校準 T500 模型內單一 DiskANN worker 的 pipeline 圖 |
| `plot_policy_sweep.py` | P4：`server_sweeps/diskann_policy_sweep.sh` 的 summary.csv（各 frontier 策略的跳數、I/O、recall 對 beam width） |
| `plot_pq_tables.py` | DiskANN PQ codebook 與每個 query 的距離表示意圖（SIFT100M 伺服器：D=128、M=31） |
| `plot_prediction_by_round.py` | `server_sweeps/analyze_prediction_by_round.py` 的輸出：下一輪讀取是舊候選還是新鄰居 |
| `plot_sim_sweep.py` | N2：`server_sweeps/diskann_sim_sweep.sh` 的 summary.csv（三張圖） |
| `plot_sim_sweep_cache.py` | N2 策略收益：有無 node cache 的比較 |
| `plot_sim_sweep_cached.py` | N2 開 node cache（100k、3 個 seed）的版本 |
| `plot_sim_time_alignment.py` | N2 示意圖：模擬的一輪怎麼把量測的主機時間和模型的 SSD 時間放在同一個虛擬時鐘上 |
| `plot_ssd_idle.py` | 「原始 DiskANN 下 SSD 大多閒置」的數字來源 |
| `plot_ssd_idle_load.py` | 不同負載下 SSD 內部時間的分解（node cache 100k trace 的 MQSim closed-loop 重演） |
| `plot_ssd_pq.py` | 2026-11-22 SSD 端 PQ 研究的圖（`reports/2026-11-22_ssd_pq_footprint.md`） |
