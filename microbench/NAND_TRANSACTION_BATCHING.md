# 研究方向：NAND transaction 分批與 SSD pipeline 飽和

## 研究問題

固定總工作量時，SSD 若把一組 NAND transactions 一次排入，或分成數批、隔一段時間再排入，端到端完成時間與每筆 transaction latency 會如何改變？這種差異是否取決於裝置目前的並行度、pipeline 是否充分利用？

例如同一搜尋階段產生 20 筆讀取工作：

```text
一次排入： t=0       [20]
分兩批：   t=0       [10]
           t=Δ                 [10]
```

「排入」需先說清楚是 host request、FTL transaction 還是 NAND operation；它們不是同一單位。

## 可檢驗的假設

1. **Pipeline underfill：**低 outstanding 時，提供更多可執行 transactions 讓更多內部資源並行工作，可能提高吞吐並縮短整組完成時間。
2. **排隊／競爭：**可用平行度已被滿足後，額外併發可能只增加等待；分批或限制 outstanding 可能降低部分 transaction latency。
3. **批間空窗：**分批造成的空窗若讓 channel、die 或 plane 閒置，可能降低吞吐；若批間距剛好讓前批釋放受限資源，也可能改善整組完成時間。
4. **主機效應：**小批次呼叫較頻繁，可能增加應用、OS、driver 的提交成本。需與 SSD 內部 transaction 排程分開量。

這些是假設，不是真機既定事實。Pipeline utilization 與 NAND contention 也可能同時存在。

## 主要量測結果

每個設定保留：

- 從需求產生到最後一筆完成的整組 latency。
- 每筆工作 latency 分布與 p50/p95/p99。
- 完成吞吐、每批完成時間、批間等待時間。
- 預定及實際送入時間、outstanding 隨時間變化；可用時記錄 block／NVMe driver issue 與 completion 時間。
- 各層等待：應用排程、主機提交前、block/driver 後，以及模擬器內部 NAND resource wait。

平均 latency 與整批完成時間不可互相代替。分批可能降低部分單筆 latency，卻因第二批較晚送出而增加整批完成時間。

## 實驗設計

### A. 找外部吞吐平台與排隊轉折

固定 I/O 大小、地址序列／working set、讀寫比例與測試階段，逐步提高 outstanding cap，觀察吞吐和 latency：

- 吞吐增加時，需求可能尚未供滿可用平行度。
- 吞吐趨平而 latency 繼續增加時，新增 outstanding 可能主要形成排隊。
- 這些外部曲線只能指出轉折區域，不能證明 NAND pipeline 已滿，也不能定位哪個 die/channel 是瓶頸。

### B. 固定總工作與平均需求率，改變批次大小／間隔

比較例如 B=1/16/64/256；固定平均到達率 λ 時，批次間隔 Δ=B/λ。條件間使用相同有限請求數與配對地址序列，記錄預定時間與實際提交時間。

批次是同一預定時間點的一組請求，不代表一次 `io_submit`，也不保證 controller 同時收到。需先驗證 host generator 能維持目標節奏。落後時保留 backlog 與 lateness，不能默默丟棄逾期請求或重設時鐘後仍稱固定平均負載。

### C. 比較一次排入與分批排入的 NAND 工作

這需要模擬器能明確控制 NAND transactions 的釋出／排程時間，並在固定總工作量下比較 20 對 10+10 等策略。保持 NAND timing、FTL mapping、channel/die/plane 數與工作量不變，只改交易釋出策略。若 MQSim 現有模型無法直接表達，新增程式行為須對應明確硬體／FTL 行為，並記錄來源與驗證方式。

## 真機與模擬器能回答的問題

- **真實 SSD：**可量 host-visible 吞吐、延遲、block request 及可用的 NVMe driver 邊界。`block_rq_issue` 不必然代表 controller 已取走命令；一般 host tracing 看不到 NAND die 開始執行的時間。
- **MQSim：**校準後可觀察模型中的 FTL transaction、channel/die/plane 排程與等待，並做真機難以直接控制的內部介入。模擬器內部 event 是模型推論，不是黑盒 SSD 的直接量測。
- **交叉驗證：**用多種真機工作負載約束共同 SSD 參數，保留未參與調參的 batch/burst 條件做驗證。外部趨勢吻合支持模型用途，不能單獨證明內部機制。

## 重要混淆因子

- fio/job 數不等於 NVMe queue 或 NAND transaction 數。
- 一個 host request 可能合併、拆分或轉成多個 FTL/NAND operations。
- 同一批的提交耗時、有限 QD 回壓、generator lateness 會改變裝置看到的流量。
- cache、FTL mapping、SSD 快慢狀態、溫度與背景 I/O 都會影響重複測試。
- Continuous refill 與 finite batch 的 warmup/drain/測量窗不同時，不能當成完全配對。
- 最大吞吐點不自動等於 NAND pipeline 滿載；host path 可能先成為瓶頸。

## 建議研究順序

1. 修正並驗證 host-side 到達時間產生器；用 tracing 確認請求經過 OS 到 NVMe driver/block 邊界的時間分布。
2. 量 continuous outstanding sweep 的吞吐平台與 latency 拐點，據此選出低於、接近、高於平台的外部負載區域。
3. 在各區域固定平均負載，掃描 batch size 與 interval；重複並保留時間序列及快慢狀態。
4. 用多條真機曲線約束 MQSim，不只配單一平均 IOPS。
5. 在 MQSim 中做一次排入／分批釋出交易的受控介入，觀察內部資源等待與整組 latency；以保留的外部工作負載驗證。
6. 若多組合理內部參數都能解釋同一外部曲線，報告不可識別性，不把任一組推定為真實 topology。

## 目前立場

研究目標不是預先減少 NAND contention，也不是把 QD 推到最大，而是找出增加供給時何時能填補有用平行度、何時只增加佇列，以及分批是否能在特定負載區間改善 latency/throughput trade-off。Sweet spot 可能是一個區域或 Pareto frontier，不一定是唯一的 batch size 與 interval。
