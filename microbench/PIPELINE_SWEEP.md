# Batch size × demand sweep（探索階段）

研究問題：保留批次間空窗，是否能在接近持續補入的吞吐下改善 latency？
這是主機輸入參數探索，不預設 SSD pipeline 已滿或未滿。尚未建立等實際到達率的因果實驗。

## 參數

固定：4 KiB random direct read、libaio、1 fio job、SIFT100M index 前 32 GiB。
批次組最大 outstanding 512，足以容納單一 batch256，但不保證 OS／NVMe 接收速度。
每個 batch 內仍由 fio 逐筆提交，沒有把它宣稱為單次 io_submit。

| Batch B | 8,192 IOPS | 16,384 IOPS | 24,576 IOPS | 32,768 IOPS |
|---:|---:|---:|---:|---:|
| 16 | 1.953125 ms | 0.9765625 ms | 0.6510417 ms | 0.48828125 ms |
| 64 | 7.8125 ms | 3.90625 ms | 2.6041667 ms | 1.953125 ms |
| 256 | 31.25 ms | 15.625 ms | 10.4166667 ms | 7.8125 ms |

表內為批次間隔 Δ=B/λ，非等上一批完成後再 sleep。
使用 8192 的倍數，讓每次請求數可被所有 batch 大小整除；v3 trace 的微秒時間戳會有小於 1 µs 的取整。
12 個 B×需求量條件覆蓋較低負載、先前約25k平台附近及其上方。平台只是選點依據，不是最大硬體吞吐宣告。

額外 4 個 continuous 基準：QD16/64/256/512，完成後持續補入、不設到達率。
不能把其中任一 QD 直接當作 NAND pipeline 塞滿。

每條件重複 3 次，每次 30 秒。Continuous 另有10秒讀取暖機；batch 組是30秒名目提供區間，全數完成才結束、無暖機。
共48次，名目26分鐘，加啟動、回壓、排空與分析；高需求下可能顯著超時。單次 fio timeout600秒，超時保存 INCOMPLETE。
所有條件每個 repeat 隨機排序，避免固定遞增負載與經過時間完全綁定。
同 repeat、同 λ 的各 batch 完整地址序列與數量一致。Continuous 是時間制，只共享地址 seed，完整序列／數量不保證一致。

## 執行步驟（全部由使用者執行）

```bash
cd /home/emmanuel/projects/MQSim
# 只列計畫，沒有fio或資料集讀取
bash microbench/realssd/run_pipeline_sweep.sh --plan
# 新參數與時序短測，16個條件各2秒，非校準資料
bash microbench/realssd/run_pipeline_sweep.sh --pilot
```

先查看 pilot 的 `pipeline_analysis/report.md` 與 `per_run.csv`，尤其低負載8,192的三個batch：
實際速率是否接近目標、批次起點間隔、批內跨度是否遠小於間隔。不能只看平均速率。
短測結果應交由分析後再決定是否正式執行；**目前沒有自動通過門檻或阻擋，--run 是使用者明確啟動完整探索**。

```bash
# 確認短測與時序限制後，才執行完整掃描
bash microbench/realssd/run_pipeline_sweep.sh --run
```

繼續使用既有 fio v3 replay，已知 pacing drift 尚未修正。這輪不聲稱修好了它。
如果 pilot 低負載也明顯不符合節奏，先修產生器，不要直接浪費時間擴大量測。
高負載的速率不足也可能是回壓；不能當成已達到目標流量，或單憑它判定 NAND 飽和。
未自動執行 block／NVMe driver tracing：先篩選有差異的條件，再另做 driver 觀察與 tracing on/off 對照，避免整個矩陣增加大量觀察開銷。

## 輸出與容量

所有量測先寫 tmpfs，結束後才歸檔至 `microbench/results/mqsim_suite_*`；不改既有資料。
正式計畫詳細 trace/log 的保守預算約13.4 GiB tmpfs，另需1 GiB可用RAM；執行前檢查，空間不足會退出，不會自動刪舊結果。
輸出 `analysis/`（逐筆時序與一般指標）和 `pipeline_analysis/`：

- `per_run.csv`：完成IOPS、mean/p99、實際送入率、批次起點間隔、批內跨度median/p99、最後一筆的相對排程偏差。
- `submission_windows.csv`：每秒實際提交數，明確標記末尾非完整秒。
- `report.md`：各條件四捨五入的三次中位數、時序review數量。完整重複保留在CSV。

時間偏差以第一筆fio submission對齊，不含絕對啟動遲延，不是完整「需求產生到完成」latency。
mean/p99採fio完整JSON；逐筆clat缺少的末尾事件不補造。

## 判讀與後續驗證

先以持續補入各QD的結果建立觀察到的吞吐範圍，再看哪些批次條件接近這個吞吐、同時降低p99。
可預先採用「至少達到continuous各QD中最高中位數IOPS的95%」作篩選門檻，95%只是研究選擇。
時序不匹配、短測、裝置狀態變動或不一致的測試階段，都會限制此比較；不能直接宣布sweet spot。
Continuous有warmup、batch沒有，因此第一輪只是候選篩選，正式確認需對候選點對齊量測階段並加入主機等待時間。
最後才針對選出的候選點增加時間／重複、追蹤OS到NVMe邊界，再決定哪些現象可以用MQSim內部資源解釋。

來源：fio的時序實作 https://github.com/axboe/fio/blob/master/iolog.c 。此設計沒有修改MQSim或SSD硬體參數。
